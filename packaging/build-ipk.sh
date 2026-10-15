#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
STAGE=$(mktemp -d)
OUT=${1:-$ROOT/dist}
VERSION=${VERSION:-0.2.1}
PKG=enigmacast-sync
PLUGIN="$STAGE/usr/lib/enigma2/python/Plugins/Extensions/EnigmaCastSync"
OWIF="$STAGE/usr/lib/enigma2/python/Plugins/Extensions/WebInterface/WebChilds/External"
mkdir -p "$STAGE/CONTROL" "$PLUGIN" "$STAGE/etc/init.d" "$OWIF" "$OUT"
cp -a "$ROOT/enigmacast_sync" "$PLUGIN/"
cp "$ROOT/plugin/plugin.py" "$PLUGIN/plugin.py"
cp "$ROOT/plugin/owif/enigmacast.py" "$OWIF/enigmacast.py"
cp "$ROOT/packaging/enigmacast-sync" "$STAGE/etc/init.d/enigmacast-sync"
cp "$ROOT/packaging/enigmacast-web" "$STAGE/etc/init.d/enigmacast-web"
cp "$ROOT/packaging/postinst" "$STAGE/CONTROL/postinst"
cp "$ROOT/packaging/prerm" "$STAGE/CONTROL/prerm"
cp "$ROOT/packaging/postrm" "$STAGE/CONTROL/postrm"
chmod 755 "$STAGE/etc/init.d/enigmacast-sync" "$STAGE/etc/init.d/enigmacast-web" \
    "$STAGE/CONTROL/postinst" "$STAGE/CONTROL/prerm" "$STAGE/CONTROL/postrm"
find "$STAGE" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
cat > "$STAGE/CONTROL/control" <<EOF
Package: $PKG
Version: $VERSION
Architecture: all
Section: extra
Maintainer: ecannemeijer
Description: EnigmaCast sync API and web dashboard
EOF
if command -v ipkg-build >/dev/null 2>&1; then
    ipkg-build -o root -g root "$STAGE" "$OUT"
else
    VERSION="$VERSION" python3 "$ROOT/packaging/build_ipk.py" "$OUT"
fi
rm -rf "$STAGE"
