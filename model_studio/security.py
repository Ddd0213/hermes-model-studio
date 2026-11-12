"""Bounded HTTPS client: no proxy, no redirects, DNS-pinned public destinations."""
from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from urllib.parse import urlsplit, urlunsplit

MAX_RESPONSE = 2 * 1024 * 1024


class StudioError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def normalize_url(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 500:
        raise StudioError("请填写有效的 HTTPS Base URL。")
    value = value.strip().rstrip("/")
    if any(ord(c) <= 32 or ord(c) >= 127 for c in value) or "\\" in value:
        raise StudioError("接口地址包含不允许的字符，请使用标准 ASCII 或 punycode 地址。")
    try:
        u = urlsplit(value)
        port = u.port
    except ValueError:
        raise StudioError("接口地址格式不正确。") from None
    if u.scheme != "https" or not u.hostname or port not in (None, 443):
        raise StudioError("安全策略仅允许 HTTPS 公网接口（443 端口）。")
    if u.username is not None or u.password is not None or u.query or u.fragment:
        raise StudioError("接口地址不能包含凭据、查询参数或锚点。")
    host = u.hostname.lower().rstrip(".")
    if host in {"localhost", "metadata", "metadata.google.internal", "instance-data.ec2.internal"} or host.endswith((".localhost", ".local", ".internal", ".docker.internal")):
        raise StudioError("禁止访问本机、内网或云元数据服务。")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) or ".." in host:
            raise StudioError("接口域名格式不正确。") from None
        authority = host
    else:
        validate_ip(addr)
        authority = f"[{host}]" if addr.version == 6 else host
    # Encoded path separators and traversal are unnecessary for an API base.
    if re.search(r"%0[0-9a-f]|%7f|%2e|%2f|%5c", u.path, re.I) or any(p in {".", ".."} for p in u.path.split("/")):
        raise StudioError("接口路径包含不允许的转义或相对路径。")
    return urlunsplit(("https", authority, u.path.rstrip("/"), "", ""))


def validate_ip(addr):
    if not addr.is_global or addr.is_multicast or addr.is_unspecified or getattr(addr, "ipv4_mapped", None) or (addr.version == 6 and (addr.sixtofour or addr.teredo)):
        raise StudioError("接口解析到非公网或保留地址，已阻止访问。")
    # Alibaba metadata uses an otherwise globally classified range.
    if addr.version == 4 and addr in ipaddress.ip_network("100.64.0.0/10"):
        raise StudioError("禁止访问云元数据或共享地址。")


def public_addresses(host: str):
    try:
        rows = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise StudioError("无法解析接口域名。", 422) from None
    candidates = []
    for family, kind, proto, _, sockaddr in rows:
        validate_ip(ipaddress.ip_address(sockaddr[0]))
        candidate = (family, kind, proto, sockaddr)
        if candidate not in candidates:
            candidates.append(candidate)
    if not candidates:
        raise StudioError("接口域名没有可用地址。", 422)
    return candidates


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, target, *, timeout=12):
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self.target = target

    def connect(self):
        family, kind, proto, sockaddr = self.target
        raw = socket.socket(family, kind, proto)
        raw.settimeout(self.timeout)
        try:
            # No second DNS resolution; TLS still verifies the original hostname/SNI.
            raw.connect(sockaddr)
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def request_json(base_url: str, suffix: str, api_mode: str, key: str = "", *, payload=None, extra_headers=None):
    base = normalize_url(base_url)
    u = urlsplit(base)
    targets = public_addresses(u.hostname)
    if not re.fullmatch(r"[a-z0-9/-]+", suffix):
        raise StudioError("不支持的 API 路径。")
    path = u.path.rstrip("/")
    if api_mode == "anthropic_messages" and not path.endswith("/v1"):
        path += "/v1"
    path += "/" + suffix
    headers = {"Accept": "application/json", "User-Agent": "Hermes-Model-Studio/1.0", "Accept-Encoding": "identity"}
    if key:
        if not isinstance(key, str) or not key.isascii() or len(key) > 4096 or any(ord(c) < 33 or ord(c) == 127 for c in key):
            raise StudioError("API 密钥含有不支持的字符。")
        headers["x-api-key" if api_mode == "anthropic_messages" else "Authorization"] = key if api_mode == "anthropic_messages" else "Bearer " + key
    if api_mode == "anthropic_messages":
        headers["anthropic-version"] = "2023-06-01"
    # Retain pre-existing credential headers only for the exact stored route.
    for name, value in (extra_headers or {}).items():
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9a-zA-Z-]+", str(name)) or str(name).lower() in {"host", "content-length", "transfer-encoding", "connection", "proxy-authorization", "accept-encoding"}:
            continue
        if isinstance(value, str) and value.isascii() and len(value) <= 4096 and not any(ord(c) < 32 or ord(c) == 127 for c in value):
            headers[str(name)] = value
    data = None if payload is None else json.dumps(payload, ensure_ascii=True).encode()
    if data is not None:
        headers["Content-Type"] = "application/json"
    connection = PinnedHTTPS(u.hostname, targets[0])
    deadline = time.monotonic() + 25
    try:
        connection.request("GET" if data is None else "POST", path, body=data, headers=headers)
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise StudioError(f"上游返回 HTTP {response.status}。请检查地址、密钥和接口格式；为保护密钥，不回传上游错误正文。", 422)
        if response.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
            raise StudioError("上游返回了不支持的压缩响应。", 422)
        raw, total = [], 0
        while True:
            if time.monotonic() >= deadline:
                raise StudioError("上游响应超时。", 504)
            block = response.read1(64 * 1024)
            if not block:
                break
            total += len(block)
            if total > MAX_RESPONSE:
                raise StudioError("上游响应超过 2 MB 限制。", 422)
            raw.append(block)
        try:
            return json.loads(b"".join(raw))
        except (ValueError, UnicodeError):
            raise StudioError("上游没有返回有效的 JSON 数据。", 422) from None
    except StudioError:
        raise
    except (OSError, http.client.HTTPException, ssl.SSLError):
        raise StudioError("无法安全连接上游接口；请检查网络和 TLS 证书。", 502) from None
    finally:
        connection.close()
