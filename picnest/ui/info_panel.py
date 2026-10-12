import os

from gi.repository import Adw, GLib, Gtk

from picnest.core import formatting
from picnest.core.media_types import type_label
from picnest.ui import icons


def _row(icon_name, title, subtitle):
    row = Adw.ActionRow(title=GLib.markup_escape_text(title), subtitle=GLib.markup_escape_text(subtitle), subtitle_selectable=True)
    row.add_css_class("property")
    row.add_prefix(Gtk.Image(icon_name=icon_name))
    return row


class InfoPanel(Gtk.Box):
    __gtype_name__ = "PicnestInfoPanel"

    def __init__(self, library, on_close):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("pn-info-panel")
        self._library = library
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        header.set_title_widget(Adw.WindowTitle(title="Info"))
        close_button = Gtk.Button(icon_name=icons.CLOSE, tooltip_text="Close Info (I)")
        close_button.add_css_class("flat")
        close_button.update_property([Gtk.AccessibleProperty.LABEL], ["Close Info"])
        close_button.connect("clicked", lambda _button: on_close())
        header.pack_end(close_button)
        self.append(header)
        self._scrolled = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.append(self._scrolled)
        self._item_id = None

    def show_item(self, item):
        if item is None:
            self._scrolled.set_child(None)
            self._item_id = None
            return
        self._item_id = item.id
        page = Adw.PreferencesPage()
        details = Adw.PreferencesGroup(title="Details")
        details.add(_row(icons.FILE, "Name", item.name))
        details.add(_row(icons.YEARS, "Date", formatting.full_date(item.taken_at or item.sort_date)))
        if item.width and item.height:
            megapixels = f" ({item.megapixels:.1f} MP)" if not item.is_video and item.megapixels >= 0.1 else ""
            details.add(_row(icons.RESOLUTION, "Dimensions", f"{item.width:,} × {item.height:,}{megapixels}"))
        if item.is_video and item.duration:
            details.add(_row(icons.VIDEOS, "Duration", formatting.human_duration(item.duration)))
        details.add(_row(icons.STORAGE, "Size", formatting.human_size(item.size)))
        details.add(_row(icons.IMAGE if not item.is_video else icons.VIDEO, "Type", type_label(item.extension)))
        details.add(_row(icons.FOLDERS, "Folder", os.path.dirname(item.path)))
        details.add(_row(icons.RECENTS, "Modified", formatting.full_date(item.mtime)))
        page.add(details)
        camera_rows = []
        if item.camera:
            camera_rows.append(_row(icons.CAMERA, "Camera", item.camera))
        if item.lens:
            camera_rows.append(_row(icons.LENS, "Lens", item.lens))
        exposure_parts = []
        if item.aperture:
            exposure_parts.append(f"ƒ/{item.aperture:.1f}")
        if item.exposure:
            exposure_parts.append(formatting.exposure_label(item.exposure))
        if item.focal_length:
            exposure_parts.append(f"{item.focal_length:g} mm")
        if item.iso:
            exposure_parts.append(f"ISO {item.iso}")
        if exposure_parts:
            camera_rows.append(_row(icons.ADJUST, "Exposure", "  ·  ".join(exposure_parts)))
        if camera_rows:
            camera_group = Adw.PreferencesGroup(title="Camera")
            for row in camera_rows:
                camera_group.add(row)
            page.add(camera_group)
        if item.has_location:
            location_group = Adw.PreferencesGroup(title="Location")
            row = _row(icons.LOCATION, "Coordinates", formatting.coordinate_label(item.latitude, item.longitude))
            location_group.add(row)
            if item.altitude:
                location_group.add(_row(icons.PANORAMAS, "Altitude", f"{item.altitude:.0f} m"))
            page.add(location_group)
        albums = self._library.item_albums(item.id)
        if albums:
            album_group = Adw.PreferencesGroup(title="Albums")
            for name in albums:
                album_group.add(_row(icons.ALBUMS, name, ""))
            page.add(album_group)
        self._scrolled.set_child(page)
