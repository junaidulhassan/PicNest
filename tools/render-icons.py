#!/usr/bin/python3
import re
import shutil
import sys
from pathlib import Path

import cairo
import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

APP_ID = "com.junaidulhassan.Picnest"
PNG_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
WORDMARK_TEXT = "Picnest"
WORDMARK_FONT_FAMILY = "Ubuntu Sans"
WORDMARK_FONT_SIZE = 96
WORDMARK_COLORS = {"light": "#1E1B3A", "dark": "#FFFFFF"}

project_root = Path(__file__).resolve().parent.parent
icons_directory = project_root / "data" / "icons"
hicolor_directory = icons_directory / "hicolor"


def render_png_sizes(source_svg):
    for size in PNG_SIZES:
        target_directory = hicolor_directory / f"{size}x{size}" / "apps"
        target_directory.mkdir(parents=True, exist_ok=True)
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(str(source_svg), size, size)
        pixbuf.savev(str(target_directory / f"{APP_ID}.png"), "png", [], [])


def install_vector_icons(source_svg, symbolic_svg):
    scalable_directory = hicolor_directory / "scalable" / "apps"
    symbolic_directory = hicolor_directory / "symbolic" / "apps"
    scalable_directory.mkdir(parents=True, exist_ok=True)
    symbolic_directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_svg, scalable_directory / f"{APP_ID}.svg")
    shutil.copyfile(symbolic_svg, symbolic_directory / f"{APP_ID}-symbolic.svg")
    shutil.copyfile(symbolic_svg, symbolic_directory / "picnest-symbolic.svg")


def text_outline_path(text):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8)
    context = cairo.Context(surface)
    context.select_font_face(WORDMARK_FONT_FAMILY, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    context.set_font_size(WORDMARK_FONT_SIZE)
    bearing_x, bearing_y, width, height, _, _ = context.text_extents(text)
    context.move_to(-bearing_x, -bearing_y)
    context.text_path(text)
    commands = []
    for operation, points in context.copy_path():
        if operation == cairo.PATH_MOVE_TO:
            commands.append("M{:.2f},{:.2f}".format(*points))
        elif operation == cairo.PATH_LINE_TO:
            commands.append("L{:.2f},{:.2f}".format(*points))
        elif operation == cairo.PATH_CURVE_TO:
            commands.append("C{:.2f},{:.2f} {:.2f},{:.2f} {:.2f},{:.2f}".format(*points))
        elif operation == cairo.PATH_CLOSE_PATH:
            commands.append("Z")
    return "".join(commands), width, height


def logo_inner_markup(source_svg):
    markup = source_svg.read_text(encoding="utf-8")
    match = re.search(r"<svg[^>]*>(.*)</svg>", markup, re.DOTALL)
    return match.group(1).strip()


def logo_viewbox(source_svg):
    match = re.search(r'viewBox="([^"]+)"', source_svg.read_text(encoding="utf-8"))
    return match.group(1)


def write_wordmarks(source_svg):
    path_data, text_width, text_height = text_outline_path(WORDMARK_TEXT)
    logo_markup = logo_inner_markup(source_svg)
    view_box = logo_viewbox(source_svg)
    gap = 28
    total_width = round(128 + gap + text_width + 8)
    text_offset_y = (128 - text_height) / 2 + 2
    for variant, color in WORDMARK_COLORS.items():
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="128" viewBox="0 0 {total_width} 128">\n'
            f'  <svg x="0" y="0" width="128" height="128" viewBox="{view_box}">{logo_markup}</svg>\n'
            f'  <path transform="translate({128 + gap},{text_offset_y:.2f})" d="{path_data}" fill="{color}"/>\n'
            "</svg>\n"
        )
        suffix = "" if variant == "light" else "-dark"
        (icons_directory / f"picnest-wordmark{suffix}.svg").write_text(svg, encoding="utf-8")


def main():
    source_svg = icons_directory / f"{APP_ID}.svg"
    symbolic_svg = icons_directory / f"{APP_ID}-symbolic.svg"
    render_png_sizes(source_svg)
    install_vector_icons(source_svg, symbolic_svg)
    write_wordmarks(source_svg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
