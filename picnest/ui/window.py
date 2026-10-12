from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from picnest.config import APP_NAME
from picnest.core.query import ViewSpec
from picnest.ui import icons
from picnest.ui.dialogs.common import show_error
from picnest.ui.dialogs.welcome import WelcomeView
from picnest.ui.editor import EditorView
from picnest.ui.grid_page import GridPage
from picnest.ui.pages.browse import AlbumsPage, FoldersPage, MemoriesPage, YearsPage
from picnest.ui.pages.collections import DuplicatesPage, HiddenPage, LibraryPage, PlaceholderPage, SearchPage, TrashPage
from picnest.ui.pages.insights import InsightsPage
from picnest.ui.viewer import Viewer
from picnest.ui.widgets import connect_weak, make_spinner, menu_button

SIDEBAR_SECTIONS = (
    ("Library", (
        ("library", "Library", icons.LIBRARY, "all"),
        ("memories", "Memories", icons.MEMORIES, None),
        ("years", "Years", icons.YEARS, None),
        ("favorites", "Favorites", icons.FAVORITE, "favorites"),
        ("recents", "Recents", icons.RECENTS, None),
        ("videos", "Videos", icons.VIDEOS, "videos"),
    )),
    ("Collections", (
        ("albums", "Albums", icons.ALBUMS, None),
        ("folders", "Folders", icons.FOLDERS, None),
    )),
    ("Smart Collections", (
        ("screenshots", "Screenshots", icons.SCREENSHOTS, "screenshots"),
        ("panoramas", "Panoramas", icons.PANORAMAS, "panoramas"),
        ("people", "People", icons.PEOPLE, "people"),
        ("duplicates", "Duplicates", icons.DUPLICATES, None),
    )),
    ("Other", (
        ("hidden", "Hidden", icons.HIDDEN, "hidden"),
        ("trash", "Trash", icons.TRASH, "trash"),
        ("insights", "Storage", icons.INSIGHTS, None),
    )),
)

SIMPLE_COLLECTIONS = {
    "favorites": (icons.FAVORITE, "No Favorites Yet", "Click the heart on any photo or video to find it here quickly."),
    "videos": (icons.VIDEOS, "No Videos", "Videos from all of your drives appear here."),
    "screenshots": (icons.SCREENSHOTS, "No Screenshots", "Screenshots are detected automatically by name and folder."),
    "panoramas": (icons.PANORAMAS, "No Panoramas", "Wide panoramic photos appear here automatically."),
}


class SidebarRow(Gtk.ListBoxRow):
    __gtype_name__ = "PicnestSidebarRow"

    def __init__(self, key, title, icon_name, section_title):
        super().__init__()
        self.key = key
        self.title = title
        self.section_title = section_title
        box = Gtk.Box(spacing=12)
        box.append(Gtk.Image(icon_name=icon_name))
        label = Gtk.Label(label=title, xalign=0, hexpand=True, ellipsize=3)
        box.append(label)
        self.count_label = Gtk.Label(xalign=1)
        self.count_label.add_css_class("dim-label")
        self.count_label.add_css_class("numeric")
        self.count_label.add_css_class("caption")
        box.append(self.count_label)
        self.set_child(box)
        self.update_property([Gtk.AccessibleProperty.LABEL], [title])

    def set_count(self, count):
        self.count_label.set_label(f"{count:,}" if count else "")


class MainWindow(Adw.ApplicationWindow):
    __gtype_name__ = "PicnestMainWindow"

    def __init__(self, application, context):
        settings = context.settings
        super().__init__(application=application, title=APP_NAME, default_width=settings.get("window-width"), default_height=settings.get("window-height"))
        self.set_size_request(360, 480)
        if settings.get("window-maximized"):
            self.maximize()
        self.context = context
        self.library = context.library
        self.settings = settings
        context.window = self
        self._sections = {}
        self._rows = {}
        self._counts_source = 0
        self._build()
        self._install_actions()
        self._setup_drop_target()
        connect_weak(self.library, "content-changed", self._on_content_changed)
        connect_weak(self.library, "items-updated", self._on_items_updated)
        self.connect("close-request", self._on_close_request)
        if settings.get("first-run-complete"):
            self._show_main()
        else:
            self._main_stack.set_visible_child_name("welcome")
            GLib.idle_add(lambda: self._welcome.focus_start() and False)

    def _build(self):
        root = Gtk.Overlay()
        self._toast_overlay = Adw.ToastOverlay()
        self.context.toast_overlay = self._toast_overlay
        root.set_child(self._toast_overlay)
        self._main_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=300)
        self._toast_overlay.set_child(self._main_stack)
        self._welcome = WelcomeView()
        self._welcome.connect("start-requested", self._on_start_requested)
        self._main_stack.add_named(self._welcome, "welcome")
        self._split_view = Adw.OverlaySplitView(min_sidebar_width=220, max_sidebar_width=280, sidebar_width_fraction=0.22)
        self.context.split_view = self._split_view
        self._split_view.set_sidebar(self._build_sidebar())
        self._content_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=200, hexpand=True)
        self._split_view.set_content(self._content_stack)
        self._main_stack.add_named(self._split_view, "main")
        self._viewer = Viewer(self.context)
        self._viewer.connect("closed", self._on_viewer_closed)
        self.context.viewer = self._viewer
        root.add_overlay(self._viewer)
        self._editor = EditorView(self.context)
        self._editor.connect("closed", self._on_editor_closed)
        self.context.editor = self._editor
        root.add_overlay(self._editor)
        self.set_content(root)
        self.context.current_navigation_view = self._current_navigation_view
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 760sp"))
        breakpoint.add_setter(self._split_view, "collapsed", GObject.Value(bool, True))
        self.add_breakpoint(breakpoint)

    def _build_sidebar(self):
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        title_box = Gtk.Box(spacing=8)
        logo = Gtk.Image(icon_name=self.get_application().get_application_id(), pixel_size=24)
        logo.update_property([Gtk.AccessibleProperty.LABEL], [f"{APP_NAME} logo"])
        title_box.append(logo)
        title_label = Gtk.Label(label=APP_NAME)
        title_label.add_css_class("heading")
        title_label.add_css_class("pn-app-title")
        title_box.append(title_label)
        header.set_title_widget(title_box)
        menu = Gio.Menu()
        primary = Gio.Menu()
        primary.append("Rescan Library", "app.rescan")
        primary.append("Import Photos and Videos…", "win.import")
        menu.append_section(None, primary)
        secondary = Gio.Menu()
        secondary.append("Preferences", "app.preferences")
        secondary.append("Keyboard Shortcuts", "app.shortcuts")
        secondary.append(f"About {APP_NAME}", "app.about")
        menu.append_section(None, secondary)
        menu_widget = menu_button(icons.MENU, "Main Menu", menu)
        menu_widget.set_primary(True)
        menu_widget.add_css_class("flat")
        header.pack_end(menu_widget)
        toolbar.add_top_bar(header)
        self._sidebar_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self._sidebar_list.add_css_class("navigation-sidebar")
        self._sidebar_list.update_property([Gtk.AccessibleProperty.LABEL], ["Sections"])
        for section_title, entries in SIDEBAR_SECTIONS:
            for key, title, icon_name, _collection in entries:
                row = SidebarRow(key, title, icon_name, section_title)
                self._rows[key] = row
                self._sidebar_list.append(row)
        self._sidebar_list.set_header_func(self._sidebar_header)
        self._sidebar_list.connect("row-selected", self._on_row_selected)
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=self._sidebar_list)
        toolbar.set_content(scrolled)
        self._scan_status = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_top=8, margin_bottom=10, visible=False)
        self._scan_status.add_css_class("pn-scan-status")
        self._scan_status.append(make_spinner(16))
        self._scan_label = Gtk.Label(xalign=0, ellipsize=3, hexpand=True)
        self._scan_label.add_css_class("caption")
        self._scan_status.append(self._scan_label)
        toolbar.add_bottom_bar(self._scan_status)
        connect_weak(self.library, "scan-progress", self._on_scan_progress)
        return toolbar

    def _sidebar_header(self, row, before):
        if before is None or before.section_title != row.section_title:
            if before is None and row.section_title == "Library":
                row.set_header(None)
                return
            label = Gtk.Label(label=row.section_title, xalign=0)
            label.add_css_class("pn-sidebar-heading")
            row.set_header(label)
        else:
            row.set_header(None)

    def _create_section(self, key):
        context = self.context
        if key == "library":
            root = LibraryPage(context)
        elif key in SIMPLE_COLLECTIONS:
            icon_name, empty_title, empty_description = SIMPLE_COLLECTIONS[key]
            root = GridPage(context, ViewSpec(key, grouping="months", title=self._rows[key].title), icon_name, empty_title, empty_description, tag=key)
        elif key == "recents":
            root = GridPage(context, ViewSpec("recents", grouping="none", title="Recents"), icons.RECENTS, "Nothing Recent", "Recently added and viewed items appear here.", tag="recents")
        elif key == "people":
            if self.library.faces_available():
                description = "Turn on Detect People in Preferences to find photos with faces." if not self.settings.get("face-detection") else "Photos with faces appear here as they are analyzed."
                root = GridPage(context, ViewSpec("people", grouping="months", title="People"), icons.PEOPLE, "No People Found", description, tag="people")
            else:
                root = PlaceholderPage(context, "People", icons.PEOPLE, "People Detection Unavailable", "Install python3-opencv to detect faces locally. Nothing is uploaded.")
        elif key == "hidden":
            root = HiddenPage(context)
        elif key == "trash":
            root = TrashPage(context)
        elif key == "duplicates":
            root = DuplicatesPage(context)
        elif key == "albums":
            root = AlbumsPage(context)
        elif key == "folders":
            root = FoldersPage(context)
        elif key == "years":
            root = YearsPage(context)
        elif key == "memories":
            root = MemoriesPage(context)
        elif key == "insights":
            root = InsightsPage(context)
        elif key == "search":
            root = SearchPage(context)
        else:
            return None
        navigation_view = Adw.NavigationView()
        navigation_view.add(root)
        self._sections[key] = navigation_view
        self._content_stack.add_named(navigation_view, key)
        return navigation_view

    def show_section(self, key):
        navigation_view = self._sections.get(key) or self._create_section(key)
        if navigation_view is None:
            return
        self._content_stack.set_visible_child(navigation_view)
        row = self._rows.get(key)
        if row is not None and self._sidebar_list.get_selected_row() is not row:
            self._sidebar_list.select_row(row)
        elif row is None:
            self._sidebar_list.unselect_all()
        if self._split_view.get_collapsed():
            self._split_view.set_show_sidebar(False)

    def _current_navigation_view(self):
        child = self._content_stack.get_visible_child()
        return child if isinstance(child, Adw.NavigationView) else None

    def _on_row_selected(self, _list_box, row):
        if row is not None:
            self.show_section(row.key)

    def _show_main(self):
        self._main_stack.set_visible_child_name("main")
        self.show_section("library")
        self.library.start()
        self.library.scan()
        self._schedule_counts()

    def _on_start_requested(self, _welcome, scan_all):
        self.settings.set("scan-all-drives", scan_all)
        self.settings.set("first-run-complete", True)
        self._show_main()

    def _install_actions(self):
        entries = {
            "search": self._activate_search,
            "toggle-sidebar": lambda: self._split_view.set_show_sidebar(not self._split_view.get_show_sidebar()),
            "import": self._import,
            "section-library": lambda: self.show_section("library"),
            "section-favorites": lambda: self.show_section("favorites"),
            "section-albums": lambda: self.show_section("albums"),
            "section-memories": lambda: self.show_section("memories"),
            "section-years": lambda: self.show_section("years"),
            "section-videos": lambda: self.show_section("videos"),
            "section-folders": lambda: self.show_section("folders"),
            "section-trash": lambda: self.show_section("trash"),
        }
        for name, callback in entries.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _action, _parameter, function=callback: function())
            self.add_action(action)

    def _activate_search(self):
        if self._main_stack.get_visible_child_name() != "main" or self._viewer.is_open:
            return
        self.show_section("search")
        page = self._sections["search"].get_visible_page()
        if isinstance(page, SearchPage):
            page.focus_entry()

    def _import(self):
        dialog = Gtk.FileDialog(title="Import Photos and Videos", modal=True)
        media_filter = Gtk.FileFilter(name="Photos and Videos")
        media_filter.add_mime_type("image/*")
        media_filter.add_mime_type("video/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(media_filter)
        dialog.set_filters(filters)

        def finished(dialog_object, result):
            try:
                files = dialog_object.open_multiple_finish(result)
            except GLib.Error:
                return
            paths = [files.get_item(index).get_path() for index in range(files.get_n_items()) if files.get_item(index).get_path()]
            self._import_paths(paths)

        dialog.open_multiple(self, None, finished)

    def _import_paths(self, paths):
        if not paths:
            return

        def done(result):
            if result.succeeded:
                self.context.show_toast(f"Imported {len(result.succeeded):,} item" + ("" if len(result.succeeded) == 1 else "s"))
            if result.failures:
                show_error(self, "Some files could not be imported", result.failures)

        self.library.import_files(paths, done)

    def _setup_drop_target(self):
        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        target.connect("drop", self._on_drop)
        self._split_view.add_controller(target)

    def _on_drop(self, _target, value, _x, _y):
        files = value.get_files() if hasattr(value, "get_files") else []
        paths = [file.get_path() for file in files if file.get_path()]
        if not paths:
            return False
        self._import_paths(paths)
        return True

    def _on_viewer_closed(self, _viewer, item_id):
        if item_id >= 0:
            self.context.focus_item(item_id)

    def _on_editor_closed(self, _editor):
        if self._viewer.is_open:
            self._viewer.grab_focus()
            self._viewer.refresh_current()

    def _on_items_updated(self, _library, _item_ids):
        if self._viewer.is_open:
            self._viewer.refresh_current()

    def _on_content_changed(self, _library, _reason):
        self._schedule_counts()

    def _schedule_counts(self):
        if not self._counts_source:
            self._counts_source = GLib.timeout_add(600, self._refresh_counts)

    def _refresh_counts(self):
        self._counts_source = 0
        self.library.run_query(self, lambda _database: (self.library.section_counts(), len(self.library.albums())), self._apply_counts)
        return GLib.SOURCE_REMOVE

    def _apply_counts(self, result):
        if result is None:
            return
        counts, album_count = result
        counts["library"] = counts.get("all", 0)
        for key, row in self._rows.items():
            if key in counts:
                row.set_count(counts[key])
        self._rows["albums"].set_count(album_count)
        self._rows["people"].set_visible(counts.get("people", 0) > 0 or self.settings.get("face-detection"))

    def _on_scan_progress(self, _library, scanning, found, _directory):
        self._scan_status.set_visible(scanning)
        self._scan_label.set_label(f"Scanning… {found:,} found" if found else "Scanning…")

    def _on_close_request(self, _window):
        if self._editor.get_visible():
            self._editor.request_close()
            return True
        if not self.is_maximized() and not self.is_fullscreen():
            width, height = self.get_default_size()
            self.settings.set("window-width", width)
            self.settings.set("window-height", height)
        self.settings.set("window-maximized", self.is_maximized())
        return False

    @property
    def viewer(self):
        return self._viewer
