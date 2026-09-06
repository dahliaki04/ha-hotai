"""Offline tests for the ExoHome client against a fake cloud (aiohttp test server)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "hotai_smart_home"))
from exohome import AuthError, ExoHomeClient, RequestError  # noqa: E402

TOKEN = "tok-123"
DEVICE = {
    "device": "SN0001",
    "connected": 1,
    "device_state": "idle",
    "profile": {"esh": {"class": "0", "esh_version": "4.0", "device_id": "4", "brand": "HOTAI", "model": "RDI016"}},
    "status": {"H00": 1, "H01": 0, "H03": 55, "H07": 68},
    "fields": ["H00", "H01", "H03", "H07"],
    "fields_range": [{"H03": {"min": 40, "max": 70}}],
}


class FakeCloud:
    def __init__(self) -> None:
        self.sockets: list[web.WebSocketResponse] = []
        self.sets: list[dict] = []
        self.reject_token = False
        self.logins = 0

    async def session(self, request: web.Request) -> web.Response:
        body = await request.json()
        if body.get("password") != "pw":
            return web.Response(text="Auth fail.", status=400)
        return web.json_response({"token": TOKEN, "id": "user-1"})

    async def phone(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.append(ws)
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                break
            req = json.loads(msg.data)
            rid, name = req["id"], req["request"]
            if name == "login":
                self.logins += 1
                ok = req["data"].get("token") == TOKEN and not self.reject_token
                await ws.send_json({"id": rid, "status": "ok" if ok else "error", "message": None if ok else "bad token"})
                if not ok:
                    await ws.close()
                    break
            elif name == "lst_device":
                await ws.send_json({"id": rid, "status": "ok", "data": [{"device": "SN0001"}]})
            elif name == "get":
                await ws.send_json({"id": rid, "status": "ok", "data": DEVICE})
            elif name == "set":
                self.sets.append(req)
                await ws.send_json({"id": rid, "status": "ok"})
                await ws.send_json(
                    {"event": "device_change", "data": {"device": req["device"], "changes": {"status": req["data"]}}}
                )
            else:
                await ws.send_json({"id": rid, "status": "error", "message": f"unknown {name}"})
        return ws

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/api:1/session", self.session)
        app.router.add_get("/api:1/phone", self.phone)
        return app


@pytest.fixture
async def cloud():
    fake = FakeCloud()
    server = TestServer(fake.app())
    await server.start_server()
    yield fake, server
    await server.close()


@pytest.mark.asyncio
async def test_login_and_roundtrip(cloud):
    fake, server = cloud
    async with aiohttp.ClientSession() as session:
        client = ExoHomeClient(session, f"{server.host}:{server.port}", secure=False)

        with pytest.raises(AuthError):
            await client.login("a@b", "wrong")
        body = await client.login("a@b", "pw")
        assert body["token"] == TOKEN and client.user_id == "user-1"

        events: list[dict] = []
        client.add_listener(events.append)
        await client.connect()
        assert client.connected

        assert await client.list_devices() == ["SN0001"]
        rec = await client.get_device("SN0001")
        assert rec["status"]["H03"] == 55

        await client.set_fields("SN0001", {"H00": 0})
        await asyncio.sleep(0.1)
        assert fake.sets[0]["device"] == "SN0001" and fake.sets[0]["data"] == {"H00": 0}
        change = [e for e in events if e.get("event") == "device_change"]
        assert change and change[0]["data"]["changes"]["status"] == {"H00": 0}

        with pytest.raises(RequestError):
            await client.request("nope")

        # server drops the socket -> client reconnects on its own and re-authenticates
        for ws in list(fake.sockets):
            await ws.close()
        await asyncio.sleep(2.5)
        assert client.connected
        assert fake.logins == 2
        assert await client.list_devices() == ["SN0001"]
        await client.disconnect()
        assert not client.connected


@pytest.mark.asyncio
async def test_ws_login_rejected(cloud):
    fake, server = cloud
    fake.reject_token = True
    async with aiohttp.ClientSession() as session:
        client = ExoHomeClient(session, f"{server.host}:{server.port}", token="stale", secure=False)
        with pytest.raises(AuthError):
            await client.connect()
        await client.disconnect()


@pytest.mark.asyncio
async def test_token_expired_triggers_reauth(cloud):
    fake, server = cloud
    async with aiohttp.ClientSession() as session:
        client = ExoHomeClient(session, f"{server.host}:{server.port}", secure=False)
        await client.login("a@b", "pw")
        await client.connect()

        calls = 0

        async def reauth() -> str:
            nonlocal calls
            calls += 1
            fake.reject_token = False
            return TOKEN

        client.set_reauth_callback(reauth)
        fake.reject_token = True  # next ws login fails once
        for ws in list(fake.sockets):
            await ws.close()
        for _ in range(80):  # up to 8 s: 1-2 s reconnect delay, rejected login, immediate retry after re-auth
            await asyncio.sleep(0.1)
            if calls and client.connected:
                break
        assert calls >= 1
        assert client.connected
        await client.disconnect()
