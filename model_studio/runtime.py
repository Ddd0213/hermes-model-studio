"""Official llm_request middleware; only explicitly managed provider/model rows."""
from __future__ import annotations
import copy
from hermes_cli.config import load_config_readonly
from .store import base_of, mode_of, models_of


def rule_for(provider, model, base_url, api_mode):
    cfg = load_config_readonly()
    providers = cfg.get('providers') if isinstance(cfg.get('providers'), dict) else {}
    candidates = []
    for pid, entry in providers.items():
        if not isinstance(entry, dict) or base_of(entry) != str(base_url or '').rstrip('/') or mode_of(entry) != api_mode:
            continue
        raw = models_of(entry).get(model)
        if not isinstance(raw, dict):
            continue
        rule = dict(raw.get('_model_studio') or {})
        for field in ("supports_vision", "supports_tools", "supports_reasoning"):
            if field not in rule and type(raw.get(field)) is bool:
                rule[field] = raw[field]
        if not rule:
            continue
        if provider in {pid, 'custom:' + pid}:
            return rule
        candidates.append(rule)
    # Runtime normalizes a custom route's provider to 'custom'; URL must be unambiguous.
    return candidates[0] if provider == 'custom' and len(candidates) == 1 else None


def _bounded_anthropic_thinking(native, limit, model):
    """Preserve both the user cap and a smaller native context-safety cap."""
    out = copy.deepcopy(native)
    if type(limit) is not int or limit <= 0:
        return out
    maximum = out.get("max_tokens")
    if type(maximum) is int and maximum > 0:
        out["max_tokens"] = min(maximum, limit)
    thinking = out.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "enabled":
        budget = thinking.get("budget_tokens")
        if type(budget) is int:
            if limit >= 2048:
                out["thinking"] = {**thinking, "budget_tokens": min(budget, limit - 1024)}
            else:
                # Never expand the output limit to make room for a legacy budget.
                from agent.anthropic_adapter import _thinking_kwargs
                out.pop("thinking", None)
                out.pop("output_config", None)
                out.update(_thinking_kwargs({"enabled": False}, model, limit))
    return out


def _responses_level(model, rule, requested):
    """Use native model rules; unknown Responses routes retain a generic vocabulary."""
    from agent.reasoning_effort import clamp_effort, codex_supported_efforts, OPENAI_COMPAT_WIRE_EFFORTS
    bare = (model or "").strip().lower().rsplit("/", 1)[-1]
    known = bare.startswith(("gpt-", "o1", "o3", "o4"))
    supported = codex_supported_efforts(model) if known else OPENAI_COMPAT_WIRE_EFFORTS
    explicit = rule.get("effort_levels")
    if isinstance(explicit, list) and explicit:
        narrowed = tuple(level for level in supported if level in explicit)
        if narrowed:
            supported = narrowed
    return clamp_effort(requested, supported)


def transform(request, rule, model, api_mode):
    out = copy.deepcopy(request)
    if rule.get("supports_tools") is False:
        for key in ("tools", "tool_choice", "parallel_tool_calls", "functions", "function_call"):
            out.pop(key, None)
            if isinstance(out.get("extra_body"), dict):
                out["extra_body"].pop(key, None)
    cap = rule.get("max_output_tokens")
    cap = cap if type(cap) is int and 0 < cap <= 1_000_000 else None
    if cap:
        key = "max_output_tokens" if api_mode == "codex_responses" else ("max_completion_tokens" if "max_completion_tokens" in out else "max_tokens")
        original = out.get(key)
        out[key] = min(original, cap) if type(original) is int and original > 0 else cap
    mode = "omit" if rule.get("supports_reasoning") is False else rule.get("reasoning_mode", "auto")
    if mode == "auto":
        return _bounded_anthropic_thinking(out, out.get("max_tokens"), model) if cap and api_mode == "anthropic_messages" else out
    extras = dict(out.get("extra_body") or {})
    native_extra = copy.deepcopy(extras)
    for key in ("reasoning", "reasoning_effort", "thinking", "enable_thinking", "thinking_budget", "output_config"):
        out.pop(key, None)
        extras.pop(key, None)

    if mode == "omit":
        pass  # Provider default; deliberately different from an explicit disable.
    elif api_mode == "anthropic_messages":
        from agent.anthropic_adapter import _thinking_kwargs
        limit = out.get("max_tokens")
        limit = limit if type(limit) is int and limit > 0 else (cap or 8192)
        if mode == "off":
            out.update(_thinking_kwargs({"enabled": False}, model, limit))
        elif mode == "effort":
            native = _thinking_kwargs({"enabled": True, "effort": rule.get("reasoning_effort")}, model, limit)
            out.update(_bounded_anthropic_thinking(native, limit, model))
        elif mode == "budget":
            budget = rule.get("thinking_budget")
            if type(budget) is int and budget >= 1024 and limit >= 2048:
                out["thinking"] = {"type": "enabled", "budget_tokens": min(budget, limit - 1024)}
                out["temperature"] = 1
        out = _bounded_anthropic_thinking(out, limit, model)
    elif api_mode == "codex_responses" and mode in {"off", "effort"}:
        requested = "none" if mode == "off" else str(rule.get("reasoning_effort") or "")
        effort = _responses_level(model, rule, requested)
        if effort:
            # Mandatory-thinking models get the lowest legal tier rather than invalid none.
            out["reasoning"] = {"effort": effort}
    elif mode in {"off", "effort"}:
        level = "none" if mode == "off" else rule.get("reasoning_effort")
        if isinstance(native_extra.get("thinking"), dict):
            extras["thinking"] = {**native_extra["thinking"], "type": "disabled" if mode == "off" else "enabled"}
            if mode != "off" and "reasoning_effort" in request:
                out["reasoning_effort"] = level
        elif "enable_thinking" in native_extra:
            extras["enable_thinking"] = mode != "off"
            if mode != "off" and "reasoning_effort" in request:
                out["reasoning_effort"] = level
        elif isinstance(native_extra.get("reasoning"), dict):
            if mode == "off":
                extras["reasoning"] = {k: v for k, v in native_extra["reasoning"].items() if k not in {"enabled", "effort"}} | {"enabled": False}
            else:
                extras["reasoning"] = {**native_extra["reasoning"], "enabled": True, "effort": level}
        else:
            out["reasoning_effort"] = level
    if extras:
        out["extra_body"] = extras
    else:
        out.pop("extra_body", None)
    return out


def middleware(request, provider='', model='', base_url='', api_mode='', **kwargs):
    rule = rule_for(provider, model, base_url, api_mode)
    if rule is None:
        return None
    return {'request': transform(request, rule, model, api_mode), 'source': 'model-studio', 'reason': 'explicit model parameters'}


def selected_entry(agent, cfg):
    """Resolve the live Hermes route. A prior turn cannot select this turn's provider."""
    from . import shared
    from hermes_constants import get_hermes_home

    providers = cfg.get("providers") or {}
    managed = {pid: entry for pid, entry in providers.items()
               if isinstance(entry, dict) and (entry.get("_model_studio") or {}).get("shared")}
    requested = str(getattr(agent, "requested_provider", "") or "")
    transport = str(getattr(agent, "provider", "") or "")
    identity = transport if requested in ("", "auto") else requested
    if identity == "custom" and transport not in ("", "auto", "custom"):
        identity = transport
    base = str(getattr(agent, "base_url", "") or "").rstrip("/")

    # Hermes uses bare "custom" for distinct endpoints. A qualified name, including
    # "custom:custom", identifies a specific provider even after its URL changes.
    if identity not in ("", "auto", "custom"):
        pid = identity.removeprefix("custom:")
        if pid in managed:
            return pid, managed[pid]
        if pid in providers:
            return None, None  # A current provider outside Model Studio.
        history = shared.read_state(get_hermes_home()).get("route_history", {})
        return (pid, None) if pid in history else (None, None)

    if not base:
        return None, None
    matches = [pid for pid, entry in managed.items() if base_of(entry) == base]
    if not matches:
        # A resumed conversation may still hold the previous URL of a renamed or removed route.
        history = shared.read_state(get_hermes_home()).get("route_history", {})
        matches = [pid for pid, urls in history.items() if base in urls]
    if len(matches) > 1:
        # Different named providers can serve the same URL. Hermes may only retain
        # the generic custom transport, so use the current model when it is unique.
        model = getattr(agent, "model", "")
        owners = [pid for pid in matches if pid in managed and model in models_of(managed[pid])]
        if len(owners) == 1 and all(pid in managed for pid in matches):
            matches = owners
    if len(matches) == 1:
        pid = matches[0]
        return pid, managed.get(pid)
    if matches:
        raise ValueError("Ambiguous Model Studio endpoint; select a named provider")
    return None, None


def refresh_runtime(**kwargs):
    """Official pre_llm_call hook: refresh the live client at a turn boundary, never log keys."""
    from agent.subagent_lifecycle import get_active_subagent_parent
    from . import shared, store
    agent = get_active_subagent_parent()
    if agent is None: return None
    try:
        cfg = store.raw_config()
        pid, entry = selected_entry(agent,cfg)
        if not entry:
            if pid:
                return {"action":"block","message":"Model Studio: this provider was removed. Select an available model."}
            agent._model_studio_provider = None
            agent._model_studio_stamp = None
            return None
        models = models_of(entry)
        old_model = str(getattr(agent,"model","") or "")
        model = old_model if old_model in models else entry.get("default_model") or next(iter(models),"")
        if not model: return {"action":"block","message":"Model Studio: this provider has no available models."}
        key, _ = store.credentials(entry)
        base, mode = base_of(entry), mode_of(entry)
        import hashlib, json
        stamp = hashlib.sha256(json.dumps([pid,model,entry,key],sort_keys=True).encode()).digest()
        seen = getattr(agent,"_model_studio_stamp",None)
        route_changed = (model != old_model or base != str(getattr(agent,"base_url","") or "").rstrip("/")
                         or mode != getattr(agent,"api_mode","") or key != getattr(agent,"api_key", ""))
        same_provider = getattr(agent, "_model_studio_provider", None) == pid
        if route_changed or (same_provider and seen is not None and seen != stamp):
            agent.switch_model(new_model=model,new_provider="custom",api_key=key,
                               base_url=base,api_mode=mode)
            agent.requested_provider = pid
        agent._model_studio_provider = pid
        agent._model_studio_stamp = stamp
    except Exception:
        # A failed refresh must not silently send an obsolete key or removed model.
        return {"action":"block","message":"Model Studio: model settings changed but could not be refreshed. Retry or select the model again."}
    return None


def guard_tool(**kwargs):
    from agent.subagent_lifecycle import get_active_subagent_parent
    from . import store
    agent = get_active_subagent_parent()
    if agent is None: return None
    try:
        pid, entry = selected_entry(agent, store.raw_config())
    except ValueError:
        return {"action": "block", "message": "Model Studio: select the named provider for this endpoint."}
    if entry:
        raw = models_of(entry).get(str(getattr(agent,"model", "")), {})
        if isinstance(raw,dict) and raw.get("supports_tools") is False:
            return {"action":"block","message":"Tool calling is disabled for this model in Model Studio."}
    return None
