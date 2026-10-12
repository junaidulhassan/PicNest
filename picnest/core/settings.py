import logging
import shutil
import subprocess

from gi.repository import Gio, GLib

from picnest.config import APP_ID, SCHEMA_SOURCE_DIRECTORY
from picnest.core import paths

logger = logging.getLogger(__name__)


def _compile_local_schema():
    schema_file = SCHEMA_SOURCE_DIRECTORY / f"{APP_ID}.gschema.xml"
    compiler = shutil.which("glib-compile-schemas")
    if not schema_file.exists() or compiler is None:
        return None
    target_directory = paths.cache_directory() / "schemas"
    target_directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(schema_file, target_directory / schema_file.name)
    result = subprocess.run([compiler, str(target_directory)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.warning("Could not compile settings schema: %s", result.stderr.strip())
        return None
    return target_directory


def _load_schema():
    default_source = Gio.SettingsSchemaSource.get_default()
    if default_source is not None:
        schema = default_source.lookup(APP_ID, True)
        if schema is not None:
            return schema
    compiled_directory = _compile_local_schema()
    if compiled_directory is None:
        raise RuntimeError(f"The settings schema {APP_ID} is not installed")
    source = Gio.SettingsSchemaSource.new_from_directory(str(compiled_directory), default_source, False)
    return source.lookup(APP_ID, False)


class Settings:
    def __init__(self):
        schema = _load_schema()
        self._gsettings = Gio.Settings.new_full(schema, None, None)
        self._schema = schema

    def get(self, key):
        return self._gsettings.get_value(key).unpack()

    def set(self, key, value):
        variant_type = self._schema.get_key(key).get_value_type()
        self._gsettings.set_value(key, GLib.Variant(variant_type.dup_string(), value))

    def connect_changed(self, key, callback):
        return self._gsettings.connect(f"changed::{key}", lambda _settings, _key: callback(self.get(key)))

    def bind(self, key, target, property_name, flags=Gio.SettingsBindFlags.DEFAULT):
        self._gsettings.bind(key, target, property_name, flags)
