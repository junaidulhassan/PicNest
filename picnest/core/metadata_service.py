import logging
import queue
import threading

from gi.repository import GLib

from picnest.core import metadata
from picnest.core.thumbnails import activity

logger = logging.getLogger(__name__)

UPDATABLE_COLUMNS = (
    "width", "height", "duration", "taken_at", "sort_date", "camera_make", "camera_model", "lens",
    "iso", "exposure", "aperture", "focal_length", "latitude", "longitude", "altitude", "flags",
    "ignored", "metadata_state",
)


class MetadataService:
    def __init__(self, database, on_updated, worker_count=2):
        self._database = database
        self._on_updated = on_updated
        self._work = queue.Queue(maxsize=256)
        self._wakeup = threading.Event()
        self._stopped = threading.Event()
        self._in_flight = set()
        self._in_flight_lock = threading.Lock()
        self._results = []
        self._results_lock = threading.Lock()
        self._workers = [threading.Thread(target=self._worker_loop, name=f"picnest-metadata-{index}", daemon=True) for index in range(worker_count)]
        self._feeder = threading.Thread(target=self._feeder_loop, name="picnest-metadata-feeder", daemon=True)
        self._writer = threading.Thread(target=self._writer_loop, name="picnest-metadata-writer", daemon=True)

    def start(self):
        for worker in self._workers:
            worker.start()
        self._feeder.start()
        self._writer.start()
        self._wakeup.set()

    def stop(self):
        self._stopped.set()
        self._wakeup.set()
        for _ in self._workers:
            try:
                self._work.put_nowait(None)
            except queue.Full:
                pass

    def wake(self):
        self._wakeup.set()

    def _feeder_loop(self):
        while not self._stopped.is_set():
            self._wakeup.wait()
            self._wakeup.clear()
            while not self._stopped.is_set():
                try:
                    rows = self._database.query(
                        """
                        SELECT id, path, kind, extension, sort_date, flags FROM media
                        WHERE metadata_state = 0 AND trashed_at IS NULL
                        AND root IN (SELECT path FROM roots WHERE online = 1 AND enabled = 1)
                        ORDER BY sort_date DESC LIMIT 200
                        """
                    )
                except Exception:
                    logger.exception("Metadata query failed")
                    break
                with self._in_flight_lock:
                    fresh = [row for row in rows if row[0] not in self._in_flight]
                    self._in_flight.update(row[0] for row in fresh)
                if not fresh:
                    if rows:
                        self._stopped.wait(0.5)
                        continue
                    break
                for row in fresh:
                    if self._stopped.is_set():
                        return
                    self._work.put(row)

    def _worker_loop(self):
        while not self._stopped.is_set():
            row = self._work.get()
            if row is None:
                break
            activity.throttle()
            try:
                item_id, updates = metadata.extract(row)
            except Exception as error:
                logger.debug("Metadata extraction failed for %s: %s", row[1], error)
                item_id, updates = row[0], {"metadata_state": 2}
            with self._results_lock:
                self._results.append((item_id, updates))

    def _writer_loop(self):
        while not self._stopped.wait(0.8):
            self._flush()
        self._flush()

    def _flush(self):
        with self._results_lock:
            results, self._results = self._results, []
        if not results:
            return
        try:
            with self._database.transaction() as connection:
                for item_id, updates in results:
                    columns = [column for column in UPDATABLE_COLUMNS if column in updates]
                    assignments = ", ".join(f"{column} = ?" for column in columns)
                    connection.execute(f"UPDATE media SET {assignments} WHERE id = ?", [updates[column] for column in columns] + [item_id])
        except Exception:
            logger.exception("Cannot store metadata")
        finally:
            with self._in_flight_lock:
                for item_id, _updates in results:
                    self._in_flight.discard(item_id)
        GLib.idle_add(self._on_updated, [item_id for item_id, _updates in results])
