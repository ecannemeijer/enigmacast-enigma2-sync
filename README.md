# EnigmaCast Sync Server

HTTPS API for EnigmaCast profiles, favorites, watch progress and playlists, plus a separate web dashboard. Both run on an Enigma2 receiver, in their own processes and their own directory. They do not change bouquets, tuners, OSCam or OpenWebif settings.

| Service | Port | Process |
| --- | --- | --- |
| Sync API | 8740 | `/etc/init.d/enigmacast-sync` |
| Web Manager | 8766 | `/etc/init.d/enigmacast-web` |

The database goes on external storage (`/media/hdd`, then USB, then other disks). If none is writable the services refuse to start, so they do not wear the internal flash. The Web Manager keeps serving when the Sync API is stopped.

The dashboard password is generated on first start and written once to `web-password-once` in the data directory. It is not stored in this repository.

## Run

```sh
python3 -m enigmacast_sync.cli --data-dir /media/hdd/enigmacast-sync init
python3 -m enigmacast_sync.cli --data-dir /media/hdd/enigmacast-sync serve
python3 -m enigmacast_sync.cli --data-dir /media/hdd/enigmacast-sync serve-web
```

`init` prints a one-time pairing code and a recovery code. Keep the recovery code offline. It is the only way back to a single new admin device if the current admin is lost.

Install the release IPK with `opkg`, or let the EnigmaCast setup wizard do that over SSH. See `docs/INSTALL.md`, `docs/API.md` and `docs/SECURITY.md`.
