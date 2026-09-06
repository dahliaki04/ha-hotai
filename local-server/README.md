# mini-exosite — local server for the dehumidifier

Goal: keep the HOTAI/Karo's dehumidifier controllable **even if the vendor cloud (Exosite) is shut
down**. The appliance's Wi-Fi module speaks MQTT-over-TLS to `w2zftb4yjfzq0000.m2.exosite.io:443`.
A local DNS record points that name at this server; the server then either relays to the real cloud
(and records the protocol) or answers on its own.

## The gate (do this first, together, ~5 min)

Everything below only works **if the module does not verify TLS certificates.** `miniexosite.py`
tells you on the first device connection:

- `TLS handshake OK ... module does NOT verify certs` → proceed.
- `TLS handshake FAILED ... module VERIFIES certificates` → stop; use the ESPHome route
  (`../esphome/hotai-dehumidifier.yaml`). No server anywhere can help.

## Run it

Needs Python 3.11+, no dependencies. Binding :443 needs root/admin.

```
# PROXY mode: relay to the real cloud AND log every MQTT packet (app keeps working)
python miniexosite.py

# STANDALONE mode: answer the device with no cloud (emulator skeleton)
python miniexosite.py --standalone
```

Then add a **Local DNS Record** in UniFi: `w2zftb4yjfzq0000.m2.exosite.io` → this server's IP,
and force the dehumidifier to reconnect (Clients → `espressif`/192.168.1.116 → Block 90 s → Unblock).

Cert/key come from `../localtest/` by default (`--cert`/`--key` to override). They are self-signed
for the Exosite hostname; the module accepts them only if it doesn't verify — which is the whole test.

## Where to host it

- **Proxmox LXC (recommended):** a 256 MB Debian 12 container on the LAN, one `systemd` service.
  Survives HA restarts, snapshot before changes, owns :443 with no conflicts.
- **The HA box (192.168.1.113):** simplest, but shares the host with HA.
- **Not Cloudflare:** Workers can't accept the device's raw inbound MQTT/TLS, and no CA will issue a
  cert for a domain you don't own. A cloud VM works but dies with your internet, defeating "local".

Wherever it runs, it must be on 192.168.1.x so the appliance can reach it, and the DNS record must
point at it.

## Plan

1. **Proxy + record** while the vendor cloud is alive. Capture every message type: power, target
   humidity, fan, timer, faults (H12), the provisioning/activation handshake, the 30 s keepalive.
2. Fill in `handle_standalone()` so it reproduces those exchanges: accept the device's CONNECT, issue
   the state it expects, accept `set`-style publishes, and bridge them to Home Assistant (local push
   into the `hotai_dehumidifier` integration via a small WebSocket/HTTP shim, mirroring the cloud API).
3. Point HA's integration "Cloud host" at this server. Same entities, no vendor cloud.

## Undo

Delete the UniFi Local DNS record and the appliance goes back to the real cloud within a minute.
Nothing else on the network is changed.

## Status

Proxy relay + MQTT decoder: working and self-tested (loopback TLS + fragmented-stream parse).
Standalone broker: skeleton only (CONNACK/PINGRESP + logging) until proxy captures show the real
topics and payloads. The device-side protocol is still opaque until the first real proxy run.
