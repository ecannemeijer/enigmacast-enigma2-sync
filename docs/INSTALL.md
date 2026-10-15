# Install

1. Copy `enigmacast_sync` to `/usr/lib/enigma2/python/Plugins/Extensions/EnigmaCastSync/enigmacast_sync`.
2. Install `packaging/enigmacast-sync` as `/etc/init.d/enigmacast-sync` and run `chmod 755`.
3. Confirm a writable `/media/hdd`, `/media/usb` or `/media/mmc`. The service will not create its database on internal flash.
4. `/etc/init.d/enigmacast-sync start`
5. Check `https://<receiver>:8740/api/v1/health` (the certificate is generated on the box; pin it in EnigmaCast).

The init script only starts and stops this service. It does not edit Enigma2 settings.

An IPK can be built with `packaging/build-ipk.sh` and installed with `opkg install`.
