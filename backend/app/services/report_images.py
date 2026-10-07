"""Owned raster preparation with actual source and embedded representation budgets."""
from __future__ import annotations

import base64
import binascii
import copy
import re
import warnings
from io import BytesIO
from typing import Any
from urllib.parse import unquote_to_bytes

from PIL import Image, ImageOps, UnidentifiedImageError

from app.services.report_contracts import (
    MAX_REPORT_IMAGE_BYTES,
    MAX_REPORT_IMAGE_PIXELS,
    ReportExportError,
    ReportLimitError,
    ReportWork,
)
from app.services.snapshots import get_snapshot_manager

_DATA_URL = re.compile(r"^data:(?P<mime>[^;,]+)(?P<base64>;base64)?,(?P<data>.*)$", re.DOTALL)
_BROWSER_RASTER_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif"}
PDF_SAFE_PROFILE_IMAGE_MAX_EDGE_PX = 640


class ReportImageBudget:
    """The 25 MiB final cap counts complete UTF-8 data URLs, including base64."""
    def __init__(self) -> None:
        self.source_bytes = 0
        self.embedded_bytes = 0
        self.pixels = 0

    @property
    def source_remaining(self) -> int:
        return MAX_REPORT_IMAGE_BYTES - self.source_bytes

    def embed(self, raw: bytes, work: ReportWork) -> str:
        work.check()
        if len(raw) > self.source_remaining:
            raise ReportLimitError("Report image sources exceed 25 MiB. Use smaller images or a shorter period.")
        self.source_bytes += len(raw)
        raster, mime = _verified_raster(raw, work, self)
        prefix = f"data:{mime};base64,"
        encoded_size = len(prefix.encode("utf-8")) + 4 * ((len(raster) + 2) // 3)
        if self.embedded_bytes + encoded_size > MAX_REPORT_IMAGE_BYTES:
            raise ReportLimitError("Embedded report images exceed 25 MiB. Use smaller images or a shorter period.")
        work.check()
        result = prefix + base64.b64encode(raster).decode("ascii")
        self.embedded_bytes += len(result.encode("utf-8"))
        return result


def _verified_raster(raw: bytes, work: ReportWork, budget: ReportImageBudget) -> tuple[bytes, str]:
    # The opener inspects actual content; a claimed PNG MIME never admits SVG.
    import pillow_heif

    pillow_heif.register_heif_opener()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as image:
                raster_format = image.format
                if raster_format is None or raster_format in {"EPS", "WMF"}:
                    raise ReportExportError("Report images must contain a supported raster image.")
                # Charge the actual source canvas for every occurrence before
                # seek/load/conversion can decode it. Compressed bytes do not
                # bound decoded canvases, and converted thumbnails are not free.
                pixels = image.width * image.height
                if budget.pixels + pixels > MAX_REPORT_IMAGE_PIXELS:
                    raise ReportLimitError("Report images exceed 64 Mi-pixels in total. Use smaller static images or a shorter period.")
                budget.pixels += pixels
                work.check()
                try:
                    image.seek(1)
                except EOFError:
                    pass
                else:
                    raise ReportExportError("PDF reports require static, single-frame images. Upload a static image before exporting.")
                work.check()
            with Image.open(BytesIO(raw)) as image:
                image.verify()
            work.check()
            if raster_format not in _BROWSER_RASTER_MIME:
                with Image.open(BytesIO(raw)) as image:
                    converted = ImageOps.exif_transpose(image)
                    converted.thumbnail((PDF_SAFE_PROFILE_IMAGE_MAX_EDGE_PX, PDF_SAFE_PROFILE_IMAGE_MAX_EDGE_PX), Image.Resampling.LANCZOS)
                    output = BytesIO()
                    if converted.mode in {"RGBA", "LA"}:
                        converted.save(output, format="PNG", optimize=True)
                        mime = "image/png"
                    else:
                        converted.convert("RGB").save(output, format="JPEG", quality=88, optimize=True)
                        mime = "image/jpeg"
                    work.check()
                    return output.getvalue(), mime
            return raw, _BROWSER_RASTER_MIME[raster_format]
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ReportLimitError("Report image canvas exceeds the supported decoded-pixel limit. Use a smaller static image.") from exc
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ReportExportError("Report image could not be processed as a safe raster image.") from exc


def _profile_image(value: str, budget: ReportImageBudget, work: ReportWork) -> str:
    match = _DATA_URL.fullmatch(value)
    if match is None:
        raise ReportExportError("External profile images cannot be embedded; upload a photo before exporting.")
    # Source query has already bounded this string. Keep direct renderer callers
    # bounded too, before either percent/base64 decoding allocates another copy.
    if len(value.encode("utf-8")) > MAX_REPORT_IMAGE_BYTES:
        raise ReportLimitError("Report profile image source exceeds 25 MiB. Use a smaller profile photo.")
    try:
        raw = (base64.b64decode(match.group("data"), validate=True) if match.group("base64")
               else unquote_to_bytes(match.group("data")))
    except (binascii.Error, ValueError) as exc:
        raise ReportExportError("Report profile image contains invalid image data.") from exc
    return budget.embed(raw, work)


def _snapshot_image(event: dict[str, Any], budget: ReportImageBudget, work: ReportWork) -> str | None:
    relative = event.get("_snapshot_path")
    if not relative:
        return None
    try:
        path = get_snapshot_manager().resolve_path(str(relative))
        chunks: list[bytes] = []
        size = 0
        with path.open("rb") as source:
            while True:
                work.check()
                # Read at most remaining+1 actual bytes; stat/recorded size are
                # never authoritative, including if the file grows while open.
                chunk = source.read(min(64 * 1024, budget.source_remaining - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > budget.source_remaining:
                    raise ReportLimitError("Report image sources exceed 25 MiB. Choose a shorter period.")
                chunks.append(chunk)
        return budget.embed(b"".join(chunks), work)
    except (FileNotFoundError, OSError):
        # Preserve unavailable owned-snapshot behavior; invalid/oversize existing
        # sources reject rather than silently truncate or omit configured media.
        return None


def prepare_report_images(snapshot: dict[str, Any], work: ReportWork) -> dict[str, Any]:
    work.check()
    report = copy.deepcopy(snapshot)
    budget = ReportImageBudget()
    person = report.get("person")
    if isinstance(person, dict):
        value = person.get("profile_photo_data_url")
        person["pdf_profile_photo_data_url"] = _profile_image(str(value), budget, work) if value else None
    for event in report.get("events", []):
        if isinstance(event, dict):
            event["pdf_snapshot_data_url"] = _snapshot_image(event, budget, work)
    work.check()
    return report
