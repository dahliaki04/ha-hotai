# What the HT046A1 module trusts (firmware 2.8.3)

Extracted from the OTA firmware image (sha1 0f484bb2…, 1,061,328 bytes). The Wi-Fi module
(Espressif/ESP-IDF + mbedTLS) bundles these CA roots and validates the cloud's certificate chain
against them. TLS failure against a self-signed cert is `unknown_ca`, i.e. normal chain validation,
NOT certificate pinning.

Trust store (outbound cloud TLS):
- DigiCert Global Root CA   <- the real Exosite cert chains here (RapidSSL TLS RSA CA G1 -> this)
- Amazon Root CA 1

(Also embedded: an "ESP32 HTTPS server example" cert + key — that is the module's OWN cert for its
AP-mode provisioning page at https://192.168.1.1:32051, not a trust anchor.)

## Consequence for local control by re-provisioning
To make the module accept OUR local server, our server's cert for the hostname the module connects to
must chain to **DigiCert Global Root CA** or **Amazon Root CA 1**:
- Free ACME CAs do NOT work: Let's Encrypt (ISRG), ZeroSSL (Sectigo), Google Trust Services are not in
  the store.
- Cheapest working cert: a **RapidSSL / DigiCert-brand DV cert** (~US$10-20/yr) for a domain you own —
  RapidSSL chains through DigiCert Global Root CA, the exact chain Exosite itself uses.
- Amazon Root CA 1 is trusted too, but AWS ACM public certs can't be exported to your own server, so
  DigiCert-brand is the practical choice.

Then: re-provision the unit (AP mode -> POST http://192.168.1.1:32051/provision with url=your domain)
and split-horizon DNS (UniFi Local DNS) that domain -> the LAN server running mini-exosite.
