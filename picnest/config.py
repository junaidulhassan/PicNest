import os
from pathlib import Path

from picnest import __version__

APP_ID = "com.junaidulhassan.Picnest"
APP_NAME = "Picnest"
APP_SLUG = "picnest"
VERSION = __version__
AUTHOR_NAME = "Junaid Ul Hassan"
AUTHOR_TITLE = "Software Engineer"
AUTHOR_CREDIT = f"Created by {AUTHOR_NAME}, {AUTHOR_TITLE}"
APP_DESCRIPTION = "A fast, beautiful home for every photo and video on your computer."
SYSTEM_CONFIG_PATH = Path("/etc/picnest/picnest.conf")

_package_directory = Path(__file__).resolve().parent


def _resolve_data_directory():
    override = os.environ.get("PICNEST_DATA_DIR")
    if override:
        return Path(override)
    candidates = (
        _package_directory.parent / "data",
        Path("/usr/share/picnest"),
    )
    for candidate in candidates:
        if (candidate / "style.css").exists():
            return candidate
    return candidates[-1]


DATA_DIRECTORY = _resolve_data_directory()
UI_ICONS_DIRECTORY = DATA_DIRECTORY / "ui-icons"
APP_ICONS_DIRECTORY = DATA_DIRECTORY / "icons"
STYLESHEET_PATH = DATA_DIRECTORY / "style.css"
SCHEMA_SOURCE_DIRECTORY = DATA_DIRECTORY
