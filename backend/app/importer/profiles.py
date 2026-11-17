"""Import profiles: a profile is a directory with tilemaker config + Lua script.

``tilemaker/profiles/<id>/{profile.json,config.json,process.lua}``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..core.config import Settings

DEFAULT_PROFILE = "classic"
_ID_RE = re.compile(r"^[a-z0-9_-]+$")


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    description: str
    config: Path
    process: Path


def list_profiles(settings: Settings) -> list[Profile]:
    root = settings.profiles_dir
    if root is None or not root.is_dir():
        return []
    out: list[Profile] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        config, process = d / "config.json", d / "process.lua"
        if not (config.is_file() and process.is_file()):
            continue
        meta: dict[str, str] = {}
        if (d / "profile.json").is_file():
            try:
                meta = json.loads((d / "profile.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
        out.append(Profile(d.name, meta.get("name", d.name), meta.get("description", ""), config, process))
    # classic first, then alphabetical
    return sorted(out, key=lambda p: (p.id != DEFAULT_PROFILE, p.id))


def get_profile(settings: Settings, profile_id: str) -> Profile:
    if not _ID_RE.match(profile_id):
        raise ValueError(f"Zła nazwa profilu: {profile_id!r}")
    for p in list_profiles(settings):
        if p.id == profile_id:
            return p
    raise ValueError(f"Nieznany profil: {profile_id}")
