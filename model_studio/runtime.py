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
        if not isinstance(raw, dict) or not isinstance(raw.get('_model_studio'), dict):
            continue
        if provider in {pid, 'custom:' + pid}:
            return raw['_model_studio']
        candidates.append(raw['_model_studio'])
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
    cap = rule.get("max_output_tokens")
    cap = cap if type(cap) is int and 0 < cap <= 1_000_000 else None
    if cap:
        key = "max_output_tokens" if api_mode == "codex_responses" else ("max_completion_tokens" if "max_completion_tokens" in out else "max_tokens")
        original = out.get(key)
        out[key] = min(original, cap) if type(original) is int and original > 0 else cap
    mode = rule.get("reasoning_mode", "auto")
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
