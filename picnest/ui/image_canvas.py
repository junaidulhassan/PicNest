from gi.repository import Adw, Gdk, GLib, GObject, Graphene, Gsk, Gtk

MAXIMUM_ZOOM = 12.0
MINIMUM_ZOOM = 1.0
ZOOM_STEP = 1.25


def _lerp(start, end, progress):
    return start + (end - start) * progress


def _lerp_rect(start, end, progress):
    return Graphene.Rect().init(
        _lerp(start.get_x(), end.get_x(), progress),
        _lerp(start.get_y(), end.get_y(), progress),
        _lerp(start.get_width(), end.get_width(), progress),
        _lerp(start.get_height(), end.get_height(), progress),
    )


class ImageCanvas(Gtk.Widget):
    __gtype_name__ = "PicnestImageCanvas"
    __gsignals__ = {
        "zoom-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "swipe": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "single-click": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "transition-finished": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self):
        super().__init__(focusable=False, hexpand=True, vexpand=True)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.add_css_class("pn-canvas")
        self._texture = None
        self._image_width = 1
        self._image_height = 1
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._rotation = 0
        self._display_rotation = 0.0
        self._flip_x = 1.0
        self._flip_y = 1.0
        self._display_flip_x = 1.0
        self._display_flip_y = 1.0
        self._transition_rect = None
        self._transition_clip = None
        self._transition_progress = 1.0
        self._transition_closing = False
        self._swipe_offset = 0.0
        self._drag_start_pan = (0.0, 0.0)
        self._pinch_start_zoom = 1.0
        self._pinch_anchor = (0.0, 0.0)
        self._animation_iterator = None
        self._animation_source = 0
        self._zoom_animation_from = None
        self._zoom_animation_to = None
        self._rotation_from = 0.0
        self._rotation_to = 0.0
        self._flip_from = (1.0, 1.0)
        self._flip_to = (1.0, 1.0)
        self._transition_animation = Adw.TimedAnimation.new(self, 0.0, 1.0, 320, Adw.CallbackAnimationTarget.new(self._on_transition_tick))
        self._transition_animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._transition_animation.connect("done", self._on_transition_done)
        self._transform_animation = Adw.TimedAnimation.new(self, 0.0, 1.0, 260, Adw.CallbackAnimationTarget.new(self._on_transform_tick))
        self._transform_animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._zoom_animation = Adw.TimedAnimation.new(self, 0.0, 1.0, 220, Adw.CallbackAnimationTarget.new(self._on_zoom_tick))
        self._zoom_animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._setup_controllers()

    def _setup_controllers(self):
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)
        click = Gtk.GestureClick()
        click.connect("released", self._on_click_released)
        self.add_controller(click)
        zoom = Gtk.GestureZoom()
        zoom.connect("begin", self._on_pinch_begin)
        zoom.connect("scale-changed", self._on_pinch_changed)
        self.add_controller(zoom)
        drag.group(zoom)
        self._drag_gesture = drag

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1

    @property
    def texture(self):
        return self._texture

    @property
    def zoom(self):
        return self._zoom

    @property
    def is_zoomed(self):
        return self._zoom > 1.01

    def set_texture(self, texture, logical_size=None, reset=True):
        self._stop_animation_playback()
        self._texture = texture
        if logical_size and logical_size[0] and logical_size[1]:
            self._image_width, self._image_height = logical_size
        elif texture is not None:
            self._image_width, self._image_height = texture.get_width(), texture.get_height()
        if reset:
            self._zoom = 1.0
            self._pan_x = self._pan_y = 0.0
            self._rotation = 0
            self._display_rotation = 0.0
            self._flip_x = self._flip_y = 1.0
            self._display_flip_x = self._display_flip_y = 1.0
            self._swipe_offset = 0.0
            self.emit("zoom-changed", self._zoom)
        self.queue_draw()

    def upgrade_texture(self, texture):
        if texture is None:
            return
        if self._texture is not None and texture.get_width() and texture.get_height():
            current_ratio = self._image_width / max(1, self._image_height)
            new_ratio = texture.get_width() / texture.get_height()
            if abs(current_ratio - new_ratio) > 0.02:
                self._image_width, self._image_height = texture.get_width(), texture.get_height()
        else:
            self._image_width, self._image_height = texture.get_width(), texture.get_height()
        self._texture = texture
        self.queue_draw()

    def play_animation(self, animation):
        self._stop_animation_playback()
        self._animation_iterator = animation.get_iter(None)
        self._show_animation_frame()

    def _show_animation_frame(self):
        iterator = self._animation_iterator
        if iterator is None:
            return GLib.SOURCE_REMOVE
        pixbuf = iterator.get_pixbuf()
        if pixbuf is not None:
            self._texture = Gdk.Texture.new_for_pixbuf(pixbuf)
            self.queue_draw()
        delay = iterator.get_delay_time()
        if delay < 0:
            self._animation_source = 0
            return GLib.SOURCE_REMOVE
        self._animation_source = GLib.timeout_add(max(20, delay), self._advance_animation)
        return GLib.SOURCE_REMOVE

    def _advance_animation(self):
        self._animation_source = 0
        if self._animation_iterator is not None:
            self._animation_iterator.advance(None)
            self._show_animation_frame()
        return GLib.SOURCE_REMOVE

    def _stop_animation_playback(self):
        if self._animation_source:
            GLib.source_remove(self._animation_source)
            self._animation_source = 0
        self._animation_iterator = None

    def clear(self):
        self._stop_animation_playback()
        self._texture = None
        self.queue_draw()

    def _rotated_size(self):
        quarter_turns = (self._rotation // 90) % 2
        if quarter_turns:
            return self._image_height, self._image_width
        return self._image_width, self._image_height

    def fit_scale(self):
        width, height = self.get_width(), self.get_height()
        image_width, image_height = self._rotated_size()
        if not width or not height or not image_width or not image_height:
            return 1.0
        return min(width / image_width, height / image_height, 1.0 if self._is_small_image() else 1e9)

    def _is_small_image(self):
        image_width, image_height = self._rotated_size()
        return image_width < self.get_width() * 0.5 and image_height < self.get_height() * 0.5 and image_width < 600

    def fitted_rect(self):
        width, height = self.get_width(), self.get_height()
        scale = self.fit_scale()
        image_width, image_height = self._rotated_size()
        drawn_width, drawn_height = image_width * scale, image_height * scale
        return Graphene.Rect().init((width - drawn_width) / 2, (height - drawn_height) / 2, drawn_width, drawn_height)

    def actual_size_zoom(self):
        fit = self.fit_scale()
        if not fit:
            return 1.0
        return max(MINIMUM_ZOOM, min(MAXIMUM_ZOOM, (1.0 / max(1, self.get_scale_factor())) / fit))

    def do_snapshot(self, snapshot):
        texture = self._texture
        if texture is None:
            return
        width, height = self.get_width(), self.get_height()
        if self._transition_rect is not None and self._transition_progress < 1.0:
            self._snapshot_transition(snapshot, texture)
            return
        scale = self.fit_scale() * self._zoom
        center_x = width / 2 + self._pan_x + self._swipe_offset
        center_y = height / 2 + self._pan_y
        snapshot.save()
        snapshot.translate(Graphene.Point().init(center_x, center_y))
        if self._display_rotation:
            snapshot.rotate(self._display_rotation)
        snapshot.scale(self._display_flip_x * scale, self._display_flip_y * scale)
        rect = Graphene.Rect().init(-self._image_width / 2, -self._image_height / 2, self._image_width, self._image_height)
        drawn_scale = scale * texture.get_width() / max(1, self._image_width)
        scaling_filter = Gsk.ScalingFilter.NEAREST if drawn_scale > 3.0 else (Gsk.ScalingFilter.TRILINEAR if drawn_scale < 0.75 else Gsk.ScalingFilter.LINEAR)
        snapshot.append_scaled_texture(texture, scaling_filter, rect)
        snapshot.restore()

    def _snapshot_transition(self, snapshot, texture):
        progress = self._transition_progress
        fitted = self.fitted_rect()
        if self._transition_closing:
            from_rect, from_clip, to_rect, to_clip = fitted, fitted, self._transition_rect, self._transition_clip
            radius = _lerp(0.0, 8.0, progress)
        else:
            from_rect, from_clip, to_rect, to_clip = self._transition_rect, self._transition_clip, fitted, fitted
            radius = _lerp(8.0, 0.0, progress)
        image_rect = _lerp_rect(from_rect, to_rect, progress)
        clip = Gsk.RoundedRect()
        clip.init_from_rect(_lerp_rect(from_clip, to_clip, progress), radius)
        snapshot.push_rounded_clip(clip)
        snapshot.append_scaled_texture(texture, Gsk.ScalingFilter.LINEAR, image_rect)
        snapshot.pop()

    def animate_from(self, source_rect):
        if source_rect is None:
            self._transition_rect = None
            self._transition_progress = 1.0
            self.queue_draw()
            self.emit("transition-finished", False)
            return
        self._transition_closing = False
        self._transition_clip = source_rect
        self._transition_rect = self._cover_rect_for(source_rect)
        self._transition_progress = 0.0
        self._transition_animation.set_duration(320)
        self._transition_animation.reset()
        self._transition_animation.play()

    def animate_to(self, target_rect):
        if target_rect is None or self._texture is None:
            self.emit("transition-finished", True)
            return False
        self._zoom = 1.0
        self._pan_x = self._pan_y = 0.0
        self._rotation = 0
        self._display_rotation = 0.0
        self._display_flip_x = self._display_flip_y = 1.0
        self._transition_closing = True
        self._transition_clip = target_rect
        self._transition_rect = self._cover_rect_for(target_rect)
        self._transition_progress = 0.0
        self._transition_animation.set_duration(260)
        self._transition_animation.reset()
        self._transition_animation.play()
        return True

    def _cover_rect_for(self, tile_rect):
        image_width, image_height = self._image_width, self._image_height
        tile_width, tile_height = tile_rect.get_width(), tile_rect.get_height()
        scale = max(tile_width / max(1, image_width), tile_height / max(1, image_height))
        drawn_width, drawn_height = image_width * scale, image_height * scale
        return Graphene.Rect().init(
            tile_rect.get_x() + (tile_width - drawn_width) / 2,
            tile_rect.get_y() + (tile_height - drawn_height) / 2,
            drawn_width,
            drawn_height,
        )

    def _on_transition_tick(self, value):
        self._transition_progress = value
        self.queue_draw()

    def _on_transition_done(self, _animation):
        closing = self._transition_closing
        self._transition_progress = 1.0
        self._transition_rect = None
        self.queue_draw()
        self.emit("transition-finished", closing)

    @property
    def transition_running(self):
        return self._transition_animation.get_state() == Adw.AnimationState.PLAYING

    def _clamp_pan(self):
        scale = self.fit_scale() * self._zoom
        image_width, image_height = self._rotated_size()
        excess_x = max(0.0, (image_width * scale - self.get_width()) / 2)
        excess_y = max(0.0, (image_height * scale - self.get_height()) / 2)
        self._pan_x = max(-excess_x, min(excess_x, self._pan_x))
        self._pan_y = max(-excess_y, min(excess_y, self._pan_y))

    def set_zoom(self, zoom, anchor=None, animate=False):
        zoom = max(MINIMUM_ZOOM, min(MAXIMUM_ZOOM, zoom))
        if anchor is None:
            anchor = (self.get_width() / 2, self.get_height() / 2)
        if animate:
            self._zoom_animation_from = (self._zoom, self._pan_x, self._pan_y)
            target_pan = self._pan_for_zoom(zoom, anchor)
            self._zoom_animation_to = (zoom, target_pan[0], target_pan[1])
            self._zoom_animation.reset()
            self._zoom_animation.play()
            return
        self._pan_x, self._pan_y = self._pan_for_zoom(zoom, anchor)
        self._zoom = zoom
        self._clamp_pan()
        self.queue_draw()
        self.emit("zoom-changed", self._zoom)

    def _pan_for_zoom(self, zoom, anchor):
        ratio = zoom / self._zoom if self._zoom else 1.0
        center_x = self.get_width() / 2
        center_y = self.get_height() / 2
        anchor_x = anchor[0] - center_x
        anchor_y = anchor[1] - center_y
        pan_x = anchor_x - (anchor_x - self._pan_x) * ratio
        pan_y = anchor_y - (anchor_y - self._pan_y) * ratio
        if zoom <= MINIMUM_ZOOM + 0.001:
            return 0.0, 0.0
        previous = (self._zoom, self._pan_x, self._pan_y)
        self._zoom, self._pan_x, self._pan_y = zoom, pan_x, pan_y
        self._clamp_pan()
        result = (self._pan_x, self._pan_y)
        self._zoom, self._pan_x, self._pan_y = previous
        return result

    def _on_zoom_tick(self, value):
        if self._zoom_animation_from is None:
            return
        start, end = self._zoom_animation_from, self._zoom_animation_to
        self._zoom = _lerp(start[0], end[0], value)
        self._pan_x = _lerp(start[1], end[1], value)
        self._pan_y = _lerp(start[2], end[2], value)
        self.queue_draw()
        self.emit("zoom-changed", self._zoom)

    def zoom_in(self):
        self.set_zoom(self._zoom * ZOOM_STEP * ZOOM_STEP, animate=True)

    def zoom_out(self):
        self.set_zoom(self._zoom / (ZOOM_STEP * ZOOM_STEP), animate=True)

    def zoom_fit(self):
        self.set_zoom(1.0, animate=True)

    def zoom_actual(self, anchor=None):
        self.set_zoom(self.actual_size_zoom(), anchor, animate=True)

    def rotate(self, degrees):
        self._rotation = (self._rotation + degrees) % 360
        self._rotation_from = self._display_rotation
        self._rotation_to = self._display_rotation + degrees
        self._flip_from = (self._display_flip_x, self._display_flip_y)
        self._flip_to = (self._display_flip_x, self._display_flip_y)
        self._zoom = 1.0
        self._pan_x = self._pan_y = 0.0
        self._transform_animation.reset()
        self._transform_animation.play()

    def flip(self, horizontal):
        if (self._rotation // 90) % 2:
            horizontal = not horizontal
        self._flip_from = (self._display_flip_x, self._display_flip_y)
        if horizontal:
            self._flip_x = -self._flip_x
        else:
            self._flip_y = -self._flip_y
        self._flip_to = (self._flip_x, self._flip_y)
        self._rotation_from = self._rotation_to = self._display_rotation
        self._transform_animation.reset()
        self._transform_animation.play()

    def _on_transform_tick(self, value):
        self._display_rotation = _lerp(self._rotation_from, self._rotation_to, value)
        self._display_flip_x = _lerp(self._flip_from[0], self._flip_to[0], value)
        self._display_flip_y = _lerp(self._flip_from[1], self._flip_to[1], value)
        if value >= 1.0:
            self._display_rotation = self._rotation_to % 360
            self._rotation_to = self._display_rotation
        self.queue_draw()

    def _on_scroll(self, controller, dx, dy):
        if self._texture is None:
            return False
        unit = controller.get_unit() if hasattr(controller, "get_unit") else Gdk.ScrollUnit.WHEEL
        state = controller.get_current_event_state()
        pointer = self._pointer_position(controller)
        if unit == Gdk.ScrollUnit.WHEEL or state & Gdk.ModifierType.CONTROL_MASK:
            factor = ZOOM_STEP ** (-dy if unit == Gdk.ScrollUnit.WHEEL else -dy * 0.05)
            self.set_zoom(self._zoom * factor, pointer)
            return True
        if self.is_zoomed:
            self._pan_x -= dx
            self._pan_y -= dy
            self._clamp_pan()
            self.queue_draw()
            return True
        return False

    def _pointer_position(self, controller):
        event = controller.get_current_event()
        if event is None:
            return None
        found, x, y = event.get_position()
        if not found:
            return None
        native = self.get_native()
        if native is None:
            return None
        surface_x, surface_y = native.get_surface_transform()
        success, point = native.compute_point(self, Graphene.Point().init(x - surface_x, y - surface_y))
        return (point.x, point.y) if success else None

    def _on_drag_begin(self, _gesture, _x, _y):
        self._drag_start_pan = (self._pan_x, self._pan_y)

    def _on_drag_update(self, gesture, offset_x, offset_y):
        if self.is_zoomed:
            self._pan_x = self._drag_start_pan[0] + offset_x
            self._pan_y = self._drag_start_pan[1] + offset_y
            self._clamp_pan()
        elif abs(offset_x) > 8:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._swipe_offset = offset_x * 0.85
        self.queue_draw()

    def _on_drag_end(self, _gesture, offset_x, offset_y):
        if not self.is_zoomed and abs(self._swipe_offset) > 0:
            threshold = max(60.0, self.get_width() * 0.12)
            direction = 0
            if offset_x < -threshold:
                direction = 1
            elif offset_x > threshold:
                direction = -1
            self._swipe_offset = 0.0
            self.queue_draw()
            if direction:
                self.emit("swipe", direction)

    def _on_click_released(self, gesture, n_press, x, y):
        if gesture.get_current_button() != Gdk.BUTTON_PRIMARY:
            return
        if n_press == 2:
            if self.is_zoomed:
                self.zoom_fit()
            else:
                self.set_zoom(max(2.0, self.actual_size_zoom()), (x, y), animate=True)
        elif n_press == 1:
            GLib.timeout_add(220, self._emit_single_click_if_idle, gesture)

    def _emit_single_click_if_idle(self, gesture):
        if not gesture.is_active():
            self.emit("single-click")
        return GLib.SOURCE_REMOVE

    def _on_pinch_begin(self, gesture, _sequence):
        self._pinch_start_zoom = self._zoom
        found, x, y = gesture.get_bounding_box_center()
        self._pinch_anchor = (x, y) if found else (self.get_width() / 2, self.get_height() / 2)

    def _on_pinch_changed(self, _gesture, scale):
        self.set_zoom(self._pinch_start_zoom * scale, self._pinch_anchor)

    def do_unmap(self):
        self._stop_animation_playback()
        Gtk.Widget.do_unmap(self)
