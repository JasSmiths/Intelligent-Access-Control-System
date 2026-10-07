"""Detached report rendering; the actual child retains every native resource."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from app.core.task_lifecycle import drain_owned
from app.services.report_contracts import ReportTimeoutError, ReportWork
from app.services.report_images import prepare_report_images

_TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent / "report_templates"),
    autoescape=select_autoescape(("html", "xml")), trim_blocks=True, lstrip_blocks=True,
)


def report_for_pdf(snapshot: dict[str, Any], work: ReportWork) -> dict[str, Any]:
    report = prepare_report_images(snapshot, work)
    brand = report.setdefault("brand", {})
    if isinstance(brand, dict):
        brand.setdefault("site", "Crest House")
        brand.setdefault("address", ["157 Rugeley Road,", "Burntwood,", "WS7 9HA"])
    return report


def _render_template(report: dict[str, Any], work: ReportWork) -> str:
    work.check()
    html: str = _TEMPLATES.get_template("person_movements.html").render(report=report)
    work.check()
    return html


def _write_pdf(output_path: Path, pdf: bytes, work: ReportWork) -> int:
    work.check()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    size = output_path.write_bytes(pdf)
    work.check()
    return size


async def render_person_movement_report_pdf(
    snapshot: dict[str, Any], output_path: Path, *, work: ReportWork,
) -> int:
    work.check()
    # The pipeline child is shielded. Retain actual threads even if its caller
    # disappears; cooperative checks prevent their output starting a later stage.
    report = await drain_owned(asyncio.to_thread(report_for_pdf, snapshot, work))
    work.check()
    html: str = await drain_owned(asyncio.to_thread(_render_template, report, work))
    work.check()
    footer = (
        '<div style="width:100%;font-family:Inter,Arial,sans-serif;font-size:8px;'
        'color:#6b7890;padding:0 14mm;display:flex;justify-content:space-between;">'
        '<span>Crest House</span><span>Report '
        f"{snapshot.get('report_id', '')}"
        ' &middot; Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>'
    )
    # start/new_page/route/emulate_media have no native deadline argument. Own
    # each acquisition/result through cleanup; a deadline during acquisition may
    # overrun while it drains, but can never release capacity or start more work.
    driver_task = asyncio.create_task(async_playwright().start())
    playwright = await drain_owned(driver_task)
    try:
        work.check()
        browser_task = asyncio.create_task(playwright.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage"], timeout=work.remaining_milliseconds(),
        ))
        browser = await drain_owned(browser_task)
        try:
            work.check()
            page = await browser.new_page(viewport={"width": 1240, "height": 1754}, device_scale_factor=1)
            work.check()
            # All assets are embedded or inline. Even malformed/template inputs
            # cannot turn Chromium into an unbudgeted external image fetcher.
            await page.route("**/*", lambda route: route.abort())
            work.check()
            await page.set_content(html, wait_until="networkidle", timeout=work.remaining_milliseconds())
            work.check()
            await page.emulate_media(media="print")
            work.check()
            # This locked Playwright API has no PDF timeout option. Cancel its
            # protocol wait at the whole deadline, then close/drain the actual
            # browser in finally. Own the file write separately, outside its
            # internal async_writefile thread, so no write can outlive cleanup.
            try:
                async with asyncio.timeout(work.remaining()):
                    pdf = await page.pdf(
                        format="A4", print_background=True,
                        prefer_css_page_size=True, display_header_footer=True,
                        header_template="<span></span>", footer_template=footer,
                        margin={"top": "0", "right": "0", "bottom": "11mm", "left": "0"},
                    )
            except TimeoutError as exc:
                raise ReportTimeoutError("Report rendering exceeded its 120-second limit. Choose a shorter period.") from exc
            work.check()
            pdf_size: int = await drain_owned(asyncio.to_thread(_write_pdf, output_path, pdf, work))
            work.check()
        finally:
            await drain_owned(browser.close())
    except PlaywrightTimeoutError as exc:
        raise ReportTimeoutError("Report rendering exceeded its 120-second limit. Choose a shorter period.") from exc
    finally:
        await drain_owned(playwright.stop())
    work.check()
    return pdf_size
