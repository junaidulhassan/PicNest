from picnest.ui.grid_page import GridPage
from picnest.ui.item_actions import ItemActions, new_toast


class AppContext:
    def __init__(self, application, settings, library):
        self.application = application
        self.settings = settings
        self.library = library
        self.actions = ItemActions(self)
        self.window = None
        self.split_view = None
        self.toast_overlay = None
        self.viewer = None
        self.editor = None
        self.current_navigation_view = None

    def show_toast(self, message, button_label=None, callback=None):
        if self.toast_overlay is not None:
            self.toast_overlay.add_toast(new_toast(message, button_label, callback))

    def open_viewer(self, ids, index, source_grid=None, slideshow=False, show_info=False):
        if self.viewer is not None:
            self.viewer.open(ids, index, source_grid, slideshow=slideshow, show_info=show_info)

    def open_editor(self, item):
        if self.editor is not None:
            self.editor.open(item)

    def current_page(self):
        navigation_view = self.current_navigation_view() if callable(self.current_navigation_view) else None
        if navigation_view is None:
            return None
        return navigation_view.get_visible_page()

    def item_rect_for_viewer(self, viewer, item_id):
        page = self.current_page()
        if isinstance(page, GridPage):
            rect, _texture = page.item_rect_in(viewer, item_id)
            return rect
        return None

    def focus_item(self, item_id):
        page = self.current_page()
        if isinstance(page, GridPage):
            page.focus_item(item_id)
