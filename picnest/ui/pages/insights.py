from gi.repository import Adw, GLib, Graphene, Gtk

from picnest.core import formatting
from picnest.ui import icons
from picnest.ui.pages.browse import BrowsePage
from picnest.ui.widgets import accent_color, icon_button, rounded_rect

BAR_HEIGHT = 8
SEGMENT_GAP = 2


class UsageBar(Gtk.Widget):
    __gtype_name__ = "PicnestUsageBar"

    def __init__(self, segments, total):
        super().__init__(hexpand=True, valign=Gtk.Align.CENTER, height_request=BAR_HEIGHT)
        self._segments = segments
        self._total = max(1, total)
        self.add_css_class("pn-usage-bar")

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            return BAR_HEIGHT, BAR_HEIGHT, -1, -1
        return 60, 200, -1, -1

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        track = self.get_color()
        track.alpha = 0.08
        snapshot.push_rounded_clip(rounded_rect(0, 0, width, height, height / 2))
        snapshot.append_color(track, Graphene.Rect().init(0, 0, width, height))
        x = 0.0
        for index, (value, kind) in enumerate(self._segments):
            segment_width = width * value / self._total
            if segment_width <= 0.5:
                continue
            color = accent_color(self) if kind == "primary" else self.get_color()
            if kind != "primary":
                color.alpha = 0.32
            drawn = max(1.0, segment_width - (SEGMENT_GAP if index < len(self._segments) - 1 else 0))
            snapshot.append_color(color, Graphene.Rect().init(x, 0, drawn, height))
            x += segment_width
        snapshot.pop()


def _bar_row(title, value_text, segments, total, tooltip):
    row = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
    title_label = Gtk.Label(label=title, xalign=0, width_chars=12, max_width_chars=18, ellipsize=3)
    row.append(title_label)
    row.append(UsageBar(segments, total))
    value_label = Gtk.Label(label=value_text, xalign=1, width_chars=16)
    value_label.add_css_class("numeric")
    value_label.add_css_class("dim-label")
    row.append(value_label)
    row.set_tooltip_text(tooltip)
    row.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    return row


class LegendSwatch(UsageBar):
    __gtype_name__ = "PicnestLegendSwatch"

    def __init__(self, kind):
        super().__init__([(1, kind)], 1)
        self.set_hexpand(False)

    def do_measure(self, orientation, for_size):
        size = BAR_HEIGHT if orientation == Gtk.Orientation.VERTICAL else 16
        return size, size, -1, -1


def _legend():
    box = Gtk.Box(spacing=16, halign=Gtk.Align.START, margin_start=12, margin_bottom=4)
    for label, primary in (("Photos and videos", True), ("Other files", False)):
        swatch = LegendSwatch("primary" if primary else "other")
        item = Gtk.Box(spacing=6)
        item.append(swatch)
        text = Gtk.Label(label=label)
        text.add_css_class("caption")
        item.append(text)
        box.append(item)
    return box


class InsightsPage(BrowsePage):
    __gtype_name__ = "PicnestInsightsPage"

    def __init__(self, context):
        super().__init__(context, "Storage", "insights", icons.INSIGHTS, "Nothing to Measure Yet", "Storage insights appear once your library has photos and videos.")
        self.header.pack_end(icon_button(icons.RESCAN, "Refresh", self.refresh))
        self._clamp = Adw.Clamp(maximum_size=860, margin_top=12, margin_bottom=24, margin_start=12, margin_end=12)
        self.scrolled.set_child(self._clamp)

    def load_data(self, _database):
        return self.library.storage_report(), self.library.cache_size()

    def populate(self, data):
        report, cache_bytes = data
        if not report.total_count:
            return False
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        box.append(self._summary(report, cache_bytes))
        if report.drives:
            group = Adw.PreferencesGroup(title="Drives", description="Space used by photos and videos on each drive")
            group.add(_legend())
            for drive in report.drives:
                title = "Computer" if drive.path == "/" else drive.label
                if drive.online and drive.total_bytes:
                    used = drive.total_bytes - drive.free_bytes
                    other = max(0, used - drive.media_bytes)
                    value = f"{formatting.human_size(drive.media_bytes)} of {formatting.human_size(drive.total_bytes)}"
                    tooltip = f"{title}: {formatting.human_size(drive.media_bytes)} in {drive.media_count:,} items, {formatting.human_size(other)} other files, {formatting.human_size(drive.free_bytes)} free"
                    group.add(_bar_row(title, value, [(drive.media_bytes, "primary"), (other, "other")], drive.total_bytes, tooltip))
                else:
                    status = "Disconnected" if not drive.online else ""
                    tooltip = f"{title}: {formatting.human_size(drive.media_bytes)} in {drive.media_count:,} items {status}".strip()
                    group.add(_bar_row(title, f"{formatting.human_size(drive.media_bytes)} {status}".strip(), [(drive.media_bytes, "primary")], max(1, report.total_bytes), tooltip))
            box.append(group)
        if report.categories:
            group = Adw.PreferencesGroup(title="By Type")
            largest = max(size for _name, size, _count in report.categories) or 1
            for name, size, count in report.categories:
                tooltip = f"{name}: {formatting.human_size(size)} in {count:,} items"
                group.add(_bar_row(name, f"{formatting.human_size(size)} · {count:,}", [(size, "primary")], largest, tooltip))
            box.append(group)
        if report.years:
            group = Adw.PreferencesGroup(title="By Year")
            largest = max(row[1] or 0 for row in report.years) or 1
            for year, size, count in report.years:
                tooltip = f"{year}: {formatting.human_size(size)} in {count:,} items"
                group.add(_bar_row(str(year), f"{formatting.human_size(size)} · {count:,}", [(size or 0, "primary")], largest, tooltip))
            box.append(group)
        self._clamp.set_child(box)
        self.window_title.set_subtitle(f"{formatting.human_size(report.total_bytes)} in {report.total_count:,} items")
        return True

    def _summary(self, report, cache_bytes):
        box = Gtk.Box(spacing=12, homogeneous=True)
        for value, label in (
            (f"{report.total_count:,}", "Items"),
            (formatting.human_size(report.total_bytes), "Library Size"),
            (formatting.human_size(cache_bytes), "Thumbnail Cache"),
        ):
            tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            tile.add_css_class("card")
            tile.add_css_class("pn-stat-tile")
            number = Gtk.Label(label=GLib.markup_escape_text(value), xalign=0)
            number.add_css_class("title-2")
            number.add_css_class("numeric")
            caption = Gtk.Label(label=label, xalign=0)
            caption.add_css_class("dim-label")
            tile.append(number)
            tile.append(caption)
            box.append(tile)
        return box
