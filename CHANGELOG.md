# Changelog

## 0.3.3

- Full backup/restore in the dashboard: database, `master.key`, TLS certificate/key and the dashboard password hash in one archive.
- Upload validation rejects unknown files, path traversal, oversized archives, corrupt databases and mismatched certificates before replacing anything.
- A safety backup is created automatically before a restore; the Sync API is stopped/started around it and the Web Manager restarts so the restored certificate takes effect.

## 0.3.2

- Web manager (8766): login works on the first try again. The handler cached the parsed request body per connection, and with HTTP/1.1 keep-alive one handler serves every request on that connection. A second POST (a retry after a typo, a login after logout, any second dashboard action) reused the first body and left its own body unread in the socket, where it was read as the next request line: `501 Unsupported method ('csrf=â€¦&password=â€¦GET')` for `/static/app.css`, so the page came back without styling, logout answered `403 csrf`, and a second dashboard action repeated the first one.
- Every request now reads exactly `Content-Length` bytes first, before any redirect, 401/403, CSRF or static answer. Bodies above 64 KiB get `413`, an invalid length `400`, chunked bodies `411`; those answers close the connection without reading the body. PUT/DELETE/PATCH get `405` after their body is read.
- `web.log` logs method, path and status only. An unknown method or a garbled request line is logged as `-`, never its content (0.3.1 could write a leftover login body, including the password, to `web.log`). `web.log` is rotated to `web.log.1` above 256 KiB.
- Less memory on small receivers: idle keep-alive connections are closed after 30 s, the TLS sniff and handshake run in the worker thread with a 10 s limit (one silent client no longer blocks the accept loop), dropped connections no longer print tracebacks, and the log viewer reads only the last 64 KiB of a log instead of the whole file.
- Sync API (8740): request bodies are capped at 4 MiB (`413`); a negative or invalid `Content-Length` gets `400` and chunked bodies `411` instead of a `500`. The API already closed the connection after every response, so it never had the keep-alive problem.
- Watchdog: the package adds one cron line (`# enigmacast-sync watchdog`, in `/etc/cron/crontabs/root`) that runs `EnigmaCastSync/watchdog` every minute. The new init script action `watchdog` starts the Sync API or the Web Manager when it is not running (e.g. killed by the OOM killer) and logs that. A service stopped on purpose (`stop`, dashboard **Stop**) is left alone until `start`/`restart`/reboot. Install, reinstall and upgrade keep exactly one line; `opkg remove` deletes it. crond is started if it was not running; it never gets a SIGHUP (busybox crond would exit).
- `sync-run` and `web-run` set `oom_score_adj` to 300: when memory runs out the kernel ends a sync server before Enigma2, and the watchdog restarts it.
- Request threads of both servers use 256 KiB stacks. `sync.log` is rotated to `sync.log.1` above 1 MiB when the Sync API starts.

## 0.3.1

- Explicit server version: `SERVER_VERSION` in `enigmacast_sync/__init__.py`, reported by `GET /api/v1/health` (`"version"`), the new unauthenticated `GET /api/v1/version` and `/api/v1/server`. The app compares it with the latest release and offers an update.
- `/api/v1/sync/pull` answers `"more": true|false` (one extra row is read), and only the columns a client applies (no `device_id`).
- Pruning and `VACUUM` no longer run inside a pull request; a background thread does it every 6 hours. A VACUUM on a slow receiver could stall the response long enough for the TV to drop it.
- TLS handshake per connection in the worker thread (it ran in the accept loop: one slow client blocked every other one), 15 s handshake and 60 s socket timeouts, TLS `close_notify` before close.
- Responses: compact ASCII JSON, `Content-Length` from the encoded bytes, `Connection: close`. The global lock covers only the database work, not writing the response, and a failed write no longer appends a second 500 response to the broken stream.
- Data directory: a database left by 0.2.4 or older in an unmounted `/media/<x>/enigmacast-sync` folder is kept (those versions only checked that the folder existed), and a mounted disk that already holds `sync.db` wins. Upgrading from 0.2.4 keeps pairings, certificate and history.
- The package version is taken from `enigmacast_sync/__init__.py` when `VERSION` is not set.

## 0.3.0

- Meerdere IPTV-providers kunnen nu met de syncserver worden gedeeld via `GET/POST /api/v1/iptv/providers`.
- De providerlijst wordt versleuteld op de ontvanger bewaard, inclusief naam, type, volgorde en aan/uit-status.
- De bestaande enkele-playlist-API blijft werken voor oudere apps.

## 0.2.8

- De webdienst op 8766 accepteert nu zowel HTTPS als gewoon HTTP. Een browser die `http://<receiver>:8766` opent krijgt een redirect naar HTTPS in plaats van een lege/reset-verbinding.
- De Sync API op 8740 blijft TLS-only.

## 0.2.7

- De installer controleert nu `python3-sqlite3`. Op OpenATV-receivers zonder harde schijf, waar de data in `/etc/enigmacast-sync` staat, stopte de service eerder direct met `ModuleNotFoundError: No module named 'sqlite3'`; daardoor werd `cert.pem` nooit aangemaakt.
- Het IPK declareert `python3-core`, `python3-sqlite3` en `openssl` als dependencies.
- De post-installatie stopt met een duidelijke melding als `sqlite3` ontbreekt, in plaats van een stille dood van de service.

## 0.2.6

- `/api/v1/sync/pull` returns bounded pages (default 500, client asks 50) instead of the entire change history. A large single TLS response could be dropped with `BrokenPipe`/`SSL BAD_LENGTH`, so the TV never applied it.
- Per-device/profile cursors are recorded when a client pulls. History already applied by every active device is pruned, keeping the newest 2,000 changes per profile. Devices unseen for 90 days no longer hold pruning back.
- Large prunes compact the SQLite database with `VACUUM`, so removed history also releases disk space.
- Added optional `limit` to `/api/v1/sync/pull` (`1`â€“`1000`).

## 0.2.5

- Data stays on a mounted disk (`/media/hdd`, then USB, MMC, CF). An empty mount folder on the internal flash is ignored. With no disk, the database is stored in `/etc/enigmacast-sync` so a receiver without a hard drive can still run the server.

## 0.2.4

- One IPTV login for every paired device, via GET and POST `/api/v1/playlists`. The password stays sealed in the playlist table.
- The dashboard starts with the password admin, and the server page can change it.

## 0.2.3

- Start the daemons through their own scripts. Tracking `/usr/bin/python3` also matched other Python processes, so the Web Manager never started.
- Ignore chown failures on disks that do not store Unix owners. The services still run as enigmacast.

## 0.2.2

- Services drop to the enigmacast user inside Python. BusyBox has that user without a matching group, and start-stop-daemon could not open /dev/null after a user switch.

## 0.2.1

- The release IPK is built in the GitHub workflow without opkg-utils.

## 0.2.0

- Web Manager on port 8766, in the same IPK as the Sync API and started as its own service.
- Dashboard for status, service control, profiles, devices and applied sync changes.
- Browser login, CSRF cookies and a LAN address check. The dashboard password is generated on the receiver.
- OpenWebif Extras link via `WebChilds/External`, without editing OpenWebif.
- Release workflow publishes the `all` IPK. Upgrades keep the database and pairing data.

## 0.1.0

- HTTPS API under `/api/v1` for health, pairing, devices, profiles, playlists, sync, backups and recovery.
- One admin device. Recovery revokes every device and issues one new pairing code.
- SQLite on external storage, AES-256-GCM for playlist secrets, ECDSA P-256 request signatures.
