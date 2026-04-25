import importlib
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

PACKAGE_NAME = "uiprotect"
PACKAGE_ROOT = settings.data_dir / "unifi-protect-package"
VERSIONS_DIR = PACKAGE_ROOT / "versions"
ACTIVE_MARKER = PACKAGE_ROOT / "active.json"


@dataclass(frozen=True)
class ProtectPackageState:
    mode: str
    version: str | None = None
    path: str | None = None
    installed_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "version": self.version,
            "path": self.path,
            "installed_at": self.installed_at,
        }


def activate_unifi_protect_package_overlay() -> ProtectPackageState:
    state = read_active_package_state()
    if state.mode != "overlay" or not state.path:
        return state
    package_path = Path(state.path)
    if not package_path.exists():
        logger.warning("unifi_protect_overlay_missing", extra={"path": state.path})
        return ProtectPackageState(mode="base", version=base_unifi_protect_version())
    path_text = str(package_path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)
    return state


def read_active_package_state() -> ProtectPackageState:
    if not ACTIVE_MARKER.exists():
        return ProtectPackageState(mode="base", version=base_unifi_protect_version())
    try:
        raw = json.loads(ACTIVE_MARKER.read_text())
        return ProtectPackageState(
            mode=str(raw.get("mode") or "base"),
            version=raw.get("version"),
            path=raw.get("path"),
            installed_at=raw.get("installed_at"),
        )
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("unifi_protect_active_marker_invalid", extra={"error": str(exc)})
        return ProtectPackageState(mode="base", version=base_unifi_protect_version())


def write_active_package_state(state: ProtectPackageState) -> None:
    PACKAGE_ROOT.mkdir(parents=True, exist_ok=True)
    ACTIVE_MARKER.write_text(json.dumps(state.as_dict(), indent=2, sort_keys=True))


def overlay_path_for_version(version: str) -> Path:
    safe = version.replace("/", "_")
    return VERSIONS_DIR / safe


def set_active_overlay(version: str, path: Path) -> ProtectPackageState:
    state = ProtectPackageState(
        mode="overlay",
        version=version,
        path=str(path),
        installed_at=datetime.now(tz=UTC).isoformat(),
    )
    write_active_package_state(state)
    activate_unifi_protect_package_overlay()
    return state


def restore_package_state(raw_state: dict[str, Any] | None) -> ProtectPackageState:
    if not raw_state or raw_state.get("mode") != "overlay":
        _remove_overlay_paths()
        state = ProtectPackageState(mode="base", version=base_unifi_protect_version())
        write_active_package_state(state)
        return state
    state = ProtectPackageState(
        mode="overlay",
        version=raw_state.get("version"),
        path=raw_state.get("path"),
        installed_at=raw_state.get("installed_at"),
    )
    write_active_package_state(state)
    activate_unifi_protect_package_overlay()
    return state


def purge_unifi_protect_modules() -> None:
    for name in list(sys.modules):
        if name == PACKAGE_NAME or name.startswith(f"{PACKAGE_NAME}."):
            sys.modules.pop(name, None)
    importlib.invalidate_caches()


def current_unifi_protect_version() -> str:
    activate_unifi_protect_package_overlay()
    return metadata.version(PACKAGE_NAME)


def base_unifi_protect_version() -> str | None:
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return None


def installed_overlay_versions() -> list[dict[str, str]]:
    if not VERSIONS_DIR.exists():
        return []
    rows: list[dict[str, str]] = []
    for path in sorted(VERSIONS_DIR.iterdir()):
        if path.is_dir():
            rows.append({"version": path.name, "path": str(path)})
    return rows


def remove_overlay_version(version: str) -> None:
    path = overlay_path_for_version(version)
    if path.exists():
        shutil.rmtree(path)


def _remove_overlay_paths() -> None:
    root = str(VERSIONS_DIR)
    sys.path[:] = [item for item in sys.path if not item.startswith(root)]
