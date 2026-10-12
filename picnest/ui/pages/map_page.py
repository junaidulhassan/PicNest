import math

from gi.repository import Adw, GLib, Gtk

from picnest.config import APP_NAME, VERSION
from picnest.core import capabilities, formatting
from picnest.core.query import ViewSpec
from picnest.ui import icons
from picnest.ui.grid_page import GridPage, sidebar_toggle
from picnest.ui.widgets import CoverPicture, connect_weak, empty_state, icon_button, pill_button, set_accessible_label

CLUSTER_CELL_PIXELS = 72
MAXIMUM_MARKERS = 400


def cluster_points(points, zoom_level):
    cell_degrees = 360.0 / (2 ** max(0.0, zoom_level)) * CLUSTER_CELL_PIXELS / 256.0
    clusters = {}
    for item_id, latitude, longitude in points:
        key = (math.floor(latitude / cell_degrees), math.floor(longitude / cell_degrees))
        cluster = clusters.get(key)
        if cluster is None:
            clusters[key] = [latitude, longitude, [item_id]]
        else:
            cluster[0] += latitude
            cluster[1] += longitude
            cluster[2].append(item_id)
    result = []
    for latitude_sum, longitude_sum, ids in clusters.values():
        result.append((latitude_sum / len(ids), longitude_sum / len(ids), ids))
    result.sort(key=lambda cluster: len(cluster[2]), reverse=True)
    return result[:MAXIMUM_MARKERS]


class MapPage(Adw.NavigationPage):
    __gtype_name__ = "PicnestMapPage"

    def __init__(self, context):
        super().__init__(title="Map", tag="map")
        self.context = context
        self.library = context.library
        self._points = []
        self._markers = []
        self._recluster_source = 0
        self._dirty = True
        self._fitted = False
        self._shumate = capabilities.shumate()
        toolbar = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT)
        header = Adw.HeaderBar()
        header.pack_start(sidebar_toggle(context))
        self._title = Adw.WindowTitle(title="Map")
        header.set_title_widget(self._title)
        header.pack_end(icon_button(icons.SEARCH, "Search", action_name="win.search"))
        self._fit_button = icon_button(icons.ZOOM_FIT, "Show All Places", self._fit_to_points)
        header.pack_end(self._fit_button)
        toolbar.add_top_bar(header)
        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        toolbar.set_content(self._stack)
        self.set_child(toolbar)
        self._map = None
        if self._shumate is None:
            self._stack.add_named(empty_state(icons.MAP, "Map Unavailable", "Install the libshumate package (gir1.2-shumate-1.0) to see your photos on a map."), "unavailable")
            self._fit_button.set_visible(False)
        else:
            self._stack.add_named(self._build_disabled_page(), "disabled")
            self._stack.add_named(empty_state(icons.LOCATION, "No Places Yet", "Photos with GPS location data appear here."), "empty")
        context.settings.connect_changed("online-map-tiles", lambda _value: self._apply_settings())
        connect_weak(self.library, "content-changed", self._on_content_changed)
        self.connect("map", self._on_map)

    def _build_disabled_page(self):
        button = pill_button("Allow Map Tiles", lambda: self.context.settings.set("online-map-tiles", True))
        return empty_state(icons.MAP, "Online Maps Are Off", "Map tiles are downloaded from OpenStreetMap. Your photos never leave your computer.", button)

    def _ensure_map(self):
        if self._map is not None or self._shumate is None:
            return
        shumate = self._shumate
        if hasattr(shumate, "set_user_agent"):
            shumate.set_user_agent(f"{APP_NAME}/{VERSION}")
        registry = shumate.MapSourceRegistry.new_with_defaults()
        source = registry.get_by_id(shumate.MAP_SOURCE_OSM_MAPNIK)
        simple_map = shumate.SimpleMap()
        simple_map.set_map_source(source)
        simple_map.add_css_class("pn-map")
        self._map = simple_map
        self._viewport = simple_map.get_viewport()
        self._marker_layer = shumate.MarkerLayer.new(self._viewport)
        simple_map.add_overlay_layer(self._marker_layer)
        self._viewport.connect("notify::zoom-level", lambda *_args: self._schedule_recluster())
        self._stack.add_named(simple_map, "map")

    def _apply_settings(self):
        if self._shumate is None:
            return
        if not self.context.settings.get("online-map-tiles"):
            self._stack.set_visible_child_name("disabled")
            self._fit_button.set_visible(False)
            return
        self._ensure_map()
        self.refresh()

    def _on_map(self, _page):
        if self._dirty:
            self._apply_settings()

    def _on_content_changed(self, _library, _reason):
        if self.get_mapped():
            self.refresh()
        else:
            self._dirty = True

    def refresh(self):
        self._dirty = False
        if self._shumate is None or not self.context.settings.get("online-map-tiles"):
            return
        self.library.run_query(self, lambda _database: self.library.map_points(), self._on_points)

    def _on_points(self, points):
        if points is None:
            return
        self._points = points
        self._title.set_subtitle(formatting.plural(len(points), "photo") + " with location" if points else "")
        if not points:
            self._stack.set_visible_child_name("empty")
            self._fit_button.set_visible(False)
            return
        self._stack.set_visible_child_name("map")
        self._fit_button.set_visible(True)
        if not self._fitted:
            self._fitted = True
            GLib.idle_add(self._fit_to_points)
        self._recluster()

    def _fit_to_points(self):
        if not self._points or self._map is None:
            return GLib.SOURCE_REMOVE
        latitudes = [point[1] for point in self._points]
        longitudes = [point[2] for point in self._points]
        center_latitude = (min(latitudes) + max(latitudes)) / 2
        center_longitude = (min(longitudes) + max(longitudes)) / 2
        span = max(max(latitudes) - min(latitudes), (max(longitudes) - min(longitudes)) / 2, 0.01)
        zoom = max(2.0, min(16.0, math.log2(180.0 / span)))
        map_widget = self._map.get_map()
        if hasattr(map_widget, "go_to_full"):
            map_widget.go_to_full(center_latitude, center_longitude, zoom)
        else:
            self._viewport.set_zoom_level(zoom)
            map_widget.center_on(center_latitude, center_longitude)
        return GLib.SOURCE_REMOVE

    def _schedule_recluster(self):
        if self._recluster_source:
            GLib.source_remove(self._recluster_source)
        self._recluster_source = GLib.timeout_add(150, self._recluster)

    def _recluster(self):
        self._recluster_source = 0
        if self._map is None:
            return GLib.SOURCE_REMOVE
        self._marker_layer.remove_all()
        self._markers = []
        for latitude, longitude, ids in cluster_points(self._points, self._viewport.get_zoom_level()):
            marker = self._shumate.Marker()
            marker.set_location(latitude, longitude)
            marker.set_child(self._marker_widget(ids))
            self._marker_layer.add_marker(marker)
            self._markers.append(marker)
        return GLib.SOURCE_REMOVE

    def _marker_widget(self, ids):
        button = Gtk.Button()
        button.add_css_class("flat")
        button.add_css_class("pn-map-marker")
        overlay = Gtk.Overlay()
        cover = CoverPicture(self.library.thumbnails, radius=26, size=(52, 52), small=True)
        cover.set_item(self.library.item(ids[0]))
        overlay.set_child(cover)
        if len(ids) > 1:
            badge = Gtk.Label(label=f"{len(ids):,}" if len(ids) < 10000 else "9999+", halign=Gtk.Align.END, valign=Gtk.Align.START)
            badge.add_css_class("pn-map-badge")
            overlay.add_overlay(badge)
        button.set_child(overlay)
        label = formatting.plural(len(ids), "photo")
        button.set_tooltip_text(label)
        set_accessible_label(button, f"{label} at this place")
        button.connect("clicked", lambda _button: self._open_cluster(ids))
        return button

    def _open_cluster(self, ids):
        if len(ids) == 1:
            self.context.open_viewer(list(ids), 0, None)
            return
        spec = ViewSpec("all", ids=tuple(ids), grouping="days", title="Photos Here")
        page = GridPage(self.context, spec, icons.LOCATION, "No Photos", "")
        page.window_title.set_subtitle(formatting.plural(len(ids), "item"))
        navigation_view = self.get_ancestor(Adw.NavigationView)
        if navigation_view is not None:
            navigation_view.push(page)
