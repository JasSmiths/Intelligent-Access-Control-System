"""Actual image bytes, closed owned files and no uncontrolled image source."""
import base64
from io import BytesIO
from time import monotonic
from types import SimpleNamespace

import pytest
from PIL import Image

from app.services import report_images as images
from app.services.report_contracts import (
    MAX_REPORT_IMAGE_BYTES, ReportExportError, ReportLimitError,
    ReportStoppedError, ReportTimeoutError, ReportWork,
)


def png_bytes():
    output = BytesIO()
    Image.new("RGB", (2, 2), "#8ab4f8").save(output, format="PNG")
    return output.getvalue()


def work():
    return ReportWork(monotonic() + 120)


def snapshot(profile=None, paths=()):
    return {"person": {"profile_photo_data_url": profile},
            "events": [{"_snapshot_path": path, "snapshot_bytes": 1} for path in paths]}


@pytest.mark.parametrize("value", ["https://inert.invalid/photo", "http://inert.invalid/photo", "/photo.png", "blob:inert", "file:///photo.png"])
def test_external_profile_sources_are_explicitly_rejected(value):
    with pytest.raises(ReportExportError, match="upload a photo"):
        images.prepare_report_images(snapshot(value), work())


def test_real_raster_content_overrides_claimed_mime_and_svg_cannot_bypass():
    raw = png_bytes()
    value = "data:image/svg+xml;base64," + base64.b64encode(raw).decode("ascii")
    report = images.prepare_report_images(snapshot(value), work())
    assert report["person"]["pdf_profile_photo_data_url"] == "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://inert.invalid/image"/></svg>'
    with pytest.raises(ReportExportError, match="raster"):
        images.prepare_report_images(snapshot("data:image/png;base64," + base64.b64encode(svg).decode("ascii")), work())


def test_embedded_limit_includes_all_url_prefixes_and_base64_at_real_25_mib_boundary(monkeypatch, tmp_path):
    raw = png_bytes()
    prefix = "data:image/png;base64,"
    total_raw = (MAX_REPORT_IMAGE_BYTES - len(prefix) * 2) // 4 * 3
    first_size = (total_raw // 2) // 3 * 3
    second_size = total_raw - first_size
    paths = [tmp_path / "first.png", tmp_path / "second.png"]
    paths[0].write_bytes(raw + b"\0" * (first_size - len(raw)))
    paths[1].write_bytes(raw + b"\0" * (second_size - len(raw)))
    monkeypatch.setattr(images, "get_snapshot_manager", lambda: SimpleNamespace(resolve_path=lambda value: tmp_path / value))
    original = snapshot(paths=[path.name for path in paths])
    report = images.prepare_report_images(original, work())
    assert sum(len(event["pdf_snapshot_data_url"].encode("utf-8")) for event in report["events"]) == MAX_REPORT_IMAGE_BYTES
    assert all("pdf_snapshot_data_url" not in event for event in original["events"])
    with paths[1].open("ab") as source:
        source.write(b"\0")
    with pytest.raises(ReportLimitError, match="Embedded report images"):
        images.prepare_report_images(original, work())


def test_profile_and_snapshot_share_one_embedded_budget(monkeypatch, tmp_path):
    raw = png_bytes()
    value = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    path = tmp_path / "snapshot.png"
    path.write_bytes(raw)
    monkeypatch.setattr(images, "get_snapshot_manager", lambda: SimpleNamespace(resolve_path=lambda _value: path))
    monkeypatch.setattr(images, "MAX_REPORT_IMAGE_BYTES", len(value) * 2 - 1)
    with pytest.raises(ReportLimitError, match="Embedded report images"):
        images.prepare_report_images(snapshot(value, [path.name]), work())


def test_actual_file_growth_is_bounded_and_file_is_closed(monkeypatch, tmp_path):
    path = tmp_path / "growing.png"
    raw = png_bytes()
    path.write_bytes(raw + b"\0" * (64 * 1024 - len(raw)))
    handles = []
    class GrowingPath:
        def open(self, _mode):
            source = path.open("rb")
            handles.append(source)
            class Reader:
                grew = False
                def __enter__(self):
                    return self
                def __exit__(self, *_args):
                    source.close()
                def read(self, size):
                    result = source.read(size)
                    if not self.grew:
                        self.grew = True
                        with path.open("ab") as target:
                            target.write(b"\0" * (100 * 1024))
                    return result
            return Reader()
    monkeypatch.setattr(images, "MAX_REPORT_IMAGE_BYTES", 128 * 1024)
    monkeypatch.setattr(images, "get_snapshot_manager", lambda: SimpleNamespace(resolve_path=lambda _value: GrowingPath()))
    with pytest.raises(ReportLimitError, match="image sources"):
        images.prepare_report_images(snapshot(paths=["growing.png"]), work())
    assert handles and all(handle.closed for handle in handles)


def test_missing_owned_snapshot_retains_existing_omission(monkeypatch, tmp_path):
    monkeypatch.setattr(images, "get_snapshot_manager", lambda: SimpleNamespace(resolve_path=lambda _value: tmp_path / "missing.png"))
    report = images.prepare_report_images(snapshot(paths=["missing.png"]), work())
    assert report["events"][0]["pdf_snapshot_data_url"] is None


def test_native_raster_dimension_guard_rejects_before_large_decode(monkeypatch):
    output = BytesIO()
    Image.new("RGB", (3, 3)).save(output, format="PNG")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 4)
    with pytest.raises(ReportLimitError, match="decoded-pixel limit") as error:
        images.prepare_report_images(snapshot("data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")), work())
    assert error.value.status_code == 413


def test_stopped_or_expired_work_never_opens_an_image(monkeypatch):
    def forbidden():
        raise AssertionError("stopped image work opened a resource")
    monkeypatch.setattr(images, "get_snapshot_manager", forbidden)
    stopped = work()
    stopped.request_stop()
    with pytest.raises(ReportStoppedError):
        images.prepare_report_images(snapshot(paths=["unused.png"]), stopped)
    with pytest.raises(ReportTimeoutError):
        images.prepare_report_images(snapshot(paths=["unused.png"]), ReportWork(monotonic() - 1))


def test_deadline_sample_cannot_turn_expiry_into_native_timeout_zero(monkeypatch):
    from app.services import report_contracts
    samples = iter([0.9999999, 1.0])
    monkeypatch.setattr(report_contracts, "monotonic", lambda: next(samples))
    budget = ReportWork(1.0)
    assert budget.remaining_milliseconds() == 1
    with pytest.raises(ReportTimeoutError):
        budget.remaining()


def canvas_png(width, height):
    """Valid highly compressible PNG without allocating its decoded canvas."""
    import struct
    import zlib
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    compressor = zlib.compressobj()
    compressed = []
    scanline = b"\0" * (width + 1)  # 8-bit greyscale, filter byte included.
    for _ in range(height):
        compressed.append(compressor.compress(scanline))
    compressed.append(compressor.flush())
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", b"".join(compressed)) + chunk(b"IEND", b""))


def test_aggregate_actual_pixels_exact_limit_and_one_more_occurrence(monkeypatch, tmp_path):
    raw = canvas_png(8192, 8192)
    assert len(raw) < 100_000  # The compressed cap alone cannot bound decoding.
    (tmp_path / "large.png").write_bytes(raw)
    (tmp_path / "one.png").write_bytes(canvas_png(1, 1))
    monkeypatch.setattr(images, "get_snapshot_manager", lambda: SimpleNamespace(resolve_path=lambda value: tmp_path / value))
    report = images.prepare_report_images(snapshot(paths=["large.png"]), work())
    assert report["events"][0]["pdf_snapshot_data_url"]
    with pytest.raises(ReportLimitError, match="64 Mi-pixels"):
        images.prepare_report_images(snapshot(paths=["large.png", "one.png"]), work())


def test_pixel_guard_precedes_frame_probe(monkeypatch):
    raw = canvas_png(8192, 8193)
    from PIL import PngImagePlugin
    original = PngImagePlugin.PngImageFile.seek
    def forbidden(self, frame):
        raise AssertionError("oversized source reached a frame probe")
    monkeypatch.setattr(PngImagePlugin.PngImageFile, "seek", forbidden)
    with pytest.raises(ReportLimitError, match="64 Mi-pixels"):
        images.prepare_report_images(snapshot("data:image/png;base64," + base64.b64encode(raw).decode("ascii")), work())
    monkeypatch.setattr(PngImagePlugin.PngImageFile, "seek", original)


@pytest.mark.parametrize("format", ["GIF", "WEBP", "PNG", "TIFF"])
def test_multiframe_sources_require_a_static_image(format):
    output = BytesIO()
    first = Image.new("RGB", (2, 2), "red")
    second = Image.new("RGB", (2, 2), "blue")
    first.save(output, format=format, save_all=True, append_images=[second], duration=100, loop=0)
    value = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    with pytest.raises(ReportExportError, match="static, single-frame"):
        images.prepare_report_images(snapshot(value), work())
