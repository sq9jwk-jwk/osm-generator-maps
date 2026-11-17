"""Configuration loaded from TOML.

Lookup order:
1. path in the ``OSMTILE_CONFIG`` environment variable,
2. ``/etc/osm-tilegen/config.toml`` (installed package),
3. development defaults (data in ``<repo>/var``).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SYSTEM_CONFIG = Path("/etc/osm-tilegen/config.toml")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    styles_dir: Path
    frontend_dist: Path
    host: str = "0.0.0.0"
    port: int = 8080
    render_threads: int = 0  # 0 = number of CPU cores
    render_engine: str = "debug"  # "debug" | "maplibre"
    tilemaker_bin: str = "tilemaker"
    osmium_bin: str = "osmium"
    tilemaker_config: Path | None = None
    tilemaker_process: Path | None = None
    tilemaker_store: bool = False  # True on low-RAM machines (RPi)
    water_polygons: Path | None = None
    coverage_zoom: int = 10
    max_upload_bytes: int = 200 * 1024**3

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def maps_dir(self) -> Path:
        return self.data_dir / "maps"

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "output"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db.sqlite"

    def ensure_dirs(self) -> None:
        for d in (self.uploads_dir, self.maps_dir, self.output_dir):
            d.mkdir(parents=True, exist_ok=True)


def _opt_path(value: str | None) -> Path | None:
    return Path(value) if value else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    env = os.environ.get("OSMTILE_CONFIG")
    path = Path(env) if env else (SYSTEM_CONFIG if SYSTEM_CONFIG.exists() else None)
    raw: dict = {}
    if path is not None:
        with path.open("rb") as f:
            raw = tomllib.load(f)

    paths = raw.get("paths", {})
    server = raw.get("server", {})
    render = raw.get("render", {})
    importer = raw.get("import", {})

    return Settings(
        data_dir=Path(paths.get("data_dir", REPO_ROOT / "var")),
        styles_dir=Path(paths.get("styles_dir", REPO_ROOT / "styles")),
        frontend_dist=Path(paths.get("frontend_dist", REPO_ROOT / "frontend" / "dist")),
        host=server.get("host", "0.0.0.0"),
        port=int(server.get("port", 8080)),
        render_threads=int(render.get("threads", 0)),
        render_engine=render.get("engine", "debug"),
        tilemaker_bin=importer.get("tilemaker_bin", "tilemaker"),
        osmium_bin=importer.get("osmium_bin", "osmium"),
        tilemaker_config=_opt_path(importer.get("tilemaker_config")),
        tilemaker_process=_opt_path(importer.get("tilemaker_process")),
        tilemaker_store=bool(importer.get("store_on_disk", False)),
        water_polygons=_opt_path(importer.get("water_polygons")),
        coverage_zoom=int(importer.get("coverage_zoom", 10)),
    )
