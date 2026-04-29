# Device ↔ Server Contract

This document defines the API contract between `tsmedia-player` (this repo, the
Raspberry Pi device application) and `tsmedia-server` (the PiSignage media
server running on a Hostinger VPS).

**Contract version:** `1.0.0`

Both repositories must pin to the same major version. Breaking changes require
a major version bump and coordinated deployment.

---

## Endpoints consumed by the device

The device is a REST client. The server exposes a PiSignage-compatible HTTP API.

| Method | Path | Purpose | Used by |
|---|---|---|---|
| `POST` | `/api/session` | Authenticate and obtain session cookie | `pisignage_adapter.py` |
| `GET` | `/api/files` | List media assets | `playlist_manager.py` |
| `POST` | `/api/files` | Upload media asset | `playlist_manager.py` |
| `GET` | `/api/playlists` | List playlists | `playlist_manager.py` |
| `POST` | `/api/playlists/<name>` | Create or update a playlist | `playlist_manager.py` |
| `POST` | `/api/pi/setplaylist/<name>` | Switch the player to a named playlist | `pisignage_adapter.py` |
| `GET` | `/api/pi/health` | Server health check | `pisignage_health.py` |

Authentication: HTTP Basic (`PISIGNAGE_USERNAME` / `PISIGNAGE_PASSWORD`)
followed by session cookie. The server's auth scheme is defined in
`tsmedia-server`.

---

## Environment variables expected by the device

These must be set in `tsv6.service` (or `tsv6-signage.service`) on the Pi:

```
PISIGNAGE_SERVER_URL    Full URL, e.g. https://tsmedia.g1tech.cloud
PISIGNAGE_USERNAME      Auth username
PISIGNAGE_PASSWORD      Auth password
PISIGNAGE_INSTALLATION  Player installation identifier (e.g. g1tech26)
PISIGNAGE_GROUP         Player group (default: default)
PISIGNAGE_ENABLED       Feature flag (true|false)
```

---

## Versioning rules

- **Patch** (1.0.x): bug fixes, no API change
- **Minor** (1.x.0): backwards-compatible additions (new endpoints, new optional fields)
- **Major** (x.0.0): breaking changes — coordinated server + device deploy required

When bumping the contract, update this file in BOTH repos in the same release.

---

## Out-of-scope

The following are NOT part of this contract and are owned independently by
each repo:

- Device-side: AWS IoT topic schemas, NFC URL format, barcode parsing rules
- Server-side: PiSignage internal database schema, Docker deployment, asset storage layout
