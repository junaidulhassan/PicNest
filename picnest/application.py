import logging

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from picnest.config import (
    APP_DESCRIPTION, APP_ICONS_DIRECTORY, APP_ID, APP_NAME, AUTHOR_CREDIT, AUTHOR_NAME, STYLESHEET_PATH,
    UI_ICONS_DIRECTORY, VERSION,
)
from picnest.core.library import Library
from picnest.core.settings import Settings
from picnest.ui.context import AppContext
from picnest.ui.dialogs.preferences import PreferencesDialog
from picnest.ui.dialogs.shortcuts import ShortcutsDialog
from picnest.ui.window import MainWindow

logger = logging.getLogger(__name__)

COLOR_SCHEMES = {
    "system": Adw.ColorScheme.DEFAULT,
    "light": Adw.ColorScheme.FORCE_LIGHT,
    "dark": Adw.ColorScheme.FORCE_DARK,
}

ACCELERATORS = {
    "app.quit": ["<Control>q"],
    "app.preferences": ["<Control>comma"],
    "app.shortcuts": ["<Control>question"],
    "app.rescan": ["<Control>r"],
    "win.search": ["<Control>f"],
    "win.toggle-sidebar": ["F9"],
    "win.import": ["<Control>o"],
    "win.section-library": ["<Alt>1"],
    "win.section-favorites": ["<Alt>2"],
    "win.section-albums": ["<Alt>3"],
    "win.section-memories": ["<Alt>4"],
    "win.section-years": ["<Alt>5"],
    "win.section-videos": ["<Alt>6"],
    "win.section-map": ["<Alt>7"],
    "win.section-folders": ["<Alt>8"],
    "win.section-trash": ["<Alt>9"],
    "window.close": ["<Control>w"],
}


class PicnestApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        GLib.set_application_name(APP_NAME)
        self.settings = None
        self.library = None
        self.context = None
        self.window = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        Gtk.Window.set_default_icon_name(APP_ID)
        display = Gdk.Display.get_default()
        icon_theme = Gtk.IconTheme.get_for_display(display)
        for directory in (UI_ICONS_DIRECTORY, APP_ICONS_DIRECTORY / "hicolor", APP_ICONS_DIRECTORY):
            if directory.exists():
                icon_theme.add_search_path(str(directory))
        provider = Gtk.CssProvider()
        if STYLESHEET_PATH.exists():
            provider.load_from_path(str(STYLESHEET_PATH))
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.settings = Settings()
        self._apply_color_scheme(self.settings.get("color-scheme"))
        self.settings.connect_changed("color-scheme", self._apply_color_scheme)
        self.library = Library(self.settings)
        self.context = AppContext(self, self.settings, self.library)
        for name, callback in (
            ("quit", self._quit),
            ("preferences", self._show_preferences),
            ("shortcuts", self._show_shortcuts),
            ("about", self._show_about),
            ("rescan", self._rescan),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _action, _parameter, function=callback: function())
            self.add_action(action)
        for action_name, accelerators in ACCELERATORS.items():
            self.set_accels_for_action(action_name, accelerators)

    def do_activate(self):
        if self.window is None:
            self.window = MainWindow(self, self.context)
        self.window.present()

    def do_open(self, files, _count, _hint):
        self.do_activate()
        self.library.start()
        item_ids = []
        for file in files:
            path = file.get_path()
            if path:
                item_id = self.library.index_path(path)
                if item_id is not None:
                    item_ids.append(item_id)
        if item_ids:
            self.context.open_viewer(item_ids, 0)

    def do_shutdown(self):
        if self.library is not None:
            self.library.shutdown()
        Adw.Application.do_shutdown(self)

    def _apply_color_scheme(self, value):
        Adw.StyleManager.get_default().set_color_scheme(COLOR_SCHEMES.get(value, Adw.ColorScheme.DEFAULT))

    def _quit(self):
        if self.window is not None:
            self.window.close()
        self.quit()

    def _show_preferences(self):
        if self.window is not None:
            PreferencesDialog(self.context).present(self.window)

    def _show_shortcuts(self):
        if self.window is not None:
            ShortcutsDialog().present(self.window)

    def _rescan(self):
        if self.settings.get("first-run-complete"):
            self.library.scan()
            self.context.show_toast("Scanning your library")

    def _show_about(self):
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            developer_name=AUTHOR_NAME,
            version=VERSION,
            comments=f"{APP_DESCRIPTION}\n\n{AUTHOR_CREDIT}",
            license_type=Gtk.License.MIT_X11,
            copyright=f"© 2026 {AUTHOR_NAME}",
            developers=[f"{AUTHOR_NAME}, Software Engineer"],
            designers=[AUTHOR_NAME],
        )
        about.add_credit_section("Icons", ["Phosphor Icons https://phosphoricons.com", "GNOME Adwaita Icons"])
        about.add_acknowledgement_section("Built With", ["GTK https://gtk.org", "libadwaita https://gnome.pages.gitlab.gnome.org/libadwaita/", "GStreamer https://gstreamer.freedesktop.org", "SQLite https://sqlite.org"])
        about.present(self.window)
