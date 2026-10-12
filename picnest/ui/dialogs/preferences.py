from gi.repository import Adw, GLib, Gtk

from picnest.core import faces, formatting, security
from picnest.core.tasks import run_in_thread
from picnest.ui import icons
from picnest.ui.dialogs.common import confirm
from picnest.ui.widgets import set_accessible_label

COLOR_SCHEMES = (("system", "Follow System"), ("light", "Light"), ("dark", "Dark"))


class PreferencesDialog(Adw.PreferencesDialog):
    __gtype_name__ = "PicnestPreferencesDialog"

    def __init__(self, context):
        super().__init__(title="Preferences", search_enabled=True)
        self._context = context
        self._settings = context.settings
        self._library = context.library
        self.add(self._library_page())
        self.add(self._appearance_page())
        self.add(self._privacy_page())
        self.add(self._storage_page())

    def _switch_row(self, title, subtitle, key):
        row = Adw.SwitchRow(title=title, subtitle=subtitle)
        self._settings.bind(key, row, "active")
        return row

    def _library_page(self):
        page = Adw.PreferencesPage(title="Library", icon_name=icons.LIBRARY)
        scanning = Adw.PreferencesGroup(title="Scanning", description="Picnest finds photos and videos automatically and keeps them up to date.")
        scanning.add(self._switch_row("Scan All Drives", "Include every internal drive and connected external drive", "scan-all-drives"))
        scanning.add(self._switch_row("Include Hidden Folders", "Scan folders whose names start with a dot", "include-hidden-folders"))
        minimum = Adw.SpinRow.new_with_range(0, 1024, 1)
        minimum.set_title("Ignore Images Smaller Than")
        minimum.set_subtitle("Size in kilobytes, skips icons and tiny graphics")
        self._settings.bind("minimum-image-kilobytes", minimum, "value")
        scanning.add(minimum)
        rescan = Adw.ButtonRow(title="Rescan Library Now") if hasattr(Adw, "ButtonRow") else Adw.ActionRow(title="Rescan Library Now", activatable=True)
        rescan.connect("activated", lambda _row: self._rescan())
        scanning.add(rescan)
        page.add(scanning)
        page.add(self._drives_group())
        self._included_group = Adw.PreferencesGroup(title="Included Folders", description="Always scanned, in addition to your drives")
        add_included = Gtk.Button(icon_name=icons.ADD, valign=Gtk.Align.CENTER, tooltip_text="Add Folder")
        add_included.add_css_class("flat")
        set_accessible_label(add_included, "Add included folder")
        add_included.connect("clicked", lambda _button: self._choose_folder("included-folders"))
        self._included_group.set_header_suffix(add_included)
        page.add(self._included_group)
        self._excluded_group = Adw.PreferencesGroup(title="Excluded Folders", description="Never scanned, and removed from the library")
        add_excluded = Gtk.Button(icon_name=icons.ADD, valign=Gtk.Align.CENTER, tooltip_text="Add Folder")
        add_excluded.add_css_class("flat")
        set_accessible_label(add_excluded, "Add excluded folder")
        add_excluded.connect("clicked", lambda _button: self._choose_folder("excluded-folders"))
        self._excluded_group.set_header_suffix(add_excluded)
        page.add(self._excluded_group)
        self._folder_rows = {"included-folders": [], "excluded-folders": []}
        self._refresh_folders("included-folders")
        self._refresh_folders("excluded-folders")
        viewing = Adw.PreferencesGroup(title="Viewing")
        viewing.add(self._switch_row("Video Previews on Hover", "Play muted previews when the pointer rests on a video", "hover-video-preview"))
        viewing.add(self._switch_row("Loop Videos", "Restart videos automatically when they end", "loop-videos"))
        interval = Adw.SpinRow.new_with_range(1, 60, 1)
        interval.set_title("Slideshow Interval")
        interval.set_subtitle("Seconds per photo")
        self._settings.bind("slideshow-interval", interval, "value")
        viewing.add(interval)
        page.add(viewing)
        smart = Adw.PreferencesGroup(title="Smart Features", description="All processing happens locally on this computer")
        face_row = Adw.SwitchRow(title="Detect People", subtitle="Find photos with faces for the People collection")
        if faces.is_available():
            self._settings.bind("face-detection", face_row, "active")
            face_row.connect("notify::active", self._on_face_detection_toggled)
        else:
            face_row.set_sensitive(False)
            face_row.set_subtitle("Install python3-opencv to enable local face detection")
        smart.add(face_row)
        page.add(smart)
        return page

    def _drives_group(self):
        group = Adw.PreferencesGroup(title="Drives", description="Turn a drive off to hide its photos and videos until you turn it back on")
        for path, label, removable, online, enabled in self._library.roots():
            title = "Computer" if path == "/" else label
            subtitle = path if online else f"{path}  ·  Disconnected"
            row = Adw.SwitchRow(title=GLib.markup_escape_text(title), subtitle=GLib.markup_escape_text(subtitle), active=bool(enabled))
            row.add_prefix(Gtk.Image(icon_name=icons.DRIVE_REMOVABLE if removable else icons.DRIVE))
            row.connect("notify::active", lambda switch, _param, root=path: self._library.set_root_enabled(root, switch.get_active()))
            group.add(row)
        return group

    def _appearance_page(self):
        page = Adw.PreferencesPage(title="Appearance", icon_name=icons.GRID)
        group = Adw.PreferencesGroup(title="Style")
        scheme = Adw.ComboRow(title="Color Scheme", model=Gtk.StringList.new([label for _value, label in COLOR_SCHEMES]))
        values = [value for value, _label in COLOR_SCHEMES]
        current = self._settings.get("color-scheme")
        scheme.set_selected(values.index(current) if current in values else 0)
        scheme.connect("notify::selected", lambda row, _param: self._settings.set("color-scheme", values[row.get_selected()]))
        group.add(scheme)
        size = Adw.SpinRow.new_with_range(0, 8, 1)
        size.set_title("Thumbnail Size")
        size.set_subtitle("Also adjustable with Ctrl + scroll or pinch in any grid")
        self._settings.bind("grid-zoom-level", size, "value")
        group.add(size)
        page.add(group)
        return page

    def _privacy_page(self):
        page = Adw.PreferencesPage(title="Privacy", icon_name=icons.LOCK)
        history = Adw.PreferencesGroup(title="History")
        history.add(self._switch_row("Remember Recently Viewed", "Show viewed items in Recents", "remember-recents"))
        clear_recents = Adw.ActionRow(title="Clear Recently Viewed", activatable=True)
        clear_recents.add_suffix(Gtk.Image(icon_name=icons.CLEAR))
        clear_recents.connect("activated", lambda _row: self._clear_recents())
        history.add(clear_recents)
        history.add(self._switch_row("Remember Search History", "Offer previous searches as suggestions", "remember-search-history"))
        clear_search = Adw.ActionRow(title="Clear Search History", activatable=True)
        clear_search.add_suffix(Gtk.Image(icon_name=icons.CLEAR))
        clear_search.connect("activated", lambda _row: self._clear_search())
        history.add(clear_search)
        page.add(history)
        hidden = Adw.PreferencesGroup(title="Hidden Album", description="Require a password to open the Hidden album")
        self._password_row = Adw.ActionRow(title="Password Protection", activatable=True)
        self._password_row.connect("activated", lambda _row: self._edit_password())
        self._password_row.add_suffix(Gtk.Image(icon_name=icons.GO_NEXT))
        hidden.add(self._password_row)
        page.add(hidden)
        self._update_password_row()
        network = Adw.PreferencesGroup(title="Network")
        network.add(self._switch_row("Online Map Tiles", "Download map imagery from OpenStreetMap for the Map view", "online-map-tiles"))
        page.add(network)
        return page

    def _storage_page(self):
        page = Adw.PreferencesPage(title="Storage", icon_name=icons.STORAGE)
        cache = Adw.PreferencesGroup(title="Thumbnails")
        cache.add(self._switch_row("Generate Thumbnails in Background", "Prepare thumbnails for the whole library while idle", "background-thumbnails"))
        self._cache_row = Adw.ActionRow(title="Thumbnail Cache", subtitle="Calculating…")
        clear = Gtk.Button(label="Clear", valign=Gtk.Align.CENTER)
        clear.add_css_class("destructive-action")
        clear.connect("clicked", lambda _button: self._clear_cache())
        self._cache_row.add_suffix(clear)
        cache.add(self._cache_row)
        page.add(cache)
        trash = Adw.PreferencesGroup(title="Trash")
        retention = Adw.SpinRow.new_with_range(1, 365, 1)
        retention.set_title("Delete Trashed Items After")
        retention.set_subtitle("Days")
        self._settings.bind("trash-retention-days", retention, "value")
        trash.add(retention)
        page.add(trash)
        maintenance = Adw.PreferencesGroup(title="Maintenance", description="Your library database and cache are stored in your personal folders and are only removed when you choose to.")
        rebuild = Adw.ActionRow(title="Rebuild Library", subtitle="Re-read every file. Favorites, albums and hidden items are kept.", activatable=True)
        rebuild.add_suffix(Gtk.Image(icon_name=icons.RESCAN))
        rebuild.connect("activated", lambda _row: self._rebuild())
        maintenance.add(rebuild)
        page.add(maintenance)
        self._update_cache_size()
        return page

    def _refresh_folders(self, key):
        group = self._included_group if key == "included-folders" else self._excluded_group
        for row in self._folder_rows[key]:
            group.remove(row)
        self._folder_rows[key] = []
        folders = self._settings.get(key)
        if not folders:
            placeholder = Adw.ActionRow(title="No folders")
            placeholder.add_css_class("dim-label")
            group.add(placeholder)
            self._folder_rows[key].append(placeholder)
            return
        for folder in folders:
            row = Adw.ActionRow(title=GLib.markup_escape_text(folder))
            row.add_prefix(Gtk.Image(icon_name=icons.FOLDERS))
            remove = Gtk.Button(icon_name=icons.CLEAR, valign=Gtk.Align.CENTER, tooltip_text="Remove")
            remove.add_css_class("flat")
            set_accessible_label(remove, f"Remove {folder}")
            remove.connect("clicked", lambda _button, value=folder: self._remove_folder(key, value))
            row.add_suffix(remove)
            group.add(row)
            self._folder_rows[key].append(row)

    def _choose_folder(self, key):
        dialog = Gtk.FileDialog(title="Choose Folder", modal=True)

        def finished(dialog_object, result):
            try:
                folder = dialog_object.select_folder_finish(result)
            except GLib.Error:
                return
            path = folder.get_path() if folder is not None else None
            if not path:
                return
            folders = list(self._settings.get(key))
            if path not in folders:
                folders.append(path)
                self._settings.set(key, folders)
                self._refresh_folders(key)
                if key == "included-folders":
                    self._library.scan()

        dialog.select_folder(self.get_root(), None, finished)

    def _remove_folder(self, key, folder):
        folders = [value for value in self._settings.get(key) if value != folder]
        self._settings.set(key, folders)
        self._refresh_folders(key)
        if key == "excluded-folders":
            self._library.scan()

    def _rescan(self):
        self._library.scan()
        self.add_toast(Adw.Toast(title="Scanning your library"))

    def _on_face_detection_toggled(self, row, _param):
        if row.get_active():
            self._library.start_face_detection()
        else:
            self._library.stop_face_detection()

    def _clear_recents(self):
        self._library.clear_recents()
        self.add_toast(Adw.Toast(title="Recently viewed cleared"))

    def _clear_search(self):
        self._library.clear_search_history()
        self.add_toast(Adw.Toast(title="Search history cleared"))

    def _update_password_row(self):
        protected = bool(self._settings.get("hidden-password-hash"))
        self._password_row.set_subtitle("On" if protected else "Off")

    def _edit_password(self):
        protected = bool(self._settings.get("hidden-password-hash"))
        dialog = Adw.AlertDialog(heading="Change Password" if protected else "Set Password", body="Choose a password for the Hidden album." if not protected else "Enter your current password, then a new one. Leave the new password empty to turn protection off.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        current = Gtk.PasswordEntry(placeholder_text="Current password", show_peek_icon=True, visible=protected)
        new = Gtk.PasswordEntry(placeholder_text="New password", show_peek_icon=True)
        repeat = Gtk.PasswordEntry(placeholder_text="Repeat new password", show_peek_icon=True, activates_default=True)
        error = Gtk.Label(css_classes=["error"], visible=False)
        for widget in (current, new, repeat, error):
            box.append(widget)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "_Cancel")
        dialog.add_response("save", "_Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        dialog.set_prefer_wide_layout(False)

        def on_response(_dialog, response):
            if response != "save":
                return
            if protected and not security.verify_password(current.get_text(), self._settings.get("hidden-password-hash")):
                self.add_toast(Adw.Toast(title="The current password is incorrect"))
                return
            if new.get_text() != repeat.get_text():
                self.add_toast(Adw.Toast(title="The new passwords do not match"))
                return
            if new.get_text():
                self._settings.set("hidden-password-hash", security.hash_password(new.get_text()))
                self.add_toast(Adw.Toast(title="Hidden album password saved"))
            else:
                self._settings.set("hidden-password-hash", "")
                self.add_toast(Adw.Toast(title="Password protection turned off"))
            self._update_password_row()

        dialog.connect("response", on_response)
        dialog.present(self)

    def _update_cache_size(self):
        run_in_thread(self._library.cache_size, lambda size, _error: self._cache_row.set_subtitle(formatting.human_size(size or 0)), name="picnest-cache-size")

    def _clear_cache(self):
        def run():
            self._cache_row.set_subtitle("Clearing…")
            self._library.clear_thumbnail_cache(self._update_cache_size)

        confirm(self.get_root(), "Clear Thumbnail Cache?", "Thumbnails will be generated again as you browse.", "_Clear", run)

    def _rebuild(self):
        confirm(self.get_root(), "Rebuild Library?", "Picnest will forget files it found and scan all locations again. Favorites, albums and hidden items are kept.", "_Rebuild", lambda: self._library.reset_library())
