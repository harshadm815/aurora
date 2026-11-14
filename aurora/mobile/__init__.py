"""
AURORA Mobile Companion: use AURORA from your phone.

    devices.py   pairing codes, paired devices (only a hash of each device's key is kept),
                 per-device permissions, an access log (what was used, never what was said)
    server.py    a small web server on THIS PC ONLY (127.0.0.1): the phone app and its API
    web/         the phone app (a web app you add to the home screen)

The server never listens on your Wi-Fi or the internet. To reach it from
the phone, Tailscale (a private, encrypted network between your own
devices) forwards it with a real HTTPS certificate: see
Docs/VOICE_WAKE_MOBILE.md. Every request must carry a paired device's key.
"""
