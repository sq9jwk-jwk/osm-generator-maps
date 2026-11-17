#!/bin/sh
# Spike M0.5: verify the toolchain on Debian 13 (amd64 and Raspberry Pi arm64).
# Usage: ./scripts/spike-render.sh scripts/testdata/monaco-latest.osm.pbf
set -eu
PBF="${1:?podaj plik .osm.pbf}"
WORK="$(mktemp -d /tmp/osmtile-spike.XXXX)"
echo "== katalog roboczy: $WORK"
echo "== architektura: $(dpkg --print-architecture), RAM: $(free -h | awk '/Mem/{print $2}')"

echo "== 1. osmium fileinfo"
osmium fileinfo -j "$PBF" | head -c 600; echo

echo "== 2. tilemaker -> vector.mbtiles"
/usr/bin/time -v tilemaker --input "$PBF" --output "$WORK/vector.mbtiles" 2> "$WORK/tilemaker.time" >/dev/null
grep -E "Elapsed|Maximum resident" "$WORK/tilemaker.time"
ls -lh "$WORK/vector.mbtiles"

echo "== 3. MapLibre Native (headless) - kandydaci"
echo "   a) mlnative (Python, Linux x64/arm64):"
echo "      python3 -m venv $WORK/venv && $WORK/venv/bin/pip install mlnative"
echo "   b) node + @maplibre/maplibre-gl-native (gotowe binarki pod Ubuntu):"
echo "      npm install @maplibre/maplibre-gl-native sharp   (uruchomienie przez xvfb-run -a)"
echo "   c) mbgl-render ze źródeł: https://maplibre.org/maplibre-native/docs/book/platforms/linux"
echo
echo "Zapisz wyniki (czas/RAM tilemakera, który wariant renderu działa, czas renderu"
echo "100 kafelków 128/256/512 px) w CLAUDE.md, sekcja 'Otwarte kwestie'."
