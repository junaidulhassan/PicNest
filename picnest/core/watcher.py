import logging
import os

from gi.repository import Gio, GLib

from picnest.core.media_types import MEDIA_EXTENSIONS, extension_of

logger = logging.getLogger(__name__)

DEBOUNCE_MILLISECONDS = 1500
DEFAULT_WATCH_LIMIT = 6000


def _watch_limit():
    try:
        with open("/proc/sys/fs/inotify/max_user_watches", encoding="ascii") as handle:
            system_limit = int(handle.read().strip())
        return max(256, min(DEFAULT_WATCH_LIMIT, system_limit // 4))
    except (OSError, ValueError):
        return 2048


class DirectoryWatcher:
    def __init__(self, on_file_ready, on_path_removed, on_directory_added):
        self._on_file_ready = on_file_ready
        self._on_path_removed = on_path_removed
        self._on_directory_added = on_directory_added
        self._monitors = {}
        self._pending = {}
        self._limit = _watch_limit()
        self._flush_source = 0

    def watch_many(self, directories):
        for directory in directories:
            if len(self._monitors) >= self._limit:
                logger.info("Directory watch limit of %d reached", self._limit)
                break
            self.watch(directory)

    def watch(self, directory):
        if directory in self._monitors or len(self._monitors) >= self._limit:
            return
        try:
            monitor = Gio.File.new_for_path(directory).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except GLib.Error as error:
            logger.debug("Cannot watch %s: %s", directory, error.message)
            return
        monitor.set_rate_limit(800)
        monitor.connect("changed", self._on_changed)
        self._monitors[directory] = monitor

    def unwatch_prefix(self, prefix):
        normalized = prefix.rstrip("/") + "/"
        for directory in [directory for directory in self._monitors if directory == prefix or directory.startswith(normalized)]:
            self._monitors.pop(directory).cancel()

    def clear(self):
        for monitor in self._monitors.values():
            monitor.cancel()
        self._monitors.clear()

    def _schedule(self, path, action):
        self._pending[path] = action
        if self._flush_source:
            GLib.source_remove(self._flush_source)
        self._flush_source = GLib.timeout_add(DEBOUNCE_MILLISECONDS, self._flush)

    def _flush(self):
        self._flush_source = 0
        pending, self._pending = self._pending, {}
        for path, action in pending.items():
            if action == "ready":
                if os.path.isdir(path):
                    self._on_directory_added(path)
                elif os.path.isfile(path):
                    self._on_file_ready(path)
            elif action == "removed":
                if path in self._monitors:
                    self._monitors.pop(path).cancel()
                self._on_path_removed(path)
        return GLib.SOURCE_REMOVE

    def _is_relevant(self, path):
        return extension_of(path) in MEDIA_EXTENSIONS or os.path.isdir(path)

    def _on_changed(self, _monitor, file, other_file, event_type):
        path = file.get_path()
        if path is None:
            return
        name = os.path.basename(path)
        if event_type != Gio.FileMonitorEvent.RENAMED and (name.startswith(".") or name.endswith((".tmp", ".part", ".crdownload"))):
            return
        if event_type in (Gio.FileMonitorEvent.CREATED, Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.MOVED_IN):
            if self._is_relevant(path):
                self._schedule(path, "ready")
        elif event_type in (Gio.FileMonitorEvent.DELETED, Gio.FileMonitorEvent.MOVED_OUT):
            if extension_of(path) in MEDIA_EXTENSIONS or path in self._monitors or "." not in name:
                self._schedule(path, "removed")
        elif event_type == Gio.FileMonitorEvent.RENAMED and other_file is not None:
            self._schedule(path, "removed")
            new_path = other_file.get_path()
            if new_path and self._is_relevant(new_path):
                self._schedule(new_path, "ready")
