"""Narrow, revision-checked writes using Hermes' native config/secret lifecycle."""
from __future__ import annotations

import contextlib
import copy
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
from pathlib import Path

from hermes_cli import config as hc
from hermes_constants import get_hermes_home
from .metadata import metadata_for, model_id, positive
from .security import StudioError, normalize_url, public_addresses

_LOCK = threading.RLock()
_REVKEY = secrets.token_bytes(32)
MODES = {"chat_completions", "codex_responses", "anthropic_messages"}
EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}


def text(value, limit, name):
    if not isinstance(value, str) or len(value.strip()) > limit or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise StudioError(f"{name}格式无效或超过长度限制。")
    return value.strip()


def raw_config():
    return hc.require_readable_config_before_write(hc.get_config_path())


def signature():
    # HMAC prevents the revision from becoming an offline credential verifier.
    dig = hmac.new(_REVKEY, digestmod=hashlib.sha256)
    for path in (hc.get_config_path(), hc.get_env_path()):
        try:
            dig.update(path.read_bytes())
        except FileNotFoundError:
            dig.update(b"missing")
    return dig.hexdigest()


def revision_check(expected):
    if not isinstance(expected, str) or not hmac.compare_digest(expected, signature()):
        raise StudioError("配置已在其他位置变化，请刷新后重试。", 409)


@contextlib.contextmanager
def mutation():
    # Share Dashboard's own lock with native Config/Keys writes when in that host.
    host = sys.modules.get("hermes_cli.web_server")
    native = getattr(host, "_CONFIG_MUTATION_LOCK", None)
    lock = native if native is not None else contextlib.nullcontext()
    with _LOCK, lock:
        base = get_hermes_home() / "plugin-data/model-studio"
        if base.is_symlink():
            raise StudioError("插件状态目录不可为符号链接。", 500)
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(base, 0o700)
        fd = os.open(base / "write.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def write_config(cfg):
    # Full raw document has just been read; only explicitly targeted fields changed.
    # Native backend retains comments and refuses invalid/unreadable or managed files.
    from hermes_cli.config_backend import get_config_backend
    backend = get_config_backend()
    if backend.name != "file" or hc.is_managed():
        raise StudioError("当前安装由管理员或远程配置管理，此插件只读。", 409)
    hc.atomic_config_replace(hc.get_config_path(), cfg)
    os.chmod(hc.get_config_path(), 0o600)
    hc.invalidate_env_cache()


def base_of(entry):
    return str(entry.get("base_url") or entry.get("url") or entry.get("api") or "").strip().rstrip("/")


def mode_of(entry):
    raw = entry.get("api_mode") or entry.get("transport") or "chat_completions"
    return {"openai-completions": "chat_completions", "openai-responses": "codex_responses", "anthropic-messages": "anthropic_messages"}.get(raw, raw)


def expand(value, env):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\$\{([A-Z_a-z][A-Z_a-z0-9]*)\}", lambda m: env.get(m[1], ""), value)


def credentials(entry):
    env = hc.load_env()
    name = entry.get("key_env") or entry.get("api_key_env")
    key = str(env.get(name) or "") if name else expand(entry.get("api_key", ""), env)
    headers = {str(k): expand(v, env) for k, v in entry.get("extra_headers", {}).items()} if isinstance(entry.get("extra_headers"), dict) else {}
    return key, headers


def current(cfg):
    m = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    return {"provider": str(m.get("provider") or ""), "model": str(m.get("default") or m.get("name") or ""), "base": str(m.get("base_url") or "").rstrip("/")}


def active(cfg, pid, entry):
    cur = current(cfg)
    # Hermes normalizes named custom providers to the wire provider "custom";
    # identity then comes from the exact route URL, not the literal label.
    if cur["provider"] == "custom":
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        configured = providers.get("custom") if isinstance(providers.get("custom"), dict) else {}
        route = cur["base"] or base_of(configured)
        return bool(route) and route == base_of(entry)
    return cur["provider"] in {pid, "custom:" + pid} and (not cur["base"] or cur["base"] == base_of(entry))


def models_of(entry):
    models = entry.get("models")
    if isinstance(models, dict):
        return models
    if isinstance(models, list):
        return {x: {} for x in models if isinstance(x, str)}
    fallback = entry.get("default_model") or entry.get("model")
    return {fallback: {}} if isinstance(fallback, str) and fallback else {}


def provider_row(cfg, pid, entry, rev):
    mode = mode_of(entry)
    key, headers = credentials(entry)
    try:
        base = normalize_url(base_of(entry))
        allowed = mode in MODES and not entry.get("key_cmd")
    except StudioError:
        base, allowed = base_of(entry), False
    models = []
    for mid, raw in list(models_of(entry).items())[:200]:
        if not isinstance(mid, str) or len(mid) > 160:
            continue
        raw = raw if isinstance(raw, dict) else {}
        own = raw.get("_model_studio") if isinstance(raw.get("_model_studio"), dict) else {}
        meta = metadata_for(mid, base, mode)
        row = {"id": mid, "context_length": (own.get("context_length_override") if "context_length_override" in own else positive(raw.get("context_length"))),
               "max_output_tokens": own.get("max_output_tokens"), "reasoning_mode": own.get("reasoning_mode", "auto"),
               "reasoning_effort": own.get("reasoning_effort"), "thinking_budget": own.get("thinking_budget"),
               "effort_levels": own.get("effort_levels", []), "metadata": meta}
        models.append(row)
    info = entry.get("_model_studio") if isinstance(entry.get("_model_studio"), dict) else {}
    fallback = str(entry.get("model") or entry.get("default_model") or (models[0]["id"] if models else ""))
    return {"id": pid, "name": str(entry.get("name") or pid)[:160], "note": str(info.get("note") or "")[:400],
            "base_url": base, "api_mode": mode, "has_key": bool(key), "has_extra_headers": bool(headers),
            "default_model": fallback, "models": models, "is_current": active(cfg, pid, entry),
            "editable": allowed, "revision": rev,
            "warning": "此服务保留服务器上已有的额外请求头；网页不读取或修改它们。" if headers else ("此服务格式或认证方式不受本页支持，保留只读。" if not allowed else "")}


def list_providers():
    with _LOCK:
        cfg = raw_config()
        rev = signature()
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        rows = [provider_row(cfg, str(pid), entry, rev) for pid, entry in providers.items() if isinstance(entry, dict) and base_of(entry)]
        cur = current(cfg)
        return {"providers": rows, "current": {k: cur[k] for k in ["provider", "model"]}, "revision": rev, "runtime_enabled": True}


def find(cfg, pid):
    if not isinstance(pid, str) or not pid or len(pid) > 100:
        raise StudioError("服务 ID 无效。")
    providers = cfg.get("providers")
    if not isinstance(providers, dict) or not isinstance(providers.get(pid), dict):
        raise StudioError("服务不存在，请刷新页面。", 404)
    return providers[pid]


def parse_models(values, api_mode):
    if not isinstance(values, list) or not 1 <= len(values) <= 200:
        raise StudioError("请选择 1–200 个模型。")
    result = {}
    for value in values:
        if not isinstance(value, dict):
            raise StudioError("模型设置格式无效。")
        mid = model_id(value.get("id"))
        if mid in result:
            raise StudioError("模型 ID 不能重复。")
        row = {}
        for key, maximum in [("context_length", 4_000_000), ("max_output_tokens", 1_000_000), ("thinking_budget", 1_000_000)]:
            raw = value.get(key)
            if raw not in (None, "") and positive(raw, maximum) is None:
                raise StudioError(f"{mid} 的 {key} 必须是范围内的正整数。")
            row[key] = positive(raw, maximum)
        mode = value.get("reasoning_mode", "auto")
        if mode not in {"auto", "off", "effort", "budget", "omit"}:
            raise StudioError("思考模式无效。")
        effort = value.get("reasoning_effort") or None
        if effort and effort not in EFFORTS:
            raise StudioError("思考等级不受 Hermes 支持。")
        levels = value.get("effort_levels", [])
        if not isinstance(levels, list) or len(levels) > 8 or any(x not in EFFORTS for x in levels):
            raise StudioError("思考等级列表无效。")
        if mode == "effort" and (not effort or (levels and effort not in levels)):
            raise StudioError("请选择一个有效思考等级。")
        if mode == "budget" and (api_mode != "anthropic_messages" or not row["thinking_budget"] or row["thinking_budget"] < 1024):
            raise StudioError("独立思考预算仅支持 Anthropic Messages，且至少为 1024。")
        if row["context_length"] and row["max_output_tokens"] and row["max_output_tokens"] >= row["context_length"]:
            raise StudioError("单次最大输出必须小于上下文窗口。")
        if mode == "budget" and (not row["max_output_tokens"] or row["thinking_budget"] + 1024 > row["max_output_tokens"]):
            raise StudioError("思考预算需要设置最大输出，并至少保留 1024 Token 用于回答。")
        row.update(reasoning_mode=mode, reasoning_effort=effort, effort_levels=list(dict.fromkeys(levels)))
        result[mid] = row
    return result


def clean_key(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.isascii() or not 1 <= len(value) <= 4096 or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise StudioError("密钥格式无效：请勿包含空格或换行。")
    if value.startswith("${") or "..." in value or "••" in value:
        raise StudioError("请输入完整的新密钥，不能保存脱敏占位符。")
    return value


def request_credentials(body):
    base = normalize_url(body.get("base_url", ""))
    mode = body.get("api_mode", "chat_completions")
    if mode not in MODES:
        raise StudioError("接口格式不受支持。")
    new_key = clean_key(body.get("api_key"))
    pid = body.get("id")
    if not pid:
        return base, mode, new_key or "", {}
    cfg = raw_config()
    revision_check(body.get("revision"))
    entry = find(cfg, pid)
    old_key, headers = credentials(entry)
    same_target = base == normalize_url(base_of(entry)) and mode == mode_of(entry)
    if not same_target:
        if headers:
            raise StudioError("此服务有额外认证请求头。更换地址或接口格式请新建服务，避免泄漏旧凭据。")
        if old_key and not new_key:
            raise StudioError("更换地址或接口格式后，请重新输入密钥。")
        headers = {}
    return base, mode, new_key or (old_key if same_target else ""), headers


def save_provider(body):
    name = text(body.get("name", ""), 100, "服务名称")
    if not name:
        raise StudioError("请填写服务名称。")
    note = text(body.get("note", ""), 400, "备注")
    base = normalize_url(body.get("base_url", ""))
    public_addresses(__import__("urllib.parse", fromlist=["urlsplit"]).urlsplit(base).hostname)
    api_mode = body.get("api_mode", "chat_completions")
    if api_mode not in MODES:
        raise StudioError("接口格式不受支持。")
    models = parse_models(body.get("models"), api_mode)
    default = model_id(body.get("default_model") or next(iter(models)))
    if default not in models:
        raise StudioError("默认模型必须在已添加列表中。")
    new_key = clean_key(body.get("api_key"))
    if body.get("clear_key"):
        raise StudioError("安全起见，清除密钥请删除未启用的服务；编辑时留空表示保留。")
    with mutation():
        cfg = raw_config()
        revision_check(body.get("revision"))
        existing = copy.deepcopy(find(cfg, body["id"])) if body.get("id") else {}
        if existing:
            if existing.get("key_cmd") or mode_of(existing) not in MODES:
                raise StudioError("此服务使用本页不支持的认证或接口格式，保持只读。", 409)
            request_credentials(body)
        providers = cfg.setdefault("providers", {})
        if not isinstance(providers, dict):
            raise StudioError("现有 providers 配置格式不兼容。", 409)
        pid = body.get("id") or "custom-" + secrets.token_hex(6)
        if not existing and pid in providers:
            raise StudioError("服务 ID 冲突，请重试。", 409)
        entry = copy.deepcopy(existing)
        entry.update(name=name, base_url=base, default_model=default, api_mode=api_mode, discover_models=False)
        for k in ["api", "url", "transport", "model"]:
            entry.pop(k, None)
        old_models = models_of(existing)
        mapped = {}
        for mid, row in models.items():
            raw = copy.deepcopy(old_models.get(mid)) if isinstance(old_models.get(mid), dict) else {}
            auto = metadata_for(mid, base, api_mode)
            effective_context = row["context_length"] or auto.get("context_length")
            if effective_context:
                raw["context_length"] = effective_context
            else:
                raw.pop("context_length", None)
            raw["_model_studio"] = {k: v for k, v in row.items() if k != "context_length"}
            raw["_model_studio"]["context_length_override"] = row["context_length"]
            mapped[mid] = raw
        entry["models"] = mapped
        entry["_model_studio"] = {"version": 1, "note": note}
        old_key, _ = credentials(existing)
        stored_key = new_key or old_key
        new_env = None
        old_env = existing.get("key_env") or existing.get("api_key_env")
        before = signature()
        try:
            # Unique slot makes a failed save reversible without disturbing the old secret.
            if new_key or (existing.get("api_key") and stored_key):
                new_env = "HERMES_CUSTOM_STUDIO_" + secrets.token_hex(8).upper() + "_API_KEY"
                hc.save_env_value(new_env, stored_key)
                if hc.load_env().get(new_env) != stored_key:
                    raise StudioError("密钥保存失败，配置未更新。", 500)
                entry["key_env"] = new_env
                entry.pop("api_key_env", None)
                entry.pop("api_key", None)
            providers[pid] = entry
            # Keep main-route mirror coherent only if this is the currently active service.
            if existing and active(cfg, pid, existing):
                main = cfg.get("model", {})
                if not isinstance(main, dict):
                    raise StudioError("默认模型配置格式不兼容。", 409)
                main["base_url"] = base
                main["api_mode"] = api_mode
                if new_env:
                    main["key_env"] = new_env
                    main.pop("api_key", None)
                if main.get("default") not in mapped:
                    raise StudioError("不能移除正在使用的默认模型，请先切换默认模型。", 409)
                # A deliberate per-model context edit must update the active route's mirror.
                selected_model = main.get("default")
                old_cfg = old_models.get(selected_model) if isinstance(old_models.get(selected_model), dict) else {}
                new_cfg = mapped.get(selected_model, {})
                if old_cfg.get("context_length") != new_cfg.get("context_length"):
                    if new_cfg.get("context_length"):
                        main["context_length"] = new_cfg["context_length"]
                    else:
                        main.pop("context_length", None)
            write_config(cfg)
        except BaseException:
            if new_env:
                hc.remove_env_value(new_env)
            raise
        # Only remove obsolete secrets owned by this plugin and not referenced elsewhere.
        if new_env and isinstance(old_env, str) and old_env.startswith("HERMES_CUSTOM_STUDIO_") and old_env not in json.dumps(cfg):
            hc.remove_env_value(old_env)
        rows = list_providers()
        return next(r for r in rows["providers"] if r["id"] == pid)


def activate_provider(body):
    if body.get("confirm") is not True:
        raise StudioError("请确认切换默认模型。")
    with mutation():
        cfg = raw_config()
        revision_check(body.get("revision"))
        pid = body.get("id")
        entry = find(cfg, pid)
        mid = model_id(body.get("model"))
        if mid not in models_of(entry):
            raise StudioError("该模型不在此服务的已添加列表中。")
        base = normalize_url(base_of(entry))
        if mode_of(entry) not in MODES:
            raise StudioError("此服务的接口格式不受支持。")
        # Match the native model assignment validation (e.g. minimum context and route identity).
        from hermes_cli.web_server_config import _apply_main_model_assignment, _validated_main_model_selection
        selected = _validated_main_model_selection(cfg, pid, mid, base)
        model = _apply_main_model_assignment(cfg.get("model", {}), selected)
        model["api_mode"] = mode_of(entry)
        if entry.get("key_env"):
            model["key_env"] = entry["key_env"]
            model.pop("api_key", None)
        cfg["model"] = model
        write_config(cfg)


def delete_provider(body):
    if body.get("confirm") is not True:
        raise StudioError("请确认删除服务。")
    with mutation():
        cfg = raw_config()
        revision_check(body.get("revision"))
        pid = body.get("id")
        entry = find(cfg, pid)
        if active(cfg, pid, entry):
            raise StudioError("正在使用的服务不能删除，请先切换默认服务。", 409)
        # Refuse deletion if another config route references this provider or its secret.
        other = copy.deepcopy(cfg)
        other["providers"].pop(pid)
        key_env = entry.get("key_env")
        if re.search(r"(?<![A-Za-z0-9_-])" + re.escape(str(pid)) + r"(?![A-Za-z0-9_-])", json.dumps(other, ensure_ascii=False)):
            raise StudioError("其他模型路由仍在引用此服务，请先解除引用。", 409)
        write_config(other)
        if isinstance(key_env, str) and key_env.startswith("HERMES_CUSTOM_STUDIO_") and key_env not in json.dumps(other):
            hc.remove_env_value(key_env)
