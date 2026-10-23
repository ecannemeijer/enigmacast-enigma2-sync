# EnigmaCast Sync Server

HTTPS API for EnigmaCast profiles, favorites, watch progress and playlists, plus a separate web dashboard. Both run on an Enigma2 receiver, in their own processes and their own directory. They do not change bouquets, tuners, OSCam or OpenWebif settings.

| Service | Port | Process |
| --- | --- | --- |
| Sync API | 8740 | `/etc/init.d/enigmacast-sync` |
| Web Manager | 8766 | `/etc/init.d/enigmacast-web` |

The database goes on a mounted disk (`/media/hdd`, then USB, then other disks). An empty `/media/hdd` folder on the internal flash does not count. With no disk the data is stored in `/etc/enigmacast-sync`, so a receiver without a hard drive can still run the server. The Web Manager keeps serving when the Sync API is stopped. Open the dashboard at `https://<receiver>:8766/`; `http://<receiver>:8766/` redirects to HTTPS instead of resetting the connection.

The dashboard password starts as `admin` and is written once to `web-password-once` in the data directory. Change it on the Server page. An existing password hash is kept.

The IPK depends on `python3-core`, `python3-sqlite3` and `openssl`. Some OpenATV images ship Python without the `sqlite3` module; without it the service exits before it can create `cert.pem`. The package installs the dependency automatically where the feed provides it.

## Run

```sh
python3 -m enigmacast_sync.cli --data-dir /media/hdd/enigmacast-sync init
python3 -m enigmacast_sync.cli --data-dir /media/hdd/enigmacast-sync serve
python3 -m enigmacast_sync.cli --data-dir /media/hdd/enigmacast-sync serve-web
```

`init` prints a one-time pairing code and a recovery code. Keep the recovery code offline. It is the only way back to a single new admin device if the current admin is lost.

Install the release IPK with `opkg`, or let the EnigmaCast setup wizard do that over SSH. See `docs/INSTALL.md`, `docs/API.md` and `docs/SECURITY.md`. The Dutch guide, including a manual install and the dashboard password, is `docs/HANDLEIDING.md`.
