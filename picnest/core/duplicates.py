import hashlib
import logging

from picnest.core.query import VISIBLE_FILTER

logger = logging.getLogger(__name__)

SAMPLE_BYTES = 1024 * 1024


def content_fingerprint(path, size):
    digest = hashlib.blake2b(digest_size=16)
    digest.update(str(size).encode("ascii"))
    with open(path, "rb") as handle:
        if size <= SAMPLE_BYTES * 3:
            digest.update(handle.read())
        else:
            digest.update(handle.read(SAMPLE_BYTES))
            handle.seek(size // 2 - SAMPLE_BYTES // 2)
            digest.update(handle.read(SAMPLE_BYTES))
            handle.seek(size - SAMPLE_BYTES)
            digest.update(handle.read(SAMPLE_BYTES))
    return digest.hexdigest()


def find_duplicates(database, on_progress=None, should_cancel=None):
    rows = database.query(
        f"""
        SELECT id, path, size FROM media
        WHERE {VISIBLE_FILTER} AND content_hash IS NULL AND size IN (
            SELECT size FROM media WHERE {VISIBLE_FILTER} GROUP BY size HAVING COUNT(*) > 1
        )
        ORDER BY size DESC
        """
    )
    total = len(rows)
    results = []
    for index, (item_id, path, size) in enumerate(rows):
        if should_cancel is not None and should_cancel():
            break
        try:
            fingerprint = content_fingerprint(path, size)
        except OSError as error:
            logger.debug("Cannot fingerprint %s: %s", path, error)
            fingerprint = f"unreadable-{item_id}"
        results.append((fingerprint, item_id))
        if len(results) >= 200:
            _store(database, results)
            results.clear()
        if on_progress is not None and index % 25 == 0:
            on_progress(index + 1, total)
    _store(database, results)
    if on_progress is not None:
        on_progress(total, total)
    return total


def _store(database, results):
    if not results:
        return
    with database.transaction() as connection:
        connection.executemany("UPDATE media SET content_hash = ? WHERE id = ?", results)


def duplicate_selection(database):
    rows = database.query(
        f"""
        SELECT id, content_hash FROM media
        WHERE {VISIBLE_FILTER} AND content_hash IN (
            SELECT content_hash FROM media WHERE {VISIBLE_FILTER} AND content_hash IS NOT NULL
            GROUP BY content_hash HAVING COUNT(*) > 1
        )
        ORDER BY content_hash, favorite DESC, sort_date ASC, length(path) ASC, id ASC
        """
    )
    keep = set()
    extra = []
    for item_id, content_hash in rows:
        if content_hash in keep:
            extra.append(item_id)
        else:
            keep.add(content_hash)
    return extra
