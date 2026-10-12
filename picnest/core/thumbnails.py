import itertools
import logging
import os
import queue
import threading
import time
from collections import OrderedDict

from gi.repository import Gdk, GdkPixbuf, GLib

from picnest.core import capabilities, paths
from picnest.core.media_types import MediaKind, is_raw
from picnest.core.models import ThumbnailState
from picnest.core.video_frames import grab_frame

logger = logging.getLogger(__name__)

TIER_SIZES = {"normal": 320, "large": 720}
SMALL_VARIANT_SIZE = 160
PLACEHOLDER_SIZE = 8
PRIORITY_VISIBLE = 0
PRIORITY_PREFETCH = 1
PRIORITY_BACKGROUND = 2
MEMORY_BUDGET_BYTES = 160 * 1024 * 1024


class ActivityMonitor:
    def __init__(self):
        self._busy_until = 0.0

    def mark_busy(self, seconds=0.35):
        self._busy_until = time.monotonic() + seconds

    def throttle(self):
        while time.monotonic() < self._busy_until:
            time.sleep(0.03)


activity = ActivityMonitor()


_tier_directories = {}


def _tier_directory(tier):
    directory = _tier_directories.get(tier)
    if directory is None:
        directory = paths.thumbnail_directory(tier)
        _tier_directories[tier] = directory
    return directory


def reset_directory_cache():
    _tier_directories.clear()


def thumbnail_path(item_id, mtime, tier, state):
    extension = "png" if state == ThumbnailState.PNG else "jpg"
    return _tier_directory(tier) / f"{item_id % 256:02x}" / f"{item_id}-{int(mtime)}.{extension}"


def encode_placeholder(pixbuf):
    width, height = pixbuf.get_width(), pixbuf.get_height()
    if width <= 0 or height <= 0:
        return None
    scale = PLACEHOLDER_SIZE / max(width, height)
    tiny_width = max(1, round(width * scale))
    tiny_height = max(1, round(height * scale))
    tiny = pixbuf.scale_simple(tiny_width, tiny_height, GdkPixbuf.InterpType.BILINEAR)
    if tiny.get_has_alpha():
        background = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, tiny_width, tiny_height)
        background.fill(0x80808000)
        tiny.composite(background, 0, 0, tiny_width, tiny_height, 0, 0, 1, 1, GdkPixbuf.InterpType.NEAREST, 255)
        tiny = background
    pixels = tiny.get_pixels()
    stride = tiny.get_rowstride()
    channels = tiny.get_n_channels()
    rows = bytearray()
    for row in range(tiny_height):
        start = row * stride
        line = pixels[start : start + tiny_width * channels]
        if channels == 4:
            line = bytes(value for index, value in enumerate(line) if index % 4 != 3)
        rows.extend(line)
    return bytes([tiny_width, tiny_height]) + bytes(rows)


def decode_placeholder(blob):
    if not blob or len(blob) < 3:
        return None
    width, height = blob[0], blob[1]
    pixel_bytes = blob[2:]
    if len(pixel_bytes) != width * height * 3:
        return None
    return Gdk.MemoryTexture.new(width, height, Gdk.MemoryFormat.R8G8B8, GLib.Bytes.new(pixel_bytes), width * 3)


def _pixbuf_from_pillow(image):
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    has_alpha = image.mode == "RGBA"
    width, height = image.size
    data = image.tobytes()
    stride = width * (4 if has_alpha else 3)
    return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, has_alpha, 8, width, height, stride)


def load_with_pillow(path, size):
    image_module = capabilities.pillow()
    if image_module is None:
        return None
    try:
        with image_module.open(path) as image:
            image.draft("RGB", (size * 2, size * 2))
            image = capabilities.pillow_ops().exif_transpose(image)
            image.thumbnail((size, size))
            return _pixbuf_from_pillow(image)
    except Exception as error:
        logger.debug("Pillow thumbnail failed for %s: %s", path, error)
        return None


def load_raw_preview(path, size):
    module = capabilities.gexiv2()
    if module is None:
        return None
    try:
        reader = module.Metadata()
        reader.open_path(path)
        previews = reader.get_preview_properties() or []
        if not previews:
            return None
        best = max(previews, key=lambda preview: preview.get_width() * preview.get_height())
        getter = getattr(reader, "try_get_preview_image", None) or reader.get_preview_image
        data = getter(best).get_data()
        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        pixbuf = loader.get_pixbuf()
        if pixbuf is None:
            return None
        orientation_getter = getattr(reader, "try_get_orientation", None) or reader.get_orientation
        orientation = int(orientation_getter())
        pixbuf = _apply_orientation(pixbuf, orientation)
        return _scale_to_fit(pixbuf, size)
    except Exception as error:
        logger.debug("RAW preview failed for %s: %s", path, error)
        return None


def _apply_orientation(pixbuf, orientation):
    rotations = {
        2: (False, True, 0), 3: (False, False, 180), 4: (True, False, 0),
        5: (False, True, 90), 6: (False, False, 270), 7: (False, True, 270), 8: (False, False, 90),
    }
    vertical_flip, horizontal_flip, angle = rotations.get(orientation, (False, False, 0))
    if horizontal_flip:
        pixbuf = pixbuf.flip(True)
    if vertical_flip:
        pixbuf = pixbuf.flip(False)
    if angle:
        pixbuf = pixbuf.rotate_simple(angle)
    return pixbuf


def _scale_to_fit(pixbuf, size):
    width, height = pixbuf.get_width(), pixbuf.get_height()
    if max(width, height) <= size:
        return pixbuf
    scale = size / max(width, height)
    return pixbuf.scale_simple(max(1, round(width * scale)), max(1, round(height * scale)), GdkPixbuf.InterpType.BILINEAR)


def render_source(path, kind, extension, size):
    if kind == MediaKind.VIDEO:
        pixbuf, _duration = grab_frame(path)
        return _scale_to_fit(pixbuf, size) if pixbuf is not None else None
    if is_raw(extension):
        pixbuf = load_raw_preview(path, size)
        if pixbuf is not None:
            return pixbuf
        return load_with_pillow(path, size)
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
        oriented = pixbuf.apply_embedded_orientation()
        return oriented if oriented is not None else pixbuf
    except GLib.Error:
        return load_with_pillow(path, size)


def write_thumbnail(pixbuf, target_path):
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = target_path.with_name(f".{target_path.name}.{threading.get_ident()}.tmp")
    if pixbuf.get_has_alpha():
        pixbuf.savev(str(temporary_path), "png", ["compression"], ["3"])
    else:
        pixbuf.savev(str(temporary_path), "jpeg", ["quality"], ["84"])
    os.replace(temporary_path, target_path)


class ThumbnailRequest:
    __slots__ = ("item_id", "path", "kind", "extension", "mtime", "state", "tier", "small", "callbacks", "priority")

    def __init__(self, item, tier, small, priority):
        self.item_id = item.id
        self.path = item.path
        self.kind = item.kind
        self.extension = item.extension
        self.mtime = item.mtime
        self.state = item.thumbnail_state
        self.tier = tier
        self.small = small
        self.callbacks = {}
        self.priority = priority


class BackgroundRow:
    __slots__ = ("id", "path", "kind", "extension", "mtime", "thumbnail_state")

    def __init__(self, row):
        self.id, self.path, self.kind, self.extension, self.mtime, self.thumbnail_state = row


class ThumbnailService:
    def __init__(self, database, on_generated, worker_count=3):
        self._database = database
        self._on_generated = on_generated
        self._queue = queue.PriorityQueue()
        self._pending = {}
        self._pending_lock = threading.Lock()
        self._sequence = itertools.count()
        self._token_counter = itertools.count(1)
        self._token_keys = {}
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._stopped = threading.Event()
        self._background_allowed = False
        self._background_wakeup = threading.Event()
        self._background_queued = set()
        self._result_lock = threading.Lock()
        self._results = []
        self._workers = [threading.Thread(target=self._worker_loop, name=f"picnest-thumbnail-{index}", daemon=True) for index in range(worker_count)]
        self._feeder = threading.Thread(target=self._background_feeder, name="picnest-thumbnail-feeder", daemon=True)
        self._flusher = threading.Thread(target=self._flush_loop, name="picnest-thumbnail-writer", daemon=True)

    def start(self):
        for worker in self._workers:
            worker.start()
        self._feeder.start()
        self._flusher.start()

    def stop(self):
        self._stopped.set()
        self._background_wakeup.set()
        for _ in self._workers:
            self._queue.put((-1, 0, None))

    def set_background_enabled(self, enabled):
        self._background_allowed = enabled
        if enabled:
            self._background_wakeup.set()

    def wake_background(self):
        self._background_wakeup.set()

    def cached(self, item, small):
        key = (item.id, int(item.mtime), "small" if small else "normal")
        texture = self._cache.get(key)
        if texture is not None:
            self._cache.move_to_end(key)
        return texture

    def request(self, item, callback, small=False, tier="normal", priority=PRIORITY_VISIBLE):
        variant = "small" if small and tier == "normal" else tier
        key = (item.id, int(item.mtime), variant)
        texture = self._cache.get(key)
        if texture is not None:
            self._cache.move_to_end(key)
            callback(texture)
            return 0
        token = next(self._token_counter)
        with self._pending_lock:
            request = self._pending.get(key)
            if request is None:
                request = ThumbnailRequest(item, tier, variant == "small", priority)
                self._pending[key] = request
                self._queue.put((priority, -next(self._sequence), key))
            elif priority < request.priority:
                request.priority = priority
                self._queue.put((priority, -next(self._sequence), key))
            request.callbacks[token] = callback
            self._token_keys[token] = key
        return token

    def cancel(self, token):
        if not token:
            return
        with self._pending_lock:
            key = self._token_keys.pop(token, None)
            request = self._pending.get(key) if key is not None else None
            if request is not None:
                request.callbacks.pop(token, None)

    def forget(self, item_id):
        for key in [key for key in self._cache if key[0] == item_id]:
            texture = self._cache.pop(key)
            self._cache_bytes -= texture.get_width() * texture.get_height() * 4

    def clear_memory(self):
        self._cache.clear()
        self._cache_bytes = 0

    def _remember(self, key, texture):
        if key in self._cache:
            return
        self._cache[key] = texture
        self._cache_bytes += texture.get_width() * texture.get_height() * 4
        while self._cache_bytes > MEMORY_BUDGET_BYTES and self._cache:
            _old_key, old_texture = self._cache.popitem(last=False)
            self._cache_bytes -= old_texture.get_width() * old_texture.get_height() * 4

    def _worker_loop(self):
        while not self._stopped.is_set():
            priority, _sequence, key = self._queue.get()
            if key is None:
                break
            if priority == PRIORITY_BACKGROUND:
                activity.throttle()
                self._process_background(key)
                continue
            with self._pending_lock:
                request = self._pending.get(key)
                if request is None or request.priority != priority:
                    continue
                if not request.callbacks:
                    del self._pending[key]
                    continue
            texture = self._produce_texture(request)
            GLib.idle_add(self._deliver, key, texture, priority=GLib.PRIORITY_DEFAULT_IDLE)

    def _produce_texture(self, request):
        size = TIER_SIZES[request.tier]
        state = request.state
        if state == ThumbnailState.FAILED and request.tier == "normal":
            return None
        candidate_states = (ThumbnailState.PNG, ThumbnailState.JPEG) if state == ThumbnailState.PNG else (ThumbnailState.JPEG, ThumbnailState.PNG)
        for candidate_state in candidate_states:
            candidate = thumbnail_path(request.item_id, request.mtime, request.tier, candidate_state)
            if candidate.exists():
                texture = self._load_texture(candidate, request.small)
                if texture is not None:
                    return texture
        pixbuf = render_source(request.path, request.kind, request.extension, size)
        if pixbuf is None:
            if request.tier == "normal":
                self._record_result(request.item_id, ThumbnailState.FAILED, None)
            return None
        new_state = ThumbnailState.PNG if pixbuf.get_has_alpha() else ThumbnailState.JPEG
        target = thumbnail_path(request.item_id, request.mtime, request.tier, new_state)
        try:
            write_thumbnail(pixbuf, target)
        except GLib.Error as error:
            logger.warning("Cannot write thumbnail %s: %s", target, error.message)
        if request.tier == "normal":
            self._record_result(request.item_id, new_state, encode_placeholder(pixbuf))
        if request.small:
            pixbuf = _scale_to_fit(pixbuf, SMALL_VARIANT_SIZE)
        return Gdk.Texture.new_for_pixbuf(pixbuf)

    def _load_texture(self, target, small):
        try:
            if small:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(target), SMALL_VARIANT_SIZE, SMALL_VARIANT_SIZE, True)
                return Gdk.Texture.new_for_pixbuf(pixbuf)
            return Gdk.Texture.new_from_filename(str(target))
        except GLib.Error:
            return None

    def _deliver(self, key, texture):
        with self._pending_lock:
            request = self._pending.pop(key, None)
            if request is not None:
                for token in request.callbacks:
                    self._token_keys.pop(token, None)
        if request is None:
            return GLib.SOURCE_REMOVE
        if texture is not None:
            self._remember(key, texture)
        for callback in list(request.callbacks.values()):
            try:
                callback(texture)
            except Exception:
                logger.exception("Thumbnail callback failed")
        return GLib.SOURCE_REMOVE

    def _record_result(self, item_id, state, placeholder):
        with self._result_lock:
            self._results.append((item_id, state, placeholder))

    def _flush_loop(self):
        while not self._stopped.wait(0.6):
            self._flush_results()

    def _flush_results(self):
        with self._result_lock:
            results, self._results = self._results, []
        if not results:
            return
        try:
            with self._database.transaction() as connection:
                connection.executemany(
                    "UPDATE media SET thumbnail_state = ?, placeholder = COALESCE(?, placeholder) WHERE id = ?",
                    [(state, placeholder, item_id) for item_id, state, placeholder in results],
                )
        except Exception:
            logger.exception("Cannot store thumbnail states")
            return
        for item_id, _state, _placeholder in results:
            self._background_queued.discard(item_id)
        GLib.idle_add(self._on_generated, results)

    def _process_background(self, key):
        row = key[3]
        try:
            pixbuf = render_source(row.path, row.kind, row.extension, TIER_SIZES["normal"])
            if pixbuf is None:
                self._record_result(row.id, ThumbnailState.FAILED, None)
                return
            state = ThumbnailState.PNG if pixbuf.get_has_alpha() else ThumbnailState.JPEG
            write_thumbnail(pixbuf, thumbnail_path(row.id, row.mtime, "normal", state))
            self._record_result(row.id, state, encode_placeholder(pixbuf))
        except Exception as error:
            logger.debug("Background thumbnail failed for %s: %s", row.path, error)
            self._record_result(row.id, ThumbnailState.FAILED, None)

    def _background_feeder(self):
        while not self._stopped.is_set():
            if not self._background_allowed:
                self._background_wakeup.wait()
                self._background_wakeup.clear()
                continue
            if self._queue.qsize() > 16 or len(self._background_queued) > 48:
                time.sleep(0.25)
                continue
            try:
                rows = self._database.query(
                    """
                    SELECT id, path, kind, extension, mtime, thumbnail_state FROM media
                    WHERE thumbnail_state = 0 AND ignored = 0 AND trashed_at IS NULL
                    AND root IN (SELECT path FROM roots WHERE online = 1 AND enabled = 1)
                    ORDER BY sort_date DESC LIMIT 160
                    """
                )
            except Exception:
                logger.exception("Background thumbnail query failed")
                time.sleep(2)
                continue
            fresh = [BackgroundRow(row) for row in rows if row[0] not in self._background_queued]
            if not fresh:
                if self._background_queued:
                    time.sleep(0.5)
                    continue
                self._background_wakeup.wait(60)
                self._background_wakeup.clear()
                continue
            for row in fresh[:32]:
                self._background_queued.add(row.id)
                self._queue.put((PRIORITY_BACKGROUND, next(self._sequence), (row.id, int(row.mtime), "background", row)))
            time.sleep(0.05)
