"""Probe the HOTAI / ExoHome cloud with your 和泰智慧家 account — no Home Assistant needed.

    python tools/probe.py EMAIL PASSWORD                 # list devices, dump full records, listen 30 s
    python tools/probe.py EMAIL PASSWORD --listen 120    # keep printing device_change events
    python tools/probe.py EMAIL PASSWORD --set SN H00=1  # send a command (power on) and watch the echo
    python tools/probe.py EMAIL PASSWORD --models        # also download the cloud information models

Writes probe_dump.json next to this script (token redacted). Requires: pip install aiohttp
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "hotai_smart_home"))
from exohome import DEFAULT_HOST, ExoHomeClient, ExoHomeError  # noqa: E402


def _kv(pairs: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in pairs:
        k, _, v = p.partition("=")
        out[k.strip()] = int(v)
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("email")
    ap.add_argument("password")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--listen", type=int, default=30, help="seconds to stay connected and print events")
    ap.add_argument("--set", nargs="+", metavar="ARG", help="SN followed by KEY=VALUE pairs, e.g. --set ABC123 H00=1")
    ap.add_argument("--models", action="store_true", help="download /api:1/info-model too")
    ap.add_argument("--out", default=str(Path(__file__).with_name("probe_dump.json")))
    args = ap.parse_args()

    dump: dict = {"host": args.host, "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    async with aiohttp.ClientSession() as session:
        client = ExoHomeClient(session, args.host)
        print(f"[1] REST login as {args.email} @ {args.host} ...")
        try:
            body = await client.login(args.email, args.password)
        except ExoHomeError as err:
            print("    login failed:", err)
            return 1
        print(f"    ok, user id = {body.get('id')}")
        dump["login"] = {k: ("<redacted>" if k == "token" else v) for k, v in body.items()}

        events: list[dict] = []

        def on_event(ev: dict) -> None:
            if ev.get("event", "").startswith("_"):
                print(f"    [ws] {ev['event']}")
                return
            events.append({"t": time.strftime("%H:%M:%S"), **ev})
            print(f"    [event {time.strftime('%H:%M:%S')}] {json.dumps(ev, ensure_ascii=False)}")

        client.add_listener(on_event)

        print("[2] WebSocket login ...")
        await client.connect()
        me = await client.get_me()
        dump["me"] = me
        print("    get_me:", json.dumps(me, ensure_ascii=False)[:300])

        print("[3] lst_device ...")
        raw_list = (await client.request("lst_device")).get("data")
        dump["lst_device"] = raw_list
        sns = [d["device"] if isinstance(d, dict) else str(d) for d in (raw_list or [])]
        print(f"    {len(sns)} device(s): {sns}")

        dump["devices"] = {}
        for sn in sns:
            rec = await client.get_device(sn)
            dump["devices"][sn] = rec
            esh = (rec.get("profile") or {}).get("esh") or {}
            mod = (rec.get("profile") or {}).get("module") or {}
            print(f"\n    == {sn} ==")
            print(f"       brand/model : {esh.get('brand')} / {esh.get('model')}   esh device_id={esh.get('device_id')} class={esh.get('class')}")
            print(f"       module      : fw={mod.get('firmware_version')} mac={mod.get('mac_address')} ip={mod.get('local_ip')} ssid={mod.get('ssid')}")
            print(f"       connected   : {rec.get('connected')}   device_state={rec.get('device_state')}")
            print(f"       name        : {(rec.get('properties') or {}).get('displayName')}")
            print(f"       fields      : {rec.get('fields')}")
            print(f"       fields_range: {json.dumps(rec.get('fields_range'), ensure_ascii=False)}")
            print(f"       status      : {json.dumps(rec.get('status'), ensure_ascii=False)}")
            extra = {k: v for k, v in rec.items() if k not in ("profile", "status", "fields", "fields_range", "properties", "users", "calendar")}
            print(f"       other keys  : {json.dumps(extra, ensure_ascii=False)[:300]}")

        if args.models:
            print("\n[4] info models ...")
            try:
                dump["info_models"] = await client.info_models()
                print("    ", json.dumps(dump["info_models"], ensure_ascii=False)[:500])
            except ExoHomeError as err:
                print("    info-model failed:", err)

        if args.set:
            sn, fields = args.set[0], _kv(args.set[1:])
            print(f"\n[5] set {sn} <- {fields}")
            resp = await client.request("set", sn, fields)
            print("    response:", json.dumps(resp, ensure_ascii=False))
            dump["set"] = {"sn": sn, "fields": fields, "response": resp}

        if args.listen > 0:
            print(f"\n[6] listening for events for {args.listen}s (change something in the app or on the unit) ...")
            await asyncio.sleep(args.listen)
            for sn in sns:
                dump["devices"][sn + "_after"] = await client.get_device(sn)
        dump["events"] = events
        await client.disconnect()

    Path(args.out).write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
