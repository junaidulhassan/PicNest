import datetime
import time
from dataclasses import dataclass, field

from picnest.core import formatting
from picnest.core.media_types import RAW_IMAGE_EXTENSIONS, MediaKind
from picnest.core.models import GridContent, GridSection, MediaFlags

ONLINE_FILTER = "root IN (SELECT path FROM roots WHERE online = 1 AND enabled = 1)"
VISIBLE_FILTER = f"trashed_at IS NULL AND hidden = 0 AND ignored = 0 AND {ONLINE_FILTER}"

SORT_EXPRESSIONS = {
    "date-taken": "sort_date",
    "date-modified": "mtime",
    "name": "name COLLATE NOCASE",
    "size": "size",
    "type": "extension",
}

GROUP_EXPRESSIONS = {
    "days": "strftime('%Y-%m-%d', {column}, 'unixepoch', 'localtime')",
    "months": "strftime('%Y-%m', {column}, 'unixepoch', 'localtime')",
    "years": "strftime('%Y', {column}, 'unixepoch', 'localtime')",
}


@dataclass
class SearchFilters:
    text: str = ""
    media_type: str = "all"
    year: int | None = None
    date_from: float | None = None
    date_to: float | None = None
    extensions: tuple = ()
    camera: str = ""
    album_id: int | None = None
    has_location: bool = False
    location_text: str = ""
    minimum_size: int = 0
    maximum_size: int = 0
    minimum_megapixels: float = 0.0
    orientation: str = "any"
    favorites_only: bool = False

    def is_empty(self):
        return (
            not self.text.strip() and self.media_type == "all" and self.year is None and self.date_from is None
            and self.date_to is None and not self.extensions and not self.camera and self.album_id is None
            and not self.has_location and not self.location_text and not self.minimum_size
            and not self.maximum_size and not self.minimum_megapixels and self.orientation == "any"
            and not self.favorites_only
        )


@dataclass
class ViewSpec:
    collection: str
    grouping: str = "months"
    sort_field: str = "date-taken"
    descending: bool = True
    album_id: int | None = None
    folder: str | None = None
    year: int | None = None
    month_day: tuple | None = None
    ids: tuple | None = None
    title: str = ""
    filters: SearchFilters = field(default_factory=SearchFilters)

    def key(self):
        return f"{self.collection}:{self.album_id}:{self.folder}:{self.year}:{self.month_day}"


def _search_clauses(filters, clauses, parameters):
    text = filters.text.strip()
    for token in text.split():
        lowered = token.lower()
        if lowered in ("video", "videos"):
            clauses.append("kind = ?")
            parameters.append(int(MediaKind.VIDEO))
            continue
        if lowered in ("photo", "photos", "image", "images"):
            clauses.append("kind = ?")
            parameters.append(int(MediaKind.IMAGE))
            continue
        if lowered.isdigit() and len(lowered) == 4 and 1900 <= int(lowered) <= 2100:
            clauses.append("strftime('%Y', sort_date, 'unixepoch', 'localtime') = ?")
            parameters.append(lowered)
            continue
        like = f"%{token}%"
        clauses.append(
            "(name LIKE ? OR folder LIKE ? OR camera_make LIKE ? OR camera_model LIKE ? OR lens LIKE ? OR extension = ? "
            "OR id IN (SELECT media_id FROM album_items JOIN albums ON albums.id = album_items.album_id WHERE albums.name LIKE ?))"
        )
        parameters.extend([like, like, like, like, like, lowered.lstrip("."), like])
    if filters.favorites_only:
        clauses.append("favorite = 1")
    media_type = filters.media_type
    if media_type == "photos":
        clauses.append("kind = ?")
        parameters.append(int(MediaKind.IMAGE))
    elif media_type == "videos":
        clauses.append("kind = ?")
        parameters.append(int(MediaKind.VIDEO))
    elif media_type == "raw":
        placeholders = ",".join("?" * len(RAW_IMAGE_EXTENSIONS))
        clauses.append(f"extension IN ({placeholders})")
        parameters.extend(sorted(RAW_IMAGE_EXTENSIONS))
    elif media_type == "screenshots":
        clauses.append(f"(flags & {MediaFlags.SCREENSHOT}) != 0")
    elif media_type == "live":
        clauses.append(f"(flags & {MediaFlags.LIVE_PHOTO}) != 0")
    elif media_type == "panoramas":
        clauses.append(f"(flags & {MediaFlags.PANORAMA}) != 0")
    elif media_type == "animated":
        clauses.append(f"(flags & {MediaFlags.ANIMATED}) != 0")
    if filters.year is not None:
        clauses.append("strftime('%Y', sort_date, 'unixepoch', 'localtime') = ?")
        parameters.append(str(filters.year))
    if filters.date_from is not None:
        clauses.append("sort_date >= ?")
        parameters.append(filters.date_from)
    if filters.date_to is not None:
        clauses.append("sort_date < ?")
        parameters.append(filters.date_to)
    if filters.extensions:
        placeholders = ",".join("?" * len(filters.extensions))
        clauses.append(f"extension IN ({placeholders})")
        parameters.extend(filters.extensions)
    if filters.camera:
        clauses.append("(camera_make || ' ' || camera_model) LIKE ?")
        parameters.append(f"%{filters.camera}%")
    if filters.album_id is not None:
        clauses.append("id IN (SELECT media_id FROM album_items WHERE album_id = ?)")
        parameters.append(filters.album_id)
    if filters.has_location:
        clauses.append("latitude IS NOT NULL AND longitude IS NOT NULL")
    if filters.location_text:
        clauses.append("folder LIKE ?")
        parameters.append(f"%{filters.location_text}%")
    if filters.minimum_size:
        clauses.append("size >= ?")
        parameters.append(filters.minimum_size)
    if filters.maximum_size:
        clauses.append("size <= ?")
        parameters.append(filters.maximum_size)
    if filters.minimum_megapixels:
        clauses.append("width * height >= ?")
        parameters.append(int(filters.minimum_megapixels * 1_000_000))
    if filters.orientation == "landscape":
        clauses.append("width > height * 1.05")
    elif filters.orientation == "portrait":
        clauses.append("height > width * 1.05")
    elif filters.orientation == "square":
        clauses.append("width > 0 AND ABS(width - height) <= width * 0.05")


def build_where(spec):
    clauses = []
    parameters = []
    collection = spec.collection
    if collection == "trash":
        clauses.append(f"trashed_at IS NOT NULL AND {ONLINE_FILTER}")
    elif collection == "hidden":
        clauses.append(f"trashed_at IS NULL AND hidden = 1 AND ignored = 0 AND {ONLINE_FILTER}")
    else:
        clauses.append(VISIBLE_FILTER)
    if collection == "favorites":
        clauses.append("favorite = 1")
    elif collection == "videos":
        clauses.append("kind = ?")
        parameters.append(int(MediaKind.VIDEO))
    elif collection == "photos":
        clauses.append("kind = ?")
        parameters.append(int(MediaKind.IMAGE))
    elif collection == "screenshots":
        clauses.append(f"(flags & {MediaFlags.SCREENSHOT}) != 0")
    elif collection == "panoramas":
        clauses.append(f"(flags & {MediaFlags.PANORAMA}) != 0")
    elif collection == "live":
        clauses.append(f"(flags & {MediaFlags.LIVE_PHOTO}) != 0")
    elif collection == "people":
        clauses.append("faces > 0")
    elif collection == "album" and spec.album_id is not None:
        clauses.append("id IN (SELECT media_id FROM album_items WHERE album_id = ?)")
        parameters.append(spec.album_id)
    elif collection == "folder" and spec.folder is not None:
        clauses.append("folder = ?")
        parameters.append(spec.folder)
    elif collection == "year" and spec.year is not None:
        start = datetime.datetime(spec.year, 1, 1).timestamp()
        end = datetime.datetime(spec.year + 1, 1, 1).timestamp()
        clauses.append("sort_date >= ? AND sort_date < ?")
        parameters.extend([start, end])
    elif collection == "memory" and spec.month_day is not None:
        clauses.append("strftime('%m-%d', sort_date, 'unixepoch', 'localtime') = ?")
        parameters.append(f"{spec.month_day[0]:02d}-{spec.month_day[1]:02d}")
        if spec.year is not None:
            clauses.append("strftime('%Y', sort_date, 'unixepoch', 'localtime') = ?")
            parameters.append(str(spec.year))
    if collection == "search":
        _search_clauses(spec.filters, clauses, parameters)
    if spec.ids is not None:
        placeholders = ",".join("?" * len(spec.ids)) or "NULL"
        clauses.append(f"id IN ({placeholders})")
        parameters.extend(spec.ids)
    return " AND ".join(f"({clause})" for clause in clauses), parameters


def _section_title(grouping, timestamp, key):
    if grouping == "days":
        return formatting.day_title(timestamp)
    if grouping == "months":
        return formatting.month_title(timestamp)
    if grouping == "years":
        return formatting.year_title(timestamp)
    return key


def _sections_from_rows(rows, grouping, kind_index=2):
    sections = []
    current_key = None
    start = 0
    photo_count = video_count = 0
    first_timestamp = None
    for index, row in enumerate(rows):
        key = row[1]
        if key != current_key:
            if current_key is not None:
                sections.append(
                    GridSection(current_key, _section_title(grouping, first_timestamp, current_key), formatting.item_count_label(photo_count, video_count), start, index - start, photos=photo_count, videos=video_count)
                )
            current_key = key
            start = index
            first_timestamp = row[3]
            photo_count = video_count = 0
        if row[kind_index] == MediaKind.VIDEO:
            video_count += 1
        else:
            photo_count += 1
    if current_key is not None:
        sections.append(
            GridSection(current_key, _section_title(grouping, first_timestamp, current_key), formatting.item_count_label(photo_count, video_count), start, len(rows) - start, photos=photo_count, videos=video_count)
        )
    return sections


def run_view_query(database, spec):
    if spec.collection == "recents":
        return _run_recents(database, spec)
    if spec.collection == "duplicates":
        return _run_duplicates(database, spec)
    where, parameters = build_where(spec)
    order_column = SORT_EXPRESSIONS.get(spec.sort_field, "sort_date")
    direction = "DESC" if spec.descending else "ASC"
    grouping = spec.grouping if spec.sort_field == "date-taken" else "none"
    if spec.collection == "trash":
        order_column, grouping, direction = "trashed_at", "none", "DESC"
    if spec.sort_field == "type":
        grouping = "type"
    if grouping in GROUP_EXPRESSIONS:
        group_expression = GROUP_EXPRESSIONS[grouping].format(column="sort_date")
    elif grouping == "type":
        group_expression = "extension"
    else:
        group_expression = "''"
    secondary = ", sort_date DESC" if order_column != "sort_date" else ""
    sql = (
        f"SELECT id, {group_expression}, kind, sort_date FROM media WHERE {where} "
        f"ORDER BY {order_column} {direction}{secondary}, id {direction}"
    )
    rows = database.query(sql, parameters)
    ids = [row[0] for row in rows]
    if grouping == "none":
        photos = sum(1 for row in rows if row[2] != MediaKind.VIDEO)
        videos = len(rows) - photos
        sections = [GridSection("all", spec.title, formatting.item_count_label(photos, videos), 0, len(rows), show_header=False, photos=photos, videos=videos)] if rows else []
    elif grouping == "type":
        sections = _sections_from_rows(rows, "type")
        for section in sections:
            section.title = section.key.upper() if section.key else "Unknown"
    else:
        sections = _sections_from_rows(rows, grouping)
    return GridContent(ids=ids, sections=sections, view_key=spec.key())


def _run_recents(database, spec):
    cutoff = time.time() - 60 * 86400
    viewed = database.query(
        f"SELECT id, kind FROM media WHERE {VISIBLE_FILTER} AND viewed_at IS NOT NULL AND viewed_at >= ? ORDER BY viewed_at DESC LIMIT 300",
        (cutoff,),
    )
    viewed_ids = [row[0] for row in viewed]
    viewed_set = set(viewed_ids)
    added = database.query(
        f"SELECT id, kind FROM media WHERE {VISIBLE_FILTER} AND added_at >= ? ORDER BY added_at DESC, sort_date DESC LIMIT 3000",
        (cutoff,),
    )
    added_ids = [row[0] for row in added if row[0] not in viewed_set]
    sections = []
    if viewed_ids:
        photos = sum(1 for row in viewed if row[1] != MediaKind.VIDEO)
        sections.append(GridSection("viewed", "Recently Viewed", formatting.item_count_label(photos, len(viewed) - photos), 0, len(viewed_ids), photos=photos, videos=len(viewed) - photos))
    if added_ids:
        added_kinds = {row[0]: row[1] for row in added}
        photos = sum(1 for item_id in added_ids if added_kinds[item_id] != MediaKind.VIDEO)
        sections.append(GridSection("added", "Recently Added", formatting.item_count_label(photos, len(added_ids) - photos), len(viewed_ids), len(added_ids), photos=photos, videos=len(added_ids) - photos))
    return GridContent(ids=viewed_ids + added_ids, sections=sections, view_key=spec.key())


def _run_duplicates(database, spec):
    rows = database.query(
        f"""
        SELECT id, content_hash, size FROM media
        WHERE {VISIBLE_FILTER} AND content_hash IS NOT NULL AND content_hash IN (
            SELECT content_hash FROM media WHERE {VISIBLE_FILTER} AND content_hash IS NOT NULL
            GROUP BY content_hash HAVING COUNT(*) > 1
        )
        ORDER BY size DESC, content_hash, sort_date ASC, id ASC
        """
    )
    ids = []
    sections = []
    current_hash = None
    start = 0
    for index, (item_id, content_hash, size) in enumerate(rows):
        if content_hash != current_hash:
            if current_hash is not None:
                count = index - start
                sections.append(GridSection(current_hash, f"{count} copies", f"{formatting.human_size(rows[start][2])} each, {formatting.human_size(rows[start][2] * (count - 1))} reclaimable", start, count))
            current_hash = content_hash
            start = index
        ids.append(item_id)
    if current_hash is not None:
        count = len(rows) - start
        sections.append(GridSection(current_hash, f"{count} copies", f"{formatting.human_size(rows[start][2])} each, {formatting.human_size(rows[start][2] * (count - 1))} reclaimable", start, count))
    return GridContent(ids=ids, sections=sections, view_key=spec.key())
