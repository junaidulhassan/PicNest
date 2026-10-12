from collections import OrderedDict
from dataclasses import dataclass, field

from picnest.core.database import ITEM_COLUMNS, ITEM_SELECT
from picnest.core.media_types import MediaKind


class MediaFlags:
    SCREENSHOT = 1
    PANORAMA = 2
    LIVE_PHOTO = 4
    ANIMATED = 8


class ThumbnailState:
    MISSING = 0
    JPEG = 1
    PNG = 2
    FAILED = 3


class MediaItem:
    __slots__ = ITEM_COLUMNS

    def __init__(self, row):
        for column, value in zip(ITEM_COLUMNS, row):
            setattr(self, column, value)

    @property
    def is_video(self):
        return self.kind == MediaKind.VIDEO

    @property
    def is_favorite(self):
        return bool(self.favorite)

    @property
    def is_trashed(self):
        return self.trashed_at is not None

    @property
    def is_live_photo(self):
        return bool(self.flags & MediaFlags.LIVE_PHOTO)

    @property
    def is_screenshot(self):
        return bool(self.flags & MediaFlags.SCREENSHOT)

    @property
    def is_animated(self):
        return bool(self.flags & MediaFlags.ANIMATED)

    @property
    def has_location(self):
        return self.latitude is not None and self.longitude is not None

    @property
    def megapixels(self):
        return (self.width or 0) * (self.height or 0) / 1_000_000

    @property
    def camera(self):
        make = (self.camera_make or "").strip()
        model = (self.camera_model or "").strip()
        if make and model.lower().startswith(make.lower().split(" ")[0]):
            return model
        return " ".join(part for part in (make, model) if part)

@dataclass
class GridSection:
    key: str
    title: str
    subtitle: str
    start: int
    count: int
    collapsed: bool = False
    show_header: bool = True
    photos: int = 0
    videos: int = 0


@dataclass
class GridContent:
    ids: list
    sections: list
    view_key: str = ""
    empty_reason: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def item_count(self):
        return len(self.ids)


@dataclass
class Album:
    id: int
    name: str
    created_at: float
    cover_id: int | None
    item_count: int


@dataclass
class FolderEntry:
    folder: str
    root: str
    item_count: int
    cover_id: int | None
    latest: float


@dataclass
class YearEntry:
    year: int
    item_count: int
    video_count: int
    cover_id: int | None


class ItemStore:
    def __init__(self, database, capacity=30000):
        self._database = database
        self._capacity = capacity
        self._items = OrderedDict()

    def get(self, item_id):
        item = self._items.get(item_id)
        if item is not None:
            self._items.move_to_end(item_id)
            return item
        row = self._database.query_one(f"SELECT {ITEM_SELECT} FROM media WHERE id = ?", (item_id,))
        if row is None:
            return None
        item = MediaItem(row)
        self._remember(item)
        return item

    def prefetch(self, item_ids):
        missing = [item_id for item_id in item_ids if item_id not in self._items]
        for offset in range(0, len(missing), 500):
            chunk = missing[offset : offset + 500]
            placeholders = ",".join("?" * len(chunk))
            for row in self._database.query(f"SELECT {ITEM_SELECT} FROM media WHERE id IN ({placeholders})", chunk):
                self._remember(MediaItem(row))

    def get_many(self, item_ids):
        self.prefetch(item_ids)
        return [item for item in (self.get(item_id) for item_id in item_ids) if item is not None]

    def invalidate(self, item_ids=None):
        if item_ids is None:
            self._items.clear()
            return
        for item_id in item_ids:
            self._items.pop(item_id, None)

    def update_fields(self, item_id, **fields):
        item = self._items.get(item_id)
        if item is None:
            return
        for name, value in fields.items():
            setattr(item, name, value)

    def _remember(self, item):
        self._items[item.id] = item
        if len(self._items) > self._capacity:
            for _ in range(len(self._items) - self._capacity):
                self._items.popitem(last=False)
