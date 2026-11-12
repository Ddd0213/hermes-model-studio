"""Local capability enrichment. No download, telemetry or model-name substring guesses."""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from hermes_constants import get_hermes_home
from .security import StudioError, normalize_url, request_json

_LOCK = threading.RLock()
_CATALOG_CACHE = (None, {})
_REMOTE = {}


def model_id(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 160 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise StudioError("模型 ID 应为 1–160 个字符，且不能包含控制字符。")
    return value.strip()


def positive(value, maximum=4_000_000):
    return value if type(value) is int and 0 < value <= maximum else None


def effort_values(raw):
    values = []
    for opt in raw.get("reasoning_options", []) if isinstance(raw.get("reasoning_options"), list) else []:
        if isinstance(opt, dict) and opt.get("type") in {"effort", "reasoning_effort"}:
            for v in opt.get("values", []) if isinstance(opt.get("values"), list) else []:
                if isinstance(v, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,23}", v) and v not in values:
                    values.append(v)
    return values[:8]


def normalize_metadata(raw, source):
    limit = raw.get("limit") if isinstance(raw.get("limit"), dict) else {}
    context = next((positive(raw.get(k)) for k in ["context_length", "context_window", "contextWindow", "inputTokenLimit"] if positive(raw.get(k))), None) or positive(limit.get("context"))
    output = next((positive(raw.get(k), 1_000_000) for k in ["max_output_tokens", "max_tokens", "maxTokens", "outputTokenLimit"] if positive(raw.get(k), 1_000_000)), None) or positive(limit.get("output"), 1_000_000)
    reasoning = raw.get("supports_reasoning", raw.get("reasoning"))
    reasoning = reasoning if isinstance(reasoning, bool) else None
    levels = effort_values(raw)
    raw_levels = raw.get("effort_levels", raw.get("reasoning_effort_values", []))
    if not levels and isinstance(raw_levels, list):
        levels = list(dict.fromkeys(v for v in raw_levels if isinstance(v, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,23}", v)))[:8]
    return {"context_length": context, "max_output_tokens": output, "supports_reasoning": reasoning,
            "effort_levels": levels, "family": str(raw.get("family") or "")[:80],
            "source": source if context or output or reasoning is not None or levels else "unknown"}


def catalog():
    global _CATALOG_CACHE
    path = get_hermes_home() / "models_dev_cache.json"
    if not path.is_file():
        # Named profile fallback: read the root cache, never credentials/config from another profile.
        path = Path.home() / ".hermes/models_dev_cache.json"
    try:
        st = path.stat()
        signature = (str(path), st.st_mtime_ns, st.st_size)
        with _LOCK:
            if signature == _CATALOG_CACHE[0]:
                return _CATALOG_CACHE[1]
            if st.st_size > 32 * 1024 * 1024:
                return {}
            value = json.loads(path.read_text())
            if not isinstance(value, dict):
                return {}
            _CATALOG_CACHE = signature, value
            return value
    except (OSError, ValueError):
        return {}


def catalog_metadata(mid, base_url):
    data = catalog()
    host = urlsplit(base_url).hostname
    scoped = []
    for pid, entry in data.items():
        if not isinstance(entry, dict):
            continue
        if urlsplit(str(entry.get("api") or "")).hostname == host:
            scoped.append(entry)
    candidates = []
    for entry in scoped or data.values():
        if not isinstance(entry, dict) or not isinstance(entry.get("models"), dict):
            continue
        for ident, raw in entry["models"].items():
            if str(ident).lower() == mid.lower() and isinstance(raw, dict):
                candidates.append(normalize_metadata(raw, "catalog"))
    if not candidates:
        # Only exact, vendor-qualified final IDs; never strip semantic suffixes.
        bare = mid.split("/")[-1].lower()
        for pid in ["anthropic", "openai", "google", "deepseek", "moonshotai", "zai", "minimax"]:
            entry = data.get(pid, {})
            for ident, raw in (entry.get("models") or {}).items() if isinstance(entry, dict) else []:
                if str(ident).split("/")[-1].lower() == bare and isinstance(raw, dict):
                    candidates.append(normalize_metadata(raw, "catalog"))
    if not candidates:
        return normalize_metadata({}, "unknown")
    result = {"source": "catalog"}
    for key in ["context_length", "max_output_tokens", "supports_reasoning", "effort_levels", "family"]:
        vals = [x.get(key) for x in candidates if x.get(key) not in (None, "", [])]
        result[key] = vals[0] if vals and all(v == vals[0] for v in vals) else ([] if key == "effort_levels" else None)
    if not any(result.get(k) is not None for k in ["context_length", "max_output_tokens", "supports_reasoning"]) and not result["effort_levels"]:
        result["source"] = "unknown"
    return result


def metadata_for(mid, base_url, api_mode="chat_completions"):
    result = catalog_metadata(mid, base_url)
    with _LOCK:
        remote = _REMOTE.get((base_url, api_mode), (0, {}))
        raw = remote[1].get(mid) if time.monotonic() - remote[0] < 3600 else None
    if raw:
        over = normalize_metadata(raw, "endpoint")
        for key, value in over.items():
            if value not in (None, "", [], "unknown"):
                result[key] = value
    return result


def discover(base_url, api_mode, key, headers=None):
    base_url = normalize_url(base_url)
    payload = request_json(base_url, "models", api_mode, key, extra_headers=headers)
    values = payload.get("data", payload.get("models", [])) if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        raise StudioError("接口未返回有效模型列表。", 422)
    parsed = {}
    for raw in values[:2000]:
        if isinstance(raw, str):
            raw = {"id": raw}
        if not isinstance(raw, dict):
            continue
        secret_values = [v for v in [key, *(headers or {}).values()] if isinstance(v, str) and len(v) >= 6]
        serialized = json.dumps(raw, ensure_ascii=False)
        if any(secret in serialized for secret in secret_values):
            continue  # Never forward an accidental credential echo from an upstream.
        ident = raw.get("id") or raw.get("slug") or raw.get("name")
        try:
            ident = model_id(ident)
        except StudioError:
            continue
        parsed[ident] = raw
        if len(parsed) >= 200:
            break
    if not parsed:
        raise StudioError("接口返回成功，但没有可用模型 ID；请确认地址不是网站首页。", 422)
    with _LOCK:
        if len(_REMOTE) >= 20:
            _REMOTE.pop(next(iter(_REMOTE)))
        _REMOTE[(base_url, api_mode)] = (time.monotonic(), parsed)
    return [{"id": mid, "metadata": metadata_for(mid, base_url, api_mode)} for mid in parsed]
