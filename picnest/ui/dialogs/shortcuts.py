from gi.repository import Adw, GLib, Gtk

SHORTCUT_GROUPS = (
    (
        "General",
        (
            ("<Control>f", "Search"),
            ("<Control>comma", "Preferences"),
            ("<Control>r", "Rescan library"),
            ("<Control>question", "Keyboard shortcuts"),
            ("<Control>w", "Close window"),
            ("<Control>q", "Quit"),
            ("F9", "Toggle sidebar"),
            ("<Alt>1", "Library"),
            ("<Alt>2", "Favorites"),
            ("<Alt>3", "Albums"),
        ),
    ),
    (
        "Library Grid",
        (
            ("<Control>plus", "Larger thumbnails"),
            ("<Control>minus", "Smaller thumbnails"),
            ("<Control>a", "Select all"),
            ("<Control><Shift>a", "Deselect all"),
            ("space", "Select the focused item"),
            ("Return", "Open the focused item"),
            ("Escape", "Leave selection mode"),
            ("<Control>d", "Toggle favorite"),
            ("<Control>l", "Add to album"),
            ("<Control>h", "Hide"),
            ("Delete", "Move to trash"),
            ("<Shift>Delete", "Delete permanently"),
            ("F2", "Rename"),
            ("<Control>e", "Edit"),
            ("<Control>i", "Show info"),
            ("<Control>c", "Copy to clipboard"),
            ("<Control><Shift>e", "Export"),
            ("F5", "Slideshow"),
        ),
    ),
    (
        "Viewer",
        (
            ("Left", "Previous item"),
            ("Right", "Next item"),
            ("Escape", "Back to library"),
            ("F11", "Fullscreen"),
            ("plus", "Zoom in"),
            ("minus", "Zoom out"),
            ("0", "Fit to window"),
            ("1", "Actual size"),
            ("r", "Rotate right"),
            ("<Shift>r", "Rotate left"),
            ("h", "Flip horizontally"),
            ("v", "Flip vertically"),
            ("i", "Show info"),
            ("e", "Edit"),
            ("period", "Toggle favorite"),
            ("F5", "Slideshow"),
        ),
    ),
    (
        "Video",
        (
            ("space", "Play or pause"),
            ("<Shift>Left", "Back 5 seconds"),
            ("<Shift>Right", "Forward 5 seconds"),
            ("Up", "Volume up"),
            ("Down", "Volume down"),
            ("m", "Mute"),
        ),
    ),
)


class ShortcutsDialog(Adw.PreferencesDialog):
    __gtype_name__ = "PicnestShortcutsDialog"

    def __init__(self):
        super().__init__(title="Keyboard Shortcuts", search_enabled=True)
        page = Adw.PreferencesPage()
        for title, shortcuts in SHORTCUT_GROUPS:
            group = Adw.PreferencesGroup(title=title)
            for accelerator, description in shortcuts:
                row = Adw.ActionRow(title=GLib.markup_escape_text(description))
                label = Gtk.ShortcutLabel(accelerator=accelerator, valign=Gtk.Align.CENTER)
                row.add_suffix(label)
                group.add(row)
            page.add(group)
        self.add(page)
