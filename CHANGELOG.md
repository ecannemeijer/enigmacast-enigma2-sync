# Changelog

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
- Added optional `limit` to `/api/v1/sync/pull` (`1`–`1000`).

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
