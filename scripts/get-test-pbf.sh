#!/bin/sh
# Download a small test extract (needs internet on the dev machine only).
set -eu
DEST="$(cd "$(dirname "$0")" && pwd)/testdata"
mkdir -p "$DEST"
URL="${1:-https://download.geofabrik.de/europe/monaco-latest.osm.pbf}"
echo "Pobieram $URL"
curl -fL --progress-bar -o "$DEST/$(basename "$URL")" "$URL"
echo "Zapisano w $DEST"
# Larger example (one voivodeship, ~100-200 MB):
#   ./scripts/get-test-pbf.sh https://download.geofabrik.de/europe/poland/malopolskie-latest.osm.pbf
