import weakref

from gi.repository import Adw, Gdk, Graphene, Gsk, Gtk

from picnest.core.thumbnails import decode_placeholder
from picnest.ui import icons


def connect_weak(source, signal_name, method):
    reference = weakref.WeakMethod(method)

    def handler(_source, *arguments):
        target = reference()
        if target is not None:
            return target(_source, *arguments)
        return None

    handler_id = source.connect(signal_name, handler)
    weakref.finalize(method.__self__, _safe_disconnect, source, handler_id)
    return handler_id


def _safe_disconnect(source, handler_id):
    try:
        if source.handler_is_connected(handler_id):
            source.disconnect(handler_id)
    except Exception:
        pass


def unparent_children(widget):
    child = widget.get_first_child()
    while child is not None:
        next_child = child.get_next_sibling()
        child.unparent()
        child = next_child


def set_accessible_label(widget, label):
    widget.update_property([Gtk.AccessibleProperty.LABEL], [label])


def icon_button(icon_name, tooltip, callback=None, action_name=None, css_classes=("flat",)):
    button = Gtk.Button(icon_name=icon_name, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    for css_class in css_classes:
        button.add_css_class(css_class)
    set_accessible_label(button, tooltip)
    if action_name:
        button.set_action_name(action_name)
    if callback is not None:
        button.connect("clicked", lambda _button: callback())
    return button


def icon_toggle(icon_name, tooltip, css_classes=("flat",)):
    button = Gtk.ToggleButton(icon_name=icon_name, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    for css_class in css_classes:
        button.add_css_class(css_class)
    set_accessible_label(button, tooltip)
    return button


def menu_button(icon_name, tooltip, menu_model=None, popover=None):
    button = Gtk.MenuButton(icon_name=icon_name, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    if menu_model is not None:
        button.set_menu_model(menu_model)
    if popover is not None:
        button.set_popover(popover)
    set_accessible_label(button, tooltip)
    return button


def make_spinner(size=16):
    if hasattr(Adw, "Spinner"):
        spinner = Adw.Spinner()
        spinner.set_size_request(size, size)
        return spinner
    spinner = Gtk.Spinner(spinning=True)
    spinner.set_size_request(size, size)
    return spinner


def empty_state(icon_name, title, description="", child=None):
    page = Adw.StatusPage(icon_name=icon_name, title=title, description=description, vexpand=True)
    page.add_css_class("pn-empty-state")
    if child is not None:
        page.set_child(child)
    return page


def pill_button(label, callback, suggested=True):
    button = Gtk.Button(label=label, halign=Gtk.Align.CENTER)
    button.add_css_class("pill")
    if suggested:
        button.add_css_class("suggested-action")
    button.connect("clicked", lambda _button: callback())
    return button


def accent_color(widget):
    style_manager = Adw.StyleManager.get_default()
    if hasattr(style_manager, "get_accent_color_rgba"):
        try:
            return style_manager.get_accent_color_rgba()
        except Exception:
            pass
    color = Gdk.RGBA()
    color.parse("#3584e4")
    return color


def rounded_rect(x, y, width, height, radius):
    rectangle = Graphene.Rect().init(x, y, width, height)
    rounded = Gsk.RoundedRect()
    rounded.init_from_rect(rectangle, radius)
    return rounded


class CoverPicture(Gtk.Widget):
    __gtype_name__ = "PicnestCoverPicture"

    def __init__(self, thumbnails, radius=12, size=None, small=False):
        super().__init__()
        self._thumbnails = thumbnails
        self._radius = radius
        self._small = small
        self._item = None
        self._token = 0
        self._placeholder = None
        self._picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        self._picture.add_css_class("pn-cover-picture")
        self._picture.set_parent(self)
        self._fallback = Gtk.Image(icon_name=icons.IMAGE, pixel_size=32, visible=False)
        self._fallback.add_css_class("dim-label")
        self._fallback.set_parent(self)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.add_css_class("pn-cover")
        if size is not None:
            self.set_size_request(*size)

    def do_dispose(self):
        if getattr(self, "_token", 0):
            self._thumbnails.cancel(self._token)
            self._token = 0
        unparent_children(self)
        Gtk.Widget.do_dispose(self)

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1

    def do_size_allocate(self, width, height, baseline):
        self._picture.allocate(width, height, baseline, None)
        _minimum, natural_width, _baseline_min, _baseline_nat = self._fallback.measure(Gtk.Orientation.HORIZONTAL, -1)
        transform = Gsk.Transform().translate(Graphene.Point().init((width - natural_width) / 2, (height - natural_width) / 2))
        self._fallback.allocate(natural_width, natural_width, -1, transform)

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        snapshot.push_rounded_clip(rounded_rect(0, 0, width, height, self._radius))
        if self._placeholder is not None:
            snapshot.append_scaled_texture(self._placeholder, Gsk.ScalingFilter.LINEAR, cover_rect(self._placeholder, width, height))
        else:
            snapshot.append_color(placeholder_color(self), Graphene.Rect().init(0, 0, width, height))
        self.snapshot_child(self._picture, snapshot)
        self.snapshot_child(self._fallback, snapshot)
        snapshot.pop()

    def clear(self):
        if self._token:
            self._thumbnails.cancel(self._token)
            self._token = 0
        self._item = None
        self._placeholder = None
        self._picture.set_paintable(None)
        self._fallback.set_visible(False)

    def set_item(self, item):
        if item is not None and self._item is not None and item.id == self._item.id and self._picture.get_paintable() is not None:
            return
        self.clear()
        self._item = item
        if item is None:
            self._fallback.set_visible(True)
            self.queue_draw()
            return
        self._placeholder = decode_placeholder(item.placeholder)
        self._fallback.set_from_icon_name(icons.VIDEO if item.is_video else icons.IMAGE)
        self._token = self._thumbnails.request(item, self._on_texture, small=self._small)
        self.queue_draw()

    def _on_texture(self, texture):
        self._token = 0
        self._picture.set_paintable(texture)
        self._fallback.set_visible(texture is None)


def cover_rect(texture, width, height):
    texture_width, texture_height = texture.get_width(), texture.get_height()
    scale = max(width / max(1, texture_width), height / max(1, texture_height))
    drawn_width, drawn_height = texture_width * scale, texture_height * scale
    return Graphene.Rect().init((width - drawn_width) / 2, (height - drawn_height) / 2, drawn_width, drawn_height)


def placeholder_color(widget):
    color = widget.get_color()
    color.alpha = 0.08
    return color
