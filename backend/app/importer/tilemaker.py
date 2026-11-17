"""Wrappers around `tilemaker` and `osmium extract` (Debian packages)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from ..core.config import Settings


def build_command(
    settings: Settings,
    pbf: Path,
    out: Path,
    store_dir: Path,
    config: Path | None = None,
    process: Path | None = None,
) -> list[str]:
    config = config or settings.tilemaker_config
    process = process or settings.tilemaker_process
    cmd = [settings.tilemaker_bin, "--input", str(pbf), "--output", str(out)]
    if config:
        cmd += ["--config", str(config)]
    if process:
        cmd += ["--process", str(process)]
    if settings.tilemaker_store:
        store_dir.mkdir(parents=True, exist_ok=True)
        cmd += ["--store", str(store_dir)]
    return cmd


def _stream(
    cmd: list[str],
    cwd: Path,
    log: Callable[[str], None],
    should_cancel: Callable[[], bool],
    what: str,
) -> None:
    log("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    assert proc.stdout is not None
    tail: list[str] = []
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            log(line)
            tail = (tail + [line])[-5:]
        if should_cancel():
            proc.terminate()
            proc.wait(timeout=30)
            raise InterruptedError("cancelled")
    if proc.wait() != 0:
        raise RuntimeError(f"{what} exited with code {proc.returncode}: " + " | ".join(tail))


def run_tilemaker(
    settings: Settings,
    pbf: Path,
    out: Path,
    work_dir: Path,
    log: Callable[[str], None],
    should_cancel: Callable[[], bool],
    config: Path | None = None,
    process: Path | None = None,
) -> None:
    """Run tilemaker, streaming its output to ``log``. Raises on failure."""
    cmd = build_command(settings, pbf, out, work_dir / "store", config, process)
    # tilemaker resolves shapefile paths (water polygons) relative to its cwd.
    _stream(cmd, work_dir, log, should_cancel, "tilemaker")


def extract_region(
    settings: Settings,
    src: Path,
    dst: Path,
    bbox: tuple[float, float, float, float],
    work_dir: Path,
    log: Callable[[str], None],
    should_cancel: Callable[[], bool],
) -> None:
    """Cut ``bbox`` (W,S,E,N) out of ``src``. ``smart`` keeps multipolygon and
    route relations complete at the edge of the area."""
    w, s, e, n = bbox
    cmd = [
        settings.osmium_bin, "extract",
        "--bbox", f"{w},{s},{e},{n}",
        "--strategy", "smart",
        "--overwrite",
        "-o", str(dst),
        str(src),
    ]
    _stream(cmd, work_dir, log, should_cancel, "osmium extract")
