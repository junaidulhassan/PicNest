import os
from enum import IntEnum


class MediaKind(IntEnum):
    IMAGE = 0
    VIDEO = 1


RASTER_IMAGE_EXTENSIONS = frozenset(
    {
        "jpg", "jpeg", "jpe", "jfif", "png", "webp", "gif", "bmp", "dib", "tif", "tiff",
        "heic", "heif", "hif", "avif", "jxl", "ico", "tga", "pnm", "ppm", "pgm", "pbm", "qoi",
    }
)

VECTOR_IMAGE_EXTENSIONS = frozenset({"svg", "svgz"})

RAW_IMAGE_EXTENSIONS = frozenset(
    {
        "3fr", "arw", "cr2", "cr3", "crw", "dcr", "dng", "erf", "fff", "iiq", "k25", "kdc",
        "mef", "mos", "mrw", "nef", "nrw", "orf", "pef", "raf", "raw", "rw2", "rwl", "sr2",
        "srf", "srw", "x3f",
    }
)

VIDEO_EXTENSIONS = frozenset(
    {
        "mp4", "m4v", "mkv", "avi", "mov", "qt", "webm", "flv", "f4v", "wmv", "asf", "mpg",
        "mpeg", "mpe", "m2v", "m2ts", "mts", "3gp", "3g2", "ogv", "vob", "divx", "mxf",
    }
)

IMAGE_EXTENSIONS = RASTER_IMAGE_EXTENSIONS | VECTOR_IMAGE_EXTENSIONS | RAW_IMAGE_EXTENSIONS
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

TYPE_LABELS = {
    "jpg": "JPEG", "jpeg": "JPEG", "jpe": "JPEG", "jfif": "JPEG", "png": "PNG", "webp": "WebP",
    "gif": "GIF", "bmp": "BMP", "dib": "BMP", "tif": "TIFF", "tiff": "TIFF", "heic": "HEIC",
    "heif": "HEIF", "hif": "HEIF", "avif": "AVIF", "jxl": "JPEG XL", "svg": "SVG", "svgz": "SVG",
    "mp4": "MP4", "m4v": "MP4", "mkv": "Matroska", "avi": "AVI", "mov": "QuickTime", "qt": "QuickTime",
    "webm": "WebM", "flv": "Flash Video", "wmv": "Windows Media", "mpg": "MPEG", "mpeg": "MPEG",
    "m2ts": "AVCHD", "mts": "AVCHD", "3gp": "3GPP", "ogv": "Ogg Video",
}


def extension_of(name):
    _, extension = os.path.splitext(name)
    return extension[1:].lower()


def kind_for_extension(extension):
    if extension in VIDEO_EXTENSIONS:
        return MediaKind.VIDEO
    if extension in IMAGE_EXTENSIONS:
        return MediaKind.IMAGE
    return None


def is_raw(extension):
    return extension in RAW_IMAGE_EXTENSIONS


def is_vector(extension):
    return extension in VECTOR_IMAGE_EXTENSIONS


def type_label(extension):
    if extension in RAW_IMAGE_EXTENSIONS:
        return f"RAW ({extension.upper()})"
    return TYPE_LABELS.get(extension, extension.upper() or "Unknown")


def type_category(extension):
    if extension in VIDEO_EXTENSIONS:
        return "Videos"
    if extension in RAW_IMAGE_EXTENSIONS:
        return "RAW Photos"
    if extension in ("heic", "heif", "hif", "avif"):
        return "HEIF and AVIF"
    if extension in ("png",):
        return "PNG Images"
    if extension in ("jpg", "jpeg", "jpe", "jfif"):
        return "JPEG Photos"
    if extension in ("gif", "webp"):
        return "GIF and WebP"
    return "Other Images"
