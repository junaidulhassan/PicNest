from gi.repository import Adw, GObject, Gtk

from picnest.config import APP_DESCRIPTION, APP_ICONS_DIRECTORY, AUTHOR_CREDIT
from picnest.ui import icons


class WelcomeView(Adw.Bin):
    __gtype_name__ = "PicnestWelcomeView"
    __gsignals__ = {
        "start-requested": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self):
        super().__init__()
        self.add_css_class("pn-welcome")
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_title=False)
        header.add_css_class("flat")
        toolbar.add_top_bar(header)
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        clamp = Adw.Clamp(maximum_size=560, margin_top=24, margin_bottom=36, margin_start=24, margin_end=24, valign=Gtk.Align.CENTER)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=28)
        self._wordmark = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, height_request=96, halign=Gtk.Align.CENTER)
        self._wordmark.add_css_class("pn-wordmark")
        self._wordmark.update_property([Gtk.AccessibleProperty.LABEL], ["Picnest"])
        box.append(self._wordmark)
        tagline = Gtk.Label(label=APP_DESCRIPTION, wrap=True, justify=Gtk.Justification.CENTER)
        tagline.add_css_class("title-3")
        box.append(tagline)
        features = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        features.add_css_class("boxed-list")
        for icon_name, title, subtitle in (
            (icons.DRIVE, "Finds Everything", "Every photo and video on your internal and external drives, in one place."),
            (icons.GRID, "Fast at Any Size", "Smooth scrolling and instant search, even with hundreds of thousands of items."),
            (icons.ALBUMS, "Beautifully Organized", "Years, months and days, albums, favorites, memories and smart collections."),
            (icons.LOCK, "Private by Design", "Everything is processed on this computer. Nothing is uploaded."),
        ):
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            image = Gtk.Image(icon_name=icon_name, pixel_size=24)
            image.add_css_class("accent")
            row.add_prefix(image)
            features.append(row)
        box.append(features)
        start = Gtk.Button(label="Start Scanning", halign=Gtk.Align.CENTER)
        start.add_css_class("pill")
        start.add_css_class("suggested-action")
        start.connect("clicked", lambda _button: self.emit("start-requested", True))
        box.append(start)
        choose = Gtk.Button(label="Only Scan My Pictures and Videos Folders", halign=Gtk.Align.CENTER)
        choose.add_css_class("flat")
        choose.connect("clicked", lambda _button: self.emit("start-requested", False))
        box.append(choose)
        credit = Gtk.Label(label=AUTHOR_CREDIT)
        credit.add_css_class("dim-label")
        credit.add_css_class("caption")
        box.append(credit)
        clamp.set_child(box)
        scrolled.set_child(clamp)
        toolbar.set_content(scrolled)
        self.set_child(toolbar)
        self._start_button = start
        style_manager = Adw.StyleManager.get_default()
        style_manager.connect("notify::dark", lambda *_args: self._update_wordmark())
        self._update_wordmark()

    def _update_wordmark(self):
        dark = Adw.StyleManager.get_default().get_dark()
        path = APP_ICONS_DIRECTORY / ("picnest-wordmark-dark.svg" if dark else "picnest-wordmark.svg")
        self._wordmark.set_filename(str(path))

    def focus_start(self):
        self._start_button.grab_focus()
