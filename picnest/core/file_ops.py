import logging
import os
import shutil
import subprocess

from gi.repository import Gio, GLib

from picnest.core import capabilities
from picnest.core.image_loader import load_pillow_image

logger = logging.getLogger(__name__)

INVALID_NAME_CHARACTERS = ("/", "\0")


class FileOperationError(Exception):
    pass


def unique_path(directory, name):
    candidate = os.path.join(directory, name)
    if not os.path.exists(candidate):
        return candidate
    stem, extension = os.path.splitext(name)
    counter = 1
    while True:
        candidate = os.path.join(directory, f"{stem} ({counter}){extension}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def validate_name(name):
    stripped = name.strip()
    if not stripped or stripped in (".", ".."):
        raise FileOperationError("The name cannot be empty")
    if any(character in stripped for character in INVALID_NAME_CHARACTERS):
        raise FileOperationError("The name cannot contain “/”")
    if len(stripped.encode("utf-8")) > 255:
        raise FileOperationError("The name is too long")
    return stripped


def rename(path, new_name):
    new_name = validate_name(new_name)
    destination = os.path.join(os.path.dirname(path), new_name)
    if destination == path:
        return path
    if os.path.exists(destination):
        raise FileOperationError(f"“{new_name}” already exists in this folder")
    try:
        os.rename(path, destination)
    except OSError as error:
        raise FileOperationError(error.strerror or str(error)) from error
    return destination


def move(path, directory):
    destination = unique_path(directory, os.path.basename(path))
    try:
        shutil.move(path, destination)
    except OSError as error:
        raise FileOperationError(error.strerror or str(error)) from error
    return destination


def copy(path, directory):
    destination = unique_path(directory, os.path.basename(path))
    try:
        shutil.copy2(path, destination)
    except OSError as error:
        raise FileOperationError(error.strerror or str(error)) from error
    return destination


def export(path, directory, image_format, maximum_dimension, quality):
    if image_format == "original" and not maximum_dimension:
        return copy(path, directory)
    image_module = capabilities.pillow()
    if image_module is None:
        raise FileOperationError("Image conversion needs the Python Imaging Library")
    image = load_pillow_image(path)
    if image is None:
        raise FileOperationError("This file cannot be converted")
    image = capabilities.pillow_ops().exif_transpose(image)
    if maximum_dimension:
        image.thumbnail((maximum_dimension, maximum_dimension), image_module.Resampling.LANCZOS)
    stem = os.path.splitext(os.path.basename(path))[0]
    target_format = image_format if image_format != "original" else (image.format or "PNG").lower()
    extension = {"jpeg": "jpg", "png": "png", "webp": "webp"}.get(target_format, "png")
    destination = unique_path(directory, f"{stem}.{extension}")
    save_options = {}
    if extension in ("jpg", "webp"):
        save_options["quality"] = quality
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGB")
    try:
        image.save(destination, format={"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}[extension], **save_options)
    except OSError as error:
        raise FileOperationError(str(error)) from error
    return destination


def show_in_file_manager(path, parent_window=None):
    uri = Gio.File.new_for_path(path).get_uri()
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync(
            "org.freedesktop.FileManager1",
            "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1",
            "ShowItems",
            GLib.Variant("(ass)", ([uri], "")),
            None,
            Gio.DBusCallFlags.NONE,
            3000,
            None,
        )
        return
    except GLib.Error as error:
        logger.info("FileManager1 unavailable: %s", error.message)
    Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(os.path.dirname(path)).get_uri(), None)


def set_wallpaper(path, on_done):
    uri = Gio.File.new_for_path(path).get_uri()

    def fallback():
        try:
            background = Gio.Settings.new("org.gnome.desktop.background")
            background.set_string("picture-uri", uri)
            background.set_string("picture-uri-dark", uri)
            background.set_string("picture-options", "zoom")
            on_done(True, None)
        except Exception as error:
            on_done(False, str(error))

    def finished(connection, result):
        try:
            connection.call_finish(result)
            on_done(True, None)
        except GLib.Error as error:
            logger.info("Wallpaper portal failed: %s", error.message)
            fallback()

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        fallback()
        return
    options = {"show-preview": GLib.Variant("b", False), "set-on": GLib.Variant("s", "both")}
    bus.call(
        "org.freedesktop.portal.Desktop",
        "/org/freedesktop/portal/desktop",
        "org.freedesktop.portal.Wallpaper",
        "SetWallpaperURI",
        GLib.Variant("(ssa{sv})", ("", uri, options)),
        None,
        Gio.DBusCallFlags.NONE,
        -1,
        None,
        finished,
    )


def email_files(file_paths):
    executable = shutil.which("xdg-email")
    if executable is None:
        raise FileOperationError("No email client is available")
    arguments = [executable]
    for path in file_paths:
        arguments.extend(["--attach", path])
    try:
        subprocess.Popen(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as error:
        raise FileOperationError(str(error)) from error
