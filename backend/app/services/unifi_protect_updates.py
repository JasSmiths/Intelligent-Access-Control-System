import asyncio
import json
import re
import shutil
import sys
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from packaging.version import InvalidVersion, Version
from sqlalchemy import select

from app.ai.providers import ChatMessageInput, ProviderNotConfiguredError, get_llm_provider
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import SystemSetting
from app.modules.unifi_protect.client import (
    UnifiProtectError,
    build_unifi_protect_client,
    close_unifi_protect_client,
    get_unifi_protect_snapshot,
    list_bootstrap_cameras,
    load_unifi_protect_bootstrap,
    serialize_unifi_camera,
)
from app.modules.unifi_protect.package import (
    activate_unifi_protect_package_overlay,
    current_unifi_protect_version,
    installed_overlay_versions,
    overlay_path_for_version,
    purge_unifi_protect_modules,
    read_active_package_state,
    restore_package_state,
    set_active_overlay,
)
from app.services.settings import (
    DEFAULT_DYNAMIC_SETTINGS,
    SECRET_KEYS,
    get_runtime_config,
    invalidate_runtime_config_cache,
    setting_payload,
)
from app.services.unifi_protect import get_unifi_protect_service

logger = get_logger(__name__)

PYPI_PACKAGE_URL = "https://pypi.org/pypi/uiprotect/json"
GITHUB_RELEASE_URLS = (
    "https://api.github.com/repos/uilibs/uiprotect/releases/tags/v{version}",
    "https://api.github.com/repos/uilibs/uiprotect/releases/tags/{version}",
)
BACKUP_SCHEMA_VERSION = 1


class UnifiProtectUpdateError(RuntimeError):
    """Raised when the managed UniFi Protect update workflow cannot continue."""


class UnifiProtectUpdateService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def status(self) -> dict[str, Any]:
        current = current_unifi_protect_version()
        latest, latest_info = await self._latest_pypi_version()
        return {
            "package": "uiprotect",
            "current_version": current,
            "latest_version": latest,
            "update_available": _is_newer(latest, current),
            "active_package": read_active_package_state().as_dict(),
            "installed_overlays": installed_overlay_versions(),
            "latest_summary": latest_info,
        }

    async def analyze(self, *, target_version: str | None = None, provider: str | None = None) -> dict[str, Any]:
        current = current_unifi_protect_version()
        latest, latest_info = await self._latest_pypi_version()
        target = target_version or latest
        if not target:
            raise UnifiProtectUpdateError("Unable to determine the latest uiprotect release.")
        release_notes = await self._release_notes(target)
        analysis_text, analysis_provider = await self._analyze_release_notes(
            current_version=current,
            target_version=target,
            provider=provider,
            latest_info=latest_info,
            release_notes=release_notes,
        )
        return {
            "package": "uiprotect",
            "current_version": current,
            "target_version": target,
            "latest_version": latest,
            "update_available": _is_newer(target, current),
            "provider": analysis_provider,
            "analysis": analysis_text,
            "release_notes": release_notes,
            "latest_summary": latest_info,
        }

    async def apply(self, *, target_version: str | None = None, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            raise UnifiProtectUpdateError("Update confirmation is required before applying a package update.")
        async with self._lock:
            current = current_unifi_protect_version()
            latest, _ = await self._latest_pypi_version()
            target = target_version or latest
            if not target:
                raise UnifiProtectUpdateError("Unable to determine the target uiprotect release.")
            if not _is_newer_or_equal(target, current):
                raise UnifiProtectUpdateError(f"Target version {target} is older than the active version {current}.")

            previous_state = read_active_package_state()
            backup = await self.create_backup(reason=f"before_uiprotect_update_to_{target}")
            service = get_unifi_protect_service()
            install_path = overlay_path_for_version(target)

            try:
                await service.stop()
                await self._install_overlay(target, install_path)
                set_active_overlay(target, install_path)
                purge_unifi_protect_modules()
                verification = await self.verify()
                await service.restart()
            except Exception as exc:
                logger.warning("unifi_protect_update_failed_rolling_back", extra={"error": str(exc)})
                restore_package_state(previous_state.as_dict())
                purge_unifi_protect_modules()
                await service.restart()
                raise UnifiProtectUpdateError(
                    f"Update failed and package state was rolled back. Backup {backup['id']} is available. {exc}"
                ) from exc

            return {
                "ok": True,
                "package": "uiprotect",
                "previous_version": current,
                "current_version": current_unifi_protect_version(),
                "target_version": target,
                "backup": backup,
                "verification": verification,
            }

    async def verify(self) -> dict[str, Any]:
        runtime = await get_runtime_config()
        api = await build_unifi_protect_client(runtime)
        try:
            await load_unifi_protect_bootstrap(api)
            cameras = list_bootstrap_cameras(api)
            if not cameras:
                raise UnifiProtectError("UniFi Protect returned no readable cameras after the update.")
            first_camera = cameras[0]
            snapshot = await get_unifi_protect_snapshot(
                api,
                str(getattr(first_camera, "id", "")),
                width=160,
                height=90,
            )
            return {
                "package_version": current_unifi_protect_version(),
                "camera_count": len(cameras),
                "snapshot_bytes": len(snapshot.content),
                "sample_camera": serialize_unifi_camera(first_camera),
            }
        finally:
            await close_unifi_protect_client(api)

    async def create_backup(self, *, reason: str = "manual") -> dict[str, Any]:
        rows = await _load_unifi_setting_records()
        backup_id = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]
        backup = {
            "schema_version": BACKUP_SCHEMA_VERSION,
            "id": backup_id,
            "created_at": datetime.now(tz=UTC).isoformat(),
            "reason": reason,
            "package": {
                "name": "uiprotect",
                "version": current_unifi_protect_version(),
                "active_state": read_active_package_state().as_dict(),
            },
            "settings": rows,
        }
        path = _backup_path(backup_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(backup, indent=2, sort_keys=True))
        return _backup_summary(path, backup)

    async def list_backups(self) -> list[dict[str, Any]]:
        root = _backup_root()
        if not root.exists():
            return []
        backups = []
        for path in sorted(root.glob("*.json"), reverse=True):
            try:
                backups.append(_backup_summary(path, json.loads(path.read_text())))
            except (OSError, json.JSONDecodeError):
                continue
        return backups

    async def restore_backup(self, backup_id: str) -> dict[str, Any]:
        path = _backup_path(backup_id)
        if not path.exists():
            raise UnifiProtectUpdateError("UniFi Protect backup was not found.")
        backup = json.loads(path.read_text())
        if int(backup.get("schema_version") or 0) != BACKUP_SCHEMA_VERSION:
            raise UnifiProtectUpdateError("UniFi Protect backup schema is not supported.")

        service = get_unifi_protect_service()
        await service.stop()
        await _restore_unifi_setting_records(backup.get("settings", []))
        restore_package_state(backup.get("package", {}).get("active_state"))
        purge_unifi_protect_modules()
        invalidate_runtime_config_cache()
        await service.restart()
        return {
            "ok": True,
            "backup": _backup_summary(path, backup),
            "verification": await service.status(refresh=True),
        }

    async def delete_backup(self, backup_id: str) -> None:
        path = _backup_path(backup_id)
        if not path.exists():
            raise UnifiProtectUpdateError("UniFi Protect backup was not found.")
        path.unlink()

    def backup_file(self, backup_id: str) -> Path:
        path = _backup_path(backup_id)
        if not path.exists():
            raise UnifiProtectUpdateError("UniFi Protect backup was not found.")
        return path

    async def _latest_pypi_version(self) -> tuple[str, dict[str, Any]]:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get(PYPI_PACKAGE_URL)
        if response.status_code >= 400:
            raise UnifiProtectUpdateError(f"PyPI returned HTTP {response.status_code}.")
        payload = response.json()
        info = payload.get("info", {})
        version = str(info.get("version") or "")
        if not version:
            raise UnifiProtectUpdateError("PyPI did not return a latest uiprotect version.")
        summary = {
            "summary": info.get("summary"),
            "home_page": info.get("home_page"),
            "project_urls": info.get("project_urls") or {},
            "requires_dist": info.get("requires_dist") or [],
            "package_url": PYPI_PACKAGE_URL,
        }
        return version, summary

    async def _release_notes(self, version: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            for url_template in GITHUB_RELEASE_URLS:
                url = url_template.format(version=version)
                response = await client.get(url, headers={"Accept": "application/vnd.github+json"})
                if response.status_code == 200:
                    payload = response.json()
                    return {
                        "source": url,
                        "title": payload.get("name") or payload.get("tag_name") or version,
                        "body": _truncate(str(payload.get("body") or ""), 16000),
                        "published_at": payload.get("published_at"),
                        "html_url": payload.get("html_url"),
                    }

            pypi_response = await client.get(f"https://pypi.org/pypi/uiprotect/{version}/json")
        if pypi_response.status_code >= 400:
            return {
                "source": f"https://pypi.org/project/uiprotect/{version}/",
                "title": f"uiprotect {version}",
                "body": "No GitHub release notes were found for this version.",
            }
        payload = pypi_response.json()
        info = payload.get("info", {})
        return {
            "source": f"https://pypi.org/project/uiprotect/{version}/",
            "title": f"uiprotect {version}",
            "body": _truncate(str(info.get("description") or info.get("summary") or ""), 16000),
            "published_at": None,
            "html_url": f"https://pypi.org/project/uiprotect/{version}/",
        }

    async def _analyze_release_notes(
        self,
        *,
        current_version: str,
        target_version: str,
        provider: str | None,
        latest_info: dict[str, Any],
        release_notes: dict[str, Any],
    ) -> tuple[str, str]:
        runtime = await get_runtime_config()
        provider_name = provider or runtime.llm_provider
        prompt = (
            "Review this uiprotect package update for the Intelligent Access Control System.\n"
            f"Current version: {current_version}\n"
            f"Target version: {target_version}\n"
            "Integration touchpoints: ProtectApiClient constructor, API key auth, bootstrap update, "
            "camera snapshots, package snapshots, get_events, event thumbnails/videos, websocket subscriptions, "
            "ModelType imports, pydantic model attributes, and aiohttp session cleanup.\n\n"
            f"Package metadata: {json.dumps(latest_info, default=str)[:5000]}\n\n"
            f"Release notes source: {release_notes.get('source')}\n"
            f"Release notes:\n{release_notes.get('body') or 'No release notes body available.'}\n\n"
            "Return concise markdown with: risk level, possible breaking issues, what to verify, "
            "and a go/no-go recommendation."
        )
        if provider_name == "local":
            return _heuristic_analysis(current_version, target_version, release_notes), "local"
        try:
            llm = get_llm_provider(provider_name)
            result = await llm.complete(
                [
                    ChatMessageInput(
                        role="system",
                        content=(
                            "You are a careful Python dependency upgrade reviewer. "
                            "Be conservative and tie risks to the provided release notes only."
                        ),
                    ),
                    ChatMessageInput(role="user", content=prompt),
                ]
            )
            return result.text or _heuristic_analysis(current_version, target_version, release_notes), provider_name
        except (ProviderNotConfiguredError, Exception) as exc:
            return (
                _heuristic_analysis(current_version, target_version, release_notes)
                + f"\n\nAI provider fallback: {provider_name} could not complete analysis: {exc}",
                "local",
            )

    async def _install_overlay(self, version: str, target_path: Path) -> None:
        staging = target_path.with_name(f"{target_path.name}.staging")
        if staging.exists():
            shutil.rmtree(staging)
        if target_path.exists():
            shutil.rmtree(target_path)
        staging.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--no-deps",
            "--target",
            str(staging),
            f"uiprotect=={version}",
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
        if process.returncode != 0:
            raise UnifiProtectUpdateError(
                "pip install failed: "
                + _truncate((stderr or stdout).decode(errors="replace"), 1200)
            )
        staging.rename(target_path)


def _backup_root() -> Path:
    return settings.data_dir / "unifi-protect-backups"


def _backup_path(backup_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "", backup_id)
    return _backup_root() / f"{safe}.json"


def _backup_summary(path: Path, backup: dict[str, Any]) -> dict[str, Any]:
    settings_rows = backup.get("settings", [])
    return {
        "id": backup.get("id"),
        "created_at": backup.get("created_at"),
        "reason": backup.get("reason"),
        "package_version": backup.get("package", {}).get("version"),
        "active_package": backup.get("package", {}).get("active_state"),
        "settings_count": len(settings_rows) if isinstance(settings_rows, list) else 0,
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "download_url": f"/api/v1/integrations/unifi-protect/backups/{backup.get('id')}/download",
    }


async def _load_unifi_setting_records() -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.scalars(
                select(SystemSetting)
                .where(SystemSetting.key.like("unifi_protect_%"))
                .order_by(SystemSetting.key)
            )
        ).all()
    return [
        {
            "key": row.key,
            "category": row.category,
            "value": row.value,
            "is_secret": row.is_secret,
            "description": row.description,
        }
        for row in rows
    ]


async def _restore_unifi_setting_records(records: list[dict[str, Any]]) -> None:
    allowed = {key for key in DEFAULT_DYNAMIC_SETTINGS if key.startswith("unifi_protect_")}
    async with AsyncSessionLocal() as session:
        existing = {
            row.key: row
            for row in (await session.scalars(select(SystemSetting).where(SystemSetting.key.in_(allowed)))).all()
        }
        for record in records:
            key = str(record.get("key") or "")
            if key not in allowed:
                continue
            category, default, description = DEFAULT_DYNAMIC_SETTINGS[key]
            value = record.get("value")
            if not isinstance(value, dict):
                value = setting_payload(key, default)
            row = existing.get(key)
            if row:
                row.category = str(record.get("category") or category)
                row.value = value
                row.is_secret = bool(record.get("is_secret", key in SECRET_KEYS))
                row.description = str(record.get("description") or description)
            else:
                session.add(
                    SystemSetting(
                        key=key,
                        category=str(record.get("category") or category),
                        value=value,
                        is_secret=bool(record.get("is_secret", key in SECRET_KEYS)),
                        description=str(record.get("description") or description),
                    )
                )
        await session.commit()


def _is_newer(candidate: str, current: str) -> bool:
    try:
        return Version(candidate) > Version(current)
    except InvalidVersion:
        return candidate != current


def _is_newer_or_equal(candidate: str, current: str) -> bool:
    try:
        return Version(candidate) >= Version(current)
    except InvalidVersion:
        return True


def _heuristic_analysis(current_version: str, target_version: str, release_notes: dict[str, Any]) -> str:
    body = str(release_notes.get("body") or "")
    lowered = body.lower()
    markers = {
        "high": ["breaking", "remove", "removed", "drop support", "major", "migration"],
        "medium": ["auth", "websocket", "camera", "event", "snapshot", "pydantic", "aiohttp", "session"],
    }
    high_hits = [item for item in markers["high"] if item in lowered]
    medium_hits = [item for item in markers["medium"] if item in lowered]
    risk = "High" if high_hits else "Medium" if medium_hits else "Low"
    findings = high_hits or medium_hits or ["No obvious compatibility keywords found in the release notes."]
    return (
        f"**Risk Level:** {risk}\n\n"
        f"**Version Path:** `{current_version}` -> `{target_version}`\n\n"
        "**Potential Issues:**\n"
        + "\n".join(f"- {item}" for item in findings)
        + "\n\n**Verification Checklist:**\n"
        "- Authenticate with UniFi Protect.\n"
        "- Load bootstrap and list cameras.\n"
        "- Fetch at least one current snapshot.\n"
        "- Confirm camera/event websocket subscriptions recover.\n\n"
        "**Recommendation:** Proceed only after the automatic backup is created. "
        "If verification fails, restore the backup from this panel."
    )


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n\n[truncated]"


@lru_cache
def get_unifi_protect_update_service() -> UnifiProtectUpdateService:
    activate_unifi_protect_package_overlay()
    return UnifiProtectUpdateService()
