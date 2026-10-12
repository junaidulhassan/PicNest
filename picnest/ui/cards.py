from gi.repository import GObject, Gtk

from picnest.ui.widgets import CoverPicture, set_accessible_label


class MediaCard(Gtk.Button):
    __gtype_name__ = "PicnestMediaCard"

    def __init__(self, thumbnails, title, subtitle, cover_item, width=220, height=220, large_title=False):
        super().__init__()
        self.add_css_class("flat")
        self.add_css_class("pn-card")
        overlay = Gtk.Overlay()
        self.cover = CoverPicture(thumbnails, radius=14, size=(width, height))
        self.cover.set_item(cover_item)
        overlay.set_child(self.cover)
        shade = Gtk.Box(valign=Gtk.Align.END, height_request=int(height * 0.55), can_target=False)
        shade.add_css_class("pn-card-shade")
        overlay.add_overlay(shade)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.END, margin_start=14, margin_end=14, margin_bottom=12, can_target=False)
        title_label = Gtk.Label(label=title, xalign=0, ellipsize=3)
        title_label.add_css_class("pn-card-title")
        if large_title:
            title_label.add_css_class("large")
        labels.append(title_label)
        if subtitle:
            subtitle_label = Gtk.Label(label=subtitle, xalign=0, ellipsize=3)
            subtitle_label.add_css_class("pn-card-subtitle")
            labels.append(subtitle_label)
        overlay.add_overlay(labels)
        self.set_child(overlay)
        self.set_tooltip_text(f"{title}\n{subtitle}" if subtitle else title)
        set_accessible_label(self, f"{title}, {subtitle}" if subtitle else title)


class CardGrid(Gtk.FlowBox):
    __gtype_name__ = "PicnestCardGrid"
    __gsignals__ = {
        "card-activated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self):
        super().__init__(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            column_spacing=18,
            row_spacing=18,
            min_children_per_line=1,
            max_children_per_line=12,
            margin_top=18,
            margin_bottom=24,
            margin_start=18,
            margin_end=18,
            valign=Gtk.Align.START,
        )
        self.add_css_class("pn-card-grid")

    def clear(self):
        child = self.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.remove(child)
            child = next_child

    def add_card(self, card, payload):
        card.connect("clicked", lambda _card: self.emit("card-activated", payload))
        self.append(card)
