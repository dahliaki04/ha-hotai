# Local-control feasibility test

Answers one question: **does the dehumidifier's Wi-Fi module verify the server's TLS certificate?**
If not, we can build a fully-local server/proxy. If it does, local control needs the ESPHome hardware swap.

Facts already established from packet capture (2026-09-06):
- Module = Espressif/mbedTLS, TLS 1.2 only, no client certificate, one MQTT session to
  `w2zftb4yjfzq0000.m2.exosite.io:443`, keepalive every 30 s.
- On reconnect it does a fresh DNS lookup of that hostname, so a local DNS override will catch it.

## Steps (all on Brian's side; ~5 min)

1. **Start the fake server** on this PC (needs port 443, so an Administrator terminal):
   ```
   cd C:\Users\Dell\ha-hotai\localtest
   C:\python313\python.exe fakeserver.py
   ```
   Leave it running. If Windows Firewall prompts, allow it. This PC's LAN IP: **192.168.1.108** (Wi-Fi).

2. **Point the Exosite hostname at this PC** in UniFi:
   Network app -> Settings -> Routing -> **Local DNS Records** -> Create Entry
   - Type: A
   - Record: `w2zftb4yjfzq0000.m2.exosite.io`
   - IP: `192.168.1.108`
   Save.

3. **Force the module to reconnect**: Network app -> Clients -> `espressif` (192.168.1.116) ->
   Block, wait ~90 s, Unblock. (Keepalive is 30 s, so 90 s guarantees the session drops.)

4. Watch the fakeserver window for ONE line:
   - `*** HANDSHAKE OK ***`  -> not verified. Local control is possible; send me that line.
   - `--- HANDSHAKE FAILED ... ---` -> verified. Stop here; local means the ESPHome route.

## Undo (important)
- Delete the Local DNS Record from step 2, or the dehumidifier stays pointed at this PC and goes offline
  in the app once you stop the server.
- Ctrl-C the fakeserver window.
- Nothing else is changed; the module reconnects to the real cloud within a minute.
