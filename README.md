# EnigmaCast Sync Server

HTTPS API for EnigmaCast profiles, favorites, watch progress and playlists. It runs on an Enigma2 receiver, on its own port (8740) and its own directory. It does not change bouquets, tuners, OSCam or OpenWebif.

The database goes on external storage (`/media/hdd`, then USB, then other disks). If none is writable the server refuses to start, so it does not wear the internal flash.

## Run

```sh
python3 -m enigmacast_sync.cli --data-dir /media/hdd/enigmacast-sync init
python3 -m enigmacast_sync.cli --data-dir /media/hdd/enigmacast-sync serve
```

`init` prints a one-time pairing code and a recovery code. Keep the recovery code offline. It is the only way back to a single new admin device if the current admin is lost.

See `docs/INSTALL.md`, `docs/API.md` and `docs/SECURITY.md`.
"# enigmacast-enigma2-sync" 
