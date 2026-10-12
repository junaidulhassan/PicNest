import dataclasses

from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk

from picnest.core import formatting
from picnest.ui import icons
from picnest.ui.item_actions import ITEM_ACTION_NAMES, build_item_menu
from picnest.ui.media_grid import VirtualGrid
from picnest.ui.widgets import connect_weak, empty_state, icon_button, icon_toggle, make_spinner, menu_button, set_accessible_label

GROUPING_LABELS = (("years", "Years"), ("months", "Months"), ("days", "Days"), ("none", "All"))
SORT_LABELS = (
    ("date-taken", "Date Taken"),
    ("date-modified", "Date Modified"),
    ("name", "Name"),
    ("size", "Size"),
    ("type", "Type"),
)
SORTABLE_COLLECTIONS = {"all", "favorites", "videos", "photos", "screenshots", "panoramas", "live", "people", "album", "folder", "year", "hidden", "search", "memory"}
GROUPABLE_COLLECTIONS = {"all", "favorites", "videos", "photos", "screenshots", "panoramas", "live", "people", "album", "folder", "year", "hidden", "search"}


def sidebar_toggle(context):
    button = icon_toggle(icons.SIDEBAR, "Show Sidebar")
    split_view = context.split_view
    if split_view is not None:
        split_view.bind_property("show-sidebar", button, "active", 3)
        split_view.bind_property("collapsed", button, "visible", 2)
    return button


class SelectionBar(Gtk.Revealer):
    __gtype_name__ = "PicnestSelectionBar"

    def __init__(self, collection, album_id=None):
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_UP, transition_duration=220, reveal_child=False)
        self.add_css_class("pn-selection-bar")
        bar = Gtk.ActionBar()
        self._count_label = Gtk.Label(xalign=0)
        self._count_label.add_css_class("heading")
        bar.pack_start(self._count_label)
        center = Gtk.Box(spacing=4)
        if collection == "trash":
            center.append(self._labeled("item.restore", icons.RESTORE, "Restore"))
            center.append(self._labeled("item.delete-permanently", icons.DELETE_FOREVER, "Delete", destructive=True))
        else:
            self._favorite_button = icon_button(icons.FAVORITE, "Favorite", action_name="item.toggle-favorite")
            center.append(self._favorite_button)
            center.append(icon_button(icons.ALBUM_ADD, "Add to Album", action_name="item.add-to-album"))
            if collection == "album" and album_id is not None:
                center.append(icon_button(icons.CLEAR, "Remove from Album", action_name="item.remove-from-album"))
            share_menu = Gio.Menu()
            share_menu.append("Copy to Clipboard", "item.copy-to-clipboard")
            share_menu.append("Open With…", "item.open-with")
            share_menu.append("Send by Email…", "item.email")
            share_menu.append("Export…", "item.export")
            share_button = menu_button(icons.SHARE, "Share", share_menu)
            share_button.add_css_class("flat")
            center.append(share_button)
            if collection == "hidden":
                center.append(icon_button(icons.VISIBLE, "Unhide", action_name="item.unhide"))
            else:
                center.append(icon_button(icons.HIDDEN, "Hide", action_name="item.hide"))
            center.append(icon_button(icons.TRASH, "Move to Trash", action_name="item.trash"))
            more_menu = Gio.Menu()
            more_menu.append("Move To…", "item.move")
            more_menu.append("Copy To…", "item.copy")
            more_menu.append("Show in Files", "item.show-in-files")
            more_menu.append("Slideshow", "item.slideshow")
            selection_section = Gio.Menu()
            selection_section.append("Select All", "page.select-all")
            selection_section.append("Deselect All", "page.deselect-all")
            more_menu.append_section(None, selection_section)
            more_button = menu_button(icons.MORE, "More Actions", more_menu)
            more_button.add_css_class("flat")
            center.append(more_button)
        bar.set_center_widget(center)
        done = Gtk.Button(label="Done")
        done.set_action_name("page.end-selection")
        bar.pack_end(done)
        self.set_child(bar)

    def _labeled(self, action_name, icon_name, label, destructive=False):
        button = Gtk.Button(child=Adw.ButtonContent(icon_name=icon_name, label=label), action_name=action_name)
        button.add_css_class("flat")
        if destructive:
            button.add_css_class("destructive-action")
        return button

    def update(self, count, summary, all_favorite):
        self._count_label.set_label(summary if count else "")
        if hasattr(self, "_favorite_button"):
            self._favorite_button.set_icon_name(icons.FAVORITE_FILLED if all_favorite else icons.FAVORITE)
            label = "Remove from Favorites" if all_favorite else "Add to Favorites"
            self._favorite_button.set_tooltip_text(label)
            set_accessible_label(self._favorite_button, label)
        self.set_reveal_child(count > 0)


class GridPage(Adw.NavigationPage):
    __gtype_name__ = "PicnestGridPage"

    def __init__(self, context, spec, icon_name, empty_title, empty_description, tag=None, show_grouping_switcher=False, header_widgets=()):
        super().__init__(title=spec.title, tag=tag or spec.key())
        self.context = context
        self.library = context.library
        self.spec = spec
        self._icon_name = icon_name
        self._empty_title = empty_title
        self._empty_description = empty_description
        self._dirty = True
        self._loaded_once = False
        self._context_targets = None
        self._loading_source = 0
        self._content = None
        self._grouping_buttons = {}
        self._build(show_grouping_switcher, header_widgets)
        self._install_actions()
        connect_weak(self.library, "content-changed", self._on_content_changed)
        connect_weak(self.library, "items-updated", self._on_items_updated)
        self.connect("map", self._on_map)
        self.connect("unmap", lambda _page: self.grid.stop_previews())

    def _build(self, show_grouping_switcher, header_widgets):
        self.toolbar = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT)
        self.header = Adw.HeaderBar()
        self.header.pack_start(sidebar_toggle(self.context))
        self.window_title = Adw.WindowTitle(title=self.spec.title)
        if show_grouping_switcher:
            self.header.set_title_widget(self._build_grouping_switcher())
        else:
            self.header.set_title_widget(self.window_title)
        self.header.pack_end(self._build_view_menu())
        self.select_button = icon_toggle(icons.SELECT, "Select Items")
        self.select_button.connect("toggled", self._on_select_toggled)
        self.header.pack_end(self.select_button)
        self.search_button = icon_button(icons.SEARCH, "Search", action_name="win.search")
        self.header.pack_end(self.search_button)
        for widget in header_widgets:
            self.header.pack_end(widget)
        self.toolbar.add_top_bar(self.header)
        self.banner = Adw.Banner(revealed=False)
        self.toolbar.add_top_bar(self.banner)
        self.grid = VirtualGrid(self.library, self.context.settings)
        self.grid.connect("item-activated", self._on_item_activated)
        self.grid.connect("selection-changed", self._on_selection_changed)
        self.grid.connect("context-menu", self._on_context_menu)
        self.scrolled = Gtk.ScrolledWindow(child=self.grid, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.scrolled.add_css_class("pn-grid-scroller")
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=180)
        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, spacing=12)
        loading.append(make_spinner(32))
        self.stack.add_named(loading, "loading")
        self.empty_page = empty_state(self._icon_name, self._empty_title, self._empty_description)
        self.stack.add_named(self.empty_page, "empty")
        self.stack.add_named(self.scrolled, "grid")
        self.stack.set_visible_child_name("loading")
        self.toolbar.set_content(self.stack)
        self.selection_bar = SelectionBar(self.spec.collection, self.spec.album_id)
        self.toolbar.add_bottom_bar(self.selection_bar)
        self.set_child(self.toolbar)
        self._popover = None

    def _build_grouping_switcher(self):
        box = Gtk.Box(css_classes=["linked", "pn-segmented"], halign=Gtk.Align.CENTER)
        group_leader = None
        current = self.spec.grouping
        for value, label in GROUPING_LABELS:
            button = Gtk.ToggleButton(label=label)
            if group_leader is None:
                group_leader = button
            else:
                button.set_group(group_leader)
            button.set_active(value == current)
            button.connect("toggled", self._on_grouping_toggled, value)
            box.append(button)
            self._grouping_buttons[value] = button
        return box

    def _build_view_menu(self):
        menu = Gio.Menu()
        if self.spec.collection in SORTABLE_COLLECTIONS:
            sort_section = Gio.Menu()
            for value, label in SORT_LABELS:
                sort_section.append(label, f"page.sort-field::{value}")
            menu.append_section("Sort By", sort_section)
            order_section = Gio.Menu()
            order_section.append("Newest or Largest First", "page.sort-descending::true")
            order_section.append("Oldest or Smallest First", "page.sort-descending::false")
            menu.append_section(None, order_section)
        if self.spec.collection in GROUPABLE_COLLECTIONS:
            group_section = Gio.Menu()
            for value, label in GROUPING_LABELS:
                group_section.append("No Grouping" if value == "none" else f"Group by {label}", f"page.grouping::{value}")
            menu.append_section("Grouping", group_section)
        layout_section = Gio.Menu()
        layout_section.append("Larger Thumbnails", "page.zoom-in")
        layout_section.append("Smaller Thumbnails", "page.zoom-out")
        layout_section.append("Collapse All Sections", "page.collapse-all")
        layout_section.append("Expand All Sections", "page.expand-all")
        menu.append_section(None, layout_section)
        extra = self.extra_menu_section()
        if extra is not None:
            menu.append_section(None, extra)
        button = menu_button(icons.SORT, "View Options", menu)
        button.add_css_class("flat")
        return button

    def extra_menu_section(self):
        return None

    def _install_actions(self):
        page_group = Gio.SimpleActionGroup()
        sort_field = Gio.SimpleAction.new_stateful("sort-field", GLib.VariantType.new("s"), GLib.Variant("s", self.spec.sort_field))
        sort_field.connect("change-state", self._on_sort_field)
        page_group.add_action(sort_field)
        sort_descending = Gio.SimpleAction.new_stateful("sort-descending", GLib.VariantType.new("s"), GLib.Variant("s", "true" if self.spec.descending else "false"))
        sort_descending.connect("change-state", self._on_sort_descending)
        page_group.add_action(sort_descending)
        grouping = Gio.SimpleAction.new_stateful("grouping", GLib.VariantType.new("s"), GLib.Variant("s", self.spec.grouping))
        grouping.connect("change-state", self._on_grouping_action)
        page_group.add_action(grouping)
        self._grouping_action = grouping
        simple_actions = {
            "zoom-in": lambda: self.grid.zoom_in(),
            "zoom-out": lambda: self.grid.zoom_out(),
            "collapse-all": lambda: self.grid.collapse_all(True),
            "expand-all": lambda: self.grid.collapse_all(False),
            "select-all": lambda: self.grid.select_all(),
            "deselect-all": lambda: self.grid.clear_selection(),
            "end-selection": lambda: self.end_selection(),
        }
        for name, callback in simple_actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _action, _parameter, function=callback: function())
            page_group.add_action(action)
        self.insert_action_group("page", page_group)
        item_group = Gio.SimpleActionGroup()
        for name in ITEM_ACTION_NAMES:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", self._on_item_action, name)
            item_group.add_action(action)
        self.insert_action_group("item", item_group)
        self._item_group = item_group
        controller = Gtk.ShortcutController(scope=Gtk.ShortcutScope.LOCAL)
        shortcuts = (
            ("Delete", "item.trash"),
            ("KP_Delete", "item.trash"),
            ("<Shift>Delete", "item.delete-permanently"),
            ("<Control>d", "item.toggle-favorite"),
            ("period", "item.toggle-favorite"),
            ("<Control>h", "item.hide"),
            ("F2", "item.rename"),
            ("<Control>e", "item.edit"),
            ("<Control>i", "item.info"),
            ("<Control>l", "item.add-to-album"),
            ("<Control>c", "item.copy-to-clipboard"),
            ("<Control><Shift>e", "item.export"),
            ("<Control><Shift>a", "page.deselect-all"),
            ("F5", "item.slideshow"),
        )
        for accelerator, action_name in shortcuts:
            controller.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(accelerator), Gtk.NamedAction.new(action_name)))
        self.add_controller(controller)
        self._update_action_sensitivity()

    def _targets(self):
        if self._context_targets is not None:
            return list(self._context_targets)
        selected = self.grid.selected_ids()
        if selected:
            return selected
        focus = self.grid.focus_index
        if 0 <= focus < self.grid.item_count:
            return [self.grid.ids[focus]]
        return []

    def _on_item_action(self, _action, _parameter, name):
        targets = self._targets()
        self._context_targets = None
        if not targets:
            return
        actions = self.context.actions
        clear_after = {"trash", "restore", "delete-permanently", "hide", "unhide", "remove-from-album", "move"}
        if name == "toggle-favorite":
            actions.toggle_favorite(targets)
        elif name == "favorite":
            actions.set_favorite(targets, True)
        elif name == "unfavorite":
            actions.set_favorite(targets, False)
        elif name == "add-to-album":
            actions.add_to_album(targets)
        elif name == "hide":
            actions.set_hidden(targets, True)
        elif name == "unhide":
            actions.set_hidden(targets, False)
        elif name == "trash":
            if self.spec.collection == "trash":
                actions.delete_permanently(targets)
            else:
                actions.trash(targets)
        elif name == "restore":
            actions.restore(targets)
        elif name == "delete-permanently":
            actions.delete_permanently(targets)
        elif name == "rename":
            actions.rename(targets[0])
        elif name == "move":
            actions.move(targets)
        elif name == "copy":
            actions.copy(targets)
        elif name == "export":
            actions.export(targets)
        elif name == "show-in-files":
            actions.show_in_files(targets)
        elif name == "wallpaper":
            actions.set_wallpaper(targets)
        elif name == "copy-to-clipboard":
            actions.copy_to_clipboard(targets)
        elif name == "open-with":
            actions.open_with(targets)
        elif name == "email":
            actions.email(targets)
        elif name == "edit":
            item = self.library.item(targets[0])
            if item is not None and not item.is_video and not item.is_trashed:
                self.context.open_editor(item)
        elif name == "info":
            self._open_viewer_for(targets[0], show_info=True)
        elif name == "remove-from-album" and self.spec.album_id is not None:
            actions.remove_from_album(self.spec.album_id, targets)
        elif name == "set-album-cover" and self.spec.album_id is not None:
            actions.set_album_cover(self.spec.album_id, targets)
        elif name == "slideshow":
            ids = targets if len(targets) > 1 else list(self.grid.ids)
            start = 0 if len(targets) > 1 else max(0, self.grid.index_of(targets[0]))
            self.context.open_viewer(ids, start, None, slideshow=True)
        if name in clear_after:
            self.grid.clear_selection()
            self.grid.set_selection_mode(False)

    def _open_viewer_for(self, item_id, show_info=False):
        index = self.grid.index_of(item_id)
        if index >= 0:
            self.context.open_viewer(list(self.grid.ids), index, self.grid, show_info=show_info)

    def _on_item_activated(self, _grid, index):
        self.context.open_viewer(list(self.grid.ids), index, self.grid)

    def _on_selection_changed(self, grid):
        count = grid.selection_count
        if self.select_button.get_active() != grid.selection_mode:
            self.select_button.handler_block_by_func(self._on_select_toggled)
            self.select_button.set_active(grid.selection_mode)
            self.select_button.handler_unblock_by_func(self._on_select_toggled)
        if grid.selection_mode:
            self.window_title.set_title(f"{count:,} Selected" if count else "Select Items")
            self.window_title.set_subtitle("")
            self.add_css_class("selection-active")
        else:
            self._update_title()
            self.remove_css_class("selection-active")
        if count:
            ids = grid.selected_ids() if count <= 2000 else []
            items = self.library.items(ids) if ids else []
            all_favorite = bool(items) and all(item.is_favorite for item in items)
            videos = sum(1 for item in items if item.is_video)
            summary = formatting.item_count_label(len(items) - videos, videos) + " selected" if items else f"{count:,} items selected"
            self.selection_bar.update(count, summary, all_favorite)
        else:
            self.selection_bar.update(0, "", False)
        self._update_action_sensitivity()

    def _update_action_sensitivity(self):
        has_targets = bool(self._targets()) if self.grid is not None else False
        for name in ITEM_ACTION_NAMES:
            action = self._item_group.lookup_action(name)
            if action is not None:
                action.set_enabled(has_targets or self._context_targets is not None)

    def _on_select_toggled(self, button):
        self.grid.set_selection_mode(button.get_active())
        if not button.get_active():
            self.grid.clear_selection()

    def end_selection(self):
        self.grid.clear_selection()
        self.grid.set_selection_mode(False)

    def _on_context_menu(self, grid, index, x, y):
        item_id = grid.ids[index]
        selected = grid.selected_ids()
        self._context_targets = selected if item_id in selected else [item_id]
        self._update_action_sensitivity()
        menu = build_item_menu(self.spec.collection, len(self._context_targets) == 1, self.spec.album_id)
        if self._popover is not None:
            self._popover.unparent()
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_has_arrow(False)
        popover.set_halign(Gtk.Align.START)
        popover.set_parent(grid)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.connect("closed", self._on_popover_closed)
        self._popover = popover
        popover.popup()

    def _on_popover_closed(self, _popover):
        GLib.idle_add(self._clear_context_targets)

    def _clear_context_targets(self):
        self._context_targets = None
        self._update_action_sensitivity()
        return GLib.SOURCE_REMOVE

    def _on_grouping_toggled(self, button, value):
        if button.get_active():
            self._grouping_action.change_state(GLib.Variant("s", value))

    def _on_grouping_action(self, action, value):
        grouping = value.get_string()
        action.set_state(value)
        if self.spec.grouping == grouping:
            return
        self.spec.grouping = grouping
        button = self._grouping_buttons.get(grouping)
        if button is not None and not button.get_active():
            button.set_active(True)
        self.on_view_options_changed()
        self.reload(keep_position=False)

    def _on_sort_field(self, action, value):
        action.set_state(value)
        self.spec.sort_field = value.get_string()
        self.on_view_options_changed()
        self.reload(keep_position=False)

    def _on_sort_descending(self, action, value):
        action.set_state(value)
        self.spec.descending = value.get_string() == "true"
        self.on_view_options_changed()
        self.reload(keep_position=False)

    def on_view_options_changed(self):
        pass

    def _on_map(self, _page):
        if self._dirty:
            self.reload()
        self.grid.grab_focus()

    def _on_content_changed(self, _library, _reason):
        if self.get_mapped():
            self.reload()
        else:
            self._dirty = True

    def _on_items_updated(self, _library, item_ids):
        if self.get_mapped():
            self.grid.refresh_items(item_ids)
            if self.grid.selection_count:
                self._on_selection_changed(self.grid)

    def reload(self, keep_position=True):
        self._dirty = False
        if not self._loaded_once and not self._loading_source:
            self._loading_source = GLib.timeout_add(150, self._show_loading)
        spec = self.spec
        self.library.load_view(self, dataclasses.replace(spec), lambda content: self._on_loaded(content, keep_position))

    def _show_loading(self):
        self._loading_source = 0
        if not self._loaded_once:
            self.stack.set_visible_child_name("loading")
        return GLib.SOURCE_REMOVE

    def _on_loaded(self, content, keep_position):
        if self._loading_source:
            GLib.source_remove(self._loading_source)
            self._loading_source = 0
        if content is None:
            return
        self._loaded_once = True
        self._content = content
        self.grid.set_content(content, keep_position=keep_position)
        self._update_title()
        if content.item_count:
            self.stack.set_visible_child_name("grid")
        else:
            self.update_empty_state()
            self.stack.set_visible_child_name("empty")
        self.on_content_loaded(content)

    def update_empty_state(self):
        if self.library.scanning and self.spec.collection == "all":
            self.empty_page.set_title("Looking for Photos and Videos")
            self.empty_page.set_description("Picnest is scanning your drives. Your library will appear here in a moment.")
        else:
            self.empty_page.set_title(self._empty_title)
            self.empty_page.set_description(self._empty_description)

    def on_content_loaded(self, content):
        pass

    def _update_title(self):
        if self.grid.selection_mode:
            return
        content = self._content
        self.window_title.set_title(self.spec.title)
        if content is None:
            self.window_title.set_subtitle("")
            return
        photos = sum(section.photos for section in content.sections)
        videos = sum(section.videos for section in content.sections)
        if photos or videos:
            self.window_title.set_subtitle(formatting.item_count_label(photos, videos))
        else:
            self.window_title.set_subtitle(formatting.plural(content.item_count, "item") if content.item_count else "")

    def focus_item(self, item_id):
        index = self.grid.index_of(item_id)
        if index >= 0:
            self.grid.scroll_to_index(index, center=True)

    def item_rect_in(self, widget, item_id):
        index = self.grid.index_of(item_id)
        if index < 0:
            return None, None
        self.grid.scroll_to_index(index)
        rect = self.grid.get_item_rect(index)
        if rect is None:
            return None, None
        success, point = self.grid.compute_point(widget, Graphene.Point().init(rect.get_x(), rect.get_y()))
        if not success:
            return None, None
        return Graphene.Rect().init(point.x, point.y, rect.get_width(), rect.get_height()), self.grid.texture_for_index(index)
