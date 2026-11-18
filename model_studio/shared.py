"""Machine-wide provider sharing; profile identities and non-model settings stay local."""
from __future__ import annotations
import contextlib
import copy
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from hermes_cli import config as hc
from hermes_constants import get_hermes_home, set_hermes_home_override, reset_hermes_home_override
from .security import StudioError

ROOT = Path(__file__).resolve().parents[3]
FIELDS = ("supports_vision", "supports_tools", "supports_reasoning")
_STATUS = {"ok": False, "profiles": [], "error": "", "checked_at": 0}
_WORKER_LOCK = threading.Lock()
_WORKER = None

@contextlib.contextmanager
def scope(home):
    from agent.secret_scope import build_profile_secret_scope, set_secret_scope, reset_secret_scope
    home = Path(home)
    ht = set_hermes_home_override(home)
    st = set_secret_scope(build_profile_secret_scope(home), profile_home=str(home))
    try:
        yield
    finally:
        reset_secret_scope(st)
        reset_hermes_home_override(ht)

def homes():
    from hermes_constants import PROFILE_ID_RE, named_profile_is_live
    result = [("default", ROOT)]
    parent = ROOT / "profiles"
    if parent.is_dir():
        for p in sorted(parent.iterdir()):
            if p.name != "default" and PROFILE_ID_RE.fullmatch(p.name) and not p.is_symlink() and named_profile_is_live(p):
                if (p / "config.yaml").is_file():
                    result.append((p.name, p))
    return result

def read_config(home):
    with scope(home):
        return hc.require_readable_config_before_write(Path(home) / "config.yaml")

def state_path(home):
    return Path(home) / "plugin-data/model-studio/shared.json"

def read_state(home):
    p = state_path(home)
    if not p.exists():
        return {}
    data = json.loads(p.read_text())
    if not isinstance(data, dict):
        raise StudioError("同步状态无效，请检查服务器。", 409)
    return data

def source_providers(cfg):
    from .store import base_of, mode_of, MODES
    return {k: copy.deepcopy(v) for k, v in (cfg.get("providers") or {}).items()
            if isinstance(v, dict) and base_of(v) and mode_of(v) in MODES and not v.get("key_cmd")}

def refs_for(entry):
    credential_fields = {k:entry[k] for k in ("api_key", "base_url", "extra_headers") if k in entry}
    refs = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", json.dumps(credential_fields)))
    for k in ("key_env", "api_key_env"):
        if isinstance(entry.get(k), str): refs.add(entry[k])
    return refs

def capability(cfg, pid, mid, raw, field):
    own = raw.get("_model_studio") or {}
    if field in own:
        return own[field] if type(own[field]) is bool else None
    if type(raw.get(field)) is bool:
        return raw[field]
    overrides = cfg.get("model_overrides") or {}
    for ident in (pid, "custom:" + pid, "custom"):
        val = ((overrides.get(ident) or {}).get(mid) or {}).get(field)
        if type(val) is bool: return val
    return None

def sync_capabilities(cfg, providers, previous):
    from .store import models_of
    overrides = cfg.setdefault("model_overrides", {})
    # Only fields controlled by Model Studio; preserve unrelated metadata.
    for pid, mids in previous.items():
        for ident in {pid, "custom:" + pid}:
            section = overrides.get(ident)
            if not isinstance(section, dict): continue
            for mid in mids:
                row = section.get(mid)
                if isinstance(row, dict):
                    for f in FIELDS: row.pop(f, None)
                    if not row: section.pop(mid, None)
            if not section: overrides.pop(ident, None)
    by_model = {}
    for pid, entry in providers.items():
        for mid, raw in models_of(entry).items():
            raw = raw if isinstance(raw, dict) else {}
            caps = {f: raw[f] for f in FIELDS if type(raw.get(f)) is bool}
            for ident in {pid, "custom:" + pid}:
                row = overrides.setdefault(ident, {}).setdefault(mid, {})
                for f in FIELDS: row.pop(f, None)
                row.update(caps)
                if not row: overrides[ident].pop(mid, None)
                if not overrides[ident]: overrides.pop(ident, None)
            by_model.setdefault(mid, []).append(caps)
    # Native custom transports normalize the provider to "custom". Avoid false claims
    # for duplicate model IDs on different endpoints; runtime middleware remains scoped.
    section = overrides.setdefault("custom", {})
    all_ids = set(by_model) | {m for mids in previous.values() for m in mids}
    for mid in all_ids:
        row = section.setdefault(mid, {})
        for f in FIELDS:
            row.pop(f, None)
            vals = [x.get(f) for x in by_model.get(mid, [])]
            if vals and type(vals[0]) is bool and all(v == vals[0] for v in vals): row[f] = vals[0]
        if not row: section.pop(mid, None)
    if not section: overrides.pop("custom", None)
    if not overrides: cfg.pop("model_overrides", None)

def check_removal_routes(cfg, pid, entry):
    """Check actual inference routes in one bot, including legacy and named routes."""
    from .store import base_of

    def check(route):
        if not isinstance(route, dict):
            return
        provider = route.get("provider")
        base = str(route.get("base_url") or "").rstrip("/")
        if provider == "custom" and base:
            referenced = base == base_of(entry)
        else:
            referenced = provider in (pid, "custom:" + pid)
        if referenced:
            raise StudioError("某个 Bot 仍在使用此供应商，请先为该 Bot 选择其他供应商。", 409)

    def check_chain(chain):
        for route in chain if isinstance(chain, list) else [chain]:
            check(route)

    check(cfg.get("model"))
    for section in ("auxiliary", "model_aliases"):
        routes = cfg.get(section)
        if isinstance(routes, dict):
            for route in routes.values():
                check(route)
    moa = cfg.get("moa") or {}
    presets = moa.get("presets") if isinstance(moa, dict) else None
    if isinstance(presets, dict):
        for preset in presets.values():
            if isinstance(preset, dict):
                check(preset.get("aggregator"))
                check_chain(preset.get("reference_models"))
    delegation = cfg.get("delegation") or {}
    if isinstance(delegation, dict):
        check(delegation)
        check_chain(delegation.get("fallback_providers"))
    for section in ("fallback_providers", "fallback_model"):
        check_chain(cfg.get(section))

def repair_route(route, pid, old, new, *, main=False):
    from .store import base_of, mode_of, models_of
    if not isinstance(route, dict): return
    rp = str(route.get("provider") or "")
    base = str(route.get("base_url") or "").rstrip("/")
    matches = rp in {pid, "custom:" + pid} and (pid != "custom" or not base or base == base_of(old))
    matches = matches or (rp == "custom" and base and base == base_of(old))
    if not matches: return
    if new is None:
        raise StudioError("某个 Bot 仍在使用此供应商，请先为该 Bot 选择其他供应商。", 409)
    mids = models_of(new)
    field = "default" if main else "model"
    mid = route.get(field) or route.get("name")
    if mid and mid not in mids:
        mid = new.get("default_model") or next(iter(mids), "")
        if not mid: raise StudioError("共享供应商必须保留至少一个模型。", 409)
        route[field] = mid
        route.pop("name", None)
    route["base_url"] = base_of(new)
    route["api_mode"] = mode_of(new)
    for k in ("api_key", "api_key_env", "key_env"):
        route.pop(k, None)
    for k in ("api_key", "api_key_env", "key_env"):
        if k in new: route[k] = new[k]
    if mid in mids and isinstance(mids[mid], dict):
        ctx = mids[mid].get("context_length")
        if ctx: route["context_length"] = ctx
        else: route.pop("context_length", None)
    # Model-level capabilities otherwise shadow every per-model vision selection.
    for f in FIELDS: route.pop(f, None)

def plan_profile(home, current, source, source_cfg, source_env, original=None):
    from .store import models_of, base_of
    cfg = copy.deepcopy(current)
    # Photos belong to the model selected in the conversation, not an auxiliary
    # describer. Share the explicit native-input policy with existing and future
    # bots; it changes image routing only and never enables the vision toolset.
    cfg.setdefault("agent", {})["image_input_mode"] = "native"
    disabled = cfg["agent"].setdefault("disabled_toolsets", [])
    if not isinstance(disabled, list):
        raise StudioError("Bot 的禁用工具集配置无效，请检查 disabled_toolsets。", 409)
    if "vision" not in disabled:
        disabled.append("vision")
    state = read_state(home)
    owned = set(state.get("providers", []))
    providers = cfg.setdefault("providers", {})
    original_providers = (original or current).get("providers") or {}
    for pid, entry in providers.items():
        if isinstance(entry, dict) and (entry.get("_model_studio") or {}).get("shared"): owned.add(pid)
    previous = state.get("models", {})
    previous = {**{k: list(models_of(v)) for k,v in providers.items() if k in owned and isinstance(v,dict)}, **previous}
    for pid in owned | set(source):
        old = original_providers.get(pid) or providers.get(pid) or source.get(pid) or {}
        if not old and state.get("routes", {}).get(pid):
            old = {"base_url": state["routes"][pid]}
        new = source.get(pid)
        if home != ROOT and pid in providers and pid not in owned and new:
            if base_of(old) != base_of(new):
                raise StudioError("Bot 中存在同名但地址不同的供应商，请先解决配置冲突。", 409)
        repair_route(cfg.get("model"), pid, old, new, main=True)
        def visit(value):
            if isinstance(value, dict):
                if "provider" in value: repair_route(value, pid, old, new)
                for item in value.values(): visit(item)
            elif isinstance(value, list):
                for item in value: visit(item)
        visit(cfg.get("auxiliary", {}))
        visit(cfg.get("fallback_providers", []))
        if new is None: providers.pop(pid, None)
        else: providers[pid] = copy.deepcopy(new)
    sync_capabilities(cfg, source, previous)
    if home != ROOT:
        plugins = cfg.setdefault("plugins", {})
        enabled = plugins.setdefault("enabled", [])
        if "model-studio" not in enabled: enabled.append("model-studio")
        if "model-studio" in plugins.get("disabled", []): plugins["disabled"].remove("model-studio")
    needed = set().union(*(refs_for(e) for e in source.values())) if source else set()
    missing = [k for k in needed if k not in source_env]
    if missing: raise StudioError("共享供应商引用的密钥或环境变量不存在，配置未同步。", 409)
    history = copy.deepcopy(state.get("route_history", {}))
    for pid, url in state.get("routes", {}).items():
        if url: history[pid] = list(dict.fromkeys([*history.get(pid, []), url]))[-20:]
    for pid, entry in source.items():
        url = base_of(entry)
        if url: history[pid] = list(dict.fromkeys([*history.get(pid, []), url]))[-20:]
    new_state = {"route_history": history, "version": 1, "providers": sorted(source), "models": {k:list(models_of(e)) for k,e in source.items()},
                 "env": sorted(needed), "routes": {k:base_of(e) for k,e in source.items()}}
    return cfg, {k:source_env[k] for k in needed}, state, new_state

def _bytes(p):
    return p.read_bytes() if p.exists() else None

def _atomic_bytes(p, value, mode=0o600):
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if p.is_symlink(): raise StudioError("共享配置文件不能是符号链接。", 409)
    tmp = p.with_name(p.name + ".studio-" + str(os.getpid()) + "-" + str(threading.get_ident()))
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, "wb") as f: f.write(value); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, p)
        os.chmod(p, mode)
    finally:
        if tmp.exists(): tmp.unlink()

def commit(source_cfg=None, *, removed_provider=None):
    """Caller holds store.mutation() in root scope. Preflight all profiles; rollback failures in memory."""
    from .store import models_of
    source_cfg = copy.deepcopy(source_cfg if source_cfg is not None else read_config(ROOT))
    source = source_providers(source_cfg)
    for pid, entry in source.items():
        entry.setdefault("_model_studio", {})["shared"] = True
        for mid, raw in models_of(entry).items():
            if not isinstance(raw, dict): continue
            for field in FIELDS:
                value = capability(source_cfg, pid, mid, raw, field)
                if value is not None: raw[field] = value
                else: raw.pop(field, None)
    with scope(ROOT): source_env = hc.load_env()
    roster = homes()
    plans = []
    for name, home in roster:
        current = source_cfg if home == ROOT else read_config(home)
        original = read_config(home) if home == ROOT else current
        if removed_provider is not None:
            pid, source_entry = removed_provider
            local_entry = (original.get("providers") or {}).get(pid) or source_entry
            check_removal_routes(original, pid, local_entry)
        new_cfg, env, previous, state = plan_profile(home, current, source, source_cfg, source_env,
                                                      original=original)
        link = home / "plugins/model-studio"
        if home != ROOT and link.exists() and link.resolve() != ROOT / "plugins/model-studio":
            raise StudioError("某个 Bot 已安装独立的 Model Studio，请先统一插件来源。", 409)
        paths = [home/"config.yaml", home/".env", state_path(home)]
        if any(p.is_symlink() for p in paths): raise StudioError("共享配置文件不能是符号链接。", 409)
        plans.append((name,home,new_cfg,env,previous,state,{p:_bytes(p) for p in paths}))
    applied = []
    links = []
    try:
        # Source is committed last; interrupted follower writes converge from the source on retry.
        for name,home,cfg,env,previous,state,before in sorted(plans, key=lambda x:x[1]==ROOT):
            with scope(home):
                if hc.is_managed(): raise StudioError("当前安装由管理员或远程配置管理，此插件只读。", 409)
                if any(_bytes(p) != val for p,val in before.items()):
                    raise StudioError("配置已在其他位置变化，请刷新后重试。", 409)
                applied.append((before, {}))
                written = applied[-1][1]
                try:
                    actual = hc.load_env()
                    for k,v in env.items():
                        if actual.get(k) != v: hc.save_env_value(k,v)
                    referenced = json.dumps(cfg)
                    for k in previous.get("env", []):
                        if k not in env and k.startswith("HERMES_CUSTOM_STUDIO_") and k not in referenced:
                            hc.remove_env_value(k)
                    if hc.load_env() | env != hc.load_env(): raise StudioError("密钥保存失败，配置未更新。", 500)
                    if read_config(home) != cfg: hc.atomic_config_replace(home/"config.yaml", cfg)
                    os.chmod(home/"config.yaml", 0o600)
                    if _bytes(state_path(home)) != (payload := (json.dumps(state,ensure_ascii=False,sort_keys=True)+"\n").encode()):
                        _atomic_bytes(state_path(home),payload)
                finally:
                    written.update({p:_bytes(p) for p in before})
                if home != ROOT:
                    link = home/"plugins/model-studio"
                    if not link.exists():
                        link.parent.mkdir(parents=True,exist_ok=True)
                        link.symlink_to(ROOT/"plugins/model-studio",target_is_directory=True)
                        links.append(link)
        _STATUS.update(ok=True, profiles=[n for n,h in roster], error="", checked_at=int(time.time()))
    except BaseException:
        for before,written in reversed(applied):
            for p,old in before.items():
                if _bytes(p) == written.get(p) and _bytes(p) != old:
                    if old is None:
                        if p.exists(): p.unlink()
                    else: _atomic_bytes(p,old)
        for p in links:
            if p.is_symlink() and p.resolve()==ROOT/"plugins/model-studio": p.unlink()
        hc.invalidate_env_cache()
        raise
    return status()

def status():
    return copy.deepcopy(_STATUS)

def reconcile():
    from .store import mutation
    with scope(ROOT), mutation():
        return commit()

def _signature():
    paths = []
    for name,home in homes():
        paths += [home/"config.yaml",home/".env",home/"plugins/model-studio/plugin.yaml"]
    result=[]
    for p in paths:
        try:
            s=p.stat();result.append((str(p),s.st_mtime_ns,s.st_size))
        except FileNotFoundError:result.append((str(p),None))
    return tuple(result)

def start(ctx):
    global _WORKER
    if Path(get_hermes_home()).resolve() != ROOT: return
    with _WORKER_LOCK:
        if _WORKER and _WORKER[0].is_alive(): return
        stop = threading.Event()
        def loop():
            seen = None
            while not stop.is_set():
                try:
                    sig = _signature()
                    if sig != seen:
                        reconcile()
                        refresh_registrations()
                        seen = _signature()
                except Exception:
                    _STATUS.update(ok=False,error="自动同步未完成，请刷新配置重试。",checked_at=int(time.time()))
                stop.wait(3)
        thread=threading.Thread(target=loop,name="model-studio-sync",daemon=True)
        _WORKER=(thread,stop)
        ctx.on_unload(stop.set)
        thread.start()


def refresh_registrations():
    """A bot created just before the sync may already have cached its empty plugin list."""
    from hermes_cli.plugins import get_plugin_manager
    for name,home in homes():
        if home == ROOT: continue
        with scope(home):
            manager = get_plugin_manager()
            if not any(p.get("name")=="model-studio" and p.get("enabled") for p in manager.list_plugins()):
                manager.discover_and_load(force=True)
