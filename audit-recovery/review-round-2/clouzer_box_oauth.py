"""Minimal Box OAuth callback service for CLOUZER.

Required environment:
  BOX_CLIENT_ID
  BOX_CLIENT_SECRET
  BOX_REDIRECT_URI (for example https://clouzer-box.wispbyte.app/callback)
Optional:
  BOX_OAUTH_PORT (defaults to PORT, then 15497)
  BOX_TOKEN_FILE (defaults to ./box_tokens.json)

Never log or return OAuth tokens to the browser.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

log = logging.getLogger("clouzer.box_oauth")

BOX_AUTHORIZE_URL = "https://account.box.com/api/oauth2/authorize"
BOX_TOKEN_URL = "https://api.box.com/oauth2/token"


def _required_env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _token_path() -> Path:
    raw = (os.getenv("BOX_TOKEN_FILE") or "box_tokens.json").strip()
    return Path(raw).expanduser().resolve()


def _save_tokens(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    os.replace(temp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


async def _health(_: web.Request) -> web.Response:
    return web.json_response({"service": "clouzer-box-oauth", "status": "ok"})


async def _login(request: web.Request) -> web.StreamResponse:
    client_id = request.app["client_id"]
    redirect_uri = request.app["redirect_uri"]
    state = secrets.token_urlsafe(32)
    request.app["states"][state] = time.monotonic()
    # Expire stale states and bound memory use.
    now = time.monotonic()
    states = request.app["states"]
    for old_state, created in list(states.items()):
        if now - created > 600:
            states.pop(old_state, None)
    while len(states) > 20:
        states.pop(next(iter(states)))
    query = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    })
    raise web.HTTPFound(BOX_AUTHORIZE_URL + "?" + query)


async def _callback(request: web.Request) -> web.Response:
    if request.query.get("error"):
        # Do not reflect arbitrary provider descriptions or credentials.
        log.warning("Box OAuth authorization was declined or failed.")
        return web.Response(text="Login Box gagal atau dibatalkan. Silakan buka /login dan coba lagi.",
                            status=400)

    code = request.query.get("code", "")
    state = request.query.get("state", "")
    if not code or not state:
        return web.Response(text="Callback tidak lengkap.", status=400)

    states = request.app["states"]
    created = states.pop(state, None)
    if created is None or time.monotonic() - created > 600:
        return web.Response(text="State OAuth tidak valid atau kedaluwarsa. Buka /login lagi.",
                            status=400)

    form = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": request.app["client_id"],
        "client_secret": request.app["client_secret"],
        "redirect_uri": request.app["redirect_uri"],
    }
    try:
        async with request.app["http"].post(
            BOX_TOKEN_URL, data=form, timeout=aiohttp.ClientTimeout(total=25)
        ) as response:
            payload = await response.json(content_type=None)
            if response.status != 200 or not isinstance(payload, dict) or not payload.get("access_token"):
                log.error("Box token exchange failed (HTTP %s).", response.status)
                return web.Response(text="Box menolak pertukaran token. Periksa Redirect URI dan konfigurasi aplikasi.",
                                    status=502)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        log.exception("Box token endpoint could not be reached.")
        return web.Response(text="Tidak dapat menghubungi layanan token Box. Coba lagi nanti.",
                            status=502)

    try:
        _save_tokens(request.app["token_path"], {
            "access_token": payload.get("access_token"),
            "refresh_token": payload.get("refresh_token"),
            "token_type": payload.get("token_type"),
            "expires_in": payload.get("expires_in"),
            "obtained_at": int(time.time()),
            "issued_token_type": payload.get("issued_token_type"),
        })
    except OSError:
        log.exception("Could not persist Box OAuth tokens.")
        return web.Response(text="Login berhasil, tetapi penyimpanan token gagal. Periksa storage server.",
                            status=500)

    log.info("Box OAuth authorization completed; tokens saved locally.")
    return web.Response(
        text="Koneksi OAuth Box berhasil. Token tersimpan aman di server. Kamu boleh menutup halaman ini.",
        content_type="text/plain",
    )


async def _close_http_session(app: web.Application) -> None:
    await app["http"].close()


async def start_box_oauth_server() -> web.AppRunner | None:
    client_id = _required_env("BOX_CLIENT_ID")
    client_secret = _required_env("BOX_CLIENT_SECRET")
    redirect_uri = _required_env("BOX_REDIRECT_URI")
    if not (client_id and client_secret and redirect_uri):
        log.warning("Box OAuth server disabled: set BOX_CLIENT_ID, BOX_CLIENT_SECRET, and BOX_REDIRECT_URI.")
        return None

    port_raw = (os.getenv("BOX_OAUTH_PORT") or os.getenv("PORT") or "15497").strip()
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise RuntimeError("BOX_OAUTH_PORT/PORT must be a valid integer.") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("BOX_OAUTH_PORT/PORT is outside the valid TCP port range.")
    if not redirect_uri.startswith("https://") or not redirect_uri.endswith("/callback"):
        raise RuntimeError("BOX_REDIRECT_URI must be the exact HTTPS callback URL ending in /callback.")

    app = web.Application(client_max_size=16 * 1024)
    app["client_id"] = client_id
    app["client_secret"] = client_secret
    app["redirect_uri"] = redirect_uri
    app["token_path"] = _token_path()
    app["states"] = {}
    app["http"] = aiohttp.ClientSession()
    app.on_cleanup.append(_close_http_session)
    app.router.add_get("/health", _health)
    app.router.add_get("/login", _login)
    app.router.add_get("/callback", _callback)

    runner = web.AppRunner(app, access_log=None)
    try:
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=port)
        await site.start()
    except Exception:
        await runner.cleanup()
        await app["http"].close()
        raise
    log.warning("Box OAuth callback listener started on 0.0.0.0:%s.", port)
    return runner


# Token refresh support. The lock prevents concurrent refresh-token rotation races.
_REFRESH_LOCK = asyncio.Lock()


def _load_tokens(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


async def get_valid_access_token() -> str:
    """Return a usable Box access token, refreshing it before expiry when needed."""
    path = _token_path()
    async with _REFRESH_LOCK:
        current = _load_tokens(path)
        if not current:
            raise RuntimeError("Box belum diotorisasi. Buka /login terlebih dahulu.")

        access_token = str(current.get("access_token") or "")
        refresh_token = str(current.get("refresh_token") or "")
        try:
            expires_in = max(0, int(current.get("expires_in") or 0))
            obtained_at = int(current.get("obtained_at") or 0)
        except (TypeError, ValueError):
            expires_in, obtained_at = 0, 0

        # Refresh 90 seconds early to avoid requests racing the expiry boundary.
        if access_token and obtained_at + expires_in > int(time.time()) + 90:
            return access_token
        if not refresh_token:
            raise RuntimeError("Refresh token Box tidak tersedia. Jalankan /login ulang.")

        form = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": _required_env("BOX_CLIENT_ID"),
            "client_secret": _required_env("BOX_CLIENT_SECRET"),
        }
        if not form["client_id"] or not form["client_secret"]:
            raise RuntimeError("BOX_CLIENT_ID/BOX_CLIENT_SECRET belum dikonfigurasi.")

        timeout = aiohttp.ClientTimeout(total=25)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(BOX_TOKEN_URL, data=form) as response:
                    payload = await response.json(content_type=None)
                    if response.status != 200 or not isinstance(payload, dict) or not payload.get("access_token"):
                        # Do not log provider response bodies; they can contain sensitive details.
                        log.error("Box refresh-token exchange failed (HTTP %s).", response.status)
                        raise RuntimeError("Refresh token Box ditolak. Jalankan /login ulang.")
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            log.exception("Box refresh-token endpoint could not be reached.")
            raise RuntimeError("Tidak dapat memperbarui token Box; coba lagi nanti.") from exc

        rotated = {
            "access_token": payload.get("access_token"),
            "refresh_token": payload.get("refresh_token") or refresh_token,
            "token_type": payload.get("token_type") or current.get("token_type"),
            "expires_in": payload.get("expires_in"),
            "obtained_at": int(time.time()),
            "issued_token_type": payload.get("issued_token_type"),
        }
        _save_tokens(path, rotated)
        log.info("Box access token refreshed and token rotation persisted.")
        return str(rotated["access_token"])


async def box_api_request(method: str, endpoint: str, **kwargs):
    """Authenticated Box API request with one refresh-and-retry on HTTP 401."""
    if not endpoint.startswith("https://api.box.com/2.0/"):
        raise ValueError("Only Box API v2 HTTPS endpoints are allowed.")
    timeout = kwargs.pop("timeout", aiohttp.ClientTimeout(total=30))
    for attempt in range(2):
        token = await get_valid_access_token()
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = "Bearer " + token
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(method.upper(), endpoint, headers=headers, **kwargs) as response:
                body = await response.read()
                if response.status == 401 and attempt == 0:
                    # Force refresh by expiring the saved token timestamp.
                    async with _REFRESH_LOCK:
                        saved = _load_tokens(_token_path()) or {}
                        saved["obtained_at"] = 0
                        _save_tokens(_token_path(), saved)
                    continue
                return response.status, response.headers, body
    raise RuntimeError("Box API authentication failed after token refresh.")
