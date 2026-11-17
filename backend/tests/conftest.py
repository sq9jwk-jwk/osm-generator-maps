import os
import tempfile
from pathlib import Path

# Isolated data dir for the whole test session; must be set before app import.
_tmp = Path(tempfile.mkdtemp(prefix="osmtile-test-"))
_cfg = _tmp / "config.toml"
_cfg.write_text(f'[paths]\ndata_dir = "{(_tmp / "data").as_posix()}"\n[render]\nengine = "debug"\n')
os.environ["OSMTILE_CONFIG"] = str(_cfg)
