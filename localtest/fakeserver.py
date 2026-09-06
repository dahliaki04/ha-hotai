"""Decisive local-control test: does the dehumidifier's Wi-Fi module verify the server certificate?

We stand up a TLS 1.2 server on this PC with a SELF-SIGNED certificate for the Exosite hostname
(w2zftb4yjfzq0000.m2.exosite.io). After you point that hostname at this PC (UniFi local DNS) and
force the module to reconnect, one of two things happens:

  * HANDSHAKE OK  -> the module does NOT verify certificates. A local server / MITM proxy is possible;
                     we can build fully-local control.
  * HANDSHAKE FAILED (TLS alert right after our certificate) -> the module verifies the cert chain.
                     Local emulation is out; the ESPHome/TaiSEIA hardware swap is the only local route.

This only impersonates a host on YOUR network for YOUR own device. Stop it with Ctrl-C.

    py -3 fakeserver.py         (Windows launcher)   or   C:\python313\python.exe fakeserver.py
"""
import binascii
import socket
import ssl
import threading
import time

HOST_CN = "w2zftb4yjfzq0000.m2.exosite.io"

ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.minimum_version = ssl.TLSVersion.TLSv1_2
ctx.maximum_version = ssl.TLSVersion.TLSv1_2
ctx.load_cert_chain("cert.pem", "key.pem")
# Offer the suite the real server picked plus common mbedTLS fallbacks.
ctx.set_ciphers(
    "ECDHE-RSA-AES256-GCM-SHA384:ECDHE-RSA-AES128-GCM-SHA256:"
    "AES256-GCM-SHA384:AES128-GCM-SHA256:ECDHE-RSA-AES256-SHA384:"
    "ECDHE-RSA-AES128-SHA256:AES256-SHA256:AES128-SHA256:AES256-SHA:AES128-SHA"
)


def handle(raw, addr):
    t0 = time.time()
    try:
        s = ctx.wrap_socket(raw, server_side=True)
        print(
            f"[{time.strftime('%H:%M:%S')}] {addr[0]}  *** HANDSHAKE OK ***  proto={s.version()} "
            f"cipher={s.cipher()[0]}  -> module does NOT verify the certificate (local control POSSIBLE)",
            flush=True,
        )
        s.settimeout(15)
        try:
            data = s.recv(4096)
            print(f"    first app data ({len(data)} B): {binascii.hexlify(data[:48]).decode()} ...", flush=True)
            if data[:1] == b"\x10":
                print("    => that is an MQTT CONNECT packet. Full local emulation is on the table.", flush=True)
        except Exception as e:
            print(f"    (handshake completed but no app data yet: {e})", flush=True)
        s.close()
    except ssl.SSLError as e:
        print(
            f"[{time.strftime('%H:%M:%S')}] {addr[0]}  --- HANDSHAKE FAILED after {time.time()-t0:.2f}s: {e} ---  "
            f"-> module VERIFIES the certificate (local server NOT possible; use the ESPHome route)",
            flush=True,
        )
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] {addr[0]}  error: {e}", flush=True)


def main():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("0.0.0.0", 443))
    except OSError as e:
        print(f"Could not bind :443 ({e}). Run this terminal as Administrator, or free port 443.", flush=True)
        return
    srv.listen(5)
    print(
        f"Fake Exosite endpoint listening on 0.0.0.0:443  (TLS1.2, self-signed CN={HOST_CN}).\n"
        f"Now point {HOST_CN} at this PC in UniFi and force the dehumidifier to reconnect.\n"
        f"Watch this window. Ctrl-C to stop.",
        flush=True,
    )
    while True:
        try:
            c, a = srv.accept()
        except KeyboardInterrupt:
            print("stopped.", flush=True)
            return
        threading.Thread(target=handle, args=(c, a), daemon=True).start()


if __name__ == "__main__":
    main()
