import logging
import sqlite3
import threading
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS media (
        id INTEGER PRIMARY KEY,
        path TEXT NOT NULL UNIQUE,
        folder TEXT NOT NULL,
        name TEXT NOT NULL,
        root TEXT NOT NULL,
        kind INTEGER NOT NULL,
        extension TEXT NOT NULL,
        size INTEGER NOT NULL,
        mtime REAL NOT NULL,
        added_at REAL NOT NULL,
        taken_at REAL,
        sort_date REAL NOT NULL,
        width INTEGER NOT NULL DEFAULT 0,
        height INTEGER NOT NULL DEFAULT 0,
        duration REAL NOT NULL DEFAULT 0,
        camera_make TEXT,
        camera_model TEXT,
        lens TEXT,
        iso INTEGER,
        exposure TEXT,
        aperture REAL,
        focal_length REAL,
        latitude REAL,
        longitude REAL,
        altitude REAL,
        favorite INTEGER NOT NULL DEFAULT 0,
        hidden INTEGER NOT NULL DEFAULT 0,
        trashed_at REAL,
        trash_uri TEXT,
        viewed_at REAL,
        metadata_state INTEGER NOT NULL DEFAULT 0,
        thumbnail_state INTEGER NOT NULL DEFAULT 0,
        placeholder BLOB,
        ignored INTEGER NOT NULL DEFAULT 0,
        flags INTEGER NOT NULL DEFAULT 0,
        content_hash TEXT,
        faces INTEGER NOT NULL DEFAULT -1
    )
    """,
    "CREATE INDEX IF NOT EXISTS media_sort_date ON media(sort_date)",
    "CREATE INDEX IF NOT EXISTS media_folder ON media(folder)",
    "CREATE INDEX IF NOT EXISTS media_root ON media(root)",
    "CREATE INDEX IF NOT EXISTS media_size ON media(size)",
    "CREATE INDEX IF NOT EXISTS media_trashed ON media(trashed_at)",
    "CREATE INDEX IF NOT EXISTS media_metadata_state ON media(metadata_state)",
    "CREATE INDEX IF NOT EXISTS media_thumbnail_state ON media(thumbnail_state)",
    "CREATE INDEX IF NOT EXISTS media_viewed ON media(viewed_at)",
    "CREATE INDEX IF NOT EXISTS media_added ON media(added_at)",
    "CREATE INDEX IF NOT EXISTS media_hash ON media(content_hash)",
    """
    CREATE TABLE IF NOT EXISTS albums (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        created_at REAL NOT NULL,
        cover_id INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS album_items (
        album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
        media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
        added_at REAL NOT NULL,
        PRIMARY KEY (album_id, media_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS album_items_media ON album_items(media_id)",
    """
    CREATE TABLE IF NOT EXISTS roots (
        path TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        removable INTEGER NOT NULL DEFAULT 0,
        online INTEGER NOT NULL DEFAULT 1,
        enabled INTEGER NOT NULL DEFAULT 1,
        last_scan REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS search_history (
        query TEXT PRIMARY KEY,
        used_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
)

ITEM_COLUMNS = (
    "id", "path", "folder", "name", "root", "kind", "extension", "size", "mtime", "added_at",
    "taken_at", "sort_date", "width", "height", "duration", "camera_make", "camera_model", "lens",
    "iso", "exposure", "aperture", "focal_length", "latitude", "longitude", "altitude", "favorite",
    "hidden", "trashed_at", "trash_uri", "viewed_at", "metadata_state", "thumbnail_state",
    "placeholder", "flags", "content_hash", "faces",
)

ITEM_SELECT = ", ".join(ITEM_COLUMNS)


class Database:
    def __init__(self, path):
        self._path = str(path)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._initialize()

    def _open(self):
        connection = sqlite3.connect(self._path, timeout=30, isolation_level=None, check_same_thread=False)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA cache_size=-16000")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @property
    def connection(self):
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._open()
            self._local.connection = connection
        return connection

    def _initialize(self):
        with self.transaction() as connection:
            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement)
            columns = [row[1] for row in connection.execute("PRAGMA table_info(roots)").fetchall()]
            if "enabled" not in columns:
                connection.execute("ALTER TABLE roots ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
            connection.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def transaction(self):
        with self._write_lock:
            connection = self.connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")

    def execute(self, sql, parameters=()):
        with self._write_lock:
            return self.connection.execute(sql, parameters)

    def executemany(self, sql, rows):
        with self.transaction() as connection:
            connection.executemany(sql, rows)

    def query(self, sql, parameters=()):
        return self.connection.execute(sql, parameters).fetchall()

    def query_one(self, sql, parameters=()):
        return self.connection.execute(sql, parameters).fetchone()

    def scalar(self, sql, parameters=(), default=None):
        row = self.connection.execute(sql, parameters).fetchone()
        if row is None or row[0] is None:
            return default
        return row[0]

    def optimize(self):
        try:
            self.execute("PRAGMA optimize")
            self.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.Error as error:
            logger.debug("Database optimize failed: %s", error)


def now():
    return time.time()
