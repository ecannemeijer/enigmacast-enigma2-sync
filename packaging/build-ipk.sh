#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
STAGE=$(mktemp -d)
OUT=${1:-$ROOT/dist}
VERSION=${VERSION:-0.1.0}
PKG=enigmacast-sync
mkdir -p "$STAGE/CONTROL" "$STAGE/usr/lib/enigma2/python/Plugins/Extensions/EnigmaCastSync" "$STAGE/etc/init.d" "$OUT"
cp -a "$ROOT/enigmacast_sync" "$STAGE/usr/lib/enigma2/python/Plugins/Extensions/EnigmaCastSync/"
cp "$ROOT/plugin/plugin.py" "$STAGE/usr/lib/enigma2/python/Plugins/Extensions/EnigmaCastSync/plugin.py"
cp "$ROOT/packaging/enigmacast-sync" "$STAGE/etc/init.d/enigmacast-sync"
chmod 755 "$STAGE/etc/init.d/enigmacast-sync"
cat > "$STAGE/CONTROL/control" <<EOF
Package: $PKG
Version: $VERSION
Architecture: all
Section: extra
Maintainer: ecannemeijer
Description: EnigmaCast profile sync server
EOF
# ipkg-build when present, otherwise a reproducible tar of the same tree.
if command -v ipkg-build >/dev/null 2>&1; then
    ipkg-build -o root -g root "$STAGE" "$OUT"
else
    tar -C "$STAGE" -cf "$OUT/${PKG}_${VERSION}_all.tar" .
    echo "Wrote $OUT/${PKG}_${VERSION}_all.tar (ipkg-build not installed)"
fi
rm -rf "$STAGE"
