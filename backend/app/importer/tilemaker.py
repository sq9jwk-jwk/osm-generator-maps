"""Wrapper around the `tilemaker` binary (Debian package `tilemaker`)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from ..core.config import Settings


def build_command(settings: Settings, pbf: Path, out: Path, store_dir: Path) -> list[str]:
    cmd = [settings.tilemaker_bin, "--input", str(pbf), "--output", str(out)]
    if settings.tilemaker_config:
        cmd += ["--config", str(settings.tilemaker_config)]
    if settings.tilemaker_process:
        cmd += ["--process", str(settings.tilemaker_process)]
    if settings.tilemaker_store:
        store_dir.mkdir(parents=True, exist_ok=True)
        cmd += ["--store", str(store_dir)]
    return cmd


def run_tilemaker(
    settings: Settings,
    pbf: Path,
    out: Path,
    work_dir: Path,
    log: Callable[[str], None],
    should_cancel: Callable[[], bool],
) -> None:
    """Run tilemaker, streaming its output to ``log``. Raises on failure."""
    cmd = build_command(settings, pbf, out, work_dir / "store")
    log("$ " + " ".join(cmd))
    # tilemaker resolves shapefile paths (water polygons) relative to its cwd.
    proc = subprocess.Popen(
        cmd, cwd=work_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
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
        raise RuntimeError(
            f"tilemaker exited with code {proc.returncode}: " + " | ".join(tail)
        )
