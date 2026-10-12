#!/usr/bin/env python3
import sys
import urllib.request
import xml.etree.ElementTree as ElementTree
from pathlib import Path

PHOSPHOR_VERSION = "2.1.1"
PHOSPHOR_URL = "https://cdn.jsdelivr.net/npm/@phosphor-icons/core@{version}/assets/{weight}/{name}{suffix}.svg"

ICON_MAP = {
    "library": ("images", "regular"),
    "memories": ("sparkle", "regular"),
    "years": ("calendar", "regular"),
    "calendar-day": ("calendar-blank", "regular"),
    "favorite": ("heart", "regular"),
    "favorite-filled": ("heart", "fill"),
    "videos": ("film-strip", "regular"),
    "recents": ("clock-counter-clockwise", "regular"),
    "hidden": ("eye-slash", "regular"),
    "visible": ("eye", "regular"),
    "trash": ("trash", "regular"),
    "restore": ("arrow-counter-clockwise", "regular"),
    "albums": ("stack", "regular"),
    "album-add": ("plus-square", "regular"),
    "folders": ("folder", "regular"),
    "folder-open": ("folder-open", "regular"),
    "drive": ("hard-drives", "regular"),
    "drive-removable": ("usb", "regular"),
    "location": ("map-pin", "regular"),
    "search": ("magnifying-glass", "regular"),
    "edit": ("pencil-simple", "regular"),
    "share": ("share-network", "regular"),
    "delete-forever": ("x-circle", "regular"),
    "info": ("info", "regular"),
    "settings": ("gear-six", "regular"),
    "screenshots": ("frame-corners", "regular"),
    "panoramas": ("mountains", "regular"),
    "duplicates": ("copy", "regular"),
    "people": ("user-focus", "regular"),
    "insights": ("chart-pie-slice", "regular"),
    "rotate-left": ("arrow-counter-clockwise", "regular"),
    "rotate-right": ("arrow-clockwise", "regular"),
    "flip-horizontal": ("flip-horizontal", "regular"),
    "flip-vertical": ("flip-vertical", "regular"),
    "crop": ("crop", "regular"),
    "filters": ("circles-three", "regular"),
    "adjust": ("sliders-horizontal", "regular"),
    "play": ("play", "fill"),
    "pause": ("pause", "fill"),
    "volume-high": ("speaker-high", "regular"),
    "volume-low": ("speaker-low", "regular"),
    "volume-muted": ("speaker-slash", "regular"),
    "speed": ("gauge", "regular"),
    "pip": ("picture-in-picture", "regular"),
    "fullscreen": ("arrows-out", "regular"),
    "fullscreen-exit": ("arrows-in", "regular"),
    "slideshow": ("presentation", "regular"),
    "zoom-in": ("magnifying-glass-plus", "regular"),
    "zoom-out": ("magnifying-glass-minus", "regular"),
    "zoom-fit": ("arrows-in-simple", "regular"),
    "check": ("check", "bold"),
    "select": ("check-square", "regular"),
    "add": ("plus", "regular"),
    "move": ("arrow-bend-up-right", "regular"),
    "copy": ("copy-simple", "regular"),
    "rename": ("cursor-text", "regular"),
    "export": ("export", "regular"),
    "wallpaper": ("monitor", "regular"),
    "sort": ("sort-descending", "regular"),
    "filter": ("funnel", "regular"),
    "lock": ("lock-simple", "regular"),
    "unlock": ("lock-simple-open", "regular"),
    "import": ("download-simple", "regular"),
    "rescan": ("arrows-clockwise", "regular"),
    "grid": ("squares-four", "regular"),
    "close": ("x", "regular"),
    "go-previous": ("caret-left", "regular"),
    "go-next": ("caret-right", "regular"),
    "expand": ("caret-down", "regular"),
    "image": ("image", "regular"),
    "video": ("video-camera", "regular"),
    "camera": ("camera", "regular"),
    "lens": ("aperture", "regular"),
    "resolution": ("frame-corners", "regular"),
    "file": ("file", "regular"),
    "storage": ("hard-drive", "regular"),
    "history": ("clock-counter-clockwise", "regular"),
    "warning": ("warning", "regular"),
    "brightness": ("sun", "regular"),
    "contrast": ("circle-half", "regular"),
    "saturation": ("drop", "regular"),
    "warmth": ("thermometer-simple", "regular"),
    "reset": ("arrow-u-up-left", "regular"),
    "clear": ("x-circle", "regular"),
    "open-with": ("arrow-square-out", "regular"),
    "clipboard": ("clipboard-text", "regular"),
    "email": ("envelope-simple", "regular"),
    "orientation": ("device-rotate", "regular"),
    "scan": ("scan", "regular"),
    "welcome": ("images-square", "regular"),
}

SVG_NAMESPACE = "{http://www.w3.org/2000/svg}"


def extract_path_data(svg_text):
    root = ElementTree.fromstring(svg_text)
    if root.tag != f"{SVG_NAMESPACE}svg":
        raise ValueError("not an svg document")
    view_box = root.get("viewBox", "0 0 256 256")
    paths = []
    for element in root.iter(f"{SVG_NAMESPACE}path"):
        data = element.get("d", "")
        if data and all(character in "0123456789.,-+ MmLlHhVvCcSsQqTtAaZzEe" for character in data):
            paths.append(data)
    if not paths:
        raise ValueError("no usable path data")
    return view_box, paths


def build_symbolic_svg(view_box, paths):
    path_elements = "".join(f'<path d="{data}" fill="#2e3436"/>' for data in paths)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="{view_box}">'
        f"{path_elements}</svg>\n"
    )


def fetch(name, weight):
    suffix = "" if weight == "regular" else f"-{weight}"
    url = PHOSPHOR_URL.format(version=PHOSPHOR_VERSION, weight=weight, name=name, suffix=suffix)
    with urllib.request.urlopen(url, timeout=20) as response:
        return response.read(200_000).decode("utf-8")


def main():
    output_directory = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "data" / "ui-icons" / "hicolor" / "scalable" / "actions"
    output_directory.mkdir(parents=True, exist_ok=True)
    failures = []
    for icon_name, (phosphor_name, weight) in sorted(ICON_MAP.items()):
        try:
            view_box, paths = extract_path_data(fetch(phosphor_name, weight))
        except Exception as error:
            failures.append(f"{icon_name} ({phosphor_name}/{weight}): {error}")
            continue
        (output_directory / f"pn-{icon_name}-symbolic.svg").write_text(build_symbolic_svg(view_box, paths), encoding="utf-8")
    for failure in failures:
        print(f"failed: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
