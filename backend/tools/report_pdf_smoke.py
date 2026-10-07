"""Inert final-image PDF capability check; never starts the application."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
from typing import Any
from zoneinfo import ZoneInfo

from app.services import reports


def snapshot() -> dict[str, Any]:
    return {
        "report_id": "000001", "subject_type": "person",
        "generated_label": "07/10/2026 12:00", "brand": {"site": "Synthetic IACS"},
        "person": {"id": "00000000-0000-0000-0000-000000000001",
                   "display_name": "Synthetic Runtime Check", "vehicles": []},
        "period": {"label": "Synthetic day", "start_label": "07/10/2026 00:00",
                   "end_label": "07/10/2026 23:59", "duration_label": "1 day", "timezone": "UTC"},
        "presence": {"state": "off_site"},
        "summary": {"arrivals": 0, "departures": 0, "total": 0, "denied": 0,
                    "first_event": "None", "last_event": "None"},
        "timeline": {"all": [], "selected": []}, "events": [],
        "options": {"include_snapshots": False, "include_confidence": False},
    }


async def render(output: Path) -> dict[str, Any]:
    if os.getuid() == 0:
        raise RuntimeError("The final runtime smoke must use the image's non-root user")
    if {path.name for path in Path("/sys/class/net").iterdir()} != {"lo"}:
        raise RuntimeError("The final runtime smoke requires network isolation")
    if Path(sys.prefix) != Path("/app/.venv"):
        raise RuntimeError("The smoke must use the final image's own environment")
    size = await reports.render_person_movement_report_pdf(snapshot(), output, timezone=ZoneInfo("UTC"))
    if not output.read_bytes().startswith(b"%PDF-") or size < 1000:
        raise RuntimeError("Renderer did not produce a substantive PDF")
    template = Path(reports.__file__).parent / "report_templates/person_movements.html"
    return {"pdf_bytes": size, "pdf_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "uid": os.getuid(), "python": sys.executable, "prefix": sys.prefix,
            "playwright": importlib.metadata.version("playwright"),
            "browser_path": os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
            "renderer_path": reports.__file__, "template_path": str(template),
            "renderer_sha256": hashlib.sha256(Path(reports.__file__).read_bytes()).hexdigest(),
            "template_sha256": hashlib.sha256(template.read_bytes()).hexdigest(),
            "lock_sha256": hashlib.sha256(Path("/app/uv.lock").read_bytes()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(render(args.output)), sort_keys=True))


if __name__ == "__main__":
    main()
