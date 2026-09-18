from __future__ import annotations

import asyncio
import copy
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter

router = APIRouter()

REFRESH_INTERVAL_SECONDS = 300
HTTP_TIMEOUT_SECONDS = 10.0

CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
ZAI_QUOTA_URL = "https://api.z.ai/api/monitor/usage/quota/limit"

CLAUDE_CACHE_MISSING = "no cached claude usage yet — run claude once"
CODEX_AUTH_MISSING = "auth.json not found"
CODEX_TOKEN_EXPIRED = "codex token expired — run 'codex login'"
ZAI_KEY_INVALID = "zai api key invalid or missing"
ZAI_NO_WEEKLY = "weekly quota not exposed by z.ai API"

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = _PLUGIN_ROOT / "cache.json"

_state_lock = threading.Lock()
_cache: Dict[str, Any] = {"updated_at": None, "providers": {}}
_last_refresh_monotonic = 0.0
_background_task: Optional[asyncio.Task] = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_iso_utc(value: Any) -> Optional[str]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        raw = float(value)
        seconds = raw / 1000.0 if raw > 1e12 else raw
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def _window(percent: Any, resets_at: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(percent, (int, float)) or isinstance(percent, bool):
        return None
    return {"percent": int(round(percent)), "resets_at": _to_iso_utc(resets_at)}


def _http_get_json(url: str, headers: Dict[str, str]) -> Tuple[int, str]:
    try:
        import httpx
    except ImportError:
        pass
    else:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = client.get(url, headers=headers)
            return response.status_code, response.text
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def _provider(label: str) -> Dict[str, Any]:
    return {"label": label, "five_hour": None, "seven_day": None, "error": None}


def _fetch_claude_sync() -> Dict[str, Any]:
    provider = _provider("Claude")
    try:
        path = Path.home() / ".claude.json"
        if not path.is_file():
            provider["error"] = CLAUDE_CACHE_MISSING
            return provider
        data = json.loads(path.read_text(encoding="utf-8"))
        limits = ((data.get("cachedUsageUtilization") or {}).get("utilization") or {}).get("limits") or []
        for item in limits:
            if item.get("kind") == "session":
                provider["five_hour"] = _window(item.get("percent"), item.get("resets_at"))
            elif item.get("kind") == "weekly_all":
                provider["seven_day"] = _window(item.get("percent"), item.get("resets_at"))
        if provider["five_hour"] is None and provider["seven_day"] is None:
            provider["error"] = CLAUDE_CACHE_MISSING
    except Exception as exc:
        provider["error"] = f"failed to read ~/.claude.json: {exc}"
    return provider


def _fetch_codex_sync() -> Dict[str, Any]:
    provider = _provider("ChatGPT")
    try:
        auth_path = Path.home() / ".codex" / "auth.json"
        if not auth_path.is_file():
            provider["error"] = CODEX_AUTH_MISSING
            return provider
        tokens = (json.loads(auth_path.read_text(encoding="utf-8")) or {}).get("tokens") or {}
        access_token = tokens.get("access_token")
        if not access_token:
            provider["error"] = CODEX_AUTH_MISSING
            return provider
        headers = {
            "Authorization": f"Bearer {access_token}",
            "ChatGPT-Account-Id": tokens.get("account_id") or "",
            "Accept": "application/json",
            "Origin": "https://chatgpt.com",
            "Referer": "https://chatgpt.com/",
            "User-Agent": "codex_cli_rs/0.147.0",
        }
        status, body = _http_get_json(CODEX_USAGE_URL, headers)
        if status == 401:
            provider["error"] = CODEX_TOKEN_EXPIRED
            return provider
        if status != 200:
            provider["error"] = f"usage api returned {status}"
            return provider
        payload = json.loads(body)
        rate_limit = payload.get("rate_limit") or {}
        primary = rate_limit.get("primary_window") or {}
        secondary = rate_limit.get("secondary_window") or {}
        if primary:
            provider["five_hour"] = _window(primary.get("used_percent"), primary.get("reset_at"))
        if secondary:
            provider["seven_day"] = _window(secondary.get("used_percent"), secondary.get("reset_at"))
        if provider["five_hour"] is None and provider["seven_day"] is None:
            provider["error"] = "no rate limit data from usage api"
    except Exception as exc:
        provider["error"] = f"codex usage lookup failed: {exc}"
    return provider


def _fetch_glm_sync() -> Dict[str, Any]:
    provider = _provider("GLM")
    try:
        auth_path = Path.home() / ".local" / "share" / "opencode" / "auth.json"
        if not auth_path.is_file():
            provider["error"] = ZAI_KEY_INVALID
            return provider
        auth = json.loads(auth_path.read_text(encoding="utf-8")) or {}
        key = (auth.get("zhipuai-coding-plan") or {}).get("key") or ""
        if not key:
            provider["error"] = ZAI_KEY_INVALID
            return provider
        status, body = _http_get_json(
            ZAI_QUOTA_URL,
            {"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
        if status != 200:
            provider["error"] = ZAI_KEY_INVALID
            return provider
        payload = json.loads(body)
        if payload.get("success") is False or payload.get("code") not in (None, 200):
            provider["error"] = ZAI_KEY_INVALID
            return provider
        limits = (payload.get("data") or {}).get("limits") or []
        for item in limits:
            if item.get("type") == "TOKENS_LIMIT":
                provider["five_hour"] = _window(item.get("percentage"), item.get("nextResetTime"))
                break
        if provider["five_hour"] is None:
            provider["error"] = "no TOKENS_LIMIT quota in z.ai response"
            return provider
        provider["seven_day"] = None
        provider["error"] = ZAI_NO_WEEKLY
    except Exception as exc:
        provider["error"] = f"zai quota lookup failed: {exc}"
    return provider


def _store_cache(snapshot: Dict[str, Any]) -> None:
    global _cache, _last_refresh_monotonic
    with _state_lock:
        _cache = snapshot
        _last_refresh_monotonic = time.monotonic()
    try:
        CACHE_FILE.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


async def _refresh_all() -> Dict[str, Any]:
    claude, codex, glm = await asyncio.gather(
        asyncio.to_thread(_fetch_claude_sync),
        asyncio.to_thread(_fetch_codex_sync),
        asyncio.to_thread(_fetch_glm_sync),
    )
    snapshot = {
        "updated_at": _utc_now_iso(),
        "providers": {"claude": claude, "codex": codex, "glm": glm},
    }
    _store_cache(snapshot)
    return copy.deepcopy(snapshot)


async def _safe_refresh() -> None:
    try:
        await _refresh_all()
    except Exception:
        pass


async def _background_loop() -> None:
    while True:
        await _safe_refresh()
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)


def _ensure_background_loop() -> None:
    global _background_task
    if _background_task is not None and not _background_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _background_task = loop.create_task(_background_loop())


def _is_stale() -> bool:
    with _state_lock:
        return (time.monotonic() - _last_refresh_monotonic) > REFRESH_INTERVAL_SECONDS


def _load_cache_file() -> None:
    global _cache
    try:
        if CACHE_FILE.is_file():
            loaded = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("providers"), dict):
                _cache = loaded
    except Exception:
        pass


@router.get("/usage")
async def get_usage() -> Dict[str, Any]:
    _ensure_background_loop()
    if _is_stale():
        try:
            asyncio.get_running_loop().create_task(_safe_refresh())
        except RuntimeError:
            pass
    with _state_lock:
        return copy.deepcopy(_cache)


@router.post("/refresh")
async def post_refresh() -> Dict[str, Any]:
    _ensure_background_loop()
    try:
        return await _refresh_all()
    except Exception:
        with _state_lock:
            return copy.deepcopy(_cache)


_load_cache_file()
