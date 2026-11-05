// Copy this file to secrets.h. secrets.h is ignored by git.

#define WIFI_SSID "your-wifi-name"
#define WIFI_PASSWORD "your-wifi-password"

// A long random secret (at least 16 characters). Put the same value in
// DEVICE_CONTROLLER_TOKEN in aurora/config/local_settings.py.
// For example, generate one with:  python -c "import secrets; print(secrets.token_urlsafe(24))"
#define DEVICE_TOKEN "replace-with-a-long-random-secret"
