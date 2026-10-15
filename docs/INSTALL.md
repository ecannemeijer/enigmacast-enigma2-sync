# Install

The EnigmaCast setup wizard is the install path from the Android app. It downloads the latest GitHub release IPK and runs `opkg install` on the receiver. That package contains the Sync API, the Enigma2 plugin, the Web Manager and the OpenWebif Extras link.

An upgrade replaces those files and restarts both services. It does not delete the data directory, so the database, certificates, pairing and the dashboard password hash stay in place.

## Package contents

- `/usr/lib/enigma2/python/Plugins/Extensions/EnigmaCastSync/`
- `/etc/init.d/enigmacast-sync` and `/etc/init.d/enigmacast-web`
- `/usr/lib/enigma2/python/Plugins/Extensions/WebInterface/WebChilds/External/enigmacast.py`

Both services run as the system user `enigmacast`. The package creates that user and gives it the existing data directory. It does not edit satellite, OSCam, tuner, bouquet or streaming settings.

OpenWebif loads the Extras item **EnigmaCast Sync Server** from `WebChilds/External`. The item opens `https://<current host>:8766/`. OpenWebif's own files are not modified. If port 8766 is already taken, the Web Manager stops and writes that to its log instead of picking another port.

## Manual install

```sh
opkg install enigmacast-sync_<version>_all.ipk
```

Build that IPK with `packaging/build-ipk.sh`. A tag `v*` publishes one `all` package, which is the only architecture variant: the server is pure Python.

Confirm a writable `/media/hdd`, `/media/usb`, `/media/mmc` or `/media/cf`. Then:

```sh
python3 -m enigmacast_sync.cli --data-dir /media/hdd/enigmacast-sync init
```

Check `https://<receiver>:8740/api/v1/health` and `https://<receiver>:8766/`. The certificate is generated on the box. If it is missing, the dashboard falls back to HTTP. The Sync API does not.
