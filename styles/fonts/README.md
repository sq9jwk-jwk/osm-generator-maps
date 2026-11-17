# Fonts (glyph PBFs for MapLibre)

Noto Sans (Regular, Bold, Italic) rendered to signed-distance-field glyph ranges
for MapLibre (`{fontstack}/{range}.pbf`). Served locally, so no network is needed.

- Source of the PBFs: the MapLibre demo tiles font set (https://demotiles.maplibre.org/font/),
  built from Google's Noto Sans.
- License: SIL Open Font License 1.1 (https://openfontlicense.org). The OFL allows
  bundling and redistribution, provided the fonts are not sold on their own.
- Included ranges cover Polish and other Latin-extended letters, Cyrillic and common punctuation:
  `0-255`, `256-511`, `512-767`, `1024-1279`, `7680-7935`, `8192-8447`.
  A character outside these ranges is simply not drawn.
