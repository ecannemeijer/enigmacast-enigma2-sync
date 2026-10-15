# Changelog

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
