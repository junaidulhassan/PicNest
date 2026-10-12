import datetime
import logging
import re
from fractions import Fraction

from gi.repository import GdkPixbuf

from picnest.core import capabilities
from picnest.core.media_types import MediaKind, is_raw, is_vector
from picnest.core.models import MediaFlags
from picnest.core.video_frames import probe_video

logger = logging.getLogger(__name__)

EXIF_DATE_PATTERN = re.compile(r"(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})")
DATE_TAGS = (
    "Exif.Photo.DateTimeOriginal",
    "Exif.Photo.DateTimeDigitized",
    "Xmp.exif.DateTimeOriginal",
    "Xmp.xmp.CreateDate",
    "Xmp.photoshop.DateCreated",
    "Exif.Image.DateTime",
)
SWAPPED_ORIENTATIONS = {5, 6, 7, 8}
MINIMUM_DIMENSION = 96
EXIF_TAG_DATETIME_ORIGINAL = 0x9003
EXIF_TAG_DATETIME_DIGITIZED = 0x9004
EXIF_TAG_DATETIME = 0x0132
EXIF_TAG_MAKE = 0x010F
EXIF_TAG_MODEL = 0x0110
EXIF_TAG_ORIENTATION = 0x0112
EXIF_TAG_LENS_MODEL = 0xA434
EXIF_TAG_ISO = 0x8827
EXIF_TAG_EXPOSURE = 0x829A
EXIF_TAG_FNUMBER = 0x829D
EXIF_TAG_FOCAL_LENGTH = 0x920A
EXIF_IFD_POINTER = 0x8769
GPS_IFD_POINTER = 0x8825


def parse_exif_date(value):
    if not value:
        return None
    match = EXIF_DATE_PATTERN.search(str(value))
    if not match:
        return None
    try:
        moment = datetime.datetime(*(int(part) for part in match.groups()))
    except ValueError:
        return None
    if moment.year < 1970:
        return None
    return moment.timestamp()


def format_exposure(numerator, denominator):
    if not numerator or not denominator:
        return None
    value = numerator / denominator
    if value >= 1:
        return f"{value:g}s"
    return f"1/{round(1 / value)}s"


def _clean_text(value):
    if value is None:
        return None
    text = str(value).strip().strip("\x00").strip()
    return text or None


class ImageMetadata:
    __slots__ = (
        "width", "height", "taken_at", "camera_make", "camera_model", "lens", "iso", "exposure",
        "aperture", "focal_length", "latitude", "longitude", "altitude", "orientation", "panorama_tag",
        "animated",
    )

    def __init__(self):
        for name in self.__slots__:
            setattr(self, name, None)
        self.width = 0
        self.height = 0
        self.orientation = 1
        self.panorama_tag = False
        self.animated = False


def _gexiv2_call(reader, name, *arguments):
    method = getattr(reader, f"try_{name}", None) or getattr(reader, name)
    return method(*arguments)


def _read_with_gexiv2(path, metadata):
    module = capabilities.gexiv2()
    if module is None:
        return False
    try:
        reader = module.Metadata()
        reader.open_path(path)
    except Exception:
        return False

    def tag_string(tag):
        try:
            if not _gexiv2_call(reader, "has_tag", tag):
                return None
            return _gexiv2_call(reader, "get_tag_string", tag)
        except Exception:
            return None

    for tag in DATE_TAGS:
        parsed = parse_exif_date(tag_string(tag))
        if parsed:
            metadata.taken_at = parsed
            break
    metadata.camera_make = _clean_text(tag_string("Exif.Image.Make"))
    metadata.camera_model = _clean_text(tag_string("Exif.Image.Model"))
    metadata.lens = _clean_text(tag_string("Exif.Photo.LensModel") or tag_string("Exif.Canon.LensModel"))
    iso = tag_string("Exif.Photo.ISOSpeedRatings") or tag_string("Exif.Photo.PhotographicSensitivity")
    if iso:
        try:
            metadata.iso = int(str(iso).split()[0])
        except ValueError:
            pass
    try:
        numerator, denominator = _gexiv2_call(reader, "get_exposure_time")
        metadata.exposure = format_exposure(numerator, denominator)
    except Exception:
        pass
    try:
        fnumber = _gexiv2_call(reader, "get_fnumber")
        metadata.aperture = fnumber if fnumber and fnumber > 0 else None
    except Exception:
        pass
    try:
        focal = _gexiv2_call(reader, "get_focal_length")
        metadata.focal_length = focal if focal and focal > 0 else None
    except Exception:
        pass
    try:
        gps = _gexiv2_call(reader, "get_gps_info")
        values = tuple(gps)
        if len(values) == 4:
            has_gps, longitude, latitude, altitude = values
        else:
            has_gps = True
            longitude, latitude, altitude = values
        if has_gps and (latitude or longitude):
            metadata.latitude = latitude
            metadata.longitude = longitude
            metadata.altitude = altitude
    except Exception:
        pass
    try:
        metadata.orientation = int(_gexiv2_call(reader, "get_orientation"))
    except Exception:
        metadata.orientation = 1
    try:
        metadata.width = _gexiv2_call(reader, "get_pixel_width") or 0
        metadata.height = _gexiv2_call(reader, "get_pixel_height") or 0
    except Exception:
        pass
    projection = tag_string("Xmp.GPano.ProjectionType")
    metadata.panorama_tag = bool(projection and projection.lower() in ("equirectangular", "cylindrical"))
    return True


def _rational_to_float(value):
    try:
        return float(Fraction(value.numerator, value.denominator)) if hasattr(value, "numerator") else float(value)
    except (ZeroDivisionError, TypeError, ValueError):
        return None


def _gps_to_degrees(values, reference):
    try:
        degrees, minutes, seconds = (_rational_to_float(value) for value in values)
        result = degrees + minutes / 60 + seconds / 3600
    except (TypeError, ValueError):
        return None
    if reference in ("S", "W"):
        result = -result
    return result


def _read_with_pillow(path, metadata):
    image_module = capabilities.pillow()
    if image_module is None:
        return False
    try:
        with image_module.open(path) as image:
            metadata.width, metadata.height = image.size
            metadata.animated = bool(getattr(image, "is_animated", False))
            exif = image.getexif()
            if not exif:
                return True
            exif_ifd = exif.get_ifd(EXIF_IFD_POINTER)
            for value in (exif_ifd.get(EXIF_TAG_DATETIME_ORIGINAL), exif_ifd.get(EXIF_TAG_DATETIME_DIGITIZED), exif.get(EXIF_TAG_DATETIME)):
                parsed = parse_exif_date(value)
                if parsed:
                    metadata.taken_at = parsed
                    break
            metadata.camera_make = _clean_text(exif.get(EXIF_TAG_MAKE))
            metadata.camera_model = _clean_text(exif.get(EXIF_TAG_MODEL))
            metadata.lens = _clean_text(exif_ifd.get(EXIF_TAG_LENS_MODEL))
            metadata.orientation = int(exif.get(EXIF_TAG_ORIENTATION, 1) or 1)
            iso = exif_ifd.get(EXIF_TAG_ISO)
            if isinstance(iso, (tuple, list)):
                iso = iso[0] if iso else None
            metadata.iso = int(iso) if isinstance(iso, (int, float)) else None
            exposure = exif_ifd.get(EXIF_TAG_EXPOSURE)
            if exposure is not None and hasattr(exposure, "numerator"):
                metadata.exposure = format_exposure(exposure.numerator, exposure.denominator)
            metadata.aperture = _rational_to_float(exif_ifd.get(EXIF_TAG_FNUMBER)) if exif_ifd.get(EXIF_TAG_FNUMBER) else None
            metadata.focal_length = _rational_to_float(exif_ifd.get(EXIF_TAG_FOCAL_LENGTH)) if exif_ifd.get(EXIF_TAG_FOCAL_LENGTH) else None
            gps = exif.get_ifd(GPS_IFD_POINTER)
            if gps and 2 in gps and 4 in gps:
                latitude = _gps_to_degrees(gps[2], gps.get(1))
                longitude = _gps_to_degrees(gps[4], gps.get(3))
                if latitude is not None and longitude is not None and (latitude or longitude):
                    metadata.latitude = latitude
                    metadata.longitude = longitude
                    metadata.altitude = _rational_to_float(gps.get(6)) if gps.get(6) is not None else None
    except Exception as error:
        logger.debug("Pillow could not read %s: %s", path, error)
        return False
    return True


def read_image_metadata(path, extension):
    metadata = ImageMetadata()
    file_info = None
    try:
        file_info = GdkPixbuf.Pixbuf.get_file_info(path)
    except Exception:
        file_info = None
    if file_info is not None and file_info[0] is not None:
        metadata.width, metadata.height = file_info[1], file_info[2]
    if not is_vector(extension):
        used_gexiv2 = _read_with_gexiv2(path, metadata)
        if not used_gexiv2 or (extension in ("gif", "webp")):
            pillow_metadata = ImageMetadata()
            if _read_with_pillow(path, pillow_metadata):
                for name in ImageMetadata.__slots__:
                    current = getattr(metadata, name)
                    if current in (None, 0, False) or (name == "orientation" and current == 1):
                        setattr(metadata, name, getattr(pillow_metadata, name))
    if file_info is not None and file_info[0] is not None:
        metadata.width, metadata.height = file_info[1], file_info[2]
    if metadata.orientation in SWAPPED_ORIENTATIONS and extension not in ("heic", "heif", "hif", "avif"):
        metadata.width, metadata.height = metadata.height, metadata.width
    return metadata


def extract(item_row):
    item_id, path, kind, extension, current_sort_date, flags = item_row
    updates = {"metadata_state": 1}
    flags = flags or 0
    if kind == MediaKind.VIDEO:
        probe = probe_video(path)
        if probe is None:
            updates["metadata_state"] = 2
            return item_id, updates
        updates.update(width=probe.width, height=probe.height, duration=probe.duration)
        if probe.created_at:
            updates["taken_at"] = probe.created_at
            updates["sort_date"] = probe.created_at
        if probe.latitude is not None and probe.longitude is not None:
            updates["latitude"] = probe.latitude
            updates["longitude"] = probe.longitude
        return item_id, updates
    metadata = read_image_metadata(path, extension)
    updates.update(
        width=metadata.width or 0,
        height=metadata.height or 0,
        camera_make=metadata.camera_make,
        camera_model=metadata.camera_model,
        lens=metadata.lens,
        iso=metadata.iso,
        exposure=metadata.exposure,
        aperture=metadata.aperture,
        focal_length=metadata.focal_length,
        latitude=metadata.latitude,
        longitude=metadata.longitude,
        altitude=metadata.altitude,
    )
    if metadata.taken_at:
        updates["taken_at"] = metadata.taken_at
        updates["sort_date"] = metadata.taken_at
    if not metadata.width and not metadata.height and not is_raw(extension):
        updates["metadata_state"] = 2
    width, height = metadata.width or 0, metadata.height or 0
    if width and height and max(width, height) < MINIMUM_DIMENSION:
        updates["ignored"] = 1
    flags &= ~(MediaFlags.PANORAMA | MediaFlags.ANIMATED)
    ratio = (width / height) if width and height else 1.0
    is_screenshot = bool(flags & MediaFlags.SCREENSHOT)
    if metadata.panorama_tag or (not is_screenshot and max(width, height) >= 2400 and (ratio >= 2.0 or ratio <= 0.5)):
        flags |= MediaFlags.PANORAMA
    if metadata.animated:
        flags |= MediaFlags.ANIMATED
    updates["flags"] = flags
    return item_id, updates
