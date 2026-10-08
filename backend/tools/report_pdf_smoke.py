"""Inert final-image PDF capability/limit checks; never starts application services."""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import importlib.metadata
import json
import os
import resource
import struct
import sys
import zlib
from pathlib import Path
from time import monotonic
from typing import Any

from app.core.config import settings
from app.services import report_renderer
from app.services.report_contracts import (
    MAX_REPORT_IMAGE_BYTES, MAX_REPORT_IMAGE_PIXELS, REPORT_PIPELINE_SECONDS,
    ReportExportError, ReportWork,
)
from app.services.report_renderer import render_person_movement_report_pdf


def _png(width: int, height: int) -> bytes:
    """A real compressed canvas without allocating its decoded representation."""
    def chunk(kind: bytes, value: bytes) -> bytes:
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))
    compressor = zlib.compressobj()
    compressed: list[bytes] = []
    row = b"\0" + b"\x90" * width
    for _ in range(height):
        compressed.append(compressor.compress(row))
    compressed.append(compressor.flush())
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", b"".join(compressed)) + chunk(b"IEND", b""))


def _event(index: int, relative: str | None = None) -> dict[str, Any]:
    return {"id": str(index), "occurred_label": "08/10/2026 12:00", "type_label": "Denied",
            "tone": "red", "registration_number": f"SYN{index:08d}", "detail": "Synthetic fixture",
            "duration": {"label": "N/A", "tone": "muted"}, "source_label": "Synthetic LPR",
            "confidence_percent": 90, "_snapshot_path": relative, "_snapshot_content_type": "image/png"}


def snapshot(fixture: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "report_id": "000001", "subject_type": "person",
        "generated_label": "08/10/2026 12:00", "brand": {"site": "Synthetic IACS"},
        "person": {"id": "00000000-0000-0000-0000-000000000001",
                   "display_name": "Synthetic Runtime Check", "vehicles": []},
        "period": {"label": "Synthetic day", "start_label": "08/10/2026 00:00",
                   "end_label": "08/10/2026 23:59", "duration_label": "1 day", "timezone": "UTC"},
        "presence": {"state": "off_site"},
        "summary": {"arrivals": 0, "departures": 0, "total": 0, "denied": 0,
                    "first_event": "None", "last_event": "None"},
        "timeline": {"all": [], "selected": []}, "events": [],
        "options": {"include_snapshots": True, "include_confidence": True},
    }
    if fixture == "external":
        report["person"]["profile_photo_data_url"] = "https://inert.invalid/profile.png"
        return report
    if fixture == "max-rows":
        report["events"] = [_event(index) for index in range(10_000)]
        report["summary"].update(total=10_000, denied=10_000)
        report["options"]["include_snapshots"] = False
        return report
    root = settings.data_dir / "snapshots/access-events"
    root.mkdir(parents=True, exist_ok=True)
    if fixture == "max-images":
        # Two32Mi-pixel canvases total the approved64Mi-pixel source cap.
        # Pad valid PNG sources so complete URLs exactly total25MiB, proving
        # compressed representation and decoded canvas budgets together.
        raw = _png(4096, 8192)
        total_raw = (MAX_REPORT_IMAGE_BYTES - len("data:image/png;base64,") * 2) // 4 * 3
        first = (total_raw // 2) // 3 * 3
        sizes = [first, total_raw - first]
        for index, size in enumerate(sizes):
            path = root / f"synthetic-max-{index}.png"
            path.write_bytes(raw + b"\0" * (size - len(raw)))
            report["events"].append(_event(index, str(path.relative_to(settings.data_dir))))
        return report
    raw = _png(32, 32)
    report["person"]["profile_photo_data_url"] = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    path = root / "synthetic-runtime.png"
    path.write_bytes(_png(64, 48))
    report["events"] = [_event(1, str(path.relative_to(settings.data_dir)))]
    report["summary"].update(total=1, denied=1)
    return report


async def render(output: Path, fixture: str) -> dict[str, Any]:
    if os.getuid() == 0:
        raise RuntimeError("The final runtime smoke must use the image's non-root user")
    if {path.name for path in Path("/sys/class/net").iterdir()} != {"lo"}:
        raise RuntimeError("The final runtime smoke requires network isolation")
    if Path(sys.prefix) != Path("/app/.venv"):
        raise RuntimeError("The smoke must use the final image's own environment")
    source = snapshot(fixture)
    started = monotonic()
    try:
        size = await render_person_movement_report_pdf(source, output, work=ReportWork(started + REPORT_PIPELINE_SECONDS))
    except ReportExportError as exc:
        if fixture != "external" or exc.status_code != 400 or "upload a photo" not in str(exc):
            raise
        if output.exists():
            raise RuntimeError("Rejected external input wrote a PDF") from exc
        return {"fixture": fixture, "rejected_status": exc.status_code, "detail": str(exc), "seconds": monotonic()-started}
    if fixture == "external":
        raise RuntimeError("External image source was not rejected")
    if not output.read_bytes().startswith(b"%PDF-") or size < 1000:
        raise RuntimeError("Renderer did not produce a substantive PDF")
    template = Path(report_renderer.__file__).parent / "report_templates/person_movements.html"
    peak = Path("/sys/fs/cgroup/memory.peak")
    return {"fixture": fixture, "pdf_bytes": size, "pdf_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "uid": os.getuid(), "python": sys.executable, "prefix": sys.prefix,
            "playwright": importlib.metadata.version("playwright"), "seconds": monotonic()-started,
            "browser_path": os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
            "renderer_path": report_renderer.__file__, "template_path": str(template),
            "renderer_sha256": hashlib.sha256(Path(report_renderer.__file__).read_bytes()).hexdigest(),
            "template_sha256": hashlib.sha256(template.read_bytes()).hexdigest(),
            "lock_sha256": hashlib.sha256(Path("/app/uv.lock").read_bytes()).hexdigest(),
            "activity_rows": len(source["events"]),
            "expected_embedded_bytes": MAX_REPORT_IMAGE_BYTES if fixture == "max-images" else None,
            "expected_source_pixels": MAX_REPORT_IMAGE_PIXELS if fixture == "max-images" else None,
            "process_peak_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "children_peak_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
            "cgroup_peak_bytes": int(peak.read_text()) if peak.exists() else None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture", choices=("images", "max-images", "max-rows", "external"), default="images")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(render(args.output, args.fixture)), sort_keys=True))


if __name__ == "__main__":
    main()
