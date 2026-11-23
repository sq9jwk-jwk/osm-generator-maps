"""Modular import ("custom" profile): the user ticks map elements, we build the tilemaker setup.

``tilemaker/profiles/custom/process.lua`` is a template that reads a global ``MODULES`` table;
``config.json`` lists every layer. For an import we prepend ``MODULES = {...}`` to the Lua file
and drop the layers no enabled module produces (tilemaker rejects layers the Lua writes but
config.json lacks, so the two must always match).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CUSTOM_PROFILE = "custom"
# Layers produced regardless of the chosen modules.
BASE_LAYERS = ("transportation", "water", "waterway", "landcover")


@dataclass(frozen=True)
class Module:
    id: str
    name: str
    description: str
    layers: tuple[str, ...]
    default: bool = False


MODULES: tuple[Module, ...] = (
    Module("places", "Miejscowości", "Nazwy miast, wsi, dzielnic i wysp.", ("place",), True),
    Module("road_names", "Nazwy i numery dróg", "Etykiety ulic i numery dróg.", ("transportation_name",), True),
    Module("buildings", "Budynki", "Obrysy budynków (od z13).", ("building",), True),
    Module("landuse", "Użytkowanie terenu", "Zabudowa, przemysł, cmentarze, szkoły, boiska, lotniska.", ("landuse",), True),
    Module("parks", "Parki i obszary chronione", "Parki, ogrody, rezerwaty, parki narodowe.", ("park",), True),
    Module("water_names", "Nazwy jezior", "Etykiety zbiorników wodnych.", ("water_name",), True),
    Module("boundaries", "Granice administracyjne", "Państwa, województwa, powiaty, gminy.", ("boundary",), True),
    Module("rail", "Kolej, tramwaje, promy", "Linie kolejowe, tramwaj, metro, promy.", ()),
    Module("airports", "Pasy startowe", "Pasy startowe i drogi kołowania.", ()),
    Module("poi", "Punkty użyteczności (POI)", "Stacje, szpitale, sklepy, gastronomia, szkoły, hotele.", ("poi",)),
    Module("housenumbers", "Numery domów", "Numery adresowe (z14). Zwiększają import.", ("housenumber",)),
    Module("peaks", "Szczyty", "Szczyty z nazwą i wysokością.", ("mountain_peak",)),
    Module("tourist_poi", "POI turystyczne", "Schroniska, punkty widokowe, źródła, pola namiotowe, zamki, jaskinie.", ("poi",)),
    Module("hiking", "Szlaki piesze i rowerowe", "Kolorowe szlaki i atrybuty ścieżek. Duże dane.", ("hiking",)),
)
_BY_ID = {m.id: m for m in MODULES}
DEFAULT_MODULES = tuple(m.id for m in MODULES if m.default)


def normalize(modules: list[str] | None) -> list[str]:
    """Validate ids; returns them in catalogue order without duplicates."""
    if modules is None:
        return list(DEFAULT_MODULES)
    unknown = sorted(set(modules) - set(_BY_ID))
    if unknown:
        raise ValueError(f"Nieznane moduły: {', '.join(unknown)}")
    return [m.id for m in MODULES if m.id in set(modules)]


def layers_for(modules: list[str]) -> list[str]:
    layers = list(BASE_LAYERS)
    for mid in modules:
        for layer in _BY_ID[mid].layers:
            if layer not in layers:
                layers.append(layer)
    return layers


def lua_header(modules: list[str]) -> str:
    flags = ", ".join(f"{m} = true" for m in modules)
    return f"MODULES = {{ {flags} }}\n"


def build(base_config: Path, base_process: Path, modules: list[str], work_dir: Path) -> tuple[Path, Path]:
    """Write the generated config and Lua into ``work_dir``; returns (config, process)."""
    modules = normalize(modules)
    cfg = json.loads(base_config.read_text(encoding="utf-8"))
    keep = set(layers_for(modules))
    cfg["layers"] = {k: v for k, v in cfg["layers"].items() if k in keep}
    missing = keep - set(cfg["layers"])
    if missing:
        raise ValueError(f"Brak warstw w config.json: {sorted(missing)}")
    config_out = work_dir / "custom.tmp.config.json"
    process_out = work_dir / "custom.tmp.process.lua"
    config_out.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    process_out.write_text(lua_header(modules) + base_process.read_text(encoding="utf-8"), encoding="utf-8")
    return config_out, process_out
