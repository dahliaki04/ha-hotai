"""Async client for the Exosite ExoHome "phone" API used by the Hotai 除濕機（非官方） app.

Protocol (recovered from the app bundle, com.hotai.mobile 3.1.3):

  REST  POST https://{host}/api:1/session      {"email","password"} -> {"token","id"}
        GET  https://{host}/api:1/session      Authorization: Bearer <token>   (validate)
  WS    wss://{host}/api:1/phone
        first frame  {"id":1,"request":"login","data":{"token":...}}   (integer id; string ids get code 300)
        requests     {"id":<int>,"request":<name>,"device":<sn>|None,"data":<obj>|None}
        responses    {"id":<same>,"status":"ok"|"error",...,"data":...}
        events       {"event":"device_change"|"add_device"|"del_device"|"token_expired"|..., "data":{...}}

A device record (response of request "get") looks like
  {"device": "<sn>", "connected": 0|1, "device_state": "idle"|..., "profile": {"esh": {...}, "module": {...}},
   "status": {"H00": 1, "H01": 0, ...}, "fields": ["H00", ...], "fields_range": [...], "properties": {...}}

This module has no Home Assistant dependency so it can be exercised from the command line (see tools/probe.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

DEFAULT_HOST = "karos.apps.exosite.io"
API_VERSION = "/api:1"
WS_AUTH_ID = "ws-request-auth"
REQUEST_TIMEOUT = 10.0
CONNECT_TIMEOUT = 10.0

EVENT_DEVICE_CHANGE = "device_change"
EVENT_ADD_DEVICE = "add_device"
EVENT_DEL_DEVICE = "del_device"
EVENT_TOKEN_EXPIRED = "token_expired"

EventCallback = Callable[[dict[str, Any]], None]
ReauthCallback = Callable[[], Awaitable[str]]


class ExoHomeError(Exception):
    """Base error."""


class AuthError(ExoHomeError):
    """Login rejected or token invalid."""


class ConnectionFailed(ExoHomeError):
    """Network-level failure."""


class RequestError(ExoHomeError):
    """The cloud answered a request with status != ok."""

    def __init__(self, message: str, response: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.response = response or {}


class ExoHomeClient:
    """One authenticated connection to an ExoHome solution."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str = DEFAULT_HOST,
        *,
        token: str | None = None,
        secure: bool = True,
    ) -> None:
        self._session = session
        self._host = host
        self._secure = secure
        self._token = token
        self._user_id: str | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._listeners: list[EventCallback] = []
        self._authenticated = asyncio.Event()
        self._closing = False
        self._reauth: ReauthCallback | None = None

    # ------------------------------------------------------------------ REST
    @property
    def host(self) -> str:
        return self._host

    @property
    def token(self) -> str | None:
        return self._token

    @property
    def user_id(self) -> str | None:
        return self._user_id

    def _url(self, path: str) -> str:
        scheme = "https" if self._secure else "http"
        return f"{scheme}://{self._host}{API_VERSION}{path}"

    def _ws_url(self) -> str:
        scheme = "wss" if self._secure else "ws"
        return f"{scheme}://{self._host}{API_VERSION}/phone"

    async def login(self, email: str, password: str) -> dict[str, Any]:
        """Exchange email/password for a bearer token."""
        try:
            async with self._session.post(
                self._url("/session"),
                json={"email": email, "password": password},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                body = await _json_or_text(resp)
                # The HOTAI solution answers wrong credentials with HTTP 400 "Auth fail." (text/plain).
                if resp.status in (400, 401, 403, 404):
                    raise AuthError(_msg(body, f"login rejected ({resp.status})"))
                if resp.status >= 400:
                    raise ExoHomeError(_msg(body, f"login failed ({resp.status})"))
        except aiohttp.ClientError as err:
            raise ConnectionFailed(str(err)) from err
        if not isinstance(body, dict) or "token" not in body:
            raise ExoHomeError(f"unexpected login response: {body!r}")
        self._token = body["token"]
        self._user_id = str(body.get("id", "")) or None
        return body

    async def validate_session(self) -> bool:
        """Return True when the current token is still accepted."""
        if not self._token:
            return False
        try:
            async with self._session.get(
                self._url("/session"),
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                return resp.status < 400
        except aiohttp.ClientError as err:
            raise ConnectionFailed(str(err)) from err

    async def info_models(self) -> Any:
        """List the information models the solution publishes (UI field definitions)."""
        return await self._get_json("/info-model")

    async def info_model(self, name: str) -> Any:
        return await self._get_json(f"/info-model/{name}")

    async def _get_json(self, path: str) -> Any:
        if not self._token:
            raise AuthError("not logged in")
        try:
            async with self._session.get(
                self._url(path),
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                body = await _json_or_text(resp)
                if resp.status >= 400:
                    raise RequestError(_msg(body, f"GET {path} -> {resp.status}"))
                return body
        except aiohttp.ClientError as err:
            raise ConnectionFailed(str(err)) from err

    # ------------------------------------------------------------- WebSocket
    @property
    def connected(self) -> bool:
        return self._authenticated.is_set() and self._ws is not None and not self._ws.closed

    def set_reauth_callback(self, cb: ReauthCallback | None) -> None:
        """Called when the cloud reports the token expired; must return a fresh token."""
        self._reauth = cb

    def add_listener(self, cb: EventCallback) -> Callable[[], None]:
        self._listeners.append(cb)

        def _remove() -> None:
            if cb in self._listeners:
                self._listeners.remove(cb)

        return _remove

    async def connect(self) -> None:
        """Open the WebSocket, authenticate, and keep it alive in a background task."""
        if not self._token:
            raise AuthError("login first")
        if self._task and not self._task.done():
            await self._wait_authenticated()
            return
        self._closing = False
        self._task = asyncio.create_task(self._run(), name="exohome-ws")
        await self._wait_authenticated()

    async def _wait_authenticated(self) -> None:
        """Wait until the run loop has authenticated, or surface the error that stopped it."""
        assert self._task is not None
        waiter = asyncio.ensure_future(self._authenticated.wait())
        try:
            done, _ = await asyncio.wait(
                {waiter, self._task}, timeout=CONNECT_TIMEOUT + REQUEST_TIMEOUT, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            if not waiter.done():
                waiter.cancel()
        if self._task in done and not self._task.cancelled() and (exc := self._task.exception()):
            raise exc
        if not self._authenticated.is_set():
            raise ConnectionFailed("websocket authentication timed out")

    async def disconnect(self) -> None:
        self._closing = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        await self._close_ws()

    async def _close_ws(self) -> None:
        self._authenticated.clear()
        if self._ws and not self._ws.closed:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._ws = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionFailed("websocket closed"))
        self._pending.clear()

    async def _run(self) -> None:
        backoff = 1.0
        first = True
        while not self._closing:
            retry_now = False
            try:
                await self._connect_once()
                backoff = 1.0
                first = False
                await self._read_loop()
            except AuthError:
                # Token no longer accepted: try to obtain a fresh one, otherwise give up.
                if self._reauth is None:
                    if first:
                        raise
                    _LOGGER.error("ExoHome token rejected and no re-auth handler; stopping")
                    return
                try:
                    self._token = await self._reauth()
                    retry_now = True
                except Exception as err:  # noqa: BLE001
                    _LOGGER.error("ExoHome re-authentication failed: %s", err)
                    if first:
                        raise AuthError(str(err)) from err
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                if first:
                    raise ConnectionFailed(str(err)) from err
                _LOGGER.warning("ExoHome websocket error: %s (reconnecting in %.0fs)", err, backoff)
            finally:
                await self._close_ws()
                self._notify({"event": "_disconnected"})
            if self._closing:
                return
            if not retry_now:
                await asyncio.sleep(backoff + random.random())
                backoff = min(backoff * 2, 60.0)

    async def _connect_once(self) -> None:
        url = self._ws_url()
        try:
            self._ws = await asyncio.wait_for(self._session.ws_connect(url, heartbeat=30), CONNECT_TIMEOUT)
        except asyncio.TimeoutError as err:
            raise ConnectionFailed("ws connect timed out") from err
        except aiohttp.ClientError as err:
            raise ConnectionFailed(f"ws connect failed: {err}") from err
        # The app builds this frame with id "ws-request-auth", but its sender overwrites any truthy id
        # with the running integer counter before transmitting; the server rejects string ids (code 300).
        auth_id = self._next_id
        self._next_id += 1
        auth = {"id": auth_id, "request": "login", "data": {"token": self._token}}
        await self._ws.send_str(json.dumps(auth))
        try:
            reply = await asyncio.wait_for(self._ws.receive(), REQUEST_TIMEOUT)
        except asyncio.TimeoutError as err:
            raise ConnectionFailed("no reply to websocket login") from err
        if reply.type != aiohttp.WSMsgType.TEXT:
            raise ConnectionFailed(f"unexpected frame during login: {reply.type}")
        msg = json.loads(reply.data)
        if str(msg.get("status", "")).lower() != "ok":
            raise AuthError(msg.get("message") or f"websocket login rejected: {msg}")
        self._authenticated.set()
        self._notify({"event": "_connected"})
        _LOGGER.debug("ExoHome websocket authenticated")

    async def _read_loop(self) -> None:
        assert self._ws is not None
        async for frame in self._ws:
            if frame.type == aiohttp.WSMsgType.TEXT:
                try:
                    msg = json.loads(frame.data)
                except ValueError:
                    _LOGGER.debug("non-JSON frame: %s", frame.data)
                    continue
                self._handle_message(msg)
            elif frame.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                break
            elif frame.type == aiohttp.WSMsgType.ERROR:
                raise ConnectionFailed(f"websocket error: {self._ws.exception()}")
        raise ConnectionFailed("websocket closed by server")

    def _handle_message(self, msg: dict[str, Any]) -> None:
        msg_id = msg.get("id")
        if isinstance(msg_id, int) and msg_id in self._pending:
            fut = self._pending.pop(msg_id)
            if not fut.done():
                fut.set_result(msg)
            return
        if "event" in msg:
            if msg["event"] == EVENT_TOKEN_EXPIRED:
                _LOGGER.info("ExoHome reports token expired")
                self._notify(msg)
                # Force the run loop to reconnect through the AuthError path.
                if self._ws:
                    asyncio.create_task(self._ws.close(code=4003))
                return
            self._notify(msg)
        else:
            _LOGGER.debug("unmatched frame: %s", msg)

    def _notify(self, event: dict[str, Any]) -> None:
        for cb in list(self._listeners):
            try:
                cb(event)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("ExoHome event listener failed")

    async def request(
        self,
        name: str,
        device: str | None = None,
        data: Any = None,
        timeout: float = REQUEST_TIMEOUT,
    ) -> dict[str, Any]:
        """Send one request and return the full response frame."""
        if not self.connected or self._ws is None:
            raise ConnectionFailed("websocket not connected")
        req_id = self._next_id
        self._next_id += 1
        frame = {"id": req_id, "request": name, "device": device, "data": data}
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            await self._ws.send_str(json.dumps(frame))
            resp = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as err:
            self._pending.pop(req_id, None)
            raise RequestError(f"{name} timed out") from err
        if str(resp.get("status", "")).lower() != "ok":
            raise RequestError(resp.get("message") or f"{name} failed: {resp}", resp)
        return resp

    # ------------------------------------------------------ typed helpers
    async def get_me(self) -> dict[str, Any]:
        return (await self.request("get_me")).get("data") or {}

    async def list_device_entries(self) -> list[dict[str, Any]]:
        """lst_device rows: {"device", "owner", "role", "properties": {"displayName", ...}}."""
        data = (await self.request("lst_device")).get("data") or []
        return [d if isinstance(d, dict) else {"device": str(d)} for d in data]

    async def list_devices(self) -> list[str]:
        return [str(d["device"]) for d in await self.list_device_entries() if d.get("device")]

    async def get_device(self, sn: str) -> dict[str, Any]:
        return (await self.request("get", sn)).get("data") or {}

    async def set_fields(self, sn: str, fields: dict[str, int]) -> None:
        await self.request("set", sn, fields)

    async def get_all_devices(self) -> dict[str, dict[str, Any]]:
        """Full records, with the list-level fields (display name, owner, role) folded in —
        `get` alone does not return `properties`."""
        devices: dict[str, dict[str, Any]] = {}
        for entry in await self.list_device_entries():
            sn = str(entry.get("device") or "")
            if not sn:
                continue
            rec = await self.get_device(sn)
            for key in ("properties", "owner", "role"):
                if key in entry and key not in rec:
                    rec[key] = entry[key]
            devices[sn] = rec
        return devices


async def _json_or_text(resp: aiohttp.ClientResponse) -> Any:
    try:
        return await resp.json(content_type=None)
    except (ValueError, aiohttp.ContentTypeError):
        return await resp.text()


def _msg(body: Any, fallback: str) -> str:
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error") or fallback)
    if isinstance(body, str) and body.strip():
        return body.strip()[:200]
    return fallback
