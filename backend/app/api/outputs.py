"""Generated outputs: list, streaming ZIP download, delete."""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..core.config import get_settings

router = APIRouter(prefix="/api/outputs", tags=["outputs"])


def _dir(job_id: str) -> Path:
    if not job_id.isalnum():
        raise HTTPException(400, "Zły identyfikator")
    d = get_settings().output_dir / job_id
    if not d.is_dir():
        raise HTTPException(404, "Nie ma takiego wyniku")
    return d


@router.get("")
def list_outputs() -> list[dict[str, Any]]:
    out = []
    root = get_settings().output_dir
    if not root.exists():
        return out
    for d in root.iterdir():
        meta_file = d / "metadata.json"
        if meta_file.exists():
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            meta["size_bytes"] = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            out.append(meta)
    return sorted(out, key=lambda m: m.get("created_at", ""), reverse=True)


class _Sink(io.RawIOBase):
    """Non-seekable sink; zipfile then writes data descriptors (streamable)."""

    def __init__(self) -> None:
        self.buf = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, b: Any) -> int:  # type: ignore[override]
        self.buf += b
        return len(b)


def zip_stream(root: Path) -> Iterator[bytes]:
    sink = _Sink()
    # Tiles are already compressed images, so STORED is fast and just as small.
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED) as zf:  # type: ignore[arg-type]
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.name.endswith(".part"):
                zf.write(path, path.relative_to(root).as_posix())
                if sink.buf:
                    yield bytes(sink.buf)
                    sink.buf.clear()
    if sink.buf:
        yield bytes(sink.buf)


@router.get("/{job_id}/zip")
def download_zip(job_id: str) -> StreamingResponse:
    d = _dir(job_id)
    return StreamingResponse(
        zip_stream(d),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="tiles-{job_id}.zip"'},
    )


@router.delete("/{job_id}")
def delete_output(job_id: str) -> dict[str, bool]:
    shutil.rmtree(_dir(job_id))
    return {"ok": True}
