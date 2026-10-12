from gi.repository import Gio, GObject, Gtk

from picnest.ui.widgets import CoverPicture

THUMBNAIL_SIZE = 56
CACHE_LIMIT = 4000


class FilmstripEntry(GObject.Object):
    __gtype_name__ = "PicnestFilmstripEntry"

    def __init__(self, item_id):
        super().__init__()
        self.item_id = item_id


class IdListModel(GObject.Object, Gio.ListModel):
    __gtype_name__ = "PicnestIdListModel"

    def __init__(self):
        super().__init__()
        self._ids = []
        self._entries = {}

    def do_get_item_type(self):
        return FilmstripEntry.__gtype__

    def do_get_n_items(self):
        return len(self._ids)

    def do_get_item(self, position):
        if position >= len(self._ids):
            return None
        entry = self._entries.get(position)
        if entry is None:
            if len(self._entries) > CACHE_LIMIT:
                self._entries.clear()
            entry = FilmstripEntry(self._ids[position])
            self._entries[position] = entry
        return entry

    def set_ids(self, ids):
        removed = len(self._ids)
        self._ids = list(ids)
        self._entries.clear()
        self.items_changed(0, removed, len(self._ids))


class Filmstrip(Gtk.ScrolledWindow):
    __gtype_name__ = "PicnestFilmstrip"
    __gsignals__ = {
        "item-chosen": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, library):
        super().__init__(vscrollbar_policy=Gtk.PolicyType.NEVER, hscrollbar_policy=Gtk.PolicyType.EXTERNAL, propagate_natural_height=True)
        self._library = library
        self._model = IdListModel()
        self._selection = Gtk.SingleSelection(model=self._model, autoselect=False, can_unselect=False)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)
        factory.connect("unbind", self._on_unbind)
        self._list_view = Gtk.ListView(model=self._selection, factory=factory, orientation=Gtk.Orientation.HORIZONTAL, single_click_activate=True)
        self._list_view.add_css_class("pn-filmstrip")
        self._list_view.update_property([Gtk.AccessibleProperty.LABEL], ["Filmstrip"])
        self._list_view.connect("activate", self._on_activate)
        self.set_child(self._list_view)
        self.add_css_class("pn-filmstrip-scroller")

    def _on_setup(self, _factory, list_item):
        cover = CoverPicture(self._library.thumbnails, radius=6, size=(THUMBNAIL_SIZE, THUMBNAIL_SIZE), small=True)
        cover.add_css_class("pn-filmstrip-thumb")
        list_item.set_child(cover)

    def _on_bind(self, _factory, list_item):
        entry = list_item.get_item()
        cover = list_item.get_child()
        item = self._library.item(entry.item_id)
        cover.set_item(item)
        if item is not None:
            cover.update_property([Gtk.AccessibleProperty.LABEL], [item.name])

    def _on_unbind(self, _factory, list_item):
        list_item.get_child().clear()

    def _on_activate(self, _list_view, position):
        self.emit("item-chosen", position)

    def set_ids(self, ids):
        self._model.set_ids(ids)

    def select(self, index):
        if index < 0 or index >= self._model.get_n_items():
            return
        self._selection.set_selected(index)
        self._list_view.scroll_to(index, Gtk.ListScrollFlags.NONE, None)
