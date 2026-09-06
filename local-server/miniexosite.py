"""mini-exosite — a TLS-terminating proxy/recorder (and future emulator) for HOTAI/Karo's TaiSEIA
dehumidifiers that speak MQTT-over-TLS to Exosite ExoHome.

Purpose
-------
When the vendor cloud (karos.apps.exosite.io / *.m2.exosite.io) is redirected to this host by a
local DNS record, the appliance's Wi-Fi module connects here instead of to Exosite. This program:

  * PROXY mode (default): terminates the device's TLS using our self-signed cert, opens a *separate*
    TLS connection to the REAL Exosite endpoint (dialed by IP so the DNS override doesn't loop back),
    and relays the plaintext MQTT both ways while decoding and logging every packet. The app and cloud
    keep working. This simultaneously answers the open question — if the device completes the TLS
    handshake against our cert, it does NOT verify certificates — and records the exact device-side
    protocol we must emulate.

  * STANDALONE mode (--standalone): answers the device itself with no upstream. Currently a minimal
    MQTT broker skeleton (CONNECT->CONNACK, PINGREQ->PINGRESP, logs PUBLISH/SUBSCRIBE). The state
    machine that turns published fields into HA commands is filled in once proxy captures show us the
    real topics/payloads.

This is for the operator's own appliance on their own network. It presents a certificate for a host
the operator has redirected via their own DNS; run it only against your own device.

Run
---
    python miniexosite.py --cert cert.pem --key key.pem            # proxy mode
    python miniexosite.py --standalone                              # emulator skeleton
    python miniexosite.py --upstream-ip 54.176.200.200             # pin the real cloud IP

Needs Python 3.11+, no third-party packages. Bind to :443 needs root/admin.
"""

from __future__ import annotations

import argparse
import datetime as dt
import selectors
import socket
import ssl
import threading
from pathlib import Path

# Real Exosite device endpoints seen in the packet capture (round-robin A records for
# w2zftb4yjfzq0000.m2.exosite.io). Used to reach the true cloud while its name points at us.
UPSTREAM_IPS = ["54.176.200.200", "54.177.188.141"]
UPSTREAM_SNI = "w2zftb4yjfzq0000.m2.exosite.io"
PORT = 443

MQTT_TYPES = {
    1: "CONNECT", 2: "CONNACK", 3: "PUBLISH", 4: "PUBACK", 5: "PUBREC", 6: "PUBREL",
    7: "PUBCOMP", 8: "SUBSCRIBE", 9: "SUBACK", 10: "UNSUBSCRIBE", 11: "UNSUBACK",
    12: "PINGREQ", 13: "PINGRESP", 14: "DISCONNECT",
}


def ts() -> str:
    return dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]


class MqttLog:
    """Incrementally parse an MQTT byte stream (one direction) and print decoded packets."""

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.buf = bytearray()

    def feed(self, data: bytes) -> None:
        self.buf += data
        while True:
            pkt = self._take_packet()
            if pkt is None:
                return
            self._print(pkt)

    def _take_packet(self) -> bytes | None:
        if len(self.buf) < 2:
            return None
        # remaining-length varint starts at byte 1
        mult = 1
        val = 0
        i = 1
        while True:
            if i >= len(self.buf):
                return None
            b = self.buf[i]
            val += (b & 0x7F) * mult
            i += 1
            if not (b & 0x80):
                break
            mult *= 128
            if mult > 128**3:
                # malformed; drop a byte to resync
                self.buf.pop(0)
                return None
        total = i + val
        if len(self.buf) < total:
            return None
        pkt = bytes(self.buf[:total])
        del self.buf[:total]
        return pkt

    def _print(self, pkt: bytes) -> None:
        ptype = pkt[0] >> 4
        name = MQTT_TYPES.get(ptype, f"?{ptype}")
        flags = pkt[0] & 0x0F
        # locate payload start (skip the varint)
        i = 1
        while pkt[i] & 0x80:
            i += 1
        i += 1
        body = pkt[i:]
        detail = ""
        if name == "PUBLISH":
            qos = (flags >> 1) & 3
            tlen = (body[0] << 8) | body[1]
            topic = body[2 : 2 + tlen].decode("utf-8", "replace")
            rest = body[2 + tlen :]
            if qos > 0 and len(rest) >= 2:
                rest = rest[2:]  # skip packet id
            detail = f"topic={topic!r} qos{qos} payload={_show(rest)}"
        elif name == "CONNECT" and len(body) > 2:
            plen = (body[0] << 8) | body[1]
            proto = body[2 : 2 + plen].decode("ascii", "replace")
            detail = f"proto={proto!r} " + _kv_after_connect(body[2 + plen :])
        elif name == "SUBSCRIBE":
            detail = _show(body)
        print(f"{ts()} {self.tag} {name:10} len={len(pkt):<4} {detail}", flush=True)


def _kv_after_connect(b: bytes) -> str:
    if len(b) < 4:
        return ""
    level, flags = b[0], b[1]
    keepalive = (b[2] << 8) | b[3]
    out = f"level={level} keepalive={keepalive}s"
    rest = b[4:]
    if len(rest) >= 2:
        clen = (rest[0] << 8) | rest[1]
        cid = rest[2 : 2 + clen].decode("utf-8", "replace")
        out += f" client_id={cid!r}"
    return out


def _show(b: bytes, limit: int = 96) -> str:
    try:
        s = b.decode("utf-8")
        if s.isprintable():
            return repr(s if len(s) <= limit else s[:limit] + "…")
    except UnicodeDecodeError:
        pass
    h = b[:limit].hex()
    return f"hex:{h}" + ("…" if len(b) > limit else "")


def _connect_upstream(ip: str) -> ssl.SSLSocket:
    raw = socket.create_connection((ip, PORT), timeout=10)
    cctx = ssl.create_default_context()
    cctx.check_hostname = False
    cctx.verify_mode = ssl.CERT_NONE  # we relay bytes; we are not the trust anchor here
    return cctx.wrap_socket(raw, server_hostname=UPSTREAM_SNI)


def handle_proxy(dev: ssl.SSLSocket, addr, upstream_ips: list[str]) -> None:
    peer = addr[0]
    up = None
    for ip in upstream_ips:
        try:
            up = _connect_upstream(ip)
            print(f"{ts()} [{peer}] upstream connected -> {ip}", flush=True)
            break
        except OSError as e:
            print(f"{ts()} [{peer}] upstream {ip} failed: {e}", flush=True)
    if up is None:
        print(f"{ts()} [{peer}] no upstream; closing (device sees cloud as down)", flush=True)
        dev.close()
        return
    up_log = MqttLog(f"[{peer}] dev->cloud")
    dn_log = MqttLog(f"[{peer}] cloud->dev")
    sel = selectors.DefaultSelector()
    dev.setblocking(False)
    up.setblocking(False)
    sel.register(dev, selectors.EVENT_READ, ("dev", up, up_log))
    sel.register(up, selectors.EVENT_READ, ("up", dev, dn_log))
    try:
        while True:
            for key, _ in sel.select(timeout=120):
                who, other, log = key.data
                try:
                    data = key.fileobj.recv(16384)
                except ssl.SSLWantReadError:
                    continue
                except OSError:
                    data = b""
                if not data:
                    print(f"{ts()} [{peer}] {who} closed", flush=True)
                    return
                log.feed(data)
                try:
                    other.sendall(data)
                except OSError:
                    return
    finally:
        sel.close()
        for s in (dev, up):
            try:
                s.close()
            except OSError:
                pass


def handle_standalone(dev: ssl.SSLSocket, addr) -> None:
    """Minimal broker: keep the device happy and log what it sends. No control logic yet."""
    peer = addr[0]
    log = MqttLog(f"[{peer}] dev->us")
    dev.settimeout(90)
    try:
        while True:
            data = dev.recv(16384)
            if not data:
                break
            log.feed(data)
            # crude: reply to CONNECT with CONNACK, PINGREQ with PINGRESP
            ptype = data[0] >> 4
            if ptype == 1:
                dev.sendall(bytes([0x20, 0x02, 0x00, 0x00]))
                print(f"{ts()} [{peer}] -> CONNACK", flush=True)
            elif ptype == 12:
                dev.sendall(bytes([0xD0, 0x00]))
    except OSError as e:
        print(f"{ts()} [{peer}] closed: {e}", flush=True)
    finally:
        dev.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).parent
    # Production: a CA-issued cert for YOUR hostname. Put the leaf + intermediate(s) in
    # tls/fullchain.pem (leaf first) and the key in tls/<host>.key. The device only trusts chains
    # ending in DigiCert Global Root CA or Amazon Root CA 1 (see firmware-trust/README.md), so the
    # intermediate MUST be included or the module answers unknown_ca.
    _tls = here / "tls"
    _prod_chain = _tls / "fullchain.pem"
    _prod_keys = sorted(_tls.glob("*.key"))
    ap.add_argument("--cert", default=str(_prod_chain if _prod_chain.exists() else here.parent / "localtest" / "cert.pem"))
    ap.add_argument("--key", default=str(_prod_keys[0] if _prod_chain.exists() and _prod_keys else here.parent / "localtest" / "key.pem"))
    ap.add_argument("--sni", default=UPSTREAM_SNI, help="hostname the device was provisioned with (for logging only)")
    ap.add_argument("--standalone", action="store_true", help="emulate the cloud instead of proxying")
    ap.add_argument("--upstream-ip", action="append", help="real Exosite IP (repeatable)")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.minimum_version = ssl.TLSVersion.TLSv1_2
    sctx.maximum_version = ssl.TLSVersion.TLSv1_2
    sctx.load_cert_chain(args.cert, args.key)

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("0.0.0.0", args.port))
    except OSError as e:
        print(f"bind :{args.port} failed ({e}); run as root/admin or free the port", flush=True)
        return
    srv.listen(8)
    mode = "STANDALONE (emulator)" if args.standalone else "PROXY (relay + record)"
    ups = args.upstream_ip or UPSTREAM_IPS
    print(f"{ts()} mini-exosite listening on :{args.port}  mode={mode}", flush=True)
    if not args.standalone:
        print(f"{ts()} upstream candidates: {ups}", flush=True)
    try:
        import ssl as _ssl
        _c = _ssl._ssl._test_decode_cert(args.cert)  # type: ignore[attr-defined]
        _subj = dict(x[0] for x in _c.get("subject", ())).get("commonName")
        _iss = dict(x[0] for x in _c.get("issuer", ())).get("commonName")
        print(f"{ts()} serving cert CN={_subj!r} issued by {_iss!r} (device trusts DigiCert Global Root CA / Amazon Root CA 1 chains)", flush=True)
        if _iss == _subj:
            print(f"{ts()} NOTE: self-signed -> the device WILL reject this (unknown_ca). Use a DigiCert-brand cert in tls/fullchain.pem for real use.", flush=True)
    except Exception:
        pass
    print(f"{ts()} point the provisioned hostname at this host in local DNS, then force the device to reconnect.", flush=True)

    while True:
        raw, addr = srv.accept()
        try:
            dev = sctx.wrap_socket(raw, server_side=True)
        except ssl.SSLError as e:
            print(f"{ts()} [{addr[0]}] TLS handshake FAILED: {e}", flush=True)
            print(f"{ts()}          -> the module VERIFIES certificates. Local emulation is not possible;", flush=True)
            print(f"{ts()}             use the ESPHome/TaiSEIA hardware route instead.", flush=True)
            raw.close()
            continue
        print(f"{ts()} [{addr[0]}] TLS handshake OK ({dev.version()} {dev.cipher()[0]}) "
              f"-> module does NOT verify certs; local control is viable", flush=True)
        target = handle_standalone if args.standalone else handle_proxy
        a = (dev, addr) if args.standalone else (dev, addr, ups)
        threading.Thread(target=target, args=a, daemon=True).start()


if __name__ == "__main__":
    main()
