import dataclasses

from gi.repository import Adw, GLib, Gtk

from picnest.core import security
from picnest.core.models import GridContent
from picnest.core.query import SearchFilters, ViewSpec
from picnest.ui import icons
from picnest.ui.grid_page import GridPage, sidebar_toggle
from picnest.ui.widgets import connect_weak, empty_state, icon_button, menu_button, set_accessible_label

ORIENTATIONS = (("any", "Any Orientation"), ("landscape", "Landscape"), ("portrait", "Portrait"), ("square", "Square"))
MEDIA_TYPES = (("all", "All Media"), ("photos", "Photos"), ("videos", "Videos"), ("raw", "RAW Photos"), ("screenshots", "Screenshots"), ("panoramas", "Panoramas"), ("animated", "Animated"))
SIZES = ((0, "Any Size"), (1024 * 1024, "Larger than 1 MB"), (10 * 1024 * 1024, "Larger than 10 MB"), (100 * 1024 * 1024, "Larger than 100 MB"))
SUGGESTION_ICONS = {"albums": icons.ALBUMS, "camera": icons.CAMERA, "folders": icons.FOLDERS, "years": icons.YEARS}
RESOLUTIONS = ((0.0, "Any Resolution"), (2.0, "2 MP or more"), (8.0, "8 MP or more"), (12.0, "12 MP or more"), (24.0, "24 MP or more"))


class LibraryPage(GridPage):
    __gtype_name__ = "PicnestLibraryPage"

    def __init__(self, context):
        settings = context.settings
        spec = ViewSpec("all", grouping=settings.get("library-grouping"), sort_field=settings.get("sort-field"), descending=settings.get("sort-descending"), title="Library")
        super().__init__(context, spec, icons.LIBRARY, "No Photos or Videos Yet", "Add folders in Preferences or connect a drive. New media appears here automatically.", tag="library", show_grouping_switcher=True)
        connect_weak(self.library, "scan-progress", self._on_scan_progress)
        self.banner.set_button_label("Stop")
        self.banner.connect("button-clicked", lambda _banner: self.library.cancel_scan())

    def on_view_options_changed(self):
        settings = self.context.settings
        settings.set("library-grouping", self.spec.grouping)
        settings.set("sort-field", self.spec.sort_field)
        settings.set("sort-descending", self.spec.descending)

    def _on_scan_progress(self, _library, scanning, found, _directory):
        if scanning:
            self.banner.set_title(f"Scanning for photos and videos… {found:,} found" if found else "Scanning for photos and videos…")
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)
        if self.stack.get_visible_child_name() == "empty":
            self.update_empty_state()


class HiddenPage(GridPage):
    __gtype_name__ = "PicnestHiddenPage"

    def __init__(self, context):
        spec = ViewSpec("hidden", grouping="months", title="Hidden")
        self._lock_button = icon_button(icons.LOCK, "Lock Hidden Album", self.lock)
        super().__init__(context, spec, icons.HIDDEN, "No Hidden Items", "Hide photos and videos to keep them out of your library.", tag="hidden", header_widgets=(self._lock_button,))
        self._unlocked = False
        self._lock_page = self._build_lock_page()
        self.stack.add_named(self._lock_page, "locked")
        context.settings.connect_changed("hidden-password-hash", lambda _value: self._apply_lock_state())
        self._apply_lock_state()

    def _password_hash(self):
        return self.context.settings.get("hidden-password-hash")

    def _build_lock_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER, width_request=280)
        self._password_entry = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text="Password")
        self._password_entry.connect("activate", lambda _entry: self._try_unlock())
        set_accessible_label(self._password_entry, "Hidden album password")
        self._error_label = Gtk.Label(label="", css_classes=["error"])
        unlock = Gtk.Button(label="Unlock")
        unlock.add_css_class("pill")
        unlock.add_css_class("suggested-action")
        unlock.connect("clicked", lambda _button: self._try_unlock())
        box.append(self._password_entry)
        box.append(self._error_label)
        box.append(unlock)
        return empty_state(icons.LOCK, "Hidden Album Is Locked", "Enter your password to view hidden items.", box)

    def _apply_lock_state(self):
        protected = bool(self._password_hash())
        self._lock_button.set_visible(protected and self._unlocked)
        if protected and not self._unlocked:
            self.stack.set_visible_child_name("locked")
            self.select_button.set_sensitive(False)
        else:
            self.select_button.set_sensitive(True)
            if self.stack.get_visible_child_name() == "locked":
                self.reload(keep_position=False)

    def _try_unlock(self):
        if security.verify_password(self._password_entry.get_text(), self._password_hash()):
            self._unlocked = True
            self._password_entry.set_text("")
            self._error_label.set_label("")
            self._apply_lock_state()
        else:
            self._error_label.set_label("Incorrect password")
            self._password_entry.grab_focus()

    def lock(self):
        self._unlocked = False
        self.end_selection()
        self._apply_lock_state()

    def _on_loaded(self, content, keep_position):
        if self._password_hash() and not self._unlocked:
            self._content = content
            return
        super()._on_loaded(content, keep_position)

    def _on_map(self, page):
        if self._password_hash() and not self._unlocked:
            self.stack.set_visible_child_name("locked")
            self._password_entry.grab_focus()
            return
        super()._on_map(page)


class TrashPage(GridPage):
    __gtype_name__ = "PicnestTrashPage"

    def __init__(self, context):
        spec = ViewSpec("trash", grouping="none", title="Trash")
        empty_button = Gtk.Button(label="Empty Trash", valign=Gtk.Align.CENTER)
        empty_button.add_css_class("destructive-action")
        empty_button.connect("clicked", lambda _button: context.actions.empty_trash())
        self._empty_button = empty_button
        super().__init__(context, spec, icons.TRASH, "Trash Is Empty", "Deleted items stay here before they are removed for good.", tag="trash", header_widgets=(empty_button,))
        self._update_banner()
        context.settings.connect_changed("trash-retention-days", lambda _value: self._update_banner())

    def _update_banner(self):
        days = self.context.settings.get("trash-retention-days")
        self.banner.set_title(f"Items are permanently deleted after {days} days")
        self.banner.set_revealed(True)

    def on_content_loaded(self, content):
        self._empty_button.set_sensitive(content.item_count > 0)
        self.banner.set_revealed(content.item_count > 0)


class DuplicatesPage(GridPage):
    __gtype_name__ = "PicnestDuplicatesPage"

    def __init__(self, context):
        spec = ViewSpec("duplicates", grouping="none", title="Duplicates")
        self._scan_button = icon_button(icons.SCAN, "Find Duplicates", lambda: context.library.start_duplicate_scan())
        self._select_extras = Gtk.Button(label="Select Extra Copies", valign=Gtk.Align.CENTER)
        self._select_extras.connect("clicked", lambda _button: self._select_extra_copies())
        super().__init__(context, spec, icons.DUPLICATES, "No Duplicates Found", "Picnest compares file contents to find exact copies across all of your drives.", tag="duplicates", header_widgets=(self._select_extras, self._scan_button))
        connect_weak(self.library, "duplicates-progress", self._on_progress)
        self._scanned_once = False

    def _on_map(self, page):
        super()._on_map(page)
        if not self._scanned_once:
            self._scanned_once = True
            self.library.start_duplicate_scan()

    def _on_progress(self, _library, done, total, finished):
        if finished:
            self.banner.set_revealed(False)
            self._scan_button.set_sensitive(True)
        else:
            self._scan_button.set_sensitive(False)
            self.banner.set_title(f"Comparing files… {done:,} of {total:,}")
            self.banner.set_revealed(True)

    def _select_extra_copies(self):
        extras = self.library.duplicate_extras()
        if extras:
            self.grid.select_ids(extras)

    def on_content_loaded(self, content):
        self._select_extras.set_sensitive(content.item_count > 0)


class SearchPage(GridPage):
    __gtype_name__ = "PicnestSearchPage"

    def __init__(self, context):
        spec = ViewSpec("search", grouping="months", title="Search")
        self._filters_button = menu_button(icons.FILTER, "Filters")
        self._filters_button.add_css_class("flat")
        super().__init__(context, spec, icons.SEARCH, "Search Your Library", "Find photos and videos by name, folder, camera, album, year or type.", tag="search", header_widgets=(self._filters_button,))
        self._search_source = 0
        self.entry = Gtk.SearchEntry(placeholder_text="Search photos and videos", hexpand=True, width_request=280)
        self.entry.set_key_capture_widget(None)
        set_accessible_label(self.entry, "Search photos and videos")
        self.entry.connect("search-changed", self._on_search_changed)
        self.entry.connect("activate", self._on_activate)
        self.entry.connect("stop-search", lambda _entry: self.entry.set_text(""))
        self.search_button.set_visible(False)
        clamp = Adw.Clamp(maximum_size=520, child=self.entry)
        self.header.set_title_widget(clamp)
        self._build_filters()
        self._build_suggestions()
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda _controller: self._show_suggestions())
        focus.connect("leave", lambda _controller: GLib.timeout_add(200, self._hide_suggestions_if_unfocused))
        self.entry.add_controller(focus)
        self.connect("unmap", lambda _page: self._suggestions.popdown())

    def _hide_suggestions_if_unfocused(self):
        if self.entry.get_focus_child() is None:
            self._suggestions.popdown()
        return GLib.SOURCE_REMOVE

    def _build_filters(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12, width_request=300)
        title = Gtk.Label(label="Filters", xalign=0)
        title.add_css_class("heading")
        box.append(title)
        self._type_dropdown = self._dropdown(box, "Type", [label for _value, label in MEDIA_TYPES])
        years = ["Any Year"] + [str(year) for year in self.library.available_years()]
        self._year_values = [None] + self.library.available_years()
        self._year_dropdown = self._dropdown(box, "Year", years)
        self._cameras = [""] + self.library.cameras()
        self._camera_dropdown = self._dropdown(box, "Camera", ["Any Camera"] + self._cameras[1:])
        self._albums = [None] + [album.id for album in self.library.albums()]
        self._album_dropdown = self._dropdown(box, "Album", ["Any Album"] + [album.name for album in self.library.albums()])
        self._orientation_dropdown = self._dropdown(box, "Orientation", [label for _value, label in ORIENTATIONS])
        self._size_dropdown = self._dropdown(box, "File Size", [label for _value, label in SIZES])
        self._resolution_dropdown = self._dropdown(box, "Resolution", [label for _value, label in RESOLUTIONS])
        self._location_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        location_row = Gtk.Box(spacing=8)
        location_row.append(Gtk.Label(label="Only with location", xalign=0, hexpand=True))
        location_row.append(self._location_switch)
        self._location_switch.connect("notify::active", lambda *_args: self._apply_filters())
        box.append(location_row)
        self._folder_entry = Gtk.Entry(placeholder_text="Folder or place name")
        set_accessible_label(self._folder_entry, "Folder or place name")
        self._folder_entry.connect("changed", lambda _entry: self._schedule_search())
        box.append(self._folder_entry)
        reset = Gtk.Button(label="Clear Filters")
        reset.connect("clicked", lambda _button: self._clear_filters())
        box.append(reset)
        self._filters_button.set_popover(Gtk.Popover(child=box))

    def _dropdown(self, box, label, options):
        row = Gtk.Box(spacing=8)
        row.append(Gtk.Label(label=label, xalign=0, hexpand=True))
        dropdown = Gtk.DropDown.new_from_strings(options)
        dropdown.set_valign(Gtk.Align.CENTER)
        set_accessible_label(dropdown, label)
        dropdown.connect("notify::selected", lambda *_args: self._apply_filters())
        row.append(dropdown)
        box.append(row)
        return dropdown

    def _build_suggestions(self):
        self._suggestions = Gtk.Popover(autohide=False, has_arrow=False, position=Gtk.PositionType.BOTTOM)
        self._suggestions.add_css_class("pn-suggestions")
        self._suggestion_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, width_request=320)
        self._suggestion_list.add_css_class("navigation-sidebar")
        self._suggestion_list.connect("row-activated", self._on_suggestion_activated)
        self._suggestions.set_child(self._suggestion_list)
        self._suggestions.set_parent(self.entry)
        click_away = Gtk.GestureClick()
        click_away.connect("pressed", lambda *_args: self._suggestions.popdown())
        self.grid.add_controller(click_away)

    def _show_suggestions(self):
        text = self.entry.get_text().strip()
        self._suggestion_list.remove_all()
        rows = []
        if text:
            for kind, value in self.library.search_suggestions(text):
                rows.append((SUGGESTION_ICONS.get(kind, icons.SEARCH), value, value))
        else:
            for query in self.library.search_history():
                rows.append((icons.HISTORY, query, query))
        if not rows:
            self._suggestions.popdown()
            return
        for icon_name, label, value in rows:
            row = Gtk.ListBoxRow()
            row.query_value = value
            content = Gtk.Box(spacing=10, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
            content.append(Gtk.Image(icon_name=icon_name))
            content.append(Gtk.Label(label=label, xalign=0, ellipsize=3))
            row.set_child(content)
            self._suggestion_list.append(row)
        if not text:
            clear_row = Gtk.ListBoxRow()
            clear_row.query_value = None
            clear_label = Gtk.Label(label="Clear Search History", xalign=0, margin_top=6, margin_bottom=6, margin_start=6)
            clear_label.add_css_class("dim-label")
            clear_row.set_child(clear_label)
            self._suggestion_list.append(clear_row)
        self._suggestions.popup()

    def _on_suggestion_activated(self, _list_box, row):
        if row.query_value is None:
            self.library.clear_search_history()
            self._suggestions.popdown()
            return
        self.entry.set_text(row.query_value)
        self.entry.set_position(-1)
        self._suggestions.popdown()
        self.library.add_search_history(row.query_value)

    def focus_entry(self):
        self.entry.grab_focus()

    def _on_search_changed(self, entry):
        if entry.get_focus_child() is not None:
            self._show_suggestions()
        self._schedule_search()

    def _on_activate(self, entry):
        self._suggestions.popdown()
        self.library.add_search_history(entry.get_text())
        self._run_search()

    def _schedule_search(self):
        if self._search_source:
            GLib.source_remove(self._search_source)
        self._search_source = GLib.timeout_add(180, self._run_search)

    def _apply_filters(self):
        self._schedule_search()

    def _clear_filters(self):
        for dropdown in (self._type_dropdown, self._year_dropdown, self._camera_dropdown, self._album_dropdown, self._orientation_dropdown, self._size_dropdown, self._resolution_dropdown):
            dropdown.set_selected(0)
        self._location_switch.set_active(False)
        self._folder_entry.set_text("")

    def _current_filters(self):
        return SearchFilters(
            text=self.entry.get_text(),
            media_type=MEDIA_TYPES[self._type_dropdown.get_selected()][0],
            year=self._year_values[self._year_dropdown.get_selected()] if self._year_dropdown.get_selected() < len(self._year_values) else None,
            camera=self._cameras[self._camera_dropdown.get_selected()] if self._camera_dropdown.get_selected() < len(self._cameras) else "",
            album_id=self._albums[self._album_dropdown.get_selected()] if self._album_dropdown.get_selected() < len(self._albums) else None,
            orientation=ORIENTATIONS[self._orientation_dropdown.get_selected()][0],
            minimum_size=SIZES[self._size_dropdown.get_selected()][0],
            minimum_megapixels=RESOLUTIONS[self._resolution_dropdown.get_selected()][0],
            has_location=self._location_switch.get_active(),
            location_text=self._folder_entry.get_text().strip(),
        )

    def _run_search(self):
        self._search_source = 0
        filters = self._current_filters()
        self.spec.filters = filters
        active = not filters.is_empty()
        if dataclasses.replace(filters, text="").is_empty():
            self._filters_button.remove_css_class("accent")
        else:
            self._filters_button.add_css_class("accent")
        if not active:
            self._content = None
            self.grid.set_content(GridContent(ids=[], sections=[], view_key="search:empty"), keep_position=False)
            self.empty_page.set_icon_name(icons.SEARCH)
            self.empty_page.set_title("Search Your Library")
            self.empty_page.set_description("Find photos and videos by name, folder, camera, album, year or type.")
            self.stack.set_visible_child_name("empty")
            self.window_title.set_subtitle("")
            return GLib.SOURCE_REMOVE
        self.reload(keep_position=False)
        return GLib.SOURCE_REMOVE

    def update_empty_state(self):
        self.empty_page.set_icon_name(icons.SEARCH)
        self.empty_page.set_title("No Results")
        self.empty_page.set_description("Try a different search term or remove some filters.")

    def _on_map(self, page):
        if self.spec.filters.is_empty():
            self._run_search()
        else:
            super()._on_map(page)
        GLib.idle_add(lambda: self.entry.grab_focus() and False)

    def _on_content_changed(self, library, reason):
        if not self.spec.filters.is_empty():
            super()._on_content_changed(library, reason)


class PlaceholderPage(Adw.NavigationPage):
    __gtype_name__ = "PicnestPlaceholderPage"

    def __init__(self, context, title, icon_name, heading, description, child=None):
        super().__init__(title=title)
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.pack_start(sidebar_toggle(context))
        toolbar.add_top_bar(header)
        toolbar.set_content(empty_state(icon_name, heading, description, child))
        self.set_child(toolbar)
