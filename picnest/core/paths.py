import os
from pathlib import Path

from gi.repository import GLib

from picnest.config import APP_SLUG


def _ensure(path):
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_directory():
    return _ensure(Path(GLib.get_user_data_dir()) / APP_SLUG)


def cache_directory():
    return _ensure(Path(GLib.get_user_cache_dir()) / APP_SLUG)


def thumbnail_directory(tier):
    return _ensure(cache_directory() / "thumbnails" / tier)


def database_path():
    return data_directory() / "library.db"


def home_directory():
    return Path(GLib.get_home_dir())


def special_directory(kind):
    location = GLib.get_user_special_dir(kind)
    return Path(location) if location else None


def default_media_folders():
    folders = []
    for kind in (
        GLib.UserDirectory.DIRECTORY_PICTURES,
        GLib.UserDirectory.DIRECTORY_VIDEOS,
        GLib.UserDirectory.DIRECTORY_DESKTOP,
        GLib.UserDirectory.DIRECTORY_DOWNLOAD,
    ):
        folder = special_directory(kind)
        if folder and folder != home_directory() and folder.is_dir():
            folders.append(str(folder))
    return folders or [str(home_directory())]


def pictures_directory():
    folder = special_directory(GLib.UserDirectory.DIRECTORY_PICTURES)
    return folder if folder else home_directory() / "Pictures"


def directory_size(path):
    total = 0
    for root, _directories, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total
