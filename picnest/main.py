import logging
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")

from gi.repository import Adw, GLib

from picnest.application import PicnestApplication
from picnest.core import capabilities

MINIMUM_ADWAITA = (1, 5)


def _configure_logging():
    level = logging.DEBUG if os.environ.get("PICNEST_DEBUG") else logging.WARNING
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main():
    _configure_logging()
    version = (Adw.get_major_version(), Adw.get_minor_version())
    if version < MINIMUM_ADWAITA:
        sys.stderr.write(f"Picnest needs libadwaita {MINIMUM_ADWAITA[0]}.{MINIMUM_ADWAITA[1]} or newer, found {version[0]}.{version[1]}\n")
        return 1
    GLib.set_prgname("picnest")
    capabilities.gstreamer()
    application = PicnestApplication()
    return application.run(sys.argv)
