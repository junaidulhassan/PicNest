import logging

from gi.repository import Gdk, GdkPixbuf, GLib

from picnest.core import capabilities
from picnest.core.media_types import extension_of, is_raw
from picnest.core.thumbnails import load_raw_preview, load_with_pillow

logger = logging.getLogger(__name__)


def load_pixbuf(path, extension, maximum_dimension=0):
    if is_raw(extension):
        pixbuf = load_raw_preview(path, maximum_dimension or 8192)
        return pixbuf if pixbuf is not None else load_with_pillow(path, maximum_dimension or 8192)
    try:
        if maximum_dimension:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, maximum_dimension, maximum_dimension, True)
        else:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
    except GLib.Error as error:
        logger.debug("GdkPixbuf cannot load %s: %s", path, error.message)
        return load_with_pillow(path, maximum_dimension or 16384)
    oriented = pixbuf.apply_embedded_orientation()
    return oriented if oriented is not None else pixbuf


def load_texture(path, extension, maximum_dimension=0):
    pixbuf = load_pixbuf(path, extension, maximum_dimension)
    if pixbuf is None:
        return None
    return Gdk.Texture.new_for_pixbuf(pixbuf)


def load_animation(path):
    try:
        animation = GdkPixbuf.PixbufAnimation.new_from_file(path)
    except GLib.Error:
        return None
    if animation.is_static_image():
        return None
    return animation


def pixbuf_to_pillow(pixbuf):
    image_module = capabilities.pillow()
    if image_module is None or pixbuf is None:
        return None
    mode = "RGBA" if pixbuf.get_has_alpha() else "RGB"
    width, height = pixbuf.get_width(), pixbuf.get_height()
    return image_module.frombytes(mode, (width, height), pixbuf.read_pixel_bytes().get_data(), "raw", mode, pixbuf.get_rowstride())


def load_pillow_image(path):
    image_module = capabilities.pillow()
    if image_module is None:
        return None
    try:
        image = image_module.open(path)
        image.load()
        return image
    except Exception:
        pixbuf = load_pixbuf(path, extension_of(path))
        return pixbuf_to_pillow(pixbuf)
