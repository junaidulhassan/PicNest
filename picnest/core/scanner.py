import datetime
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field

from picnest.core.database import now
from picnest.core.media_types import MediaKind, extension_of, kind_for_extension
from picnest.core.models import MediaFlags

logger = logging.getLogger(__name__)

DATE_TIME_PATTERN = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})[-_ T.]?(\d{2})[-_.:h]?(\d{2})[-_.:m]?(\d{2})(?!\d{3})")
DATE_PATTERN = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)")
SCREENSHOT_MARKERS = ("screenshot", "screen shot", "scrnshot", "screencap", "capture d", "bildschirmfoto")
BATCH_SIZE = 400


def date_from_filename(name):
    for pattern in (DATE_TIME_PATTERN, DATE_PATTERN):
        match = pattern.search(name)
        if not match:
            continue
        parts = [int(value) for value in match.groups()]
        try:
            moment = datetime.datetime(*parts[:3]) if len(parts) == 3 else datetime.datetime(*parts)
        except ValueError:
            continue
        timestamp = moment.timestamp()
        if 631152000 <= timestamp <= time.time() + 86400:
            return timestamp
    return None


def effective_exclusions(excluded_paths, base_path):
    return [
        excluded
        for excluded in excluded_paths
        if not (base_path == excluded or base_path.startswith(excluded.rstrip("/") + "/"))
    ]


def looks_like_screenshot(name, folder):
    lowered_name = name.lower()
    if any(marker in lowered_name for marker in SCREENSHOT_MARKERS):
        return True
    folder_name = os.path.basename(folder).lower()
    return "screenshot" in folder_name or folder_name in ("screencaps", "screen captures")


@dataclass
class ScanScope:
    path: str
    root: str


@dataclass
class ScanOptions:
    scopes: list
    root_paths: list
    excluded_paths: list
    excluded_names: set
    include_hidden: bool = False
    minimum_image_bytes: int = 8192
    remove_missing: bool = True


@dataclass
class ScanResult:
    added: int = 0
    updated: int = 0
    removed: int = 0
    media_directories: set = field(default_factory=set)
    cancelled: bool = False


class Scanner:
    def __init__(self, database):
        self._database = database
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()

    def reset_cancel(self):
        self._cancel_event.clear()

    @property
    def cancelled(self):
        return self._cancel_event.is_set()

    def is_excluded_directory(self, path, name, options, excluded_paths=None):
        if not options.include_hidden and name.startswith("."):
            return True
        if name in options.excluded_names:
            return True
        for excluded in options.excluded_paths if excluded_paths is None else excluded_paths:
            if path == excluded or path.startswith(excluded.rstrip("/") + "/"):
                return True
        return False

    def is_excluded_location(self, path, root, options):
        excluded_paths = effective_exclusions(options.excluded_paths, root)
        current = path
        while current and current != root and current != "/":
            if self.is_excluded_directory(current, os.path.basename(current), options, excluded_paths):
                return True
            current = os.path.dirname(current)
        return False

    def run(self, options, on_progress=None, on_batch=None):
        result = ScanResult()
        for scope in options.scopes:
            if self.cancelled:
                result.cancelled = True
                break
            if not os.path.isdir(scope.path):
                continue
            self._scan_scope(scope, options, result, on_progress, on_batch)
        return result

    def _load_existing(self, scope):
        prefix = scope.path.rstrip("/") + "/"
        rows = self._database.query(
            "SELECT path, id, size, mtime FROM media WHERE trashed_at IS NULL AND root = ? AND (path >= ? AND path < ?)",
            (scope.root, prefix, prefix[:-1] + "0"),
        )
        return {row[0]: (row[1], row[2], row[3]) for row in rows}

    def _scan_scope(self, scope, options, result, on_progress, on_batch):
        existing = self._load_existing(scope)
        seen_paths = set()
        pending_inserts = []
        pending_updates = []
        other_roots = {root for root in options.root_paths if root != scope.root}
        excluded_paths = effective_exclusions(options.excluded_paths, scope.path)
        stack = [scope.path]
        last_progress = 0.0
        found = 0
        completed = True
        while stack:
            if self.cancelled:
                completed = False
                result.cancelled = True
                break
            directory = stack.pop()
            try:
                entries = list(os.scandir(directory))
            except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
                continue
            directory_media = []
            for entry in entries:
                name = entry.name
                try:
                    if entry.is_dir(follow_symlinks=False):
                        child_path = entry.path
                        if child_path in other_roots:
                            continue
                        if self.is_excluded_directory(child_path, name, options, excluded_paths):
                            continue
                        stack.append(child_path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                extension = extension_of(name)
                kind = kind_for_extension(extension)
                if kind is None:
                    continue
                try:
                    stat_result = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if kind == MediaKind.IMAGE and stat_result.st_size < options.minimum_image_bytes:
                    continue
                if stat_result.st_size == 0:
                    continue
                directory_media.append((entry.path, name, extension, kind, stat_result.st_size, stat_result.st_mtime))
            if not directory_media:
                continue
            result.media_directories.add(directory)
            live_stems = self._live_photo_stems(directory_media)
            for path, name, extension, kind, size, mtime in directory_media:
                seen_paths.add(path)
                found += 1
                known = existing.get(path)
                flags = 0
                ignored = 0
                stem = os.path.splitext(name)[0]
                if kind == MediaKind.IMAGE and stem in live_stems:
                    flags |= MediaFlags.LIVE_PHOTO
                if kind == MediaKind.VIDEO and stem in live_stems:
                    ignored = 1
                if looks_like_screenshot(name, directory):
                    flags |= MediaFlags.SCREENSHOT
                if known is None:
                    sort_date = date_from_filename(name) or mtime
                    pending_inserts.append(
                        (path, directory, name, scope.root, int(kind), extension, size, mtime, now(), sort_date, flags, ignored)
                    )
                elif known[1] != size or abs(known[2] - mtime) > 0.5:
                    pending_updates.append((size, mtime, flags, ignored, known[0]))
            if len(pending_inserts) + len(pending_updates) >= BATCH_SIZE:
                self._flush(pending_inserts, pending_updates, result, on_batch)
            current = time.monotonic()
            if on_progress is not None and current - last_progress > 0.25:
                last_progress = current
                on_progress(found, directory)
        self._flush(pending_inserts, pending_updates, result, on_batch)
        if completed and options.remove_missing:
            missing_ids = [value[0] for path, value in existing.items() if path not in seen_paths]
            if missing_ids:
                self.remove_ids(missing_ids)
                result.removed += len(missing_ids)
        if on_progress is not None:
            on_progress(found, scope.path)

    def _live_photo_stems(self, directory_media):
        image_stems = set()
        video_stems = set()
        for _path, name, extension, kind, size, _mtime in directory_media:
            stem = os.path.splitext(name)[0]
            if kind == MediaKind.IMAGE and extension in ("heic", "heif", "jpg", "jpeg"):
                image_stems.add(stem)
            elif kind == MediaKind.VIDEO and extension in ("mov", "mp4") and size < 30 * 1024 * 1024:
                video_stems.add(stem)
        return image_stems & video_stems

    def _flush(self, pending_inserts, pending_updates, result, on_batch):
        if not pending_inserts and not pending_updates:
            return
        with self._database.transaction() as connection:
            if pending_inserts:
                connection.executemany(
                    """
                    INSERT INTO media(path, folder, name, root, kind, extension, size, mtime, added_at, sort_date, flags, ignored)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        size = excluded.size, mtime = excluded.mtime, root = excluded.root,
                        trashed_at = NULL, trash_uri = NULL, metadata_state = 0, thumbnail_state = 0
                    """,
                    pending_inserts,
                )
            if pending_updates:
                connection.executemany(
                    """
                    UPDATE media SET size = ?, mtime = ?, flags = (flags & ~5) | ?, ignored = ?,
                        metadata_state = 0, thumbnail_state = 0, content_hash = NULL, placeholder = NULL
                    WHERE id = ?
                    """,
                    pending_updates,
                )
        result.added += len(pending_inserts)
        result.updated += len(pending_updates)
        if on_batch is not None:
            on_batch(len(pending_inserts), [row[-1] for row in pending_updates])
        pending_inserts.clear()
        pending_updates.clear()

    def remove_ids(self, item_ids):
        with self._database.transaction() as connection:
            for offset in range(0, len(item_ids), 500):
                chunk = item_ids[offset : offset + 500]
                placeholders = ",".join("?" * len(chunk))
                connection.execute(f"DELETE FROM media WHERE id IN ({placeholders})", chunk)

    def index_file(self, path, root, options):
        name = os.path.basename(path)
        directory = os.path.dirname(path)
        extension = extension_of(name)
        kind = kind_for_extension(extension)
        if kind is None:
            return None
        if self.is_excluded_location(directory, root, options):
            return None
        try:
            stat_result = os.stat(path, follow_symlinks=False)
        except OSError:
            return None
        if stat_result.st_size == 0 or (kind == MediaKind.IMAGE and stat_result.st_size < options.minimum_image_bytes):
            return None
        flags = MediaFlags.SCREENSHOT if looks_like_screenshot(name, directory) else 0
        sort_date = date_from_filename(name) or stat_result.st_mtime
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO media(path, folder, name, root, kind, extension, size, mtime, added_at, sort_date, flags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    size = excluded.size, mtime = excluded.mtime, trashed_at = NULL, trash_uri = NULL,
                    metadata_state = 0, thumbnail_state = 0, content_hash = NULL, placeholder = NULL
                """,
                (path, directory, name, root, int(kind), extension, stat_result.st_size, stat_result.st_mtime, now(), sort_date, flags),
            )
            row = connection.execute("SELECT id FROM media WHERE path = ?", (path,)).fetchone()
        return row[0] if row else None

    def remove_path(self, path):
        prefix = path.rstrip("/") + "/"
        with self._database.transaction() as connection:
            rows = connection.execute(
                "SELECT id FROM media WHERE trashed_at IS NULL AND (path = ? OR (path >= ? AND path < ?))",
                (path, prefix, prefix[:-1] + "0"),
            ).fetchall()
            ids = [row[0] for row in rows]
            for offset in range(0, len(ids), 500):
                chunk = ids[offset : offset + 500]
                placeholders = ",".join("?" * len(chunk))
                connection.execute(f"DELETE FROM media WHERE id IN ({placeholders})", chunk)
        return ids
