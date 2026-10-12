from gi.repository import Adw, GLib, Gtk

from picnest.ui import icons
from picnest.ui.widgets import CoverPicture


def confirm(parent, heading, body, confirm_label, on_confirm, destructive=True):
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", "_Cancel")
    dialog.add_response("confirm", confirm_label)
    dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("cancel" if destructive else "confirm")
    dialog.set_close_response("cancel")

    def on_response(_dialog, response):
        if response == "confirm":
            on_confirm()

    dialog.connect("response", on_response)
    dialog.present(parent)
    return dialog


def prompt_text(parent, heading, body, initial_text, confirm_label, on_submit, select_stem=False, placeholder=""):
    dialog = Adw.AlertDialog(heading=heading, body=body)
    entry = Gtk.Entry(text=initial_text, activates_default=True, placeholder_text=placeholder)
    entry.set_margin_top(6)
    dialog.set_extra_child(entry)
    dialog.add_response("cancel", "_Cancel")
    dialog.add_response("confirm", confirm_label)
    dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("confirm")
    dialog.set_close_response("cancel")

    def update_sensitivity(*_args):
        dialog.set_response_enabled("confirm", bool(entry.get_text().strip()))

    entry.connect("changed", update_sensitivity)
    update_sensitivity()

    def on_response(_dialog, response):
        if response == "confirm":
            on_submit(entry.get_text().strip())

    dialog.connect("response", on_response)
    dialog.present(parent)

    def focus_entry():
        entry.grab_focus()
        if select_stem and "." in initial_text:
            entry.select_region(0, initial_text.rfind("."))
        else:
            entry.select_region(0, -1)
        return False

    GLib.idle_add(focus_entry)
    return dialog


def show_error(parent, heading, failures):
    lines = [f"{name}: {message}" if name else message for name, message in failures[:8]]
    if len(failures) > 8:
        lines.append(f"and {len(failures) - 8} more")
    dialog = Adw.AlertDialog(heading=heading, body="\n".join(lines))
    dialog.add_response("close", "_Close")
    dialog.present(parent)


class AlbumPickerDialog(Adw.Dialog):
    __gtype_name__ = "PicnestAlbumPickerDialog"

    def __init__(self, library, thumbnails, on_chosen):
        super().__init__(title="Add to Album", content_width=380, content_height=520)
        self._library = library
        self._on_chosen = on_chosen
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        new_row = Gtk.Box(spacing=6)
        self._entry = Gtk.Entry(placeholder_text="New album name", hexpand=True, activates_default=False)
        self._entry.connect("activate", lambda _entry: self._create())
        create_button = Gtk.Button(icon_name=icons.ADD, tooltip_text="Create album")
        create_button.add_css_class("suggested-action")
        create_button.update_property([Gtk.AccessibleProperty.LABEL], ["Create album"])
        create_button.connect("clicked", lambda _button: self._create())
        new_row.append(self._entry)
        new_row.append(create_button)
        box.append(new_row)
        albums = library.albums()
        if albums:
            list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            list_box.add_css_class("boxed-list")
            for album in albums:
                row = Adw.ActionRow(title=album.name, subtitle=f"{album.item_count:,} items", activatable=True)
                cover = CoverPicture(thumbnails, radius=8, size=(40, 40), small=True)
                cover.set_valign(Gtk.Align.CENTER)
                cover.set_item(library.item(album.cover_id) if album.cover_id else None)
                row.add_prefix(cover)
                row.connect("activated", lambda _row, album_id=album.id: self._choose(album_id))
                list_box.append(row)
            scrolled = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=list_box)
            box.append(scrolled)
        else:
            box.append(Adw.StatusPage(icon_name=icons.ALBUMS, title="No Albums Yet", description="Type a name above to create your first album", vexpand=True))
        toolbar.set_content(box)
        self.set_child(toolbar)

    def _create(self):
        name = self._entry.get_text().strip()
        if not name:
            self._entry.grab_focus()
            return
        album_id = self._library.create_album(name)
        self._choose(album_id)

    def _choose(self, album_id):
        self.close()
        self._on_chosen(album_id)


class ExportDialog(Adw.Dialog):
    __gtype_name__ = "PicnestExportDialog"

    FORMATS = (("original", "Original Format"), ("jpeg", "JPEG"), ("png", "PNG"), ("webp", "WebP"))
    SIZES = ((0, "Full Size"), (3840, "Large (3840 px)"), (2048, "Medium (2048 px)"), (1024, "Small (1024 px)"))

    def __init__(self, item_count, on_export):
        super().__init__(title="Export", content_width=420)
        self._on_export = on_export
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label="_Cancel", use_underline=True)
        cancel.connect("clicked", lambda _button: self.close())
        export = Gtk.Button(label="_Export…", use_underline=True)
        export.add_css_class("suggested-action")
        export.connect("clicked", lambda _button: self._submit())
        header.pack_start(cancel)
        header.pack_end(export)
        toolbar.add_top_bar(header)
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(description=f"Export {item_count:,} item{'s' if item_count != 1 else ''}. Videos are always copied unchanged.")
        self._format_row = Adw.ComboRow(title="Format", model=Gtk.StringList.new([label for _value, label in self.FORMATS]))
        self._size_row = Adw.ComboRow(title="Size", model=Gtk.StringList.new([label for _value, label in self.SIZES]))
        self._quality_row = Adw.SpinRow.new_with_range(40, 100, 1)
        self._quality_row.set_title("Quality")
        self._quality_row.set_value(90)
        group.add(self._format_row)
        group.add(self._size_row)
        group.add(self._quality_row)
        page.add(group)
        toolbar.set_content(page)
        self.set_child(toolbar)

    def _submit(self):
        image_format = self.FORMATS[self._format_row.get_selected()][0]
        size = self.SIZES[self._size_row.get_selected()][0]
        quality = int(self._quality_row.get_value())
        self.close()
        self._on_export(image_format, size, quality)
