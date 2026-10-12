import logging
import os
import threading

from gi.repository import GLib

from picnest.core import capabilities
from picnest.core.media_types import MediaKind
from picnest.core.models import ThumbnailState
from picnest.core.thumbnails import activity, thumbnail_path

logger = logging.getLogger(__name__)

CASCADE_LOCATIONS = (
    "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
    "/usr/share/opencv/haarcascades/haarcascade_frontalface_default.xml",
)


def is_available():
    return capabilities.opencv() is not None and _cascade_path() is not None


def _cascade_path():
    cv2 = capabilities.opencv()
    candidates = list(CASCADE_LOCATIONS)
    if cv2 is not None and hasattr(cv2, "data"):
        candidates.insert(0, os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


class FaceDetector:
    def __init__(self, database, on_progress):
        self._database = database
        self._on_progress = on_progress
        self._stop = threading.Event()
        self._thread = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running or not is_available():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="picnest-faces", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()

    def _run(self):
        cv2 = capabilities.opencv()
        classifier = cv2.CascadeClassifier(_cascade_path())
        processed = 0
        while not self._stop.is_set():
            rows = self._database.query(
                "SELECT id, mtime, thumbnail_state FROM media WHERE faces = -1 AND kind = ? AND thumbnail_state IN (1, 2) "
                "AND trashed_at IS NULL AND ignored = 0 LIMIT 100",
                (int(MediaKind.IMAGE),),
            )
            if not rows:
                break
            results = []
            for item_id, mtime, state in rows:
                if self._stop.is_set():
                    break
                activity.throttle()
                path = thumbnail_path(item_id, mtime, "normal", ThumbnailState(state))
                count = 0
                image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                if image is not None:
                    image = cv2.equalizeHist(image)
                    faces = classifier.detectMultiScale(image, scaleFactor=1.1, minNeighbors=6, minSize=(24, 24))
                    count = len(faces)
                results.append((count, item_id))
            with self._database.transaction() as connection:
                connection.executemany("UPDATE media SET faces = ? WHERE id = ?", results)
            processed += len(results)
            GLib.idle_add(self._on_progress, processed, False)
        GLib.idle_add(self._on_progress, processed, True)
