#!/bin/sh
# Build a .deb for the current architecture (amd64 or arm64).
# Run on the target architecture (or in a matching container) after `make frontend`.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VERSION=$(python3 -c "import re;print(re.search(r'\"(.+)\"', open('$ROOT/backend/app/__init__.py').read()).group(1))")
ARCH=$(dpkg --print-architecture)
STAGE="$ROOT/build/osm-tilegen_${VERSION}_${ARCH}"

[ -f "$ROOT/frontend/dist/index.html" ] || { echo "Najpierw: make frontend"; exit 1; }

rm -rf "$STAGE"
install -d "$STAGE/DEBIAN" "$STAGE/usr/lib/osm-tilegen" "$STAGE/usr/share/osm-tilegen" \
    "$STAGE/lib/systemd/system"

cp -r "$ROOT/backend/app" "$STAGE/usr/lib/osm-tilegen/"
find "$STAGE/usr/lib/osm-tilegen" -name __pycache__ -prune -exec rm -rf {} +
cp -r "$ROOT/frontend/dist" "$STAGE/usr/share/osm-tilegen/ui"
cp -r "$ROOT/styles" "$STAGE/usr/share/osm-tilegen/styles"
cp -r "$ROOT/tilemaker" "$STAGE/usr/share/osm-tilegen/tilemaker"
cp "$ROOT/packaging/config.toml.example" "$STAGE/usr/share/osm-tilegen/"
cp "$ROOT/packaging/systemd/"*.service "$STAGE/lib/systemd/system/"

sed -e "s/@VERSION@/$VERSION/" -e "s/@ARCH@/$ARCH/" "$ROOT/packaging/debian/control" > "$STAGE/DEBIAN/control"
for s in postinst prerm postrm; do
    install -m 0755 "$ROOT/packaging/debian/$s" "$STAGE/DEBIAN/$s"
done

dpkg-deb --build --root-owner-group "$STAGE"
echo "Gotowe: $STAGE.deb"
