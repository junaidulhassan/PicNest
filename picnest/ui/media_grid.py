import bisect
import math

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk

from picnest.core import formatting
from picnest.core.thumbnails import activity, decode_placeholder
from picnest.ui import icons
from picnest.ui.widgets import accent_color, cover_rect, placeholder_color, rounded_rect, set_accessible_label, unparent_children

ZOOM_LEVELS = (64, 88, 112, 140, 176, 220, 280, 360, 480)
GAP = 3
SIDE_PADDING = 12
TOP_PADDING = 6
BOTTOM_PADDING = 24
HEADER_HEIGHT = 58
SECTION_SPACING = 10
TILE_RADIUS = 6
HOVER_PREVIEW_DELAY = 450
SMALL_TEXTURE_LIMIT = 170
LARGE_TEXTURE_THRESHOLD = 430


class GridLayout:
    __slots__ = (
        "columns", "tile", "width", "total_height", "section_tops", "section_tile_tops", "section_rows",
        "section_starts", "sections", "has_headers",
    )

    def __init__(self, sections, width, target_size):
        available = max(1.0, width - 2 * SIDE_PADDING)
        columns = max(1, round((available + GAP) / (target_size + GAP)))
        tile = max(16.0, (available - GAP * (columns - 1)) / columns)
        self.columns = columns
        self.tile = tile
        self.width = width
        self.sections = sections
        self.section_tops = []
        self.section_tile_tops = []
        self.section_rows = []
        self.section_starts = []
        self.has_headers = any(section.show_header for section in sections)
        y = TOP_PADDING
        row_height = tile + GAP
        for section in sections:
            self.section_tops.append(y)
            self.section_starts.append(section.start)
            if section.show_header:
                y += HEADER_HEIGHT
            rows = 0 if section.collapsed else math.ceil(section.count / columns)
            self.section_tile_tops.append(y)
            self.section_rows.append(rows)
            y += rows * row_height
            if section.show_header:
                y += SECTION_SPACING
        self.total_height = y + BOTTOM_PADDING

    @property
    def row_height(self):
        return self.tile + GAP

    def section_at_y(self, y):
        return max(0, bisect.bisect_right(self.section_tops, y) - 1) if self.section_tops else -1

    def section_of_index(self, index):
        return max(0, bisect.bisect_right(self.section_starts, index) - 1) if self.section_starts else -1

    def item_rect(self, index):
        section_index = self.section_of_index(index)
        if section_index < 0:
            return None
        section = self.sections[section_index]
        if section.collapsed:
            return None
        local = index - section.start
        row, column = divmod(local, self.columns)
        x = SIDE_PADDING + column * (self.tile + GAP)
        y = self.section_tile_tops[section_index] + row * self.row_height
        return x, y, self.tile, self.tile

    def visible_indices(self, top, bottom):
        if not self.sections:
            return
        first_section = self.section_at_y(top)
        row_height = self.row_height
        for section_index in range(first_section, len(self.sections)):
            section_top = self.section_tops[section_index]
            if section_top > bottom:
                break
            rows = self.section_rows[section_index]
            if not rows:
                continue
            tiles_top = self.section_tile_tops[section_index]
            first_row = max(0, int((top - tiles_top) // row_height))
            last_row = min(rows - 1, int((bottom - tiles_top) // row_height))
            if last_row < first_row:
                continue
            section = self.sections[section_index]
            start = section.start + first_row * self.columns
            end = min(section.start + section.count, section.start + (last_row + 1) * self.columns)
            yield from range(start, end)

    def hit_test(self, x, y):
        section_index = self.section_at_y(y)
        if section_index < 0:
            return None, -1
        section = self.sections[section_index]
        tiles_top = self.section_tile_tops[section_index]
        if section.show_header and y < tiles_top:
            return "header", section_index
        if section.collapsed:
            return None, -1
        column = int((x - SIDE_PADDING) // (self.tile + GAP))
        row = int((y - tiles_top) // self.row_height)
        if column < 0 or column >= self.columns or row < 0:
            return None, -1
        local_x = x - SIDE_PADDING - column * (self.tile + GAP)
        local_y = y - tiles_top - row * self.row_height
        if local_x > self.tile or local_y > self.tile:
            return None, -1
        local_index = row * self.columns + column
        if local_index >= section.count:
            return None, -1
        return "item", section.start + local_index

    def indices_in_rect(self, x0, y0, x1, y1):
        left, right = min(x0, x1), max(x0, x1)
        top, bottom = min(y0, y1), max(y0, y1)
        result = []
        for index in self.visible_indices(top - self.tile, bottom):
            rect = self.item_rect(index)
            if rect is None:
                continue
            x, y, size, _ = rect
            if x + size >= left and x <= right and y + size >= top and y <= bottom:
                result.append(index)
        return result


class SectionHeader(Gtk.Box):
    __gtype_name__ = "PicnestSectionHeader"

    def __init__(self, on_toggle_collapse, on_select_section):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.add_css_class("pn-section-header")
        self.section_index = -1
        self._on_toggle_collapse = on_toggle_collapse
        self._on_select_section = on_select_section
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        self._title = Gtk.Label(xalign=0, ellipsize=3)
        self._title.add_css_class("pn-section-title")
        self._subtitle = Gtk.Label(xalign=0, ellipsize=3)
        self._subtitle.add_css_class("pn-section-subtitle")
        labels.append(self._title)
        labels.append(self._subtitle)
        self.append(labels)
        self._select_button = Gtk.Button(label="Select", valign=Gtk.Align.CENTER)
        self._select_button.add_css_class("flat")
        self._select_button.add_css_class("pn-section-select")
        self._select_button.connect("clicked", lambda _button: self._on_select_section(self.section_index))
        self.append(self._select_button)
        self._collapse_button = Gtk.Button(icon_name=icons.EXPAND, valign=Gtk.Align.CENTER)
        self._collapse_button.add_css_class("flat")
        self._collapse_button.add_css_class("circular")
        self._collapse_button.add_css_class("pn-collapse-button")
        self._collapse_button.connect("clicked", lambda _button: self._on_toggle_collapse(self.section_index))
        self.append(self._collapse_button)
        click = Gtk.GestureClick()
        click.connect("released", self._on_title_clicked)
        labels.add_controller(click)

    def _on_title_clicked(self, gesture, n_press, _x, _y):
        if n_press == 1:
            self._on_toggle_collapse(self.section_index)

    def bind(self, section_index, section, all_selected, pinned):
        self.section_index = section_index
        self._title.set_label(section.title)
        self._subtitle.set_label(section.subtitle)
        self._select_button.set_label("Deselect" if all_selected else "Select")
        set_accessible_label(self._select_button, f"{'Deselect' if all_selected else 'Select'} all items in {section.title}")
        tooltip = "Expand section" if section.collapsed else "Collapse section"
        self._collapse_button.set_tooltip_text(tooltip)
        set_accessible_label(self._collapse_button, f"{tooltip} {section.title}")
        if section.collapsed:
            self.add_css_class("collapsed")
        else:
            self.remove_css_class("collapsed")
        if pinned:
            self.add_css_class("pinned")
        else:
            self.remove_css_class("pinned")


class MediaTile(Gtk.Widget):
    __gtype_name__ = "PicnestMediaTile"

    def __init__(self, grid):
        super().__init__(accessible_role=Gtk.AccessibleRole.GRID_CELL)
        self._grid = grid
        self.item = None
        self.index = -1
        self._token = 0
        self._large_token = 0
        self._placeholder = None
        self._variant = None
        self._selected = False
        self._focused = False
        self._selection_progress = 0.0
        self._preview_media = None
        self.add_css_class("pn-tile")
        self._picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        self._picture.add_css_class("pn-thumb")
        self._picture.set_parent(self)
        self._preview = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True, visible=False)
        self._preview.add_css_class("pn-preview")
        self._preview.set_parent(self)
        self._fallback = Gtk.Image(pixel_size=28, visible=False)
        self._fallback.add_css_class("pn-tile-fallback")
        self._fallback.set_parent(self)
        self._badge = Gtk.Box(spacing=3, visible=False)
        self._badge.add_css_class("pn-badge")
        self._badge_icon = Gtk.Image(pixel_size=10)
        self._badge_label = Gtk.Label()
        self._badge.append(self._badge_icon)
        self._badge.append(self._badge_label)
        self._badge.set_parent(self)
        self._favorite = Gtk.Image(icon_name=icons.FAVORITE_FILLED, pixel_size=14, visible=False)
        self._favorite.add_css_class("pn-tile-favorite")
        self._favorite.set_parent(self)
        self._check = Gtk.Image(icon_name=icons.CHECK)
        self._check.add_css_class("pn-check")
        self._check.set_parent(self)
        self._selection_animation = Adw.TimedAnimation.new(self, 0.0, 1.0, 180, Adw.CallbackAnimationTarget.new(self._on_selection_progress))
        self._selection_animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)

    def do_dispose(self):
        if hasattr(self, "_grid"):
            self.unbind()
        unparent_children(self)
        Gtk.Widget.do_dispose(self)

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1

    def do_size_allocate(self, width, height, baseline):
        self._picture.allocate(width, height, -1, None)
        if self._preview.get_visible():
            self._preview.allocate(width, height, -1, None)
        if self._fallback.get_visible():
            self._allocate_at(self._fallback, (width - 28) / 2, (height - 28) / 2, 28, 28)
        if self._badge.get_visible():
            _min, badge_width, _bmin, _bnat = self._badge.measure(Gtk.Orientation.HORIZONTAL, -1)
            _min, badge_height, _bmin, _bnat = self._badge.measure(Gtk.Orientation.VERTICAL, badge_width)
            self._allocate_at(self._badge, width - badge_width - 6, height - badge_height - 6, badge_width, badge_height)
        if self._favorite.get_visible():
            self._allocate_at(self._favorite, 7, height - 21, 14, 14)
        _min, check_size, _bmin, _bnat = self._check.measure(Gtk.Orientation.HORIZONTAL, -1)
        check_size = max(check_size, 22)
        self._allocate_at(self._check, 6, 6, check_size, check_size)

    def _allocate_at(self, child, x, y, width, height):
        child.allocate(int(width), int(height), -1, Gsk.Transform().translate(Graphene.Point().init(x, y)))

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        progress = self._selection_progress
        if progress > 0.001:
            scale = 1.0 - 0.1 * progress
            snapshot.save()
            snapshot.translate(Graphene.Point().init(width / 2, height / 2))
            snapshot.scale(scale, scale)
            snapshot.translate(Graphene.Point().init(-width / 2, -height / 2))
        radius = TILE_RADIUS + 4 * progress
        snapshot.push_rounded_clip(rounded_rect(0, 0, width, height, radius))
        if self._placeholder is not None:
            snapshot.append_scaled_texture(self._placeholder, Gsk.ScalingFilter.LINEAR, cover_rect(self._placeholder, width, height))
        else:
            snapshot.append_color(placeholder_color(self), Graphene.Rect().init(0, 0, width, height))
        self.snapshot_child(self._picture, snapshot)
        if self._preview.get_visible():
            self.snapshot_child(self._preview, snapshot)
        if self._fallback.get_visible():
            self.snapshot_child(self._fallback, snapshot)
        if self._badge.get_visible():
            self.snapshot_child(self._badge, snapshot)
        if self._favorite.get_visible():
            self.snapshot_child(self._favorite, snapshot)
        snapshot.pop()
        if progress > 0.001:
            color = accent_color(self)
            color.alpha = progress
            border_width = 3.0
            snapshot.append_border(rounded_rect(0, 0, width, height, radius), [border_width] * 4, [color] * 4)
            snapshot.restore()
        self.snapshot_child(self._check, snapshot)
        if self._focused:
            color = accent_color(self)
            snapshot.append_border(rounded_rect(-2, -2, width + 4, height + 4, radius + 2), [2.0] * 4, [color] * 4)

    def _on_selection_progress(self, value):
        self._selection_progress = value
        self.queue_draw()

    def bind(self, item, index, thumbnails, physical_size):
        self.index = index
        same_item = self.item is not None and item is not None and self.item.id == item.id and self.item.mtime == item.mtime
        self.item = item
        small = physical_size <= SMALL_TEXTURE_LIMIT
        variant = "small" if small else "normal"
        if same_item and self._variant == variant and (self._picture.get_paintable() is not None or self._token):
            self._update_badges()
            return
        self._cancel_requests(thumbnails)
        self.stop_preview()
        self._variant = variant
        self._placeholder = decode_placeholder(item.placeholder)
        self._fallback.set_visible(False)
        texture = thumbnails.cached(item, small)
        if texture is not None:
            self._picture.remove_css_class("pending")
            self._picture.set_paintable(texture)
        else:
            self._picture.add_css_class("pending")
            self._picture.set_paintable(None)
            self._token = thumbnails.request(item, self._on_texture, small=small)
        if physical_size > LARGE_TEXTURE_THRESHOLD:
            self._large_token = thumbnails.request(item, self._on_large_texture, tier="large")
        self._update_badges()
        name = item.name
        set_accessible_label(self, f"{'Video' if item.is_video else 'Photo'} {name}, {formatting.short_date(item.sort_date)}")
        self.queue_draw()

    def refresh(self, thumbnails):
        if self.item is None:
            return
        self._placeholder = decode_placeholder(self.item.placeholder)
        if self._picture.get_paintable() is None and not self._token and self.item.thumbnail_state != 3:
            self._token = thumbnails.request(self.item, self._on_texture, small=self._variant == "small")
        self._fallback.set_visible(self._picture.get_paintable() is None and self.item.thumbnail_state == 3)
        if self._fallback.get_visible():
            self._fallback.set_from_icon_name(icons.VIDEO if self.item.is_video else icons.IMAGE)
        self._update_badges()
        self.queue_allocate()
        self.queue_draw()

    def _update_badges(self):
        item = self.item
        if item.is_video:
            self._badge_icon.set_from_icon_name(icons.PLAY)
            self._badge_label.set_label(formatting.human_duration(item.duration) if item.duration else "")
            self._badge_label.set_visible(bool(item.duration))
            self._badge.set_visible(True)
        elif item.is_live_photo:
            self._badge_icon.set_from_icon_name(icons.LIVE_PHOTOS)
            self._badge_label.set_label("LIVE")
            self._badge_label.set_visible(True)
            self._badge.set_visible(True)
        elif item.is_animated:
            self._badge_icon.set_from_icon_name(icons.PLAY)
            self._badge_label.set_label("GIF" if item.extension == "gif" else "ANIM")
            self._badge_label.set_visible(True)
            self._badge.set_visible(True)
        else:
            self._badge.set_visible(False)
        self._favorite.set_visible(item.is_favorite)

    def _on_texture(self, texture):
        self._token = 0
        if texture is None:
            self._fallback.set_from_icon_name(icons.VIDEO if self.item and self.item.is_video else icons.IMAGE)
            self._fallback.set_visible(True)
            self.queue_allocate()
            return
        if self._picture.get_paintable() is None:
            self._picture.set_paintable(texture)
        self._picture.remove_css_class("pending")

    def _on_large_texture(self, texture):
        self._large_token = 0
        if texture is not None:
            self._picture.set_paintable(texture)
            self._picture.remove_css_class("pending")

    def _cancel_requests(self, thumbnails):
        if self._token:
            thumbnails.cancel(self._token)
            self._token = 0
        if self._large_token:
            thumbnails.cancel(self._large_token)
            self._large_token = 0

    def unbind(self):
        if self._grid is not None:
            self._cancel_requests(self._grid.thumbnails)
        self.stop_preview()
        self.item = None
        self.index = -1
        self._picture.set_paintable(None)
        self._placeholder = None
        self._variant = None

    def set_selected(self, selected, animate=True):
        if selected == self._selected:
            return
        self._selected = selected
        if selected:
            self.add_css_class("selected")
        else:
            self.remove_css_class("selected")
        self.update_state([Gtk.AccessibleState.SELECTED], [1 if selected else 0])
        if animate and self.get_mapped():
            self._selection_animation.set_value_from(self._selection_progress)
            self._selection_animation.set_value_to(1.0 if selected else 0.0)
            self._selection_animation.play()
        else:
            self._selection_animation.reset()
            self._selection_progress = 1.0 if selected else 0.0
            self.queue_draw()

    @property
    def texture(self):
        return self._picture.get_paintable()

    def set_focused(self, focused):
        if focused == self._focused:
            return
        self._focused = focused
        self.queue_draw()

    def check_contains(self, x, y):
        return 0 <= x <= 34 and 0 <= y <= 34

    def start_preview(self):
        if self.item is None or not self.item.is_video or self._preview_media is not None:
            return
        media = Gtk.MediaFile.new_for_filename(self.item.path)
        media.set_muted(True)
        media.set_volume(0.0)
        media.set_loop(True)
        self._preview_media = media
        self._preview.set_paintable(media)
        media.connect("notify::prepared", self._on_preview_prepared)
        media.play()

    def _on_preview_prepared(self, media, _param):
        if media is not self._preview_media or not media.get_prepared() or media.get_error() is not None:
            return
        self._preview.set_visible(True)
        self._preview.add_css_class("playing")
        self.queue_allocate()

    def stop_preview(self):
        media = self._preview_media
        if media is None:
            return
        self._preview_media = None
        media.pause()
        self._preview.set_paintable(None)
        self._preview.set_visible(False)
        self._preview.remove_css_class("playing")


class TileLayer(Gtk.Widget):
    __gtype_name__ = "PicnestTileLayer"

    def __init__(self, on_allocate):
        super().__init__()
        self._on_allocate = on_allocate

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1

    def do_size_allocate(self, width, height, baseline):
        if self._on_allocate is not None:
            self._on_allocate()

    def do_dispose(self):
        self._on_allocate = None
        unparent_children(self)
        Gtk.Widget.do_dispose(self)


class VirtualGrid(Gtk.Widget, Gtk.Scrollable):
    __gtype_name__ = "PicnestVirtualGrid"
    __gsignals__ = {
        "item-activated": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "selection-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "context-menu": (GObject.SignalFlags.RUN_FIRST, None, (int, float, float)),
        "zoom-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, library, settings):
        super().__init__(accessible_role=Gtk.AccessibleRole.GRID, focusable=True, focus_on_click=True)
        self.library = library
        self.thumbnails = library.thumbnails
        self.settings = settings
        self._hadjustment = None
        self._vadjustment = None
        self._hscroll_policy = Gtk.ScrollablePolicy.MINIMUM
        self._vscroll_policy = Gtk.ScrollablePolicy.MINIMUM
        self._ids = []
        self._index_lookup = None
        self._sections = []
        self._view_key = ""
        self._layout = None
        self._layout_dirty = True
        self._zoom_level = max(0, min(len(ZOOM_LEVELS) - 1, settings.get("grid-zoom-level")))
        self._tiles = {}
        self._free_tiles = []
        self._headers = {}
        self._free_headers = []
        self._selected = set()
        self._selection_mode = False
        self._anchor_index = -1
        self._focus_index = -1
        self._keyboard_focus = False
        self._hover_index = -1
        self._hover_source = 0
        self._previewing_tile = None
        self._pending_anchor = None
        self._rubber_band = None
        self._rubber_band_base = set()
        self._autoscroll_source = 0
        self._autoscroll_speed = 0.0
        self._zoom_scale_from = 1.0
        self._zoom_offset_from = (0.0, 0.0)
        self._zoom_progress = 1.0
        self._pinch_scale = 1.0
        self._pinch_origin = (0.0, 0.0)
        self._scroll_accumulator = 0.0
        self._pointer = (0.0, 0.0)
        self._drag_targets = []
        self._drag_index = -1
        self._rubber_band_origin = None
        self._rubber_band_started = False
        self._rubber_band_additive = False
        self._visible = []
        self._visible_fingerprint = None
        self._tiles_dirty = True
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.add_css_class("pn-grid")
        self._tile_layer = TileLayer(self._allocate_tiles)
        self._tile_layer.set_parent(self)
        self.set_vadjustment(None)
        self.set_hadjustment(None)
        self._zoom_animation = Adw.TimedAnimation.new(self, 0.0, 1.0, 260, Adw.CallbackAnimationTarget.new(self._on_zoom_animation))
        self._zoom_animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._setup_controllers()

    def do_dispose(self):
        if hasattr(self, "_hover_source"):
            self.stop_previews()
            if self._autoscroll_source:
                GLib.source_remove(self._autoscroll_source)
                self._autoscroll_source = 0
            for tile in list(self._tiles.values()) + self._free_tiles:
                tile.unbind()
        unparent_children(self)
        Gtk.Widget.do_dispose(self)

    @GObject.Property(type=Gtk.Adjustment)
    def hadjustment(self):
        return self._hadjustment

    @hadjustment.setter
    def hadjustment(self, adjustment):
        self.set_hadjustment(adjustment)

    @GObject.Property(type=Gtk.Adjustment)
    def vadjustment(self):
        return self._vadjustment

    @vadjustment.setter
    def vadjustment(self, adjustment):
        self.set_vadjustment(adjustment)

    @GObject.Property(type=Gtk.ScrollablePolicy, default=Gtk.ScrollablePolicy.MINIMUM)
    def hscroll_policy(self):
        return self._hscroll_policy

    @hscroll_policy.setter
    def hscroll_policy(self, policy):
        self._hscroll_policy = policy

    @GObject.Property(type=Gtk.ScrollablePolicy, default=Gtk.ScrollablePolicy.MINIMUM)
    def vscroll_policy(self):
        return self._vscroll_policy

    @vscroll_policy.setter
    def vscroll_policy(self, policy):
        self._vscroll_policy = policy

    def set_hadjustment(self, adjustment):
        self._hadjustment = adjustment or Gtk.Adjustment()

    def set_vadjustment(self, adjustment):
        if self._vadjustment is not None:
            self._vadjustment.disconnect_by_func(self._on_scrolled)
        self._vadjustment = adjustment or Gtk.Adjustment()
        self._vadjustment.connect("value-changed", self._on_scrolled)
        self.queue_allocate()

    def _setup_controllers(self):
        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self._on_pressed)
        click.connect("released", self._on_released)
        self.add_controller(click)
        self._click = click
        long_press = Gtk.GestureLongPress(touch_only=True)
        long_press.connect("pressed", self._on_long_press)
        self.add_controller(long_press)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)
        self._drag = drag
        drag_source = Gtk.DragSource(actions=Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        drag_source.connect("prepare", self._on_drag_prepare)
        drag_source.connect("drag-begin", self._on_dnd_begin)
        self.add_controller(drag_source)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)
        zoom = Gtk.GestureZoom()
        zoom.connect("begin", self._on_pinch_begin)
        zoom.connect("scale-changed", self._on_pinch_changed)
        zoom.connect("end", self._on_pinch_end)
        self.add_controller(zoom)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)

    @property
    def ids(self):
        return self._ids

    @property
    def sections(self):
        return self._sections

    @property
    def zoom_level(self):
        return self._zoom_level

    @property
    def selection_mode(self):
        return self._selection_mode

    @property
    def item_count(self):
        return len(self._ids)

    def set_content(self, content, keep_position=True):
        same_view = keep_position and content.view_key == self._view_key and self._layout is not None
        anchor = self._capture_anchor() if same_view else None
        collapsed = {section.key for section in self._sections if section.collapsed} if same_view else set()
        for section in content.sections:
            if section.key in collapsed:
                section.collapsed = True
        self._ids = content.ids
        self._index_lookup = None
        self._sections = content.sections
        self._view_key = content.view_key
        if self._selected:
            valid = set(self._ids)
            before = len(self._selected)
            self._selected &= valid
            if len(self._selected) != before:
                GLib.idle_add(self._emit_selection_changed)
        self._release_all_tiles()
        self._layout_dirty = True
        self._pending_anchor = anchor if anchor is not None else ("top", 0)
        self._focus_index = min(self._focus_index, len(self._ids) - 1)
        self.queue_allocate()

    def _emit_selection_changed(self):
        self.emit("selection-changed")
        return GLib.SOURCE_REMOVE

    def index_of(self, item_id):
        if self._index_lookup is None:
            self._index_lookup = {value: index for index, value in enumerate(self._ids)}
        return self._index_lookup.get(item_id, -1)

    def _capture_anchor(self):
        layout = self._layout
        if layout is None or not self._ids:
            return None
        value = self._vadjustment.get_value()
        if value <= 1:
            return ("top", 0)
        for index in layout.visible_indices(value, value + layout.row_height * 2):
            rect = layout.item_rect(index)
            if rect is not None and rect[1] + rect[3] > value:
                return ("item", self._ids[index], rect[1] - value)
        return ("offset", value)

    def _apply_anchor(self, layout, height):
        anchor, self._pending_anchor = self._pending_anchor, None
        if anchor is None:
            return
        maximum = max(0.0, layout.total_height - height)
        if anchor[0] == "top":
            value = 0.0
        elif anchor[0] == "offset":
            value = anchor[1]
        elif anchor[0] == "item":
            index = self.index_of(anchor[1])
            rect = layout.item_rect(index) if index >= 0 else None
            value = rect[1] - anchor[2] if rect else self._vadjustment.get_value()
        elif anchor[0] == "index":
            rect = layout.item_rect(anchor[1])
            value = (rect[1] - anchor[2]) if rect else self._vadjustment.get_value()
        else:
            value = self._vadjustment.get_value()
        self._vadjustment.configure(max(0.0, min(maximum, value)), 0, max(layout.total_height, height), layout.row_height * 0.5, height * 0.9, height)

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1

    def do_size_allocate(self, width, height, baseline):
        if self._layout is None or self._layout_dirty or abs(self._layout.width - width) > 0.5:
            previous = self._layout
            if self._pending_anchor is None and previous is not None and self._ids:
                self._pending_anchor = self._capture_anchor()
            self._layout = GridLayout(self._sections, width, ZOOM_LEVELS[self._zoom_level])
            self._layout_dirty = False
            self._tiles_dirty = True
            if previous is None or abs(previous.tile - self._layout.tile) > 0.5:
                self._release_all_tiles()
            if self._pending_anchor is None:
                self._pending_anchor = ("offset", self._vadjustment.get_value())
        layout = self._layout
        if self._pending_anchor is not None:
            self._apply_anchor(layout, height)
        else:
            value = min(self._vadjustment.get_value(), max(0.0, layout.total_height - height))
            self._vadjustment.configure(value, 0, max(layout.total_height, height), layout.row_height * 0.5, height * 0.9, height)
        value = self._vadjustment.get_value()
        overscan = layout.row_height
        if self._zoom_animation.get_state() == Adw.AnimationState.PLAYING or self._pinch_scale != 1.0:
            overscan = height * 1.5
        visible = list(layout.visible_indices(max(0.0, value - overscan), value + height + overscan))
        fingerprint = (visible[0], visible[-1], len(visible), int(layout.tile)) if visible else (0, -1, 0, int(layout.tile))
        if fingerprint != self._visible_fingerprint or self._tiles_dirty:
            self._visible = visible
            self._visible_fingerprint = fingerprint
            self._tile_layer.queue_allocate()
        layer_height = int(max(layout.total_height, height))
        self._tile_layer.allocate(int(width), layer_height, -1, Gsk.Transform().translate(Graphene.Point().init(0, -value)))
        self._allocate_headers(layout, width, value, height)

    def _allocate_tiles(self):
        layout = self._layout
        if layout is None:
            return
        visible = self._visible
        visible_set = set(visible)
        for index in [index for index in self._tiles if index not in visible_set]:
            tile = self._tiles.pop(index)
            tile.unbind()
            tile.set_child_visible(False)
            self._free_tiles.append(tile)
        missing = [index for index in visible if index not in self._tiles and index < len(self._ids)]
        if missing:
            self.library.store.prefetch([self._ids[index] for index in missing])
        tile_size = int(layout.tile)
        physical = tile_size * self.get_scale_factor()
        for index in missing:
            item = self.library.store.get(self._ids[index])
            if item is None:
                continue
            tile = self._free_tiles.pop() if self._free_tiles else self._create_tile()
            tile.set_child_visible(True)
            tile.bind(item, index, self.thumbnails, physical)
            tile.set_selected(item.id in self._selected, animate=False)
            tile.set_focused(index == self._focus_index and self._keyboard_focus)
            self._tiles[index] = tile
        for index, tile in self._tiles.items():
            x, y, _size, _ = layout.item_rect(index)
            tile.allocate(tile_size, tile_size, -1, Gsk.Transform().translate(Graphene.Point().init(x, y)))
        self._tiles_dirty = False

    def _create_tile(self):
        tile = MediaTile(self)
        tile.set_parent(self._tile_layer)
        return tile

    def _create_header(self):
        header = SectionHeader(self._toggle_section, self._toggle_section_selection)
        header.set_parent(self)
        return header

    def _allocate_headers(self, layout, width, value, height):
        needed = {}
        if layout.has_headers and self._sections:
            first = layout.section_at_y(value)
            for section_index in range(first, len(self._sections)):
                top = layout.section_tops[section_index]
                if top > value + height:
                    break
                if not self._sections[section_index].show_header:
                    continue
                y = top
                pinned = False
                if section_index == first and top < value:
                    next_top = layout.section_tops[section_index + 1] if section_index + 1 < len(self._sections) else layout.total_height
                    y = min(value, next_top - HEADER_HEIGHT)
                    pinned = y > top
                needed[section_index] = (y, pinned)
        for section_index in [index for index in self._headers if index not in needed]:
            header = self._headers.pop(section_index)
            header.set_child_visible(False)
            self._free_headers.append(header)
        header_width = int(width - 2 * SIDE_PADDING + 8)
        for section_index, (y, pinned) in needed.items():
            header = self._headers.get(section_index)
            if header is None:
                header = self._free_headers.pop() if self._free_headers else self._create_header()
                header.set_child_visible(True)
                self._headers[section_index] = header
            section = self._sections[section_index]
            header.bind(section_index, section, self._section_fully_selected(section), pinned)
            header.measure(Gtk.Orientation.HORIZONTAL, -1)
            header.allocate(header_width, HEADER_HEIGHT, -1, Gsk.Transform().translate(Graphene.Point().init(SIDE_PADDING - 4, y - value)))

    def _section_fully_selected(self, section):
        if not self._selected or section.count == 0:
            return False
        if section.count > 5000:
            return False
        return all(self._ids[index] in self._selected for index in range(section.start, section.start + section.count))

    def _release_all_tiles(self):
        for tile in self._tiles.values():
            tile.unbind()
            tile.set_child_visible(False)
            self._free_tiles.append(tile)
        self._tiles.clear()
        self._previewing_tile = None
        self._tiles_dirty = True

    def do_snapshot(self, snapshot):
        transformed = False
        if self._zoom_progress < 1.0 or self._pinch_scale != 1.0:
            transformed = True
            snapshot.save()
            if self._pinch_scale != 1.0:
                origin_x, origin_y = self._pinch_origin
                snapshot.translate(Graphene.Point().init(origin_x, origin_y))
                snapshot.scale(self._pinch_scale, self._pinch_scale)
                snapshot.translate(Graphene.Point().init(-origin_x, -origin_y))
            else:
                progress = self._zoom_progress
                scale = self._zoom_scale_from + (1.0 - self._zoom_scale_from) * progress
                offset_x = self._zoom_offset_from[0] * (1.0 - progress)
                offset_y = self._zoom_offset_from[1] * (1.0 - progress)
                snapshot.translate(Graphene.Point().init(offset_x, offset_y))
                snapshot.scale(scale, scale)
        self.snapshot_child(self._tile_layer, snapshot)
        if transformed:
            snapshot.restore()
        for header in self._headers.values():
            self.snapshot_child(header, snapshot)
        if self._rubber_band is not None:
            x0, y0, x1, y1 = self._rubber_band
            value = self._vadjustment.get_value()
            rect = Graphene.Rect().init(min(x0, x1), min(y0, y1) - value, abs(x1 - x0), abs(y1 - y0))
            color = accent_color(self)
            fill = color.copy()
            fill.alpha = 0.18
            snapshot.append_color(fill, rect)
            rounded = Gsk.RoundedRect()
            rounded.init_from_rect(rect, 4)
            snapshot.append_border(rounded, [1.0] * 4, [color] * 4)

    def _on_scrolled(self, _adjustment):
        activity.mark_busy()
        self._cancel_hover_preview()
        self.queue_allocate()

    def refresh_items(self, item_ids):
        if item_ids is None:
            for tile in self._tiles.values():
                if tile.item is not None:
                    tile.item = self.library.store.get(tile.item.id) or tile.item
                    tile.refresh(self.thumbnails)
            return
        wanted = set(item_ids)
        for tile in self._tiles.values():
            if tile.item is not None and tile.item.id in wanted:
                fresh = self.library.store.get(tile.item.id)
                if fresh is not None:
                    tile.item = fresh
                tile.refresh(self.thumbnails)

    def get_item_rect(self, index):
        layout = self._layout
        if layout is None or index < 0 or index >= len(self._ids):
            return None
        rect = layout.item_rect(index)
        if rect is None:
            return None
        x, y, size, _ = rect
        value = self._vadjustment.get_value()
        top = y - value
        if top + size < 0 or top > self.get_height():
            return None
        return Graphene.Rect().init(x, top, size, size)

    def texture_for_index(self, index):
        tile = self._tiles.get(index)
        return tile.texture if tile is not None else None

    def scroll_to_index(self, index, center=False):
        layout = self._layout
        if layout is None:
            self._pending_anchor = ("index", index, 40)
            return
        rect = layout.item_rect(index)
        if rect is None:
            section_index = layout.section_of_index(index)
            if section_index >= 0 and self._sections[section_index].collapsed:
                self._sections[section_index].collapsed = False
                self._layout_dirty = True
                self._pending_anchor = ("index", index, 40)
                self.queue_allocate()
            return
        value = self._vadjustment.get_value()
        height = self.get_height()
        y, size = rect[1], rect[3]
        header_offset = HEADER_HEIGHT if layout.has_headers else 0
        if center:
            target = y - (height - size) / 2
        elif y - header_offset < value:
            target = y - header_offset - GAP
        elif y + size > value + height:
            target = y + size - height + GAP
        else:
            return
        self._vadjustment.set_value(max(0.0, min(target, layout.total_height - height)))

    def set_zoom_level(self, level, anchor_point=None, animate=True):
        level = max(0, min(len(ZOOM_LEVELS) - 1, level))
        if level == self._zoom_level or self._layout is None:
            self._zoom_level = level
            return
        layout = self._layout
        width, height = self.get_width(), self.get_height()
        point_x, point_y = anchor_point if anchor_point is not None else (width / 2, height / 2)
        value = self._vadjustment.get_value()
        kind, index = layout.hit_test(point_x, point_y + value)
        if kind != "item":
            candidates = list(layout.visible_indices(value + point_y - layout.tile, value + point_y + layout.tile))
            index = candidates[len(candidates) // 2] if candidates else (self._focus_index if self._focus_index >= 0 else 0)
        old_rect = layout.item_rect(index) if self._ids else None
        self._zoom_level = level
        self.settings.set("grid-zoom-level", level)
        new_layout = GridLayout(self._sections, width, ZOOM_LEVELS[level])
        self._layout = new_layout
        self._release_all_tiles()
        if old_rect is not None:
            new_rect = new_layout.item_rect(index)
            fraction_y = (point_y - (old_rect[1] - value)) / old_rect[3]
            new_value = new_rect[1] - (point_y - fraction_y * new_rect[3])
            new_value = max(0.0, min(new_value, max(0.0, new_layout.total_height - height)))
            self._vadjustment.configure(new_value, 0, max(new_layout.total_height, height), new_layout.row_height * 0.5, height * 0.9, height)
            if animate:
                scale = old_rect[3] / new_rect[3]
                self._zoom_scale_from = scale
                self._zoom_offset_from = (old_rect[0] - scale * new_rect[0], (old_rect[1] - value) - scale * (new_rect[1] - new_value))
                self._zoom_progress = 0.0
                self._zoom_animation.reset()
                self._zoom_animation.play()
        self.queue_allocate()
        self.emit("zoom-changed", level)

    def zoom_in(self):
        self.set_zoom_level(self._zoom_level + 1)

    def zoom_out(self):
        self.set_zoom_level(self._zoom_level - 1)

    def _on_zoom_animation(self, value):
        self._zoom_progress = value
        self.queue_draw()
        if value >= 1.0:
            self.queue_allocate()

    def _on_scroll(self, controller, _dx, dy):
        state = controller.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            unit = controller.get_unit() if hasattr(controller, "get_unit") else Gdk.ScrollUnit.WHEEL
            step = 1.0 if unit == Gdk.ScrollUnit.WHEEL else 0.04
            self._scroll_accumulator += dy * step
            if abs(self._scroll_accumulator) >= 1.0:
                direction = -1 if self._scroll_accumulator > 0 else 1
                self._scroll_accumulator = 0.0
                self.set_zoom_level(self._zoom_level + direction, self._pointer)
            return True
        return False

    def _on_pinch_begin(self, gesture, _sequence):
        found, x, y = gesture.get_bounding_box_center()
        self._pinch_origin = (x, y) if found else (self.get_width() / 2, self.get_height() / 2)
        self._pinch_scale = 1.0

    def _on_pinch_changed(self, _gesture, scale):
        self._pinch_scale = max(0.4, min(2.5, scale))
        self.queue_draw()

    def _on_pinch_end(self, _gesture, _sequence):
        scale = self._pinch_scale
        self._pinch_scale = 1.0
        if self._layout is None:
            return
        target_size = self._layout.tile * scale
        level = min(range(len(ZOOM_LEVELS)), key=lambda candidate: abs(ZOOM_LEVELS[candidate] - target_size))
        if level == self._zoom_level and abs(scale - 1.0) > 0.25:
            level += 1 if scale > 1 else -1
        self.set_zoom_level(level, self._pinch_origin)
        self.queue_draw()

    def _hit(self, x, y):
        if self._layout is None:
            return None, -1
        return self._layout.hit_test(x, y + self._vadjustment.get_value())

    def _on_pressed(self, gesture, n_press, x, y):
        self.grab_focus()
        button = gesture.get_current_button()
        kind, index = self._hit(x, y)
        if button == Gdk.BUTTON_SECONDARY:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            if kind == "item":
                item_id = self._ids[index]
                if item_id not in self._selected:
                    if not self._selection_mode:
                        self._selected.clear()
                    self._set_focus(index)
                self.emit("context-menu", index, x, y)

    def _on_released(self, gesture, n_press, x, y):
        button = gesture.get_current_button()
        if button != Gdk.BUTTON_PRIMARY or n_press > 1:
            return
        kind, index = self._hit(x, y)
        if kind != "item":
            if kind is None and not self._selection_mode and self._selected:
                state = gesture.get_current_event_state()
                if not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK):
                    self.clear_selection()
            return
        state = gesture.get_current_event_state()
        tile = self._tiles.get(index)
        on_check = False
        if tile is not None:
            rect = self._layout.item_rect(index)
            on_check = tile.check_contains(x - rect[0], y + self._vadjustment.get_value() - rect[1])
        if state & Gdk.ModifierType.SHIFT_MASK and self._anchor_index >= 0:
            self._select_range(self._anchor_index, index, additive=bool(state & Gdk.ModifierType.CONTROL_MASK) or self._selection_mode)
            self._set_focus(index, keyboard=False)
            return
        if state & Gdk.ModifierType.CONTROL_MASK or self._selection_mode or on_check:
            self.toggle_index(index)
            return
        self._set_focus(index, keyboard=False)
        self._anchor_index = index
        self.emit("item-activated", index)

    def _on_long_press(self, gesture, x, y):
        kind, index = self._hit(x, y)
        if kind == "item":
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.set_selection_mode(True)
            self.toggle_index(index)

    def _rubber_band_allowed(self, x, y, state):
        kind, _index = self._hit(x, y)
        return kind != "item" or self._selection_mode or bool(state & Gdk.ModifierType.SHIFT_MASK)

    def _on_drag_begin(self, gesture, x, y):
        state = gesture.get_current_event_state()
        if gesture.get_current_button() != Gdk.BUTTON_PRIMARY or not self._rubber_band_allowed(x, y, state):
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        self._rubber_band_origin = (x, y + self._vadjustment.get_value())
        self._rubber_band_started = False
        self._rubber_band_additive = bool(state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)) or self._selection_mode

    def _on_drag_update(self, gesture, offset_x, offset_y):
        if self._rubber_band_origin is None:
            return
        if not self._rubber_band_started:
            if abs(offset_x) < 6 and abs(offset_y) < 6:
                return
            self._rubber_band_started = True
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._rubber_band_base = set(self._selected) if self._rubber_band_additive else set()
        start_x, start_y_content = self._rubber_band_origin
        _found, start_x_widget, start_y_widget = gesture.get_start_point()
        current_x = start_x_widget + offset_x
        current_y = start_y_widget + offset_y
        self._pointer = (current_x, current_y)
        value = self._vadjustment.get_value()
        self._rubber_band = (start_x, start_y_content, current_x, current_y + value)
        self._update_rubber_band_selection()
        height = self.get_height()
        if current_y < 40:
            self._autoscroll_speed = -max(4.0, (40 - current_y) * 0.8)
        elif current_y > height - 40:
            self._autoscroll_speed = max(4.0, (current_y - height + 40) * 0.8)
        else:
            self._autoscroll_speed = 0.0
        if self._autoscroll_speed and not self._autoscroll_source:
            self._autoscroll_source = GLib.timeout_add(16, self._autoscroll_tick)
        self.queue_draw()

    def _autoscroll_tick(self):
        if not self._autoscroll_speed or self._rubber_band is None:
            self._autoscroll_source = 0
            return GLib.SOURCE_REMOVE
        adjustment = self._vadjustment
        before = adjustment.get_value()
        adjustment.set_value(max(0.0, min(before + self._autoscroll_speed, adjustment.get_upper() - adjustment.get_page_size())))
        delta = adjustment.get_value() - before
        if delta and self._rubber_band is not None:
            x0, y0, x1, y1 = self._rubber_band
            self._rubber_band = (x0, y0, x1, y1 + delta)
            self._update_rubber_band_selection()
        return GLib.SOURCE_CONTINUE

    def _update_rubber_band_selection(self):
        if self._layout is None or self._rubber_band is None:
            return
        x0, y0, x1, y1 = self._rubber_band
        inside = {self._ids[index] for index in self._layout.indices_in_rect(x0, y0, x1, y1)}
        new_selection = self._rubber_band_base | inside
        if new_selection != self._selected:
            self._selected = new_selection
            self._sync_tile_selection()
            self.emit("selection-changed")

    def _on_drag_end(self, _gesture, _offset_x, _offset_y):
        self._rubber_band_origin = None
        self._autoscroll_speed = 0.0
        if self._rubber_band is not None:
            self._rubber_band = None
            if self._selected and not self._selection_mode:
                self.set_selection_mode(True)
            self.queue_draw()

    def _on_drag_prepare(self, source, x, y):
        state = source.get_current_event_state()
        if self._rubber_band_allowed(x, y, state):
            return None
        kind, index = self._hit(x, y)
        if kind != "item":
            return None
        item_id = self._ids[index]
        target_ids = list(self._selected) if item_id in self._selected else [item_id]
        items = self.library.items(target_ids)
        files = [Gio.File.new_for_path(self.library.file_path_for(item)) for item in items]
        if not files:
            return None
        self._drag_targets = target_ids
        self._drag_index = index
        file_list = Gdk.FileList.new_from_array(files)
        uri_text = "\r\n".join(file.get_uri() for file in files) + "\r\n"
        return Gdk.ContentProvider.new_union(
            [
                Gdk.ContentProvider.new_for_value(GObject.Value(Gdk.FileList, file_list)),
                Gdk.ContentProvider.new_for_bytes("text/uri-list", GLib.Bytes.new(uri_text.encode("utf-8"))),
            ]
        )

    def _on_dnd_begin(self, source, _drag):
        tile = self._tiles.get(self._drag_index)
        if tile is not None:
            paintable = tile.texture or Gtk.WidgetPaintable.new(tile)
            source.set_icon(paintable, 24, 24)

    def _on_motion(self, _controller, x, y):
        self._pointer = (x, y)
        if not self.settings.get("hover-video-preview"):
            return
        kind, index = self._hit(x, y)
        index = index if kind == "item" else -1
        if index == self._hover_index:
            return
        self._hover_index = index
        self._cancel_hover_preview()
        if index >= 0:
            tile = self._tiles.get(index)
            if tile is not None and tile.item is not None and tile.item.is_video:
                self._hover_source = GLib.timeout_add(HOVER_PREVIEW_DELAY, self._start_hover_preview, index)

    def _on_leave(self, _controller):
        self._hover_index = -1
        self._cancel_hover_preview()

    def _start_hover_preview(self, index):
        self._hover_source = 0
        tile = self._tiles.get(index)
        if tile is not None and index == self._hover_index:
            tile.start_preview()
            self._previewing_tile = tile
        return GLib.SOURCE_REMOVE

    def _cancel_hover_preview(self):
        if self._hover_source:
            GLib.source_remove(self._hover_source)
            self._hover_source = 0
        if self._previewing_tile is not None:
            self._previewing_tile.stop_preview()
            self._previewing_tile = None

    def stop_previews(self):
        self._hover_index = -1
        self._cancel_hover_preview()

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        if not self._ids or self._layout is None:
            return False
        columns = self._layout.columns
        current = self._focus_index if self._focus_index >= 0 else -1
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        movement = {
            Gdk.KEY_Left: -1, Gdk.KEY_Right: 1, Gdk.KEY_Up: -columns, Gdk.KEY_Down: columns,
            Gdk.KEY_KP_Left: -1, Gdk.KEY_KP_Right: 1, Gdk.KEY_KP_Up: -columns, Gdk.KEY_KP_Down: columns,
        }
        if keyval in movement:
            target = 0 if current < 0 else max(0, min(len(self._ids) - 1, current + movement[keyval]))
        elif keyval in (Gdk.KEY_Home, Gdk.KEY_KP_Home):
            target = 0
        elif keyval in (Gdk.KEY_End, Gdk.KEY_KP_End):
            target = len(self._ids) - 1
        elif keyval in (Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
            rows = max(1, int(self.get_height() // self._layout.row_height))
            delta = rows * columns * (1 if keyval == Gdk.KEY_Page_Down else -1)
            target = max(0, min(len(self._ids) - 1, max(current, 0) + delta))
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and current >= 0:
            if self._selection_mode:
                self.toggle_index(current)
            else:
                self.emit("item-activated", current)
            return True
        elif keyval == Gdk.KEY_space and current >= 0:
            if not self._selection_mode:
                self.set_selection_mode(True)
            self.toggle_index(current)
            return True
        elif keyval == Gdk.KEY_a and control:
            self.select_all()
            return True
        elif keyval == Gdk.KEY_Escape and (self._selected or self._selection_mode):
            self.clear_selection()
            self.set_selection_mode(False)
            return True
        elif keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add) and control:
            self.zoom_in()
            return True
        elif keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract) and control:
            self.zoom_out()
            return True
        elif keyval == Gdk.KEY_Menu and current >= 0:
            rect = self.get_item_rect(current)
            if rect is not None:
                self.emit("context-menu", current, rect.get_x() + rect.get_width() / 2, rect.get_y() + rect.get_height() / 2)
            return True
        else:
            return False
        if shift:
            anchor = self._anchor_index if self._anchor_index >= 0 else max(current, 0)
            self._anchor_index = anchor
            self._select_range(anchor, target, additive=False)
        elif not control:
            self._anchor_index = target
        self._set_focus(target, keyboard=True)
        return True

    def _set_focus(self, index, keyboard=True):
        previous = self._tiles.get(self._focus_index)
        if previous is not None:
            previous.set_focused(False)
        self._focus_index = index
        self._keyboard_focus = keyboard
        if keyboard:
            self.scroll_to_index(index)
            tile = self._tiles.get(index)
            if tile is not None:
                tile.set_focused(True)
        self.queue_allocate()

    @property
    def focus_index(self):
        return self._focus_index

    def set_selection_mode(self, enabled):
        if enabled == self._selection_mode:
            return
        self._selection_mode = enabled
        if enabled:
            self.add_css_class("selection-mode")
        else:
            self.remove_css_class("selection-mode")
            if self._selected:
                self._selected.clear()
                self._sync_tile_selection()
        self.emit("selection-changed")
        self.queue_allocate()

    def selected_ids(self):
        if not self._selected:
            return []
        order = {item_id: position for position, item_id in enumerate(self._ids)} if len(self._selected) < 50000 else {}
        return sorted(self._selected, key=lambda item_id: order.get(item_id, 0))

    @property
    def selection_count(self):
        return len(self._selected)

    def toggle_index(self, index):
        item_id = self._ids[index]
        if item_id in self._selected:
            self._selected.discard(item_id)
        else:
            self._selected.add(item_id)
        self._anchor_index = index
        self._set_focus(index, keyboard=False)
        if self._selected and not self._selection_mode:
            self.set_selection_mode(True)
        self._sync_tile_selection()
        self.emit("selection-changed")

    def _select_range(self, start, end, additive):
        low, high = min(start, end), max(start, end)
        chosen = set(self._ids[low : high + 1])
        self._selected = (self._selected | chosen) if additive else chosen
        if not self._selection_mode:
            self.set_selection_mode(True)
        self._sync_tile_selection()
        self.emit("selection-changed")

    def select_all(self):
        self._selected = set(self._ids)
        if not self._selection_mode:
            self.set_selection_mode(True)
        self._sync_tile_selection()
        self.emit("selection-changed")

    def select_ids(self, item_ids):
        self._selected = set(item_ids) & set(self._ids)
        if self._selected and not self._selection_mode:
            self.set_selection_mode(True)
        self._sync_tile_selection()
        self.emit("selection-changed")

    def clear_selection(self):
        if not self._selected:
            return
        self._selected.clear()
        self._sync_tile_selection()
        self.emit("selection-changed")

    def _sync_tile_selection(self):
        for tile in self._tiles.values():
            if tile.item is not None:
                tile.set_selected(tile.item.id in self._selected)
        self.queue_allocate()

    def _toggle_section(self, section_index):
        if section_index < 0 or section_index >= len(self._sections) or self._layout is None:
            return
        value = self._vadjustment.get_value()
        section_top = self._layout.section_tops[section_index]
        self._sections[section_index].collapsed = not self._sections[section_index].collapsed
        self._release_all_tiles()
        width, height = self.get_width(), self.get_height()
        self._layout = GridLayout(self._sections, width, ZOOM_LEVELS[self._zoom_level])
        self._layout_dirty = False
        target = self._layout.section_tops[section_index] if section_top < value else value
        target = max(0.0, min(target, max(0.0, self._layout.total_height - height)))
        self._vadjustment.configure(target, 0, max(self._layout.total_height, height), self._layout.row_height * 0.5, height * 0.9, height)
        self.queue_allocate()

    def _toggle_section_selection(self, section_index):
        if section_index < 0 or section_index >= len(self._sections):
            return
        section = self._sections[section_index]
        section_ids = set(self._ids[section.start : section.start + section.count])
        if section_ids <= self._selected:
            self._selected -= section_ids
        else:
            self._selected |= section_ids
            if not self._selection_mode:
                self.set_selection_mode(True)
        self._sync_tile_selection()
        self.emit("selection-changed")

    def collapse_all(self, collapsed):
        for section in self._sections:
            if section.show_header:
                section.collapsed = collapsed
        self._layout_dirty = True
        self._release_all_tiles()
        self._pending_anchor = ("top", 0)
        self.queue_allocate()
