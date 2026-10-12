import logging
import os
import shutil
import urllib.parse
from pathlib import Path

from gi.repository import Gio, GLib


logger = logging.getLogger(__name__)


class TrashError(Exception):
    pass


class TrashLocation:
    __slots__ = ("files_path", "info_path")

    def __init__(self, files_path, info_path):
        self.files_path = files_path
        self.info_path = info_path

    def serialize(self):
        return f"{self.files_path}\n{self.info_path}"

    @classmethod
    def parse(cls, value):
        if not value or "\n" not in value:
            return None
        files_path, info_path = value.split("\n", 1)
        return cls(files_path, info_path)


def _home_trash_directory():
    return Path(GLib.get_user_data_dir()) / "Trash"


def _mount_point_of(path):
    current = os.path.abspath(path)
    while not os.path.ismount(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return current


def _candidate_trash_directories(original_path):
    candidates = [_home_trash_directory()]
    mount_point = _mount_point_of(original_path)
    uid = os.getuid()
    candidates.append(Path(mount_point) / f".Trash-{uid}")
    candidates.append(Path(mount_point) / ".Trash" / str(uid))
    unique = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique, mount_point


def _read_trash_info(info_path):
    original = None
    deleted = None
    try:
        for line in info_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Path="):
                original = urllib.parse.unquote(line[5:])
            elif line.startswith("DeletionDate="):
                deleted = line[13:]
    except OSError:
        return None, None
    return original, deleted


def find_trashed_entry(original_path):
    directories, mount_point = _candidate_trash_directories(original_path)
    base_name = os.path.basename(original_path)
    stem = os.path.splitext(base_name)[0]
    best = None
    for trash_directory in directories:
        info_directory = trash_directory / "info"
        if not info_directory.is_dir():
            continue
        try:
            entries = [entry for entry in os.scandir(info_directory) if entry.name.startswith(stem) and entry.name.endswith(".trashinfo")]
        except OSError:
            continue
        for entry in entries:
            recorded_path, deletion_date = _read_trash_info(Path(entry.path))
            if recorded_path is None:
                continue
            if not recorded_path.startswith("/"):
                recorded_path = os.path.join(mount_point, recorded_path)
            if os.path.normpath(recorded_path) != os.path.normpath(original_path):
                continue
            files_path = trash_directory / "files" / entry.name[: -len(".trashinfo")]
            if not files_path.exists():
                continue
            if best is None or (deletion_date or "") > best[0]:
                best = (deletion_date or "", TrashLocation(str(files_path), entry.path))
    return best[1] if best else None


def move_to_trash(path):
    gfile = Gio.File.new_for_path(path)
    try:
        gfile.trash(None)
    except GLib.Error as error:
        raise TrashError(error.message) from error
    location = find_trashed_entry(path)
    return location.serialize() if location else None


def _unique_destination(path):
    if not os.path.exists(path):
        return path
    directory, name = os.path.split(path)
    stem, extension = os.path.splitext(name)
    counter = 1
    while True:
        candidate = os.path.join(directory, f"{stem} ({counter}){extension}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def restore(trash_uri, original_path):
    location = TrashLocation.parse(trash_uri) or find_trashed_entry(original_path)
    if location is None or not os.path.exists(location.files_path):
        raise TrashError("The file is no longer in the trash")
    os.makedirs(os.path.dirname(original_path), exist_ok=True)
    destination = _unique_destination(original_path)
    try:
        shutil.move(location.files_path, destination)
    except OSError as error:
        raise TrashError(str(error)) from error
    try:
        os.remove(location.info_path)
    except OSError:
        pass
    return destination


def delete_trashed(trash_uri, original_path):
    location = TrashLocation.parse(trash_uri) or find_trashed_entry(original_path)
    if location is None:
        return
    for target in (location.files_path, location.info_path):
        try:
            if os.path.isdir(target) and not os.path.islink(target):
                shutil.rmtree(target)
            elif os.path.lexists(target):
                os.remove(target)
        except OSError as error:
            raise TrashError(str(error)) from error


def trashed_file_path(trash_uri):
    location = TrashLocation.parse(trash_uri)
    if location is None:
        return None
    return location.files_path if os.path.exists(location.files_path) else None


def delete_file(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        return
    except OSError as error:
        raise TrashError(str(error)) from error
