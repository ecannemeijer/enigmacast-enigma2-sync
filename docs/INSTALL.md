# Install

The EnigmaCast setup wizard is the install path from the Android app. It downloads the latest GitHub release IPK and runs `opkg install` on the receiver. That package contains the Sync API, the Enigma2 plugin, the Web Manager and the OpenWebif Extras link.

An upgrade replaces those files and restarts both services. It does not delete the data directory, so the database, certificates, pairing and the dashboard password hash stay in place.

## Package contents

- `/usr/lib/enigma2/python/Plugins/Extensions/EnigmaCastSync/`
- `/etc/init.d/enigmacast-sync` and `/etc/init.d/enigmacast-web`
- `/usr/lib/enigma2/python/Plugins/Extensions/WebInterface/WebChilds/External/enigmacast.py`
- one line in root's crontab (`/etc/cron/crontabs/root`), marked `# enigmacast-sync watchdog`

## Watchdog and memory

Since 0.3.2 the package adds a cron job that runs `EnigmaCastSync/watchdog` every minute. It calls `/etc/init.d/enigmacast-sync watchdog` and `/etc/init.d/enigmacast-web watchdog`, which start a service that is not running, for example after the kernel's OOM killer ended it, and log that in `sync.log` or `web.log`. A service stopped on purpose (`stop`, or **Stop** in the dashboard) stays stopped until `start`, `restart` or a reboot. Reinstalling or upgrading never adds a second line; `opkg remove` deletes it. By hand: `EnigmaCastSync/watchdog install` or `EnigmaCastSync/watchdog remove`. The watchdog needs `crond`; the installer starts `/etc/init.d/busybox-cron` if cron is not running.

`sync-run` and `web-run` set `oom_score_adj` to 300 before starting Python. On a receiver that runs out of memory the kernel then ends a sync server before Enigma2, and the watchdog brings it back. Request threads use 256 KiB stacks, idle dashboard connections close after 30 s, `web.log` rotates at 256 KiB and `sync.log` at 1 MiB (when the Sync API starts), and the log viewer reads only the last 64 KiB of a log.

Both services run as the system user `enigmacast`. The package creates that user and gives it the existing data directory. It does not edit satellite, OSCam, tuner, bouquet or streaming settings.

OpenWebif loads the Extras item **EnigmaCast Sync Server** from `WebChilds/External`. The item opens `https://<current host>:8766/`. OpenWebif's own files are not modified. If port 8766 is already taken, the Web Manager stops and writes that to its log instead of picking another port.

## Manual install

```sh
opkg install enigmacast-sync_<version>_all.ipk
```

Build that IPK with `packaging/build-ipk.sh`. A tag `v*` publishes one `all` package, which is the only architecture variant: the server is pure Python.

A mounted `/media/hdd`, `/media/usb`, `/media/mmc` or `/media/cf` is used when present. Otherwise the data directory is `/etc/enigmacast-sync`. Then:

```sh
python3 -m enigmacast_sync.cli --data-dir /media/hdd/enigmacast-sync init
```

Check `https://<receiver>:8740/api/v1/health` and `https://<receiver>:8766/`. The certificate is generated on the box. If it is missing, the dashboard falls back to HTTP. The Sync API does not.
