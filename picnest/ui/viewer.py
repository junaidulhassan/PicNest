import threading
from collections import OrderedDict

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk

from picnest.core import formatting
from picnest.core.image_loader import load_animation, load_texture
from picnest.core.thumbnails import decode_placeholder
from picnest.ui import icons
from picnest.ui.filmstrip import Filmstrip
from picnest.ui.image_canvas import ImageCanvas
from picnest.ui.info_panel import InfoPanel
from picnest.ui.video_player import VideoPlayer
from picnest.ui.widgets import icon_button, icon_toggle, make_spinner, menu_button, set_accessible_label, unparent_children

FULL_IMAGE_LIMIT = 6000
PRELOAD_CACHE_SIZE = 3
CHROME_HIDE_DELAY = 2500
_load_semaphore = threading.BoundedSemaphore(2)


class Viewer(Gtk.Widget):
    __gtype_name__ = "PicnestViewer"
    __gsignals__ = {
        "closed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, context):
        super().__init__(visible=False, focusable=True, hexpand=True, vexpand=True)
        self.set_layout_manager(Gtk.BinLayout())
        self.add_css_class("pn-viewer")
        self._context = context
        self._library = context.library
        self._ids = []
        self._index = -1
        self._item = None
        self._source_grid = None
        self._slot = 0
        self._load_generation = 0
        self._full_cache = OrderedDict()
        self._slideshow_source = 0
        self._slideshow_active = False
        self._chrome_visible = True
        self._chrome_source = 0
        self._closing = False
        self._close_animated = False
        self._background_opacity = 0.0
        self._build()
        self._install_actions()
        self._fade_animation = Adw.TimedAnimation.new(self, 0.0, 1.0, 280, Adw.CallbackAnimationTarget.new(self._on_fade))
        self._fade_animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._fade_animation.connect("done", self._on_fade_done)

    def _build(self):
        self._background = Gtk.Box(hexpand=True, vexpand=True)
        self._background.add_css_class("pn-viewer-background")
        self._background.set_parent(self)
        self._split = Adw.OverlaySplitView(sidebar_position=Gtk.PackType.END, show_sidebar=False, collapsed=False, max_sidebar_width=360, min_sidebar_width=280, sidebar_width_fraction=0.28)
        self._split.set_parent(self)
        self._info_panel = InfoPanel(self._library, lambda: self.toggle_info(False))
        self._split.set_sidebar(self._info_panel)
        stage = Gtk.Overlay()
        stage.add_css_class("pn-viewer-stage")
        self._split.set_content(stage)
        self._stack = Gtk.Stack(transition_duration=260, hexpand=True, vexpand=True)
        self._slots = []
        self._canvases = []
        for name in ("a", "b"):
            slot = Adw.Bin()
            canvas = ImageCanvas()
            canvas.connect("swipe", lambda _canvas, direction: self.navigate(direction))
            canvas.connect("single-click", lambda _canvas: self._toggle_chrome())
            canvas.connect("zoom-changed", self._on_zoom_changed)
            canvas.connect("transition-finished", self._on_canvas_transition_finished)
            self._slots.append(slot)
            self._canvases.append(canvas)
            self._stack.add_named(slot, name)
        stage.set_child(self._stack)
        self._video = VideoPlayer(self._context.settings)
        self._video.connect("fullscreen-requested", lambda _player: self.toggle_fullscreen())
        self._video.connect("controls-visibility", self._on_video_controls)
        self._spinner = make_spinner(32)
        self._spinner.set_halign(Gtk.Align.CENTER)
        self._spinner.set_valign(Gtk.Align.CENTER)
        self._spinner.set_visible(False)
        self._spinner.set_can_target(False)
        stage.add_overlay(self._spinner)
        self._header_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE, transition_duration=200, reveal_child=True, valign=Gtk.Align.START)
        self._header_revealer.set_child(self._build_header())
        stage.add_overlay(self._header_revealer)
        self._previous_button = self._nav_button(icons.GO_PREVIOUS, "Previous (Left)", -1, Gtk.Align.START)
        self._next_button = self._nav_button(icons.GO_NEXT, "Next (Right)", 1, Gtk.Align.END)
        stage.add_overlay(self._previous_button)
        stage.add_overlay(self._next_button)
        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.END, spacing=8)
        self._zoom_box = self._build_zoom_controls()
        bottom.append(self._zoom_box)
        self._filmstrip = Filmstrip(self._library)
        self._filmstrip.connect("item-chosen", lambda _strip, index: self.show_index(index, 1 if index > self._index else -1))
        self._filmstrip_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP, transition_duration=220, reveal_child=True, child=self._filmstrip)
        bottom.append(self._filmstrip_revealer)
        self._bottom_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE, transition_duration=200, reveal_child=True, valign=Gtk.Align.END, child=bottom)
        stage.add_overlay(self._bottom_revealer)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)

    def _build_header(self):
        header = Adw.HeaderBar(show_title=True)
        header.add_css_class("osd")
        header.add_css_class("pn-viewer-header")
        header.pack_start(icon_button(icons.BACK, "Back to Library (Escape)", self.close))
        self._title = Adw.WindowTitle()
        header.set_title_widget(self._title)
        more_menu = Gio.Menu()
        view_section = Gio.Menu()
        view_section.append("Slideshow", "viewer.slideshow")
        view_section.append("Fullscreen", "viewer.fullscreen")
        view_section.append("Rotate Left", "viewer.rotate-left")
        view_section.append("Rotate Right", "viewer.rotate-right")
        view_section.append("Flip Horizontally", "viewer.flip-horizontal")
        view_section.append("Flip Vertically", "viewer.flip-vertical")
        more_menu.append_section(None, view_section)
        organize_section = Gio.Menu()
        organize_section.append("Add to Album…", "viewer.add-to-album")
        organize_section.append("Hide", "viewer.hide")
        organize_section.append("Rename…", "viewer.rename")
        organize_section.append("Move To…", "viewer.move")
        organize_section.append("Copy To…", "viewer.copy")
        more_menu.append_section(None, organize_section)
        file_section = Gio.Menu()
        file_section.append("Set as Wallpaper", "viewer.wallpaper")
        file_section.append("Export…", "viewer.export")
        file_section.append("Show in Files", "viewer.show-in-files")
        more_menu.append_section(None, file_section)
        self._more_button = menu_button(icons.MORE, "More Actions", more_menu)
        self._more_button.add_css_class("flat")
        header.pack_end(self._more_button)
        self._delete_button = icon_button(icons.TRASH, "Move to Trash (Delete)", lambda: self._activate("trash"))
        header.pack_end(self._delete_button)
        share_menu = Gio.Menu()
        share_menu.append("Copy to Clipboard", "viewer.copy-to-clipboard")
        share_menu.append("Open With…", "viewer.open-with")
        share_menu.append("Send by Email…", "viewer.email")
        share_menu.append("Export…", "viewer.export")
        self._share_button = menu_button(icons.SHARE, "Share", share_menu)
        self._share_button.add_css_class("flat")
        header.pack_end(self._share_button)
        self._edit_button = icon_button(icons.EDIT, "Edit (E)", lambda: self._activate("edit"))
        header.pack_end(self._edit_button)
        self._info_button = icon_toggle(icons.INFO, "Info (I)")
        self._info_button.connect("toggled", lambda button: self.toggle_info(button.get_active()))
        header.pack_end(self._info_button)
        self._favorite_button = Gtk.Button(valign=Gtk.Align.CENTER, tooltip_text="Favorite (.)")
        self._favorite_button.add_css_class("flat")
        self._favorite_button.add_css_class("pn-favorite-button")
        self._favorite_icon = Gtk.Image(icon_name=icons.FAVORITE)
        self._favorite_button.set_child(self._favorite_icon)
        self._favorite_button.connect("clicked", lambda _button: self._activate("toggle-favorite"))
        header.pack_end(self._favorite_button)
        self._restore_button = Gtk.Button(child=Adw.ButtonContent(icon_name=icons.RESTORE, label="Restore"), valign=Gtk.Align.CENTER, visible=False)
        self._restore_button.add_css_class("flat")
        self._restore_button.connect("clicked", lambda _button: self._activate("restore"))
        header.pack_end(self._restore_button)
        self._slideshow_stop = Gtk.Button(child=Adw.ButtonContent(icon_name=icons.PAUSE, label="Stop Slideshow"), valign=Gtk.Align.CENTER, visible=False)
        self._slideshow_stop.add_css_class("flat")
        self._slideshow_stop.connect("clicked", lambda _button: self.stop_slideshow())
        header.pack_start(self._slideshow_stop)
        return header

    def _build_zoom_controls(self):
        box = Gtk.Box(halign=Gtk.Align.END, margin_end=16, spacing=2)
        box.add_css_class("osd")
        box.add_css_class("pn-zoom-controls")
        box.append(icon_button(icons.ZOOM_OUT, "Zoom Out (−)", lambda: self._current_canvas().zoom_out(), css_classes=("flat", "circular")))
        self._zoom_label = Gtk.Button(label="Fit", tooltip_text="Fit to Window (0)")
        self._zoom_label.add_css_class("flat")
        self._zoom_label.add_css_class("numeric")
        self._zoom_label.connect("clicked", lambda _button: self._toggle_fit())
        box.append(self._zoom_label)
        box.append(icon_button(icons.ZOOM_IN, "Zoom In (+)", lambda: self._current_canvas().zoom_in(), css_classes=("flat", "circular")))
        return box

    def _nav_button(self, icon_name, tooltip, direction, alignment):
        button = Gtk.Button(icon_name=icon_name, tooltip_text=tooltip, halign=alignment, valign=Gtk.Align.CENTER, margin_start=16, margin_end=16)
        button.add_css_class("osd")
        button.add_css_class("circular")
        button.add_css_class("pn-nav-button")
        set_accessible_label(button, tooltip)
        button.connect("clicked", lambda _button: self.navigate(direction))
        return button

    def _install_actions(self):
        group = Gio.SimpleActionGroup()
        names = (
            "slideshow", "fullscreen", "rotate-left", "rotate-right", "flip-horizontal", "flip-vertical",
            "add-to-album", "hide", "rename", "move", "copy", "wallpaper", "export", "show-in-files",
            "copy-to-clipboard", "open-with", "email", "trash", "restore", "delete-permanently", "edit", "toggle-favorite",
        )
        self._action_group = group
        for name in names:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _action, _parameter, action_name=name: self._activate(action_name))
            group.add_action(action)
        self.insert_action_group("viewer", group)

    def _set_action_enabled(self, name, enabled):
        action = self._action_group.lookup_action(name)
        if action is not None:
            action.set_enabled(enabled)

    def do_size_allocate(self, width, height, baseline):
        self._background.allocate(width, height, baseline, None)
        self._split.allocate(width, height, baseline, None)

    def do_dispose(self):
        if hasattr(self, "_video"):
            self._video.stop()
        unparent_children(self)
        Gtk.Widget.do_dispose(self)

    @property
    def is_open(self):
        return self.get_visible() and not self._closing

    def _current_canvas(self):
        return self._canvases[self._slot]

    def open(self, ids, index, source_grid=None, slideshow=False, show_info=False):
        if not ids:
            return
        self._ids = list(ids)
        self._index = max(0, min(index, len(self._ids) - 1))
        self._source_grid = source_grid
        self._closing = False
        self._close_animated = False
        self._fade_animation.skip()
        self._split.set_opacity(1.0)
        self._full_cache.clear()
        self.set_visible(True)
        self.grab_focus()
        self._filmstrip.set_ids(self._ids)
        self._filmstrip_revealer.set_visible(len(self._ids) > 1)
        source_rect, source_texture = self._source_geometry(self._index)
        self._show(self._index, 0, source_rect=source_rect, source_texture=source_texture)
        self._fade_animation.set_value_from(self._background_opacity)
        self._fade_animation.set_value_to(1.0)
        self._fade_animation.play()
        self._set_chrome_visible(True)
        if show_info:
            self._info_button.set_active(True)
        if slideshow:
            self.start_slideshow()

    def _source_geometry(self, index):
        grid = self._source_grid
        if grid is None or not grid.get_mapped():
            return None, None
        rect = grid.get_item_rect(index)
        if rect is None:
            return None, None
        success, point = grid.compute_point(self, Graphene.Point().init(rect.get_x(), rect.get_y()))
        if not success:
            return None, None
        return Graphene.Rect().init(point.x, point.y, rect.get_width(), rect.get_height()), grid.texture_for_index(index)

    def show_index(self, index, direction):
        if 0 <= index < len(self._ids) and index != self._index:
            self._show(index, direction)

    def navigate(self, direction):
        if not self._ids:
            return
        target = self._index + direction
        if self._slideshow_active and target >= len(self._ids):
            target = 0
        if 0 <= target < len(self._ids):
            self._show(target, direction)

    def _show(self, index, direction, source_rect=None, source_texture=None):
        item = self._library.item(self._ids[index])
        if item is None:
            self._ids.pop(index)
            if not self._ids:
                self.close()
                return
            self._show(min(index, len(self._ids) - 1), direction)
            return
        self._index = index
        self._item = item
        self._load_generation += 1
        generation = self._load_generation
        previous_slot = self._slot
        self._slot = 1 - previous_slot if direction else previous_slot
        slot = self._slots[self._slot]
        canvas = self._canvases[self._slot]
        if self._video.get_parent() is not None:
            self._video.stop()
            self._video.get_parent().set_child(None)
        if self._slideshow_active:
            self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        elif direction > 0:
            self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        elif direction < 0:
            self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
        else:
            self._stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self._spinner.set_visible(False)
        if item.is_video:
            canvas.clear()
            slot.set_child(self._video)
            self._stack.set_visible_child(slot)
            self._video.set_bottom_inset(96 if self._chrome_visible and len(self._ids) > 1 else 16)
            self._video.load(self._library.file_path_for(item), item.name, autoplay=True)
            self._zoom_box.set_visible(False)
        else:
            slot.set_child(canvas)
            self._stack.set_visible_child(slot)
            self._zoom_box.set_visible(True)
            initial = source_texture or self._library.thumbnails.cached(item, False) or decode_placeholder(item.placeholder)
            logical = (item.width, item.height) if item.width and item.height else None
            canvas.set_texture(initial, logical)
            if initial is None:
                self._library.thumbnails.request(item, lambda texture, expected=generation, target=canvas: self._on_preview_texture(texture, expected, target))
            if source_rect is not None and initial is not None:
                canvas.animate_from(source_rect)
            self._load_full(item, canvas, generation)
        self._update_chrome()
        self._filmstrip.select(index)
        if self._info_button.get_active():
            self._info_panel.show_item(item)
        self._library.record_viewed(item.id)
        self._preload_neighbors()

    def _on_preview_texture(self, texture, generation, canvas):
        if generation == self._load_generation and texture is not None and canvas.texture is None:
            canvas.upgrade_texture(texture)

    def _load_full(self, item, canvas, generation):
        cached = self._full_cache.get(item.id)
        if cached is not None:
            self._full_cache.move_to_end(item.id)
            canvas.upgrade_texture(cached)
            return
        path = self._library.file_path_for(item)
        GLib.timeout_add(350, self._maybe_show_spinner, generation)

        def work():
            with _load_semaphore:
                if generation != self._load_generation:
                    return None, None
                animation = load_animation(path) if item.is_animated else None
                texture = load_texture(path, item.extension, FULL_IMAGE_LIMIT)
                return texture, animation

        def deliver(result):
            texture, animation = result
            self._spinner.set_visible(False)
            if generation != self._load_generation:
                if texture is not None:
                    self._remember_full(item.id, texture)
                return GLib.SOURCE_REMOVE
            if texture is not None:
                self._remember_full(item.id, texture)
                canvas.upgrade_texture(texture)
            if animation is not None:
                canvas.play_animation(animation)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=lambda: GLib.idle_add(deliver, work()), name="picnest-viewer-load", daemon=True).start()

    def _maybe_show_spinner(self, generation):
        if generation == self._load_generation and self._item is not None and not self._item.is_video and self._item.id not in self._full_cache:
            self._spinner.set_visible(True)
        return GLib.SOURCE_REMOVE

    def _remember_full(self, item_id, texture):
        self._full_cache[item_id] = texture
        self._full_cache.move_to_end(item_id)
        while len(self._full_cache) > PRELOAD_CACHE_SIZE:
            self._full_cache.popitem(last=False)

    def _preload_neighbors(self):
        for offset in (1, -1):
            index = self._index + offset
            if 0 <= index < len(self._ids):
                item = self._library.item(self._ids[index])
                if item is None or item.is_video or item.id in self._full_cache:
                    continue
                self._library.thumbnails.request(item, lambda _texture: None)
                if offset == 1:
                    self._preload_full(item)

    def _preload_full(self, item):
        path = self._library.file_path_for(item)

        def work():
            with _load_semaphore:
                return load_texture(path, item.extension, FULL_IMAGE_LIMIT)

        def deliver(texture):
            if texture is not None and self.get_visible():
                self._remember_full(item.id, texture)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=lambda: GLib.idle_add(deliver, work()), name="picnest-viewer-preload", daemon=True).start()

    def _update_chrome(self):
        item = self._item
        if item is None:
            return
        self._title.set_title(item.name)
        self._title.set_subtitle(formatting.full_date(item.taken_at or item.sort_date))
        trashed = item.is_trashed
        self._favorite_button.set_visible(not trashed)
        self._edit_button.set_visible(not trashed and not item.is_video)
        self._restore_button.set_visible(trashed)
        self._share_button.set_visible(not trashed)
        self._update_favorite_icon(animate=False)
        self._delete_button.set_tooltip_text("Delete Permanently (Delete)" if trashed else "Move to Trash (Delete)")
        set_accessible_label(self._delete_button, "Delete Permanently" if trashed else "Move to Trash")
        self._previous_button.set_sensitive(self._index > 0)
        self._next_button.set_sensitive(self._index < len(self._ids) - 1 or self._slideshow_active)
        is_image = not item.is_video
        for name in ("rotate-left", "rotate-right", "flip-horizontal", "flip-vertical", "wallpaper", "edit"):
            self._set_action_enabled(name, is_image and not trashed)
        for name in ("add-to-album", "hide", "rename", "move", "copy", "export", "copy-to-clipboard", "open-with", "email"):
            self._set_action_enabled(name, not trashed)

    def _update_favorite_icon(self, animate=True):
        favorite = self._item is not None and self._item.is_favorite
        self._favorite_icon.set_from_icon_name(icons.FAVORITE_FILLED if favorite else icons.FAVORITE)
        label = "Remove from Favorites (.)" if favorite else "Add to Favorites (.)"
        self._favorite_button.set_tooltip_text(label)
        set_accessible_label(self._favorite_button, label)
        if favorite:
            self._favorite_button.add_css_class("active")
        else:
            self._favorite_button.remove_css_class("active")
        if animate:
            self._favorite_button.remove_css_class("pop")
            GLib.idle_add(lambda: self._favorite_button.add_css_class("pop") and False)

    def refresh_current(self):
        if self._item is None:
            return
        fresh = self._library.item(self._item.id)
        if fresh is not None:
            was_favorite = self._item.is_favorite
            self._item = fresh
            self._update_chrome()
            self._update_favorite_icon(animate=fresh.is_favorite != was_favorite)

    def _activate(self, name):
        item = self._item
        if item is None:
            return
        actions = self._context.actions
        ids = [item.id]
        if name == "toggle-favorite":
            actions.toggle_favorite(ids)
            self.refresh_current()
        elif name == "trash":
            if item.is_trashed:
                actions.delete_permanently(ids, lambda result: self._remove_current() if result.succeeded else None)
            else:
                self._remove_current()
                actions.trash(ids)
        elif name == "delete-permanently":
            actions.delete_permanently(ids, lambda result: self._remove_current() if result.succeeded else None)
        elif name == "restore":
            actions.restore(ids, lambda result: self._remove_current() if result.succeeded else None)
        elif name == "hide":
            self._remove_current()
            actions.set_hidden(ids, True)
        elif name == "edit":
            if not item.is_video:
                self._context.open_editor(item)
        elif name == "slideshow":
            self.start_slideshow()
        elif name == "fullscreen":
            self.toggle_fullscreen()
        elif name == "rotate-left":
            self._current_canvas().rotate(-90)
        elif name == "rotate-right":
            self._current_canvas().rotate(90)
        elif name == "flip-horizontal":
            self._current_canvas().flip(True)
        elif name == "flip-vertical":
            self._current_canvas().flip(False)
        elif name == "add-to-album":
            actions.add_to_album(ids)
        elif name == "rename":
            actions.rename(item.id)
        elif name == "move":
            actions.move(ids)
        elif name == "copy":
            actions.copy(ids)
        elif name == "wallpaper":
            actions.set_wallpaper(ids)
        elif name == "export":
            actions.export(ids)
        elif name == "show-in-files":
            actions.show_in_files(ids)
        elif name == "copy-to-clipboard":
            actions.copy_to_clipboard(ids)
        elif name == "open-with":
            actions.open_with(ids)
        elif name == "email":
            actions.email(ids)

    def _remove_current(self):
        if not self._ids or self._index < 0:
            return
        self._ids.pop(self._index)
        self._filmstrip.set_ids(self._ids)
        if not self._ids:
            self.close(animate=False)
            return
        self._show(min(self._index, len(self._ids) - 1), 1)

    def toggle_info(self, show):
        if self._info_button.get_active() != show:
            self._info_button.set_active(show)
            return
        self._split.set_show_sidebar(show)
        if show:
            self._info_panel.show_item(self._item)

    def toggle_fullscreen(self):
        window = self.get_root()
        if window is None:
            return
        if window.is_fullscreen():
            window.unfullscreen()
        else:
            window.fullscreen()
        GLib.timeout_add(150, self._sync_fullscreen)

    def _sync_fullscreen(self):
        window = self.get_root()
        fullscreen = window is not None and window.is_fullscreen()
        self._video.set_fullscreen_state(fullscreen)
        if fullscreen:
            self._schedule_chrome_hide()
        return GLib.SOURCE_REMOVE

    def start_slideshow(self):
        if len(self._ids) < 2:
            return
        self._slideshow_active = True
        self._slideshow_stop.set_visible(True)
        window = self.get_root()
        if window is not None and not window.is_fullscreen():
            window.fullscreen()
        self._set_chrome_visible(False)
        self._schedule_slideshow()
        self._update_chrome()

    def _schedule_slideshow(self):
        if self._slideshow_source:
            GLib.source_remove(self._slideshow_source)
        interval = max(1, self._context.settings.get("slideshow-interval"))
        self._slideshow_source = GLib.timeout_add_seconds(interval, self._slideshow_tick)

    def _slideshow_tick(self):
        if not self._slideshow_active:
            self._slideshow_source = 0
            return GLib.SOURCE_REMOVE
        if self._item is not None and self._item.is_video and self._video.playing:
            return GLib.SOURCE_CONTINUE
        self.navigate(1)
        return GLib.SOURCE_CONTINUE

    def stop_slideshow(self):
        if not self._slideshow_active:
            return
        self._slideshow_active = False
        self._slideshow_stop.set_visible(False)
        if self._slideshow_source:
            GLib.source_remove(self._slideshow_source)
            self._slideshow_source = 0
        window = self.get_root()
        if window is not None and window.is_fullscreen():
            window.unfullscreen()
        self._set_chrome_visible(True)
        self._update_chrome()

    def close(self, animate=True):
        if not self.get_visible() or self._closing:
            return
        self._closing = True
        self.stop_slideshow()
        window = self.get_root()
        if window is not None and window.is_fullscreen():
            window.unfullscreen()
        self._video.stop()
        item_id = self._item.id if self._item is not None else -1
        canvas = self._current_canvas()
        target_rect = None
        if animate and self._item is not None and not self._item.is_video and not canvas.is_zoomed:
            target_rect = self._context.item_rect_for_viewer(self, item_id)
        self._set_chrome_visible(False)
        self._close_animated = target_rect is not None and canvas.animate_to(target_rect)
        if not animate:
            self._finish_close()
        else:
            self._fade_animation.set_value_from(self._background_opacity)
            self._fade_animation.set_value_to(0.0)
            self._fade_animation.play()
        self.emit("closed", item_id)

    def _on_canvas_transition_finished(self, canvas, closing):
        if closing and canvas is self._current_canvas() and self._closing:
            self._finish_close()

    def _on_fade(self, value):
        self._background_opacity = value
        self._background.set_opacity(value)
        if self._closing and not self._close_animated:
            self._split.set_opacity(value)

    def _on_fade_done(self, _animation):
        if self._closing and self._background_opacity <= 0.01 and not self._current_canvas().transition_running:
            self._finish_close()

    def _finish_close(self):
        if not self._closing:
            return
        self._fade_animation.skip()
        self._closing = False
        self._close_animated = False
        self.set_visible(False)
        self._split.set_opacity(1.0)
        self._background_opacity = 0.0
        self._background.set_opacity(0.0)
        for canvas in self._canvases:
            canvas.clear()
        self._full_cache.clear()
        self._item = None
        self._ids = []
        self._info_button.set_active(False)

    def _toggle_chrome(self):
        self._set_chrome_visible(not self._chrome_visible)

    def _set_chrome_visible(self, visible):
        self._chrome_visible = visible
        self._header_revealer.set_reveal_child(visible)
        self._bottom_revealer.set_reveal_child(visible)
        self._video.set_bottom_inset(96 if visible and len(self._ids) > 1 else 16)
        self._previous_button.set_visible(visible)
        self._next_button.set_visible(visible)
        self.set_cursor(None if visible or self._item is None else Gdk.Cursor.new_from_name("none", None))

    def _schedule_chrome_hide(self):
        if self._chrome_source:
            GLib.source_remove(self._chrome_source)
        self._chrome_source = GLib.timeout_add(CHROME_HIDE_DELAY, self._auto_hide_chrome)

    def _auto_hide_chrome(self):
        self._chrome_source = 0
        window = self.get_root()
        if (window is not None and window.is_fullscreen()) or self._slideshow_active:
            self._set_chrome_visible(False)
        return GLib.SOURCE_REMOVE

    def _on_motion(self, _controller, _x, _y):
        if self._closing:
            return
        window = self.get_root()
        fullscreen = window is not None and window.is_fullscreen()
        if not self._chrome_visible and (fullscreen or self._slideshow_active):
            self._set_chrome_visible(True)
        if fullscreen or self._slideshow_active:
            self._schedule_chrome_hide()

    def _on_video_controls(self, _player, visible):
        window = self.get_root()
        if window is not None and window.is_fullscreen():
            self._set_chrome_visible(visible)

    def _on_zoom_changed(self, canvas, zoom):
        if canvas is not self._current_canvas():
            return
        if zoom <= 1.001:
            self._zoom_label.set_label("Fit")
        else:
            percent = canvas.fit_scale() * zoom * canvas.get_scale_factor() * 100
            self._zoom_label.set_label(f"{percent:.0f}%")

    def _toggle_fit(self):
        canvas = self._current_canvas()
        if canvas.is_zoomed:
            canvas.zoom_fit()
        else:
            canvas.zoom_actual()

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        if not self.get_visible():
            return False
        focus = self.get_root().get_focus() if self.get_root() is not None else None
        if isinstance(focus, (Gtk.Text, Gtk.Entry)):
            return False
        control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        is_video = self._item is not None and self._item.is_video
        canvas = self._current_canvas()
        if keyval == Gdk.KEY_Escape:
            window = self.get_root()
            if self._slideshow_active:
                self.stop_slideshow()
            elif window is not None and window.is_fullscreen():
                window.unfullscreen()
                GLib.timeout_add(150, self._sync_fullscreen)
            elif canvas.is_zoomed:
                canvas.zoom_fit()
            else:
                self.close()
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Page_Up, Gdk.KEY_KP_Left) and not (is_video and control):
            if is_video and shift:
                self._video.seek_relative(-5)
            else:
                self.navigate(-1)
            return True
        if keyval in (Gdk.KEY_Right, Gdk.KEY_Page_Down, Gdk.KEY_KP_Right):
            if is_video and shift:
                self._video.seek_relative(5)
            else:
                self.navigate(1)
            return True
        if keyval == Gdk.KEY_Home:
            self.show_index(0, -1)
            return True
        if keyval == Gdk.KEY_End:
            self.show_index(len(self._ids) - 1, 1)
            return True
        if keyval in (Gdk.KEY_F11, Gdk.KEY_f) and not control:
            self.toggle_fullscreen()
            return True
        if keyval == Gdk.KEY_space:
            if is_video:
                self._video.toggle_playback()
            elif self._slideshow_active:
                self.stop_slideshow()
            else:
                self.navigate(1)
            return True
        if keyval == Gdk.KEY_F5:
            self.start_slideshow()
            return True
        if is_video and keyval == Gdk.KEY_m:
            self._video.toggle_mute()
            return True
        if is_video and keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
            self._video.adjust_volume(0.05 if keyval == Gdk.KEY_Up else -0.05)
            return True
        if not is_video:
            if keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                canvas.zoom_in()
                return True
            if keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
                canvas.zoom_out()
                return True
            if keyval in (Gdk.KEY_0, Gdk.KEY_KP_0):
                canvas.zoom_fit()
                return True
            if keyval in (Gdk.KEY_1, Gdk.KEY_KP_1):
                canvas.zoom_actual()
                return True
            if keyval in (Gdk.KEY_r, Gdk.KEY_R) and not control:
                canvas.rotate(-90 if shift else 90)
                return True
            if keyval == Gdk.KEY_h and not control:
                canvas.flip(True)
                return True
            if keyval == Gdk.KEY_v and not control:
                canvas.flip(False)
                return True
            if keyval == Gdk.KEY_e and not control:
                self._activate("edit")
                return True
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            self._activate("delete-permanently" if shift else "trash")
            return True
        if keyval == Gdk.KEY_i and not control:
            self.toggle_info(not self._info_button.get_active())
            return True
        if keyval == Gdk.KEY_period or (keyval == Gdk.KEY_d and control):
            self._activate("toggle-favorite")
            return True
        if keyval == Gdk.KEY_F2:
            self._activate("rename")
            return True
        if keyval == Gdk.KEY_c and control:
            self._activate("copy-to-clipboard")
            return True
        return False
