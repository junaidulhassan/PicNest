import os
from dataclasses import dataclass

from PIL import Image, ImageEnhance, ImageOps

from picnest.core.file_ops import unique_path

PILLOW_WRITABLE = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP", "tif": "TIFF", "tiff": "TIFF", "bmp": "BMP"}
FILTER_NAMES = ("Original", "Vivid", "Warm", "Cool", "Mono", "Noir", "Sepia", "Fade", "Dramatic")


@dataclass
class EditState:
    rotation: int = 0
    flip_horizontal: bool = False
    flip_vertical: bool = False
    crop: tuple = (0.0, 0.0, 1.0, 1.0)
    filter_name: str = "Original"
    brightness: float = 0.0
    contrast: float = 0.0
    saturation: float = 0.0
    warmth: float = 0.0

    def is_identity(self):
        return (
            self.rotation % 360 == 0 and not self.flip_horizontal and not self.flip_vertical
            and self.crop == (0.0, 0.0, 1.0, 1.0) and self.filter_name == "Original"
            and not self.brightness and not self.contrast and not self.saturation and not self.warmth
        )

    def copy(self):
        return EditState(self.rotation, self.flip_horizontal, self.flip_vertical, tuple(self.crop), self.filter_name, self.brightness, self.contrast, self.saturation, self.warmth)


def apply_geometry(image, state):
    rotation = state.rotation % 360
    if rotation == 90:
        image = image.transpose(Image.Transpose.ROTATE_270)
    elif rotation == 180:
        image = image.transpose(Image.Transpose.ROTATE_180)
    elif rotation == 270:
        image = image.transpose(Image.Transpose.ROTATE_90)
    if state.flip_horizontal:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if state.flip_vertical:
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return image


def apply_crop(image, state):
    left, top, right, bottom = state.crop
    if (left, top, right, bottom) == (0.0, 0.0, 1.0, 1.0):
        return image
    width, height = image.size
    box = (round(left * width), round(top * height), max(round(left * width) + 1, round(right * width)), max(round(top * height) + 1, round(bottom * height)))
    return image.crop(box)


def _apply_warmth(image, amount):
    if not amount:
        return image
    alpha = image.getchannel("A") if image.mode == "RGBA" else None
    red, green, blue = image.convert("RGB").split()
    factor = amount / 100.0
    red = red.point(lambda value: max(0, min(255, int(value * (1 + 0.18 * factor)))))
    blue = blue.point(lambda value: max(0, min(255, int(value * (1 - 0.18 * factor)))))
    merged = Image.merge("RGB", (red, green, blue))
    if alpha is not None:
        merged.putalpha(alpha)
    return merged


def apply_filter(image, name):
    if name == "Original":
        return image
    alpha = image.getchannel("A") if image.mode == "RGBA" else None
    rgb = image.convert("RGB")
    if name == "Vivid":
        rgb = ImageEnhance.Contrast(ImageEnhance.Color(rgb).enhance(1.35)).enhance(1.08)
    elif name == "Warm":
        rgb = _apply_warmth(ImageEnhance.Color(rgb).enhance(1.08), 60)
    elif name == "Cool":
        rgb = _apply_warmth(rgb, -60)
    elif name == "Mono":
        rgb = ImageOps.grayscale(rgb).convert("RGB")
    elif name == "Noir":
        rgb = ImageEnhance.Contrast(ImageOps.grayscale(rgb)).enhance(1.45).convert("RGB")
    elif name == "Sepia":
        gray = ImageOps.grayscale(rgb)
        rgb = ImageOps.colorize(gray, (52, 34, 18), (255, 236, 200))
    elif name == "Fade":
        rgb = ImageEnhance.Contrast(ImageEnhance.Color(rgb).enhance(0.8)).enhance(0.82)
        rgb = Image.blend(rgb, Image.new("RGB", rgb.size, (238, 232, 224)), 0.08)
    elif name == "Dramatic":
        rgb = ImageEnhance.Color(ImageEnhance.Contrast(rgb).enhance(1.32)).enhance(0.88)
    if alpha is not None:
        rgb.putalpha(alpha)
    return rgb


def apply_adjustments(image, state):
    if not (state.brightness or state.contrast or state.saturation or state.warmth):
        return image
    alpha = image.getchannel("A") if image.mode == "RGBA" else None
    image = image.convert("RGB")
    if state.brightness:
        image = ImageEnhance.Brightness(image).enhance(1 + state.brightness / 100.0 * 0.6)
    if state.contrast:
        image = ImageEnhance.Contrast(image).enhance(1 + state.contrast / 100.0 * 0.7)
    if state.saturation:
        image = ImageEnhance.Color(image).enhance(max(0.0, 1 + state.saturation / 100.0))
    if state.warmth:
        image = _apply_warmth(image, state.warmth)
    if alpha is not None:
        image.putalpha(alpha)
    return image


def render(image, state, include_crop=True):
    image = apply_geometry(image, state)
    if include_crop:
        image = apply_crop(image, state)
    image = apply_filter(image, state.filter_name)
    return apply_adjustments(image, state)


def prepare_preview(image, maximum_dimension):
    preview = image.copy()
    preview.thumbnail((maximum_dimension, maximum_dimension), Image.Resampling.LANCZOS)
    if preview.mode not in ("RGB", "RGBA"):
        preview = preview.convert("RGBA" if "A" in preview.getbands() else "RGB")
    return preview


def save_result(source_image, state, original_path, original_extension, replace_original, exif_bytes=None):
    result = render(source_image, state)
    writable_format = PILLOW_WRITABLE.get(original_extension)
    directory = os.path.dirname(original_path)
    stem = os.path.splitext(os.path.basename(original_path))[0]
    if replace_original and writable_format is not None:
        destination = original_path
        image_format = writable_format
    else:
        if writable_format is None:
            extension = "png" if result.mode == "RGBA" else "jpg"
            image_format = "PNG" if extension == "png" else "JPEG"
        else:
            extension = original_extension
            image_format = writable_format
        destination = unique_path(directory, f"{stem} (edited).{extension}")
    options = {}
    if image_format == "JPEG":
        if result.mode != "RGB":
            result = result.convert("RGB")
        options.update(quality=95, subsampling=0, optimize=True)
    elif image_format == "WEBP":
        options.update(quality=95)
    if exif_bytes and image_format in ("JPEG", "WEBP", "PNG", "TIFF"):
        options["exif"] = exif_bytes
    temporary = f"{destination}.picnest-tmp"
    result.save(temporary, format=image_format, **options)
    os.replace(temporary, destination)
    return destination


def normalized_exif(image):
    try:
        exif = image.getexif()
    except Exception:
        return None
    if not exif:
        return None
    exif[0x0112] = 1
    try:
        return exif.tobytes()
    except Exception:
        return None
