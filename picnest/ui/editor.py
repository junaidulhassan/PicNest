import threading

from gi.repository import Adw, Gdk, GLib, GObject, Graphene, Gsk, Gtk
from PIL import ImageOps

from picnest.core import editing
from picnest.core.image_loader import load_pillow_image, load_pixbuf, pixbuf_to_pillow
from picnest.core.media_types import is_raw, is_vector
from picnest.ui import icons
from picnest.ui.dialogs.common import confirm, show_error
from picnest.ui.widgets import accent_color, icon_button, make_spinner, set_accessible_label

PREVIEW_DIMENSION = 1600
FILTER_THUMBNAIL_DIMENSION = 120
CANVAS_MARGIN = 28
HANDLE_SIZE = 18
ASPECT_RATIOS = (("Free", None), ("Original", "original"), ("Square", 1.0), ("4:3", 4 / 3), ("3:2", 3 / 2), ("16:9", 16 / 9), ("3:4", 3 / 4), ("9:16", 9 / 16))
ADJUSTMENTS = (("brightness", "Brightness", icons.BRIGHTNESS), ("contrast", "Contrast", icons.CONTRAST), ("saturation", "Saturation", icons.SATURATION), ("warmth", "Warmth", icons.WARMTH))


def pillow_to_texture(image):
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    width, height = image.size
    memory_format = Gdk.MemoryFormat.R8G8B8A8 if image.mode == "RGBA" else Gdk.MemoryFormat.R8G8B8
    stride = width * (4 if image.mode == "RGBA" else 3)
    return Gdk.MemoryTexture.new(width, height, memory_format, GLib.Bytes.new(image.tobytes()), stride)


class EditorCanvas(Gtk.Widget):
    __gtype_name__ = "PicnestEditorCanvas"
    __gsignals__ = {
        "crop-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True)
        self.add_css_class("pn-editor-canvas")
        self._texture = None
        self.crop_mode = False
        self.crop = [0.0, 0.0, 1.0, 1.0]
        self.aspect = None
        self._drag_mode = None
        self._drag_origin_crop = None
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", lambda *_args: setattr(self, "_drag_mode", None))
        self.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1

    def set_texture(self, texture):
        self._texture = texture
        self.queue_draw()

    def image_rect(self):
        if self._texture is None:
            return None
        width = max(1, self.get_width() - 2 * CANVAS_MARGIN)
        height = max(1, self.get_height() - 2 * CANVAS_MARGIN)
        texture_width, texture_height = self._texture.get_width(), self._texture.get_height()
        scale = min(width / texture_width, height / texture_height)
        drawn_width, drawn_height = texture_width * scale, texture_height * scale
        return Graphene.Rect().init((self.get_width() - drawn_width) / 2, (self.get_height() - drawn_height) / 2, drawn_width, drawn_height)

    def crop_rect(self):
        rect = self.image_rect()
        if rect is None:
            return None
        left, top, right, bottom = self.crop
        return Graphene.Rect().init(
            rect.get_x() + left * rect.get_width(),
            rect.get_y() + top * rect.get_height(),
            (right - left) * rect.get_width(),
            (bottom - top) * rect.get_height(),
        )

    def do_snapshot(self, snapshot):
        rect = self.image_rect()
        if rect is None:
            return
        snapshot.append_scaled_texture(self._texture, Gsk.ScalingFilter.TRILINEAR, rect)
        if not self.crop_mode:
            return
        crop = self.crop_rect()
        shade = Gdk.RGBA()
        shade.parse("rgba(0,0,0,0.55)")
        x, y, width, height = rect.get_x(), rect.get_y(), rect.get_width(), rect.get_height()
        cx, cy, cw, ch = crop.get_x(), crop.get_y(), crop.get_width(), crop.get_height()
        for shade_rect in (
            (x, y, width, cy - y),
            (x, cy + ch, width, y + height - cy - ch),
            (x, cy, cx - x, ch),
            (cx + cw, cy, x + width - cx - cw, ch),
        ):
            if shade_rect[2] > 0 and shade_rect[3] > 0:
                snapshot.append_color(shade, Graphene.Rect().init(*shade_rect))
        white = Gdk.RGBA()
        white.parse("rgba(255,255,255,0.9)")
        guide = Gdk.RGBA()
        guide.parse("rgba(255,255,255,0.35)")
        for fraction in (1 / 3, 2 / 3):
            snapshot.append_color(guide, Graphene.Rect().init(cx + cw * fraction, cy, 1, ch))
            snapshot.append_color(guide, Graphene.Rect().init(cx, cy + ch * fraction, cw, 1))
        border = Gsk.RoundedRect()
        border.init_from_rect(crop, 0)
        snapshot.append_border(border, [1.5] * 4, [white] * 4)
        handle_color = accent_color(self)
        for hx, hy in ((cx, cy), (cx + cw, cy), (cx, cy + ch), (cx + cw, cy + ch)):
            handle = Gsk.RoundedRect()
            handle.init_from_rect(Graphene.Rect().init(hx - 7, hy - 7, 14, 14), 7)
            snapshot.push_rounded_clip(handle)
            snapshot.append_color(white, Graphene.Rect().init(hx - 7, hy - 7, 14, 14))
            snapshot.pop()
            snapshot.append_border(handle, [2.0] * 4, [handle_color] * 4)

    def _hit_mode(self, x, y):
        crop = self.crop_rect()
        if crop is None:
            return None
        cx, cy, cw, ch = crop.get_x(), crop.get_y(), crop.get_width(), crop.get_height()
        corners = {"top-left": (cx, cy), "top-right": (cx + cw, cy), "bottom-left": (cx, cy + ch), "bottom-right": (cx + cw, cy + ch)}
        for name, (hx, hy) in corners.items():
            if abs(x - hx) <= HANDLE_SIZE and abs(y - hy) <= HANDLE_SIZE:
                return name
        if cx <= x <= cx + cw and cy <= y <= cy + ch:
            return "move"
        return None

    def _on_motion(self, _controller, x, y):
        if not self.crop_mode:
            self.set_cursor(None)
            return
        mode = self._hit_mode(x, y)
        names = {"top-left": "nw-resize", "bottom-right": "se-resize", "top-right": "ne-resize", "bottom-left": "sw-resize", "move": "move"}
        self.set_cursor(Gdk.Cursor.new_from_name(names[mode], None) if mode else None)

    def _on_drag_begin(self, gesture, x, y):
        if not self.crop_mode:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        self._drag_mode = self._hit_mode(x, y)
        self._drag_origin_crop = list(self.crop)

    def _on_drag_update(self, _gesture, offset_x, offset_y):
        if self._drag_mode is None:
            return
        rect = self.image_rect()
        if rect is None:
            return
        dx = offset_x / rect.get_width()
        dy = offset_y / rect.get_height()
        left, top, right, bottom = self._drag_origin_crop
        minimum = 0.04
        if self._drag_mode == "move":
            width, height = right - left, bottom - top
            left = max(0.0, min(1.0 - width, left + dx))
            top = max(0.0, min(1.0 - height, top + dy))
            self.crop = [left, top, left + width, top + height]
        else:
            if "left" in self._drag_mode:
                left = max(0.0, min(right - minimum, left + dx))
            else:
                right = min(1.0, max(left + minimum, right + dx))
            if "top" in self._drag_mode:
                top = max(0.0, min(bottom - minimum, top + dy))
            else:
                bottom = min(1.0, max(top + minimum, bottom + dy))
            self.crop = self._constrain([left, top, right, bottom], self._drag_mode)
        self.queue_draw()
        self.emit("crop-changed")

    def _constrain(self, crop, anchor_mode):
        ratio = self._target_ratio()
        if ratio is None:
            return crop
        rect = self.image_rect()
        image_ratio = rect.get_width() / rect.get_height()
        left, top, right, bottom = crop
        width = right - left
        height = width * image_ratio / ratio
        if "top" in anchor_mode:
            top = bottom - height
            if top < 0:
                top = 0.0
                height = bottom
                width = height * ratio / image_ratio
        else:
            bottom = top + height
            if bottom > 1:
                bottom = 1.0
                height = bottom - top
                width = height * ratio / image_ratio
        if "left" in anchor_mode:
            left = right - width
        else:
            right = left + width
        return [max(0.0, left), max(0.0, top), min(1.0, right), min(1.0, bottom)]

    def _target_ratio(self):
        if self.aspect is None or self._texture is None:
            return None
        return self.aspect

    def apply_aspect(self, ratio):
        self.aspect = ratio
        if ratio is None or self._texture is None:
            self.queue_draw()
            return
        image_ratio = self._texture.get_width() / self._texture.get_height()
        if ratio > image_ratio:
            width, height = 1.0, image_ratio / ratio
        else:
            width, height = ratio / image_ratio, 1.0
        left = (1.0 - width) / 2
        top = (1.0 - height) / 2
        self.crop = [left, top, left + width, top + height]
        self.queue_draw()
        self.emit("crop-changed")


class EditorView(Adw.Bin):
    __gtype_name__ = "PicnestEditorView"
    __gsignals__ = {
        "closed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, context):
        super().__init__(visible=False)
        self.add_css_class("pn-editor")
        self._context = context
        self._item = None
        self._source = None
        self._preview = None
        self._exif = None
        self._state = editing.EditState()
        self._render_generation = 0
        self._render_source = 0
        self._busy = False
        self._filter_buttons = {}
        self._scales = {}
        self._build()

    def _build(self):
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label="_Cancel", use_underline=True)
        cancel.connect("clicked", lambda _button: self.request_close())
        header.pack_start(cancel)
        self._title = Adw.WindowTitle(title="Edit")
        header.set_title_widget(self._title)
        save_menu = Adw.SplitButton(label="Save Copy", tooltip_text="Save as a new file next to the original")
        save_menu.add_css_class("suggested-action")
        save_menu.connect("clicked", lambda _button: self._save(replace=False))
        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        replace_button = Gtk.Button(label="Replace Original…")
        replace_button.add_css_class("flat")
        replace_button.connect("clicked", lambda _button: (save_menu.get_popover().popdown(), self._save(replace=True)))
        popover_box.append(replace_button)
        save_menu.set_popover(Gtk.Popover(child=popover_box))
        self._save_button = save_menu
        header.pack_end(save_menu)
        self._reset_button = icon_button(icons.RESET, "Reset All Changes", self._reset)
        header.pack_end(self._reset_button)
        toolbar.add_top_bar(header)
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        canvas_overlay = Gtk.Overlay(hexpand=True, vexpand=True)
        self._canvas = EditorCanvas()
        self._canvas.connect("crop-changed", self._on_crop_changed)
        canvas_overlay.set_child(self._canvas)
        self._spinner = make_spinner(32)
        self._spinner.set_halign(Gtk.Align.CENTER)
        self._spinner.set_valign(Gtk.Align.CENTER)
        self._spinner.set_can_target(False)
        canvas_overlay.add_overlay(self._spinner)
        body.append(canvas_overlay)
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=340, hexpand=False)
        panel.add_css_class("pn-editor-panel")
        self._view_stack = Adw.ViewStack(vexpand=True)
        self._view_stack.add_titled_with_icon(self._build_adjust_page(), "adjust", "Adjust", icons.ADJUST)
        self._view_stack.add_titled_with_icon(self._build_filters_page(), "filters", "Filters", icons.FILTERS)
        self._view_stack.add_titled_with_icon(self._build_crop_page(), "crop", "Crop", icons.CROP)
        self._view_stack.connect("notify::visible-child-name", self._on_mode_changed)
        switcher = Adw.ViewSwitcher(stack=self._view_stack, policy=Adw.ViewSwitcherPolicy.WIDE, margin_top=12, margin_bottom=6, margin_start=12, margin_end=12)
        panel.append(switcher)
        panel.append(self._view_stack)
        body.append(panel)
        toolbar.set_content(body)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)
        self.set_child(toolbar)

    def _build_adjust_page(self):
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        for key, label, icon_name in ADJUSTMENTS:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
            title_box = Gtk.Box(spacing=8)
            title_box.append(Gtk.Image(icon_name=icon_name))
            title_label = Gtk.Label(label=label, xalign=0, hexpand=True)
            title_box.append(title_label)
            value_label = Gtk.Label(label="0", css_classes=["dim-label", "numeric"])
            title_box.append(value_label)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -100, 100, 1)
            scale.set_value(0)
            scale.set_draw_value(False)
            scale.add_mark(0, Gtk.PositionType.BOTTOM, None)
            set_accessible_label(scale, label)
            scale.connect("value-changed", self._on_adjustment_changed, key, value_label)
            row.append(title_box)
            row.append(scale)
            group.add(row)
            self._scales[key] = scale
        page.add(group)
        reset_group = Adw.PreferencesGroup()
        reset = Gtk.Button(label="Reset Adjustments", halign=Gtk.Align.CENTER)
        reset.add_css_class("pill")
        reset.connect("clicked", lambda _button: self._reset_adjustments())
        reset_group.add(reset)
        page.add(reset_group)
        return page

    def _build_filters_page(self):
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3, min_children_per_line=2, column_spacing=8, row_spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12, homogeneous=True)
        for name in editing.FILTER_NAMES:
            button = Gtk.Button()
            button.add_css_class("flat")
            button.add_css_class("pn-filter-tile")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, width_request=84, height_request=84)
            picture.add_css_class("pn-filter-preview")
            box.append(picture)
            box.append(Gtk.Label(label=name))
            button.set_child(box)
            button.connect("clicked", lambda _button, filter_name=name: self._choose_filter(filter_name))
            set_accessible_label(button, f"{name} filter")
            flow.append(button)
            self._filter_buttons[name] = (button, picture)
        scrolled.set_child(flow)
        return scrolled

    def _build_crop_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        aspect_label = Gtk.Label(label="Aspect Ratio", xalign=0)
        aspect_label.add_css_class("heading")
        box.append(aspect_label)
        aspect_flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4, min_children_per_line=2, column_spacing=6, row_spacing=6, homogeneous=True)
        group_leader = None
        for label, ratio in ASPECT_RATIOS:
            button = Gtk.ToggleButton(label=label, active=ratio is None)
            if group_leader is None:
                group_leader = button
            else:
                button.set_group(group_leader)
            button.connect("toggled", self._on_aspect_toggled, ratio)
            aspect_flow.append(button)
        box.append(aspect_flow)
        transform_label = Gtk.Label(label="Rotate and Flip", xalign=0)
        transform_label.add_css_class("heading")
        box.append(transform_label)
        transform_box = Gtk.Box(spacing=6, homogeneous=True)
        transform_box.append(icon_button(icons.ROTATE_LEFT, "Rotate Left", lambda: self._rotate(-90), css_classes=()))
        transform_box.append(icon_button(icons.ROTATE_RIGHT, "Rotate Right", lambda: self._rotate(90), css_classes=()))
        transform_box.append(icon_button(icons.FLIP_HORIZONTAL, "Flip Horizontally", lambda: self._flip(True), css_classes=()))
        transform_box.append(icon_button(icons.FLIP_VERTICAL, "Flip Vertically", lambda: self._flip(False), css_classes=()))
        box.append(transform_box)
        reset = Gtk.Button(label="Reset Crop", halign=Gtk.Align.CENTER)
        reset.add_css_class("pill")
        reset.connect("clicked", lambda _button: self._reset_crop())
        box.append(reset)
        return box

    def open(self, item):
        self._item = item
        self._state = editing.EditState()
        self._source = None
        self._preview = None
        self._exif = None
        self._title.set_title("Edit")
        self._title.set_subtitle(item.name)
        self._canvas.set_texture(None)
        self._canvas.crop = [0.0, 0.0, 1.0, 1.0]
        self._canvas.aspect = None
        self._view_stack.set_visible_child_name("adjust")
        for scale in self._scales.values():
            scale.set_value(0)
        self._save_button.set_sensitive(False)
        self._spinner.set_visible(True)
        self.set_visible(True)
        self.grab_focus()
        path = self._context.library.file_path_for(item)
        extension = item.extension

        def load():
            if is_raw(extension) or is_vector(extension) or extension in ("heic", "heif", "hif", "avif", "jxl"):
                image = pixbuf_to_pillow(load_pixbuf(path, extension, 8192))
                exif = None
            else:
                image = load_pillow_image(path)
                exif = editing.normalized_exif(image) if image is not None else None
                if image is not None:
                    image = ImageOps.exif_transpose(image)
            if image is None:
                return None
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA" if "A" in image.getbands() or image.mode == "P" else "RGB")
            preview = editing.prepare_preview(image, PREVIEW_DIMENSION)
            thumbnail = editing.prepare_preview(preview, FILTER_THUMBNAIL_DIMENSION)
            filter_previews = {name: editing.apply_filter(thumbnail, name) for name in editing.FILTER_NAMES}
            return image, preview, exif, filter_previews

        def loaded(result):
            self._spinner.set_visible(False)
            if item is not self._item:
                return GLib.SOURCE_REMOVE
            if result is None:
                show_error(self.get_root(), "Cannot Edit This File", [("", "The image could not be opened for editing.")])
                self._close()
                return GLib.SOURCE_REMOVE
            self._source, self._preview, self._exif, filter_previews = result
            for name, image in filter_previews.items():
                self._filter_buttons[name][1].set_paintable(pillow_to_texture(image))
            self._update_filter_selection()
            self._save_button.set_sensitive(True)
            self._schedule_render(immediate=True)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=lambda: GLib.idle_add(loaded, load()), name="picnest-editor-load", daemon=True).start()

    def _on_mode_changed(self, stack, _param):
        crop_mode = stack.get_visible_child_name() == "crop"
        if crop_mode != self._canvas.crop_mode:
            self._canvas.crop_mode = crop_mode
            if not crop_mode:
                self._state.crop = tuple(self._canvas.crop)
            self._schedule_render(immediate=True)

    def _on_adjustment_changed(self, scale, key, value_label):
        value = round(scale.get_value())
        value_label.set_label(f"{value:+d}" if value else "0")
        setattr(self._state, key, float(value))
        self._schedule_render()

    def _on_crop_changed(self, canvas):
        self._state.crop = tuple(canvas.crop)

    def _on_aspect_toggled(self, button, ratio):
        if not button.get_active():
            return
        if ratio == "original" and self._preview is not None:
            width, height = self._source.size
            if (self._state.rotation // 90) % 2:
                width, height = height, width
            ratio = width / height
        self._canvas.apply_aspect(ratio)
        self._state.crop = tuple(self._canvas.crop)

    def _choose_filter(self, name):
        self._state.filter_name = name
        self._update_filter_selection()
        self._schedule_render(immediate=True)

    def _update_filter_selection(self):
        for name, (button, _picture) in self._filter_buttons.items():
            if name == self._state.filter_name:
                button.add_css_class("selected")
            else:
                button.remove_css_class("selected")

    def _rotate(self, degrees):
        self._state.rotation = (self._state.rotation + degrees) % 360
        self._state.crop = (0.0, 0.0, 1.0, 1.0)
        self._canvas.crop = [0.0, 0.0, 1.0, 1.0]
        self._schedule_render(immediate=True)

    def _flip(self, horizontal):
        if horizontal:
            self._state.flip_horizontal = not self._state.flip_horizontal
        else:
            self._state.flip_vertical = not self._state.flip_vertical
        left, top, right, bottom = self._state.crop
        if horizontal:
            self._state.crop = (1.0 - right, top, 1.0 - left, bottom)
        else:
            self._state.crop = (left, 1.0 - bottom, right, 1.0 - top)
        self._canvas.crop = list(self._state.crop)
        self._schedule_render(immediate=True)

    def _reset_crop(self):
        self._state.crop = (0.0, 0.0, 1.0, 1.0)
        self._state.rotation = 0
        self._state.flip_horizontal = self._state.flip_vertical = False
        self._canvas.crop = [0.0, 0.0, 1.0, 1.0]
        self._schedule_render(immediate=True)

    def _reset_adjustments(self):
        for scale in self._scales.values():
            scale.set_value(0)

    def _reset(self):
        self._reset_adjustments()
        self._state.filter_name = "Original"
        self._update_filter_selection()
        self._reset_crop()

    def _schedule_render(self, immediate=False):
        if self._preview is None:
            return
        if self._render_source:
            GLib.source_remove(self._render_source)
            self._render_source = 0
        if immediate:
            self._render()
        else:
            self._render_source = GLib.timeout_add(40, self._render)

    def _render(self):
        self._render_source = 0
        self._render_generation += 1
        generation = self._render_generation
        state = self._state.copy()
        preview = self._preview
        include_crop = not self._canvas.crop_mode

        def work():
            return pillow_to_texture(editing.render(preview, state, include_crop=include_crop))

        def done(texture):
            if generation == self._render_generation:
                self._canvas.set_texture(texture)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=lambda: GLib.idle_add(done, work()), name="picnest-editor-render", daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _save(self, replace):
        if self._source is None or self._busy:
            return
        if self._canvas.crop_mode:
            self._state.crop = tuple(self._canvas.crop)
        if self._state.is_identity():
            self._close()
            return
        if replace:
            confirm(self.get_root(), "Replace Original?", "The original file will be overwritten with your edits. This cannot be undone.", "_Replace", lambda: self._perform_save(True))
        else:
            self._perform_save(False)

    def _perform_save(self, replace):
        self._busy = True
        self._spinner.set_visible(True)
        self._save_button.set_sensitive(False)
        item = self._item
        state = self._state.copy()
        source = self._source
        exif = self._exif
        path = self._context.library.file_path_for(item)

        def work():
            try:
                return editing.save_result(source, state, path, item.extension, replace, exif), None
            except Exception as error:
                return None, str(error)

        def done(result):
            destination, error = result
            self._busy = False
            self._spinner.set_visible(False)
            self._save_button.set_sensitive(True)
            if error is not None:
                show_error(self.get_root(), "Could Not Save", [("", error)])
                return GLib.SOURCE_REMOVE
            self._context.library.index_path(destination)
            self._context.show_toast("Edits saved" if destination == path else "Saved a copy of your edits")
            self._close()
            return GLib.SOURCE_REMOVE

        threading.Thread(target=lambda: GLib.idle_add(done, work()), name="picnest-editor-save", daemon=True).start()

    def request_close(self):
        if self._source is not None and not self._state.is_identity():
            confirm(self.get_root(), "Discard Edits?", "Your changes to this photo will be lost.", "_Discard", self._close)
        else:
            self._close()

    def _close(self):
        self.set_visible(False)
        self._source = None
        self._preview = None
        self._canvas.set_texture(None)
        self.emit("closed")

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.request_close()
            return True
        if keyval == Gdk.KEY_s and state & Gdk.ModifierType.CONTROL_MASK:
            self._save(replace=False)
            return True
        return False
