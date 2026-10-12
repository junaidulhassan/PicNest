import logging
import queue
import threading

from gi.repository import GLib

logger = logging.getLogger(__name__)


def run_in_thread(work, on_done=None, name="picnest-task"):
    def runner():
        try:
            result = work()
            error = None
        except Exception as caught:
            logger.exception("Background task %s failed", name)
            result, error = None, caught
        if on_done is not None:
            GLib.idle_add(_finish, on_done, result, error)

    thread = threading.Thread(target=runner, name=name, daemon=True)
    thread.start()
    return thread


def _finish(on_done, result, error):
    on_done(result, error)
    return GLib.SOURCE_REMOVE


class QueryExecutor:
    def __init__(self, database):
        self._database = database
        self._queue = queue.Queue()
        self._generations = {}
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, name="picnest-query", daemon=True)
        self._thread.start()

    def submit(self, owner, work, callback):
        with self._lock:
            generation = self._generations.get(owner, 0) + 1
            self._generations[owner] = generation
        self._queue.put((owner, generation, work, callback))

    def stop(self):
        self._queue.put(None)

    def _is_current(self, owner, generation):
        with self._lock:
            return self._generations.get(owner) == generation

    def _loop(self):
        while True:
            task = self._queue.get()
            if task is None:
                break
            owner, generation, work, callback = task
            if not self._is_current(owner, generation):
                continue
            try:
                result = work(self._database)
            except Exception:
                logger.exception("Query failed")
                result = None
            GLib.idle_add(self._deliver, owner, generation, callback, result)

    def _deliver(self, owner, generation, callback, result):
        if self._is_current(owner, generation):
            callback(result)
        return GLib.SOURCE_REMOVE
