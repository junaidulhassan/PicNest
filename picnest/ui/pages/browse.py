import os

from gi.repository import Adw, Gio, GLib, Gtk

from picnest.core import formatting
from picnest.core.query import ViewSpec
from picnest.ui import icons
from picnest.ui.cards import CardGrid, MediaCard
from picnest.ui.dialogs.common import confirm, prompt_text
from picnest.ui.grid_page import GridPage, sidebar_toggle
from picnest.ui.widgets import CoverPicture, connect_weak, empty_state, icon_button, pill_button, set_accessible_label


class BrowsePage(Adw.NavigationPage):
    __gtype_name__ = "PicnestBrowsePage"

    def __init__(self, context, title, tag, icon_name, empty_title, empty_description):
        super().__init__(title=title, tag=tag)
        self.context = context
        self.library = context.library
        self._dirty = True
        toolbar = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT)
        self.header = Adw.HeaderBar()
        self.header.pack_start(sidebar_toggle(context))
        self.window_title = Adw.WindowTitle(title=title)
        self.header.set_title_widget(self.window_title)
        self.header.pack_end(icon_button(icons.SEARCH, "Search", action_name="win.search"))
        toolbar.add_top_bar(self.header)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=180)
        self.scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.stack.add_named(self.scrolled, "content")
        self.empty_page = empty_state(icon_name, empty_title, empty_description)
        self.stack.add_named(self.empty_page, "empty")
        toolbar.set_content(self.stack)
        self.toolbar = toolbar
        self.set_child(toolbar)
        connect_weak(self.library, "content-changed", self._on_content_changed)
        self.connect("map", self._on_map)

    def _on_map(self, _page):
        if self._dirty:
            self.refresh()

    def _on_content_changed(self, _library, _reason):
        if self.get_mapped():
            self.refresh()
        else:
            self._dirty = True

    def refresh(self):
        self._dirty = False
        self.library.run_query(self, self.load_data, self._on_data)

    def load_data(self, database):
        raise NotImplementedError

    def _on_data(self, data):
        if data is None:
            return
        if self.populate(data):
            self.stack.set_visible_child_name("content")
        else:
            self.stack.set_visible_child_name("empty")

    def populate(self, data):
        raise NotImplementedError

    def push_grid(self, spec, icon_name, empty_title, empty_description, header_widgets=(), page_class=GridPage):
        page = page_class(self.context, spec, icon_name, empty_title, empty_description, tag=None, header_widgets=header_widgets)
        navigation_view = self.get_ancestor(Adw.NavigationView)
        if navigation_view is not None:
            navigation_view.push(page)
        return page


class AlbumsPage(BrowsePage):
    __gtype_name__ = "PicnestAlbumsPage"

    def __init__(self, context):
        super().__init__(context, "Albums", "albums", icons.ALBUMS, "No Albums Yet", "Create albums to collect photos and videos from anywhere on your computer.")
        self.header.pack_end(icon_button(icons.ADD, "New Album", lambda: context.actions.new_album()))
        self.empty_page.set_child(pill_button("New Album", lambda: context.actions.new_album()))
        self._cards = CardGrid()
        self._cards.connect("card-activated", lambda _grid, album: self._open_album(album))
        self.scrolled.set_child(self._cards)
        connect_weak(self.library, "albums-changed", self._on_albums_changed)

    def _on_albums_changed(self, library):
        self._on_content_changed(library, "albums")

    def load_data(self, _database):
        return self.library.albums()

    def populate(self, albums):
        self._cards.clear()
        self.window_title.set_subtitle(formatting.plural(len(albums), "album") if albums else "")
        for album in albums:
            cover = self.library.item(album.cover_id) if album.cover_id else None
            card = MediaCard(self.library.thumbnails, album.name, formatting.plural(album.item_count, "item"), cover, 200, 200)
            self._cards.add_card(card, album)
        return bool(albums)

    def _open_album(self, album):
        spec = ViewSpec("album", album_id=album.id, grouping="none", title=album.name)
        rename = icon_button(icons.RENAME, "Rename Album", None)
        delete = icon_button(icons.TRASH, "Delete Album", None)
        page = self.push_grid(spec, icons.ALBUMS, "This Album Is Empty", "Select photos anywhere in Picnest and choose Add to Album.", header_widgets=(delete, rename))
        rename.connect("clicked", lambda _button: self._rename_album(album, page))
        delete.connect("clicked", lambda _button: self._delete_album(album))

    def _rename_album(self, album, page):
        def submit(name):
            self.library.rename_album(album.id, name)
            page.spec.title = name
            page.set_title(name)
            page.window_title.set_title(name)

        prompt_text(self.get_root(), "Rename Album", "", album.name, "_Rename", submit)

    def _delete_album(self, album):
        def run():
            self.library.delete_album(album.id)
            navigation_view = self.get_ancestor(Adw.NavigationView)
            if navigation_view is not None:
                navigation_view.pop_to_page(self)

        confirm(self.get_root(), f"Delete “{album.name}”?", "The album is removed, but its photos and videos stay in your library.", "_Delete Album", run)


class FoldersPage(BrowsePage):
    __gtype_name__ = "PicnestFoldersPage"

    def __init__(self, context):
        super().__init__(context, "Folders", "folders", icons.FOLDERS, "No Folders", "Folders that contain photos or videos appear here, grouped by drive.")
        self._filter_text = ""
        self._entries = []
        self._roots = []
        self._filter_entry = Gtk.SearchEntry(placeholder_text="Filter folders", width_request=220)
        set_accessible_label(self._filter_entry, "Filter folders")
        self._filter_entry.connect("search-changed", self._on_filter_changed)
        self.header.pack_end(self._filter_entry)
        self._content = Adw.Clamp(maximum_size=900, margin_top=12, margin_bottom=24, margin_start=12, margin_end=12)
        self.scrolled.set_child(self._content)

    def load_data(self, _database):
        return self.library.folders(), self.library.roots()

    def populate(self, data):
        self._entries, self._roots = data
        self.window_title.set_subtitle(formatting.plural(len(self._entries), "folder") if self._entries else "")
        return self._render()

    def _on_filter_changed(self, entry):
        self._filter_text = entry.get_text().strip().lower()
        if self._render():
            self.stack.set_visible_child_name("content")

    def _render(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        root_labels = {row[0]: (row[1], bool(row[2]), bool(row[3])) for row in self._roots}
        groups = {}
        shown = 0
        for entry in self._entries:
            if self._filter_text and self._filter_text not in entry.folder.lower():
                continue
            groups.setdefault(entry.root, []).append(entry)
        for root, entries in groups.items():
            label, removable, _online = root_labels.get(root, (os.path.basename(root) or root, False, True))
            title = "Computer" if root == "/" else label
            group = Adw.PreferencesGroup(title=GLib.markup_escape_text(title), description=GLib.markup_escape_text(root if root != "/" else "Internal storage"))
            header_icon = Gtk.Image(icon_name=icons.DRIVE_REMOVABLE if removable else icons.DRIVE)
            group.set_header_suffix(header_icon)
            for entry in entries[:400]:
                relative = os.path.relpath(entry.folder, root) if entry.folder.startswith(root) else entry.folder
                row = Adw.ActionRow(title=GLib.markup_escape_text(os.path.basename(entry.folder) or entry.folder), subtitle=GLib.markup_escape_text(relative), activatable=True)
                cover = CoverPicture(self.library.thumbnails, radius=8, size=(48, 48), small=True)
                cover.set_valign(Gtk.Align.CENTER)
                cover.set_item(self.library.item(entry.cover_id) if entry.cover_id else None)
                row.add_prefix(cover)
                count = Gtk.Label(label=f"{entry.item_count:,}", css_classes=["dim-label", "numeric"])
                row.add_suffix(count)
                row.add_suffix(Gtk.Image(icon_name=icons.GO_NEXT))
                row.connect("activated", lambda _row, folder=entry.folder: self._open_folder(folder))
                group.add(row)
                shown += 1
            if len(entries) > 400:
                more = Adw.ActionRow(title=f"{len(entries) - 400:,} more folders", subtitle="Use the filter to narrow the list")
                more.add_css_class("dim-label")
                group.add(more)
            box.append(group)
        self._content.set_child(box)
        return shown > 0

    def _open_folder(self, folder):
        spec = ViewSpec("folder", folder=folder, grouping="none", sort_field="date-taken", title=os.path.basename(folder) or folder)
        open_button = icon_button(icons.FOLDER_OPEN, "Open in Files", lambda: Gtk.FileLauncher.new(Gio.File.new_for_path(folder)).launch(self.get_root(), None, None))
        page = self.push_grid(spec, icons.FOLDERS, "This Folder Is Empty", "", header_widgets=(open_button,))
        page.window_title.set_subtitle(folder)


class YearsPage(BrowsePage):
    __gtype_name__ = "PicnestYearsPage"

    def __init__(self, context):
        super().__init__(context, "Years", "years", icons.YEARS, "No Photos Yet", "Your library grouped by year appears here.")
        self._cards = CardGrid()
        self._cards.set_max_children_per_line(6)
        self._cards.connect("card-activated", lambda _grid, entry: self._open_year(entry))
        self.scrolled.set_child(self._cards)

    def load_data(self, _database):
        return self.library.years()

    def populate(self, years):
        self._cards.clear()
        for entry in years:
            photos = entry.item_count - entry.video_count
            cover = self.library.item(entry.cover_id) if entry.cover_id else None
            card = MediaCard(self.library.thumbnails, str(entry.year), formatting.item_count_label(photos, entry.video_count), cover, 320, 220, large_title=True)
            self._cards.add_card(card, entry)
        self.window_title.set_subtitle(formatting.plural(len(years), "year") if years else "")
        return bool(years)

    def _open_year(self, entry):
        spec = ViewSpec("year", year=entry.year, grouping="months", title=str(entry.year))
        self.push_grid(spec, icons.YEARS, "Nothing From This Year", "")


class MemoriesPage(BrowsePage):
    __gtype_name__ = "PicnestMemoriesPage"

    def __init__(self, context):
        super().__init__(context, "Memories", "memories", icons.MEMORIES, "No Memories Yet", "Picnest creates highlights like “On This Day” as your library grows.")
        self._cards = CardGrid()
        self._cards.set_max_children_per_line(4)
        self._cards.connect("card-activated", lambda _grid, memory: self._open_memory(memory))
        self.scrolled.set_child(self._cards)

    def load_data(self, _database):
        return self.library.memories()

    def populate(self, memories):
        self._cards.clear()
        for memory in memories:
            cover = self.library.item(memory.cover_id) if memory.cover_id else None
            subtitle = f"{memory.subtitle}  ·  {formatting.plural(memory.item_count, 'item')}"
            card = MediaCard(self.library.thumbnails, memory.title, subtitle, cover, 360, 240, large_title=True)
            self._cards.add_card(card, memory)
        return bool(memories)

    def _open_memory(self, memory):
        play = icon_button(icons.SLIDESHOW, "Play Slideshow", None)
        page = self.push_grid(memory.spec, icons.MEMORIES, "Nothing Here Anymore", "", header_widgets=(play,))
        page.window_title.set_subtitle(memory.subtitle)
        play.connect("clicked", lambda _button: self.context.open_viewer(list(page.grid.ids), 0, None, slideshow=True) if page.grid.ids else None)
