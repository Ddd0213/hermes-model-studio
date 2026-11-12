"""Authenticated, bounded dashboard API; no generic proxy or secret-read route."""
from __future__ import annotations
import asyncio
import importlib.util
import json
import sys
import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

_PACKAGE = Path(__file__).resolve().parent.parent / 'model_studio'
if 'model_studio' not in sys.modules:
    spec = importlib.util.spec_from_file_location('model_studio', _PACKAGE / '__init__.py', submodule_search_locations=[str(_PACKAGE)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules['model_studio'] = mod
    spec.loader.exec_module(mod)
from model_studio import store, metadata
from model_studio.security import StudioError, request_json

router = APIRouter()
_RATE_LOCK = threading.Lock()
_RATE = {}
_SLOTS = threading.BoundedSemaphore(2)
_ACTIONS = {'discover', 'metadata', 'test', 'save', 'activate', 'delete'}


def guard(request, write=False):
    # A gated installation must already have authenticated the cookie/bearer.
    gated = bool(getattr(request.app.state, 'auth_required', False))
    principal = getattr(request.state, 'session', None) or getattr(request.state, 'native_http_principal', None)
    if not gated or not principal:
        raise StudioError('此插件要求启用官方 Dashboard 登录认证。', 401)
    if request.query_params.get('profile') not in (None, '', 'default'):
        raise StudioError('第一版仅管理默认配置；其他配置保持不变。', 409)
    if not write:
        return
    if request.headers.get('x-hermes-model-studio') != '1':
        raise StudioError('缺少安全请求头。', 403)
    if request.headers.get('sec-fetch-site') in {'cross-site', 'same-site'}:
        raise StudioError('已拒绝跨站请求。', 403)
    origin = request.headers.get('origin', '')
    if not origin:
        raise StudioError('缺少请求来源。', 403)
    from hermes_cli.config import load_config_readonly
    # Use configured URL rather than trusting forwarded host/proto supplied by a client.
    cfg = load_config_readonly()
    public = str((cfg.get('dashboard') or {}).get('public_url') or '')
    if not public:
        public = request.url.scheme + '://' + request.url.netloc
    expected = urlsplit(public)
    actual = urlsplit(origin)
    if (actual.scheme, actual.hostname, actual.port or (443 if actual.scheme == 'https' else 80)) != (expected.scheme, expected.hostname, expected.port or (443 if expected.scheme == 'https' else 80)) or actual.path not in {'', '/'} or actual.query or actual.fragment:
        raise StudioError('请求来源与官方 Dashboard 地址不一致。', 403)
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise StudioError('仅接受 JSON 请求。', 415)


def response(data, status=200):
    return JSONResponse(data, status_code=status, headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache', 'X-Content-Type-Options': 'nosniff'})


def limited(request, action):
    user = getattr(request.state, 'session', None)
    ident = str(getattr(user, 'user_id', '') or 'native')
    now = time.monotonic()
    with _RATE_LOCK:
        key = (ident, action)
        calls = _RATE.setdefault(key, deque())
        while calls and now - calls[0] > 60:
            calls.popleft()
        if len(calls) >= (8 if action in {'discover', 'test'} else 30):
            raise StudioError('操作过于频繁，请稍后再试。', 429)
        calls.append(now)


def perform(action, body):
    if action == 'save':
        row = store.save_provider(body)
        return {'ok': True, 'id': row['id'], 'provider': row}
    if action == 'activate':
        store.activate_provider(body)
        return {'ok': True}
    if action == 'delete':
        store.delete_provider(body)
        return {'ok': True}
    if action == 'metadata':
        from model_studio.security import normalize_url
        base = normalize_url(body.get('base_url', ''))
        ids = body.get('ids')
        if not isinstance(ids, list) or len(ids) > 200:
            raise StudioError('模型 ID 列表无效。')
        mode = body.get('api_mode', 'chat_completions')
        if mode not in store.MODES:
            raise StudioError('接口格式不受支持。')
        return {'models': [{'id': metadata.model_id(mid), 'metadata': metadata.metadata_for(metadata.model_id(mid), base, mode)} for mid in ids]}
    base, mode, key, headers = store.request_credentials(body)
    if not _SLOTS.acquire(blocking=False):
        raise StudioError('已有网络检查正在执行，请稍后重试。', 429)
    try:
        if action == 'discover':
            rows = metadata.discover(base, mode, key, headers)
            return {'models': rows, 'source': 'endpoint', 'warning': '最多显示 200 个模型；能力目录匹配不代表该接口已验证支持。'}
        mid = metadata.model_id(body.get('model'))
        if mode == 'anthropic_messages':
            suffix = 'messages'
            payload = {'model': mid, 'max_tokens': 64, 'messages': [{'role': 'user', 'content': 'Reply with OK.'}]}
        elif mode == 'codex_responses':
            suffix = 'responses'
            payload = {'model': mid, 'input': 'Reply with OK.', 'max_output_tokens': 64, 'stream': False}
        else:
            suffix = 'chat/completions'
            payload = {'model': mid, 'messages': [{'role': 'user', 'content': 'Reply with OK.'}], 'max_tokens': 64, 'stream': False}
        started = time.monotonic()
        result = request_json(base, suffix, mode, key, payload=payload, extra_headers=headers)
        if not isinstance(result, dict) or result.get('error') or not any(result.get(k) for k in ['choices', 'content', 'output', 'output_text']):
            raise StudioError('接口返回成功，但没有有效模型响应。', 422)
        return {'ok': True, 'message': '最小模型请求成功；测试未启用工具。', 'latency_ms': int((time.monotonic() - started) * 1000)}
    finally:
        _SLOTS.release()


@router.get('/providers')
def providers(request: Request):
    try:
        guard(request)
        return response(store.list_providers())
    except StudioError as exc:
        return response({'detail': exc.message}, exc.status)
    except Exception:
        return response({'detail': '读取服务失败，请检查服务器配置。'}, 500)


@router.post('/{action}')
async def action_endpoint(action: str, request: Request):
    try:
        guard(request, write=True)
        if action not in _ACTIONS:
            raise StudioError('操作不存在。', 404)
        limited(request, action)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 128 * 1024:
                raise StudioError('请求超过大小限制。', 413)
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            raise StudioError('JSON 请求格式无效。') from None
        if not isinstance(body, dict):
            raise StudioError('请求必须为 JSON 对象。')
        result = await asyncio.to_thread(perform, action, body)
        return response(result)
    except StudioError as exc:
        return response({'detail': exc.message}, exc.status)
    except HTTPException as exc:
        # Native model validator only returns sanitized user-facing reasons.
        return response({'detail': 'Hermes 拒绝此模型设置；请检查模型规格或先完成提供商配置。'}, exc.status_code)
    except Exception:
        # No upstream body, exception text, request data, headers or secret in logs/responses.
        return response({'detail': '操作未完成，请刷新状态后重试。服务器没有回传内部异常或凭据。'}, 500)
