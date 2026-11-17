# Styles

Each directory with a `style.json` is a map view offered in the UI. The app injects local
tile, glyph and sprite URLs when serving a style, so the files contain no external addresses.

| Directory | Origin | License |
|---|---|---|
| `basic`, `classic`, `tourist` | own (generated from `basic`) | project license |
| `bright` | [OSM Bright for OpenMapTiles](https://github.com/openmaptiles/osm-bright-gl-style) | BSD-3 (code), CC-BY 4.0 (design) |
| `positron` | [Positron](https://github.com/openmaptiles/positron-gl-style) | BSD-3 (code), CC-BY 3.0/4.0 (design) |
| `darkmatter` | [Dark Matter](https://github.com/openmaptiles/dark-matter-gl-style) | BSD-3 (code), CC-BY 3.0/4.0 (design) |
| `liberty` | [OSM Liberty](https://github.com/maputnik/osm-liberty) | BSD (code), CC-BY 4.0 (design), icons per its LICENSE |

Third-party styles keep their `LICENSE.md` next to them and the attribution
"© OpenMapTiles © OpenStreetMap contributors" is shown on the map. Do not remove either.

Modifications made to the third-party styles: external sources, glyph and sprite URLs removed
(injected by the app), the raster shaded-relief layer of Liberty removed (needs the network),
and fonts replaced by the bundled Noto Sans (Metropolis and Roboto are not shipped):

| Original | Replacement |
|---|---|
| Metropolis Light / Regular, Roboto Regular | Noto Sans Regular |
| Metropolis Light Italic / Medium Italic, Roboto Condensed Italic | Noto Sans Italic |
| Roboto Medium | Noto Sans Bold |

Styles list the import profiles they need in `metadata["osm-tilegen"].profiles` (empty = any).
`bright`, `positron`, `darkmatter` and `liberty` need the `full` profile (complete OpenMapTiles schema).
