import datetime
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field

from gi.repository import Gio, GLib, GObject

from picnest.core import duplicates, faces, file_ops, paths, trash
from picnest.core.database import Database, now
from picnest.core.media_types import MEDIA_EXTENSIONS, extension_of, type_category
from picnest.core.metadata_service import MetadataService
from picnest.core.models import Album, FolderEntry, ItemStore, YearEntry
from picnest.core.query import VISIBLE_FILTER, SearchFilters, ViewSpec, run_view_query
from picnest.core.scanner import Scanner, ScanOptions, ScanScope
from picnest.core.tasks import QueryExecutor, run_in_thread
from picnest.core.thumbnails import ThumbnailService, reset_directory_cache, thumbnail_path
from picnest.core.volumes import list_mount_points, read_system_configuration, root_for_path
from picnest.core.watcher import DirectoryWatcher

logger = logging.getLogger(__name__)

CONTENT_REFRESH_DELAY_MILLISECONDS = 1200
COVER_SCORE = "MAX(favorite * 1000000000000000 + (kind = 0) * 100000000000000 + MIN(width * height, 99999999999999))"


@dataclass
class Memory:
    key: str
    title: str
    subtitle: str
    spec: ViewSpec
    cover_id: int
    item_count: int


@dataclass
class DriveUsage:
    path: str
    label: str
    removable: bool
    online: bool
    media_bytes: int
    media_count: int
    total_bytes: int
    free_bytes: int


@dataclass
class StorageReport:
    drives: list
    years: list
    categories: list
    total_bytes: int
    total_count: int


@dataclass
class OperationResult:
    succeeded: list
    failures: list
    failed_ids: list = field(default_factory=list)


class Library(GObject.Object):
    __gsignals__ = {
        "content-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "items-updated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "scan-progress": (GObject.SignalFlags.RUN_FIRST, None, (bool, int, str)),
        "albums-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "duplicates-progress": (GObject.SignalFlags.RUN_FIRST, None, (int, int, bool)),
        "faces-progress": (GObject.SignalFlags.RUN_FIRST, None, (int, bool)),
    }

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.database = Database(paths.database_path())
        self.store = ItemStore(self.database)
        self.thumbnails = ThumbnailService(self.database, self._on_thumbnails_generated)
        self.metadata = MetadataService(self.database, self._on_metadata_updated)
        self.scanner = Scanner(self.database)
        self.watcher = DirectoryWatcher(self._on_file_ready, self._on_path_removed, self._on_directory_added)
        self.queries = QueryExecutor(self.database)
        self.face_detector = faces.FaceDetector(self.database, self._on_faces_progress)
        self._volume_monitor = Gio.VolumeMonitor.get()
        self._scan_thread = None
        self._scan_requested = None
        self._scan_found = 0
        self._content_refresh_source = 0
        self._pending_reason = ""
        self._duplicate_thread = None
        self._duplicate_cancel = threading.Event()
        self._roots = []
        self._disabled_roots = set()
        self._started = False

    @property
    def scanning(self):
        return self._scan_thread is not None and self._scan_thread.is_alive()

    def start(self):
        if self._started:
            return
        self._started = True
        self.refresh_roots()
        self._purge_unsupported_types()
        self.database.execute("UPDATE media SET metadata_state = 0 WHERE metadata_state = 2 AND kind = 1")
        self.database.execute("UPDATE media SET thumbnail_state = 0 WHERE thumbnail_state = 3 AND kind = 1")
        self.thumbnails.start()
        self.thumbnails.set_background_enabled(self.settings.get("background-thumbnails"))
        self.metadata.start()
        self._volume_monitor.connect("mount-added", self._on_mount_added)
        self._volume_monitor.connect("mount-removed", self._on_mount_removed)
        self.settings.connect_changed("background-thumbnails", self.thumbnails.set_background_enabled)
        self.settings.connect_changed("excluded-folders", lambda _value: self._apply_exclusions())
        GLib.timeout_add_seconds(20, self._run_maintenance)
        GLib.timeout_add_seconds(6 * 3600, self._run_maintenance_repeating)
        if self.settings.get("face-detection"):
            GLib.timeout_add_seconds(30, self._start_faces_once)

    def shutdown(self):
        self.scanner.cancel()
        self._duplicate_cancel.set()
        self.face_detector.stop()
        self.thumbnails.stop()
        self.metadata.stop()
        self.queries.stop()
        self.watcher.clear()
        self.database.optimize()

    def _purge_unsupported_types(self):
        placeholders = ",".join("?" * len(MEDIA_EXTENSIONS))
        with self.database.transaction() as connection:
            connection.execute(f"DELETE FROM media WHERE extension NOT IN ({placeholders})", sorted(MEDIA_EXTENSIONS))

    def refresh_roots(self):
        mounts = list_mount_points()
        mounted_paths = {mount.path for mount in mounts}
        with self.database.transaction() as connection:
            for mount in mounts:
                connection.execute(
                    """
                    INSERT INTO roots(path, label, removable, online) VALUES (?, ?, ?, 1)
                    ON CONFLICT(path) DO UPDATE SET label = excluded.label, removable = excluded.removable, online = 1
                    """,
                    (mount.path, mount.label, int(mount.removable)),
                )
            for (path,) in connection.execute("SELECT path FROM roots").fetchall():
                if path not in mounted_paths:
                    connection.execute("UPDATE roots SET online = 0 WHERE path = ?", (path,))
        self._roots = sorted(mounted_paths)
        self._disabled_roots = {row[0] for row in self.database.query("SELECT path FROM roots WHERE enabled = 0")}
        return mounts

    def _scan_options(self, only_root=None):
        system_excluded_paths, excluded_names = read_system_configuration()
        user_excluded = [path.rstrip("/") for path in self.settings.get("excluded-folders") if path]
        excluded_paths = system_excluded_paths + user_excluded + [str(paths.cache_directory()), str(paths.data_directory())]
        home = str(paths.home_directory())
        excluded_paths = [path for path in excluded_paths if not (home == path or home.startswith(path.rstrip("/") + "/")) or path in user_excluded]
        scopes = []
        override = os.environ.get("PICNEST_SCAN_ROOTS")
        included = [path for path in self.settings.get("included-folders") if path and os.path.isdir(path)]
        if override:
            scopes = [ScanScope(path, root_for_path(path, self._roots)) for path in override.split(":") if os.path.isdir(path)]
        elif only_root is not None:
            scopes = [ScanScope(only_root, only_root)]
        elif self.settings.get("scan-all-drives"):
            scopes = [ScanScope(root, root) for root in self._roots if root not in self._disabled_roots]
            for folder in included:
                if not any(folder == scope.path or folder.startswith(scope.path.rstrip("/") + "/") for scope in scopes):
                    scopes.append(ScanScope(folder, root_for_path(folder, self._roots)))
        else:
            for folder in included or paths.default_media_folders():
                scopes.append(ScanScope(folder, root_for_path(folder, self._roots)))
        scopes = [scope for scope in scopes if scope.root not in self._disabled_roots]
        return ScanOptions(
            scopes=scopes,
            root_paths=list(self._roots),
            excluded_paths=excluded_paths,
            excluded_names=excluded_names,
            include_hidden=self.settings.get("include-hidden-folders"),
            minimum_image_bytes=max(0, self.settings.get("minimum-image-kilobytes")) * 1024,
            remove_missing=True,
        )

    def scan(self, only_root=None):
        if self.scanning:
            self._scan_requested = only_root or "all"
            return
        self.scanner.reset_cancel()
        options = self._scan_options(only_root)
        self._scan_found = 0
        self.emit("scan-progress", True, 0, "")

        def progress(found, directory):
            GLib.idle_add(self._report_scan_progress, found, directory)

        def batch(added_count, updated_ids):
            GLib.idle_add(self._on_scan_batch, added_count, updated_ids)

        def work():
            started = time.monotonic()
            result = self.scanner.run(options, progress, batch)
            logger.info("Scan finished in %.1fs: %d added, %d updated, %d removed", time.monotonic() - started, result.added, result.updated, result.removed)
            return result

        def finished(result, error):
            self._scan_thread = None
            if result is not None:
                self.watcher.watch_many(sorted(result.media_directories, key=len))
                self.watcher.watch_many(paths.default_media_folders())
                with self.database.transaction() as connection:
                    for scope in options.scopes:
                        connection.execute("UPDATE roots SET last_scan = ? WHERE path = ?", (now(), scope.root))
            self.emit("scan-progress", False, self._scan_found, "")
            self._schedule_content_refresh("scan", immediate=True)
            self.metadata.wake()
            self.thumbnails.wake_background()
            requested, self._scan_requested = self._scan_requested, None
            if requested is not None:
                self.scan(None if requested == "all" else requested)

        self._scan_thread = run_in_thread(work, finished, name="picnest-scanner")

    def cancel_scan(self):
        self.scanner.cancel()

    def _report_scan_progress(self, found, directory):
        self._scan_found = found
        if self.scanning:
            self.emit("scan-progress", True, found, directory)
        return GLib.SOURCE_REMOVE

    def _on_scan_batch(self, added_count, updated_ids):
        if updated_ids:
            self.store.invalidate(updated_ids)
            for item_id in updated_ids:
                self.thumbnails.forget(item_id)
        self._schedule_content_refresh("scan")
        self.metadata.wake()
        self.thumbnails.wake_background()
        return GLib.SOURCE_REMOVE

    def _schedule_content_refresh(self, reason, immediate=False):
        self._pending_reason = reason
        if immediate:
            if self._content_refresh_source:
                GLib.source_remove(self._content_refresh_source)
                self._content_refresh_source = 0
            self.emit("content-changed", reason)
            return
        if not self._content_refresh_source:
            self._content_refresh_source = GLib.timeout_add(CONTENT_REFRESH_DELAY_MILLISECONDS, self._emit_content_refresh)

    def _emit_content_refresh(self):
        self._content_refresh_source = 0
        self.emit("content-changed", self._pending_reason)
        return GLib.SOURCE_REMOVE

    def _on_metadata_updated(self, item_ids):
        self.store.invalidate(item_ids)
        self._schedule_content_refresh("metadata")
        self.emit("items-updated", item_ids)
        return GLib.SOURCE_REMOVE

    def _on_thumbnails_generated(self, results):
        for item_id, state, placeholder in results:
            if placeholder is not None:
                self.store.update_fields(item_id, thumbnail_state=state, placeholder=placeholder)
            else:
                self.store.update_fields(item_id, thumbnail_state=state)
        self.emit("items-updated", [item_id for item_id, _state, _placeholder in results])
        return GLib.SOURCE_REMOVE

    def _on_mount_added(self, _monitor, mount):
        root = mount.get_root()
        path = root.get_path() if root is not None else None
        GLib.timeout_add(1500, self._handle_mount_added, path)

    def _handle_mount_added(self, path):
        self.refresh_roots()
        self._schedule_content_refresh("mounts", immediate=True)
        if path and path in self._roots and path not in self._disabled_roots and self.settings.get("scan-all-drives") and self.settings.get("first-run-complete"):
            self.scan(only_root=path)
        return GLib.SOURCE_REMOVE

    def _on_mount_removed(self, _monitor, mount):
        root = mount.get_root()
        path = root.get_path() if root is not None else None
        if path:
            self.watcher.unwatch_prefix(path)
        GLib.timeout_add(500, self._handle_mount_removed)

    def _handle_mount_removed(self):
        self.refresh_roots()
        self.store.invalidate()
        self._schedule_content_refresh("mounts", immediate=True)
        return GLib.SOURCE_REMOVE

    def _on_file_ready(self, path):
        if root_for_path(path, self._roots) in self._disabled_roots:
            return
        options = self._scan_options()
        item_id = self.scanner.index_file(path, root_for_path(path, self._roots), options)
        if item_id is not None:
            self.store.invalidate([item_id])
            self.thumbnails.forget(item_id)
            self.metadata.wake()
            self.thumbnails.wake_background()
            self._schedule_content_refresh("watch")

    def index_path(self, path):
        item_id = self.scanner.index_file(path, root_for_path(path, self._roots), self._scan_options())
        if item_id is not None:
            self.store.invalidate([item_id])
            self.thumbnails.forget(item_id)
            self.metadata.wake()
            self.emit("items-updated", [item_id])
            self._schedule_content_refresh("edit", immediate=True)
        return item_id

    def _on_path_removed(self, path):
        removed = self.scanner.remove_path(path)
        if removed:
            self.store.invalidate(removed)
            self._schedule_content_refresh("watch")

    def _on_directory_added(self, directory):
        options = self._scan_options()
        root = root_for_path(directory, self._roots)
        if self.scanner.is_excluded_location(directory, root, options):
            return
        options.scopes = [ScanScope(directory, root)]
        options.remove_missing = False

        def work():
            return self.scanner.run(options)

        def finished(result, _error):
            if result is not None:
                self.watcher.watch(directory)
                self.watcher.watch_many(sorted(result.media_directories))
                self._schedule_content_refresh("watch")
                self.metadata.wake()
                self.thumbnails.wake_background()

        run_in_thread(work, finished, name="picnest-directory-scan")

    def _apply_exclusions(self):
        excluded = [path.rstrip("/") for path in self.settings.get("excluded-folders") if path]
        if not excluded:
            return
        for path in excluded:
            removed = self.scanner.remove_path(path)
            self.watcher.unwatch_prefix(path)
            if removed:
                self.store.invalidate(removed)
        self._schedule_content_refresh("settings", immediate=True)

    def load_view(self, owner, spec, callback):
        self.queries.submit(owner, lambda database: run_view_query(database, spec), callback)

    def run_query(self, owner, work, callback):
        self.queries.submit(owner, work, callback)

    def item(self, item_id):
        return self.store.get(item_id)

    def items(self, item_ids):
        return self.store.get_many(item_ids)

    def _update_column(self, item_ids, column, value):
        with self.database.transaction() as connection:
            for offset in range(0, len(item_ids), 500):
                chunk = list(item_ids[offset : offset + 500])
                placeholders = ",".join("?" * len(chunk))
                connection.execute(f"UPDATE media SET {column} = ? WHERE id IN ({placeholders})", [value] + chunk)
        for item_id in item_ids:
            self.store.update_fields(item_id, **{column: value})

    def set_favorite(self, item_ids, favorite):
        item_ids = list(item_ids)
        self._update_column(item_ids, "favorite", 1 if favorite else 0)
        self.emit("items-updated", item_ids)
        self._schedule_content_refresh("favorites", immediate=True)

    def set_hidden(self, item_ids, hidden):
        item_ids = list(item_ids)
        self._update_column(item_ids, "hidden", 1 if hidden else 0)
        self.emit("items-updated", item_ids)
        self._schedule_content_refresh("hidden", immediate=True)

    def record_viewed(self, item_id):
        if not self.settings.get("remember-recents"):
            return
        self._update_column([item_id], "viewed_at", now())

    def clear_recents(self):
        self.database.execute("UPDATE media SET viewed_at = NULL WHERE viewed_at IS NOT NULL")
        self.store.invalidate()
        self._schedule_content_refresh("recents", immediate=True)

    def _finish_operation(self, reason, on_done):
        def finished(result, error):
            self.store.invalidate()
            self._schedule_content_refresh(reason, immediate=True)
            if result is None:
                result = OperationResult([], [("", str(error) if error else "Unknown error")])
            if on_done is not None:
                on_done(result)

        return finished

    def trash(self, item_ids, on_done=None):
        items = self.store.get_many(list(item_ids))

        def work():
            succeeded, failures, failed_ids = [], [], []
            for item in items:
                if item.is_trashed:
                    continue
                try:
                    trash_uri = trash.move_to_trash(item.path)
                except trash.TrashError as error:
                    failures.append((item.name, str(error)))
                    failed_ids.append(item.id)
                    continue
                self.database.execute("UPDATE media SET trashed_at = ?, trash_uri = ? WHERE id = ?", (now(), trash_uri, item.id))
                succeeded.append(item.id)
            return OperationResult(succeeded, failures, failed_ids)

        run_in_thread(work, self._finish_operation("trash", on_done), name="picnest-trash")

    def restore(self, item_ids, on_done=None):
        items = self.store.get_many(list(item_ids))

        def work():
            succeeded, failures = [], []
            for item in items:
                if not item.is_trashed:
                    continue
                try:
                    destination = trash.restore(item.trash_uri, item.path)
                except trash.TrashError as error:
                    failures.append((item.name, str(error)))
                    continue
                self.database.execute(
                    "UPDATE media SET trashed_at = NULL, trash_uri = NULL, path = ?, name = ?, folder = ? WHERE id = ?",
                    (destination, os.path.basename(destination), os.path.dirname(destination), item.id),
                )
                succeeded.append(item.id)
            return OperationResult(succeeded, failures)

        run_in_thread(work, self._finish_operation("trash", on_done), name="picnest-restore")

    def delete_permanently(self, item_ids, on_done=None):
        items = self.store.get_many(list(item_ids))

        def work():
            succeeded, failures = [], []
            for item in items:
                try:
                    if item.is_trashed:
                        trash.delete_trashed(item.trash_uri, item.path)
                    else:
                        trash.delete_file(item.path)
                except trash.TrashError as error:
                    failures.append((item.name, str(error)))
                    continue
                self.database.execute("DELETE FROM media WHERE id = ?", (item.id,))
                self._remove_thumbnail_files(item)
                succeeded.append(item.id)
            return OperationResult(succeeded, failures)

        run_in_thread(work, self._finish_operation("trash", on_done), name="picnest-delete")

    def empty_trash(self, on_done=None):
        item_ids = [row[0] for row in self.database.query("SELECT id FROM media WHERE trashed_at IS NOT NULL")]
        self.delete_permanently(item_ids, on_done)

    def trash_count(self):
        return self.database.scalar("SELECT COUNT(*) FROM media WHERE trashed_at IS NOT NULL", default=0)

    def _remove_thumbnail_files(self, item):
        for tier in ("normal", "large"):
            for state in (1, 2):
                try:
                    os.remove(thumbnail_path(item.id, item.mtime, tier, state))
                except OSError:
                    pass

    def _run_maintenance(self):
        retention_days = self.settings.get("trash-retention-days")
        cutoff = now() - retention_days * 86400
        expired = [row[0] for row in self.database.query("SELECT id FROM media WHERE trashed_at IS NOT NULL AND trashed_at < ?", (cutoff,))]
        if expired:
            self.delete_permanently(expired)
        self.database.optimize()
        return GLib.SOURCE_REMOVE

    def _run_maintenance_repeating(self):
        self._run_maintenance()
        return GLib.SOURCE_CONTINUE

    def rename(self, item_id, new_name):
        item = self.store.get(item_id)
        if item is None:
            raise file_ops.FileOperationError("The item no longer exists")
        destination = file_ops.rename(item.path, new_name)
        self.database.execute("UPDATE media SET path = ?, name = ? WHERE id = ?", (destination, os.path.basename(destination), item_id))
        self.store.invalidate([item_id])
        self.emit("items-updated", [item_id])
        self._schedule_content_refresh("rename", immediate=True)
        return destination

    def move(self, item_ids, directory, on_done=None):
        items = self.store.get_many(list(item_ids))
        root = root_for_path(directory, self._roots)

        def work():
            succeeded, failures = [], []
            for item in items:
                try:
                    destination = file_ops.move(item.path, directory)
                except file_ops.FileOperationError as error:
                    failures.append((item.name, str(error)))
                    continue
                self.database.execute(
                    "UPDATE media SET path = ?, name = ?, folder = ?, root = ? WHERE id = ?",
                    (destination, os.path.basename(destination), directory, root, item.id),
                )
                succeeded.append(item.id)
            return OperationResult(succeeded, failures)

        self.watcher.watch(directory)
        run_in_thread(work, self._finish_operation("move", on_done), name="picnest-move")

    def copy(self, item_ids, directory, on_done=None):
        items = self.store.get_many(list(item_ids))
        root = root_for_path(directory, self._roots)

        def work():
            succeeded, failures = [], []
            options = self._scan_options()
            for item in items:
                try:
                    destination = file_ops.copy(item.path, directory)
                except file_ops.FileOperationError as error:
                    failures.append((item.name, str(error)))
                    continue
                new_id = self.scanner.index_file(destination, root, options)
                if new_id is not None:
                    succeeded.append(new_id)
            return OperationResult(succeeded, failures)

        self.watcher.watch(directory)
        run_in_thread(work, self._finish_operation("copy", on_done), name="picnest-copy")

    def export(self, item_ids, directory, image_format, maximum_dimension, quality, on_done=None):
        items = self.store.get_many(list(item_ids))

        def work():
            succeeded, failures = [], []
            for item in items:
                try:
                    succeeded.append(file_ops.export(item.path, directory, image_format if not item.is_video else "original", maximum_dimension if not item.is_video else 0, quality))
                except file_ops.FileOperationError as error:
                    failures.append((item.name, str(error)))
            return OperationResult(succeeded, failures)

        def finished(result, error):
            if on_done is not None:
                on_done(result or OperationResult([], [("", str(error))]))

        run_in_thread(work, finished, name="picnest-export")

    def import_files(self, file_paths, on_done=None):
        target_directory = paths.pictures_directory() / "Picnest Imports"
        options = self._scan_options()

        def inside_library(path):
            return any(path == scope.path or path.startswith(scope.path.rstrip("/") + "/") for scope in options.scopes) and not any(
                path.startswith(excluded.rstrip("/") + "/") for excluded in options.excluded_paths
            )

        def work():
            succeeded, failures = [], []
            for source in file_paths:
                if extension_of(source) not in MEDIA_EXTENSIONS:
                    failures.append((os.path.basename(source), "Not a supported photo or video"))
                    continue
                destination = source
                if not inside_library(source):
                    try:
                        target_directory.mkdir(parents=True, exist_ok=True)
                        destination = file_ops.copy(source, str(target_directory))
                    except (OSError, file_ops.FileOperationError) as error:
                        failures.append((os.path.basename(source), str(error)))
                        continue
                new_id = self.scanner.index_file(destination, root_for_path(destination, self._roots), options)
                if new_id is not None:
                    succeeded.append(new_id)
            return OperationResult(succeeded, failures)

        def finished(result, error):
            self.watcher.watch(str(target_directory))
            self.metadata.wake()
            self._finish_operation("import", on_done)(result, error)

        run_in_thread(work, finished, name="picnest-import")

    def albums(self):
        rows = self.database.query(
            f"""
            SELECT albums.id, albums.name, albums.created_at,
                COALESCE(albums.cover_id, (
                    SELECT media.id FROM album_items JOIN media ON media.id = album_items.media_id
                    WHERE album_items.album_id = albums.id AND {VISIBLE_FILTER}
                    ORDER BY media.sort_date DESC LIMIT 1
                )),
                (SELECT COUNT(*) FROM album_items JOIN media ON media.id = album_items.media_id
                    WHERE album_items.album_id = albums.id AND {VISIBLE_FILTER})
            FROM albums ORDER BY albums.name COLLATE NOCASE
            """
        )
        return [Album(*row) for row in rows]

    def album(self, album_id):
        for album in self.albums():
            if album.id == album_id:
                return album
        return None

    def create_album(self, name, item_ids=()):
        name = name.strip() or "Untitled Album"
        with self.database.transaction() as connection:
            cursor = connection.execute("INSERT INTO albums(name, created_at) VALUES (?, ?)", (name, now()))
            album_id = cursor.lastrowid
            connection.executemany(
                "INSERT OR IGNORE INTO album_items(album_id, media_id, added_at) VALUES (?, ?, ?)",
                [(album_id, item_id, now()) for item_id in item_ids],
            )
        self.emit("albums-changed")
        return album_id

    def rename_album(self, album_id, name):
        self.database.execute("UPDATE albums SET name = ? WHERE id = ?", (name.strip() or "Untitled Album", album_id))
        self.emit("albums-changed")

    def delete_album(self, album_id):
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM album_items WHERE album_id = ?", (album_id,))
            connection.execute("DELETE FROM albums WHERE id = ?", (album_id,))
        self.emit("albums-changed")

    def add_to_album(self, album_id, item_ids):
        with self.database.transaction() as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO album_items(album_id, media_id, added_at) VALUES (?, ?, ?)",
                [(album_id, item_id, now()) for item_id in item_ids],
            )
        self.emit("albums-changed")
        self._schedule_content_refresh("albums", immediate=True)

    def remove_from_album(self, album_id, item_ids):
        with self.database.transaction() as connection:
            connection.executemany("DELETE FROM album_items WHERE album_id = ? AND media_id = ?", [(album_id, item_id) for item_id in item_ids])
            connection.execute("UPDATE albums SET cover_id = NULL WHERE id = ? AND cover_id NOT IN (SELECT media_id FROM album_items WHERE album_id = ?)", (album_id, album_id))
        self.emit("albums-changed")
        self._schedule_content_refresh("albums", immediate=True)

    def item_albums(self, item_id):
        rows = self.database.query(
            "SELECT albums.name FROM album_items JOIN albums ON albums.id = album_items.album_id WHERE album_items.media_id = ? ORDER BY albums.name",
            (item_id,),
        )
        return [row[0] for row in rows]

    def set_album_cover(self, album_id, item_id):
        self.database.execute("UPDATE albums SET cover_id = ? WHERE id = ?", (item_id, album_id))
        self.emit("albums-changed")

    def folders(self):
        rows = self.database.query(
            f"""
            SELECT folder, root, COUNT(*), MAX(sort_date), id FROM media
            WHERE {VISIBLE_FILTER} GROUP BY folder ORDER BY root, folder COLLATE NOCASE
            """
        )
        return [FolderEntry(folder=row[0], root=row[1], item_count=row[2], latest=row[3], cover_id=row[4]) for row in rows]

    def roots(self):
        return self.database.query("SELECT path, label, removable, online, enabled FROM roots ORDER BY path")

    def set_root_enabled(self, path, enabled):
        self.database.execute("UPDATE roots SET enabled = ? WHERE path = ?", (1 if enabled else 0, path))
        if enabled:
            self._disabled_roots.discard(path)
        else:
            self._disabled_roots.add(path)
            self.watcher.unwatch_prefix(path)
        self.store.invalidate()
        self._schedule_content_refresh("drives", immediate=True)
        if enabled:
            self.scan(only_root=path)
            self.metadata.wake()
            self.thumbnails.wake_background()

    def years(self):
        rows = self.database.query(
            f"""
            SELECT CAST(strftime('%Y', sort_date, 'unixepoch', 'localtime') AS INTEGER) AS year, COUNT(*),
                SUM(kind = 1), id, {COVER_SCORE}
            FROM media WHERE {VISIBLE_FILTER} GROUP BY year ORDER BY year DESC
            """
        )
        return [YearEntry(year=row[0], item_count=row[1], video_count=row[2] or 0, cover_id=row[3]) for row in rows if row[0]]

    def memories(self):
        today = datetime.date.today()
        result = []
        rows = self.database.query(
            f"""
            SELECT CAST(strftime('%Y', sort_date, 'unixepoch', 'localtime') AS INTEGER) AS year, COUNT(*), id, {COVER_SCORE}
            FROM media WHERE {VISIBLE_FILTER} AND strftime('%m-%d', sort_date, 'unixepoch', 'localtime') = ?
            GROUP BY year HAVING year < ? ORDER BY year DESC
            """,
            (today.strftime("%m-%d"), today.year),
        )
        for year, count, cover_id, _score in rows:
            years_ago = today.year - year
            title = "One Year Ago Today" if years_ago == 1 else f"{years_ago} Years Ago Today"
            spec = ViewSpec("memory", grouping="none", month_day=(today.month, today.day), year=year, title=title)
            result.append(Memory(f"day-{year}", title, today.replace(year=year).strftime("%-d %B %Y") if not (today.month == 2 and today.day == 29) else str(year), spec, cover_id, count))
        first_of_month = today.replace(day=1)
        last_month_end = first_of_month
        last_month_start = (first_of_month - datetime.timedelta(days=1)).replace(day=1)
        result.extend(self._range_memory("last-month", "Last Month", last_month_start, last_month_end, last_month_start.strftime("%B %Y")))
        for years_back in (1, 2, 3, 5, 10):
            try:
                start = (today - datetime.timedelta(days=3)).replace(year=today.year - years_back)
                end = (today + datetime.timedelta(days=4)).replace(year=today.year - years_back)
            except ValueError:
                continue
            title = f"This Week, {today.year - years_back}"
            result.extend(self._range_memory(f"week-{years_back}", title, start, end, f"{start.strftime('%-d %b')} to {(end - datetime.timedelta(days=1)).strftime('%-d %b %Y')}", minimum=4))
        for year in (today.year - 1, today.year - 2):
            start = datetime.date(year, 1, 1)
            end = datetime.date(year + 1, 1, 1)
            filters = SearchFilters(date_from=self._timestamp(start), date_to=self._timestamp(end), favorites_only=True)
            count, cover_id = self._favorite_summary(filters)
            if count >= 3:
                spec = ViewSpec("search", grouping="months", title=f"Best of {year}", filters=filters)
                result.append(Memory(f"best-{year}", f"Best of {year}", f"{count} favorites", spec, cover_id, count))
        return result

    def _timestamp(self, date_value):
        return datetime.datetime.combine(date_value, datetime.time()).timestamp()

    def _range_memory(self, key, title, start, end, subtitle, minimum=6):
        start_timestamp, end_timestamp = self._timestamp(start), self._timestamp(end)
        row = self.database.query_one(
            f"SELECT COUNT(*), id, {COVER_SCORE} FROM media WHERE {VISIBLE_FILTER} AND sort_date >= ? AND sort_date < ?",
            (start_timestamp, end_timestamp),
        )
        if not row or row[0] < minimum:
            return []
        filters = SearchFilters(date_from=start_timestamp, date_to=end_timestamp)
        spec = ViewSpec("search", grouping="days", title=title, filters=filters)
        return [Memory(key, title, subtitle, spec, row[1], row[0])]

    def _favorite_summary(self, filters):
        row = self.database.query_one(
            f"SELECT COUNT(*), id, {COVER_SCORE} FROM media WHERE {VISIBLE_FILTER} AND favorite = 1 AND sort_date >= ? AND sort_date < ?",
            (filters.date_from, filters.date_to),
        )
        return (row[0], row[1]) if row else (0, None)

    def cameras(self):
        rows = self.database.query(
            f"SELECT DISTINCT TRIM(COALESCE(camera_make, '') || ' ' || COALESCE(camera_model, '')) AS camera FROM media WHERE {VISIBLE_FILTER} AND camera_model IS NOT NULL ORDER BY camera"
        )
        return [row[0] for row in rows if row[0]]

    def available_years(self):
        return [entry.year for entry in self.years()]

    def section_counts(self):
        row = self.database.query_one(
            f"""
            SELECT COUNT(*), SUM(favorite = 1), SUM(kind = 1), SUM((flags & 1) != 0), SUM((flags & 2) != 0), SUM(faces > 0)
            FROM media WHERE {VISIBLE_FILTER}
            """
        )
        hidden = self.database.scalar("SELECT COUNT(*) FROM media WHERE hidden = 1 AND trashed_at IS NULL AND ignored = 0", default=0)
        keys = ("all", "favorites", "videos", "screenshots", "panoramas", "people")
        counts = {key: (value or 0) for key, value in zip(keys, row or ())}
        counts["hidden"] = hidden
        counts["trash"] = self.trash_count()
        return counts

    def add_search_history(self, query):
        query = query.strip()
        if not query or not self.settings.get("remember-search-history"):
            return
        self.database.execute("INSERT INTO search_history(query, used_at) VALUES (?, ?) ON CONFLICT(query) DO UPDATE SET used_at = excluded.used_at", (query, now()))
        self.database.execute("DELETE FROM search_history WHERE query NOT IN (SELECT query FROM search_history ORDER BY used_at DESC LIMIT 30)")

    def search_history(self, limit=8):
        return [row[0] for row in self.database.query("SELECT query FROM search_history ORDER BY used_at DESC LIMIT ?", (limit,))]

    def clear_search_history(self):
        self.database.execute("DELETE FROM search_history")

    def search_suggestions(self, prefix, limit=8):
        prefix = prefix.strip()
        if len(prefix) < 2:
            return []
        like = f"{prefix}%"
        contains = f"%{prefix}%"
        suggestions = []
        for (name,) in self.database.query("SELECT name FROM albums WHERE name LIKE ? ORDER BY name LIMIT 3", (contains,)):
            suggestions.append(("albums", name))
        for camera in [camera for camera in self.cameras() if prefix.lower() in camera.lower()][:3]:
            suggestions.append(("camera", camera))
        for (folder,) in self.database.query(
            f"SELECT DISTINCT folder FROM media WHERE {VISIBLE_FILTER} AND folder LIKE ? ORDER BY length(folder) LIMIT 3", (contains,)
        ):
            suggestions.append(("folders", os.path.basename(folder) or folder))
        for (year,) in self.database.query(
            f"SELECT DISTINCT strftime('%Y', sort_date, 'unixepoch', 'localtime') AS year FROM media WHERE {VISIBLE_FILTER} AND year LIKE ? ORDER BY year DESC LIMIT 3",
            (like,),
        ):
            suggestions.append(("years", year))
        return suggestions[:limit]

    def storage_report(self):
        drives = []
        root_rows = self.database.query(
            f"""
            SELECT roots.path, roots.label, roots.removable, roots.online, COALESCE(SUM(media.size), 0), COUNT(media.id)
            FROM roots LEFT JOIN media ON media.root = roots.path AND media.trashed_at IS NULL AND media.ignored = 0
            GROUP BY roots.path ORDER BY roots.online DESC, roots.path
            """
        )
        for path, label, removable, online, media_bytes, media_count in root_rows:
            total_bytes = free_bytes = 0
            if online:
                try:
                    statistics = os.statvfs(path)
                    total_bytes = statistics.f_blocks * statistics.f_frsize
                    free_bytes = statistics.f_bavail * statistics.f_frsize
                except OSError:
                    pass
            if media_count or online:
                drives.append(DriveUsage(path, label, bool(removable), bool(online), media_bytes, media_count, total_bytes, free_bytes))
        years = self.database.query(
            f"""
            SELECT CAST(strftime('%Y', sort_date, 'unixepoch', 'localtime') AS INTEGER) AS year, SUM(size), COUNT(*)
            FROM media WHERE {VISIBLE_FILTER} GROUP BY year ORDER BY year DESC
            """
        )
        categories = {}
        for extension, size, count in self.database.query(f"SELECT extension, SUM(size), COUNT(*) FROM media WHERE {VISIBLE_FILTER} GROUP BY extension"):
            category = type_category(extension)
            total_size, total_count = categories.get(category, (0, 0))
            categories[category] = (total_size + (size or 0), total_count + count)
        category_rows = sorted(((name, size, count) for name, (size, count) in categories.items()), key=lambda row: row[1], reverse=True)
        total_bytes = sum(row[1] for row in category_rows)
        total_count = sum(row[2] for row in category_rows)
        return StorageReport(drives, [row for row in years if row[0]], category_rows, total_bytes, total_count)

    def cache_size(self):
        return paths.directory_size(paths.cache_directory() / "thumbnails")

    def clear_thumbnail_cache(self, on_done=None):
        def work():
            shutil.rmtree(paths.cache_directory() / "thumbnails", ignore_errors=True)
            reset_directory_cache()
            self.database.execute("UPDATE media SET thumbnail_state = 0, placeholder = NULL")

        def finished(_result, _error):
            self.thumbnails.clear_memory()
            self.store.invalidate()
            self.emit("items-updated", None)
            self.thumbnails.wake_background()
            if on_done is not None:
                on_done()

        run_in_thread(work, finished, name="picnest-clear-cache")

    def reset_library(self, on_done=None):
        self.scanner.cancel()

        def work():
            with self.database.transaction() as connection:
                connection.execute("DELETE FROM media WHERE trashed_at IS NULL AND favorite = 0 AND hidden = 0 AND id NOT IN (SELECT media_id FROM album_items)")

        def finished(_result, _error):
            self.store.invalidate()
            self._schedule_content_refresh("reset", immediate=True)
            self.scan()
            if on_done is not None:
                on_done()

        run_in_thread(work, finished, name="picnest-reset")

    def start_duplicate_scan(self):
        if self._duplicate_thread is not None and self._duplicate_thread.is_alive():
            return
        self._duplicate_cancel.clear()

        def progress(done, total):
            GLib.idle_add(lambda: self.emit("duplicates-progress", done, total, False) and False)

        def work():
            return duplicates.find_duplicates(self.database, progress, self._duplicate_cancel.is_set)

        def finished(total, _error):
            self._duplicate_thread = None
            self.emit("duplicates-progress", total or 0, total or 0, True)
            self._schedule_content_refresh("duplicates", immediate=True)

        self._duplicate_thread = run_in_thread(work, finished, name="picnest-duplicates")

    def duplicate_extras(self):
        return duplicates.duplicate_selection(self.database)

    def faces_available(self):
        return faces.is_available()

    def start_face_detection(self):
        return self.face_detector.start()

    def stop_face_detection(self):
        self.face_detector.stop()

    def _start_faces_once(self):
        self.face_detector.start()
        return GLib.SOURCE_REMOVE

    def _on_faces_progress(self, processed, finished):
        self.emit("faces-progress", processed, finished)
        if finished:
            self._schedule_content_refresh("faces", immediate=True)
        return GLib.SOURCE_REMOVE

    def file_path_for(self, item):
        if item.is_trashed:
            return trash.trashed_file_path(item.trash_uri) or item.path
        return item.path
