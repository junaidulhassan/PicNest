import logging

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from picnest.core import file_ops
from picnest.core.image_loader import load_texture
from picnest.core.tasks import run_in_thread
from picnest.ui.dialogs.common import AlbumPickerDialog, ExportDialog, confirm, prompt_text, show_error

logger = logging.getLogger(__name__)

ITEM_ACTION_NAMES = (
    "toggle-favorite", "favorite", "unfavorite", "add-to-album", "hide", "unhide", "trash", "restore",
    "delete-permanently", "rename", "move", "copy", "export", "show-in-files", "wallpaper", "copy-to-clipboard",
    "open-with", "email", "edit", "info", "remove-from-album", "set-album-cover", "slideshow",
)


def describe_count(count):
    return f"{count:,} item" + ("" if count == 1 else "s")


class ItemActions:
    def __init__(self, context):
        self._context = context

    @property
    def library(self):
        return self._context.library

    @property
    def window(self):
        return self._context.window

    def _toast(self, message, button_label=None, callback=None):
        self._context.show_toast(message, button_label, callback)

    def _report(self, result, success_message, failure_heading):
        if result.failures:
            show_error(self.window, failure_heading, result.failures)
        elif success_message:
            self._toast(success_message)

    def toggle_favorite(self, item_ids):
        items = self.library.items(item_ids)
        if not items:
            return
        make_favorite = not all(item.is_favorite for item in items)
        self.set_favorite(item_ids, make_favorite)

    def set_favorite(self, item_ids, favorite):
        if not item_ids:
            return
        self.library.set_favorite(item_ids, favorite)
        if len(item_ids) > 1:
            self._toast(f"{'Added' if favorite else 'Removed'} {describe_count(len(item_ids))} {'to' if favorite else 'from'} Favorites")

    def set_hidden(self, item_ids, hidden):
        if not item_ids:
            return
        self.library.set_hidden(item_ids, hidden)
        message = f"{describe_count(len(item_ids))} {'hidden' if hidden else 'moved back to the library'}"
        self._toast(message, "Undo", lambda: self.library.set_hidden(item_ids, not hidden))

    def trash(self, item_ids, on_done=None):
        if not item_ids:
            return

        def finished(result):
            if result.failed_ids:
                self._offer_permanent_delete(result)
            elif result.failures:
                show_error(self.window, "Some items could not be moved to the trash", result.failures)
            if result.succeeded:
                ids = list(result.succeeded)
                self._toast(f"{describe_count(len(ids))} moved to Trash", "Undo", lambda: self.library.restore(ids))
            if on_done is not None:
                on_done(result)

        self.library.trash(item_ids, finished)

    def _offer_permanent_delete(self, result):
        count = len(result.failed_ids)
        reason = result.failures[0][1] if result.failures else ""
        failed_ids = list(result.failed_ids)

        def run():
            self.library.delete_permanently(failed_ids, lambda outcome: self._report(outcome, f"Deleted {describe_count(len(outcome.succeeded))}", "Some items could not be deleted"))

        confirm(
            self.window,
            f"Delete {describe_count(count)} Permanently?",
            f"{describe_count(count)} could not be moved to the trash ({reason}). You can delete {'it' if count == 1 else 'them'} permanently instead. This cannot be undone.",
            "_Delete Permanently",
            run,
        )

    def restore(self, item_ids, on_done=None):
        def finished(result):
            self._report(result, f"Restored {describe_count(len(result.succeeded))}", "Some items could not be restored")
            if on_done is not None:
                on_done(result)

        self.library.restore(item_ids, finished)

    def delete_permanently(self, item_ids, on_done=None):
        if not item_ids:
            return
        count = len(item_ids)

        def run():
            def finished(result):
                self._report(result, f"Deleted {describe_count(len(result.succeeded))}", "Some items could not be deleted")
                if on_done is not None:
                    on_done(result)

            self.library.delete_permanently(item_ids, finished)

        confirm(
            self.window,
            f"Permanently Delete {describe_count(count)}?",
            "These files will be removed from your disk. This cannot be undone.",
            "_Delete",
            run,
        )

    def empty_trash(self):
        count = self.library.trash_count()
        if not count:
            return
        confirm(
            self.window,
            "Empty Trash?",
            f"{describe_count(count)} will be permanently deleted. This cannot be undone.",
            "_Empty Trash",
            lambda: self.library.empty_trash(lambda result: self._report(result, "Trash emptied", "Some items could not be deleted")),
        )

    def add_to_album(self, item_ids):
        if not item_ids:
            return

        def chosen(album_id):
            self.library.add_to_album(album_id, item_ids)
            album = self.library.album(album_id)
            self._toast(f"Added {describe_count(len(item_ids))} to “{album.name if album else 'album'}”")

        AlbumPickerDialog(self.library, self.library.thumbnails, chosen).present(self.window)

    def new_album(self, item_ids=()):
        def create(name):
            self.library.create_album(name, item_ids)
            self._toast(f"Album “{name}” created")

        prompt_text(self.window, "New Album", "Give your album a name", "", "_Create", create, placeholder="Album name")

    def rename(self, item_id):
        item = self.library.item(item_id)
        if item is None or item.is_trashed:
            return

        def submit(new_name):
            try:
                self.library.rename(item_id, new_name)
            except file_ops.FileOperationError as error:
                show_error(self.window, "Could Not Rename", [("", str(error))])

        prompt_text(self.window, "Rename", "", item.name, "_Rename", submit, select_stem=True)

    def _choose_folder(self, title, callback):
        dialog = Gtk.FileDialog(title=title, modal=True)

        def finished(dialog_object, result):
            try:
                folder = dialog_object.select_folder_finish(result)
            except GLib.Error:
                return
            if folder is not None and folder.get_path():
                callback(folder.get_path())

        dialog.select_folder(self.window, None, finished)

    def move(self, item_ids):
        if not item_ids:
            return
        self._choose_folder(
            "Move To",
            lambda directory: self.library.move(item_ids, directory, lambda result: self._report(result, f"Moved {describe_count(len(result.succeeded))}", "Some items could not be moved")),
        )

    def copy(self, item_ids):
        if not item_ids:
            return
        self._choose_folder(
            "Copy To",
            lambda directory: self.library.copy(item_ids, directory, lambda result: self._report(result, f"Copied {describe_count(len(result.succeeded))}", "Some items could not be copied")),
        )

    def export(self, item_ids):
        if not item_ids:
            return

        def options_chosen(image_format, size, quality):
            self._choose_folder(
                "Export To",
                lambda directory: self.library.export(
                    item_ids, directory, image_format, size, quality,
                    lambda result: self._report(result, f"Exported {describe_count(len(result.succeeded))}", "Some items could not be exported"),
                ),
            )

        ExportDialog(len(item_ids), options_chosen).present(self.window)

    def show_in_files(self, item_ids):
        items = self.library.items(item_ids[:1])
        if items:
            file_ops.show_in_file_manager(self.library.file_path_for(items[0]))

    def set_wallpaper(self, item_ids):
        items = [item for item in self.library.items(item_ids[:1]) if not item.is_video]
        if not items:
            return

        def done(success, error):
            if success:
                self._toast("Wallpaper set")
            else:
                show_error(self.window, "Could Not Set Wallpaper", [("", error or "Unknown error")])

        file_ops.set_wallpaper(items[0].path, done)

    def copy_to_clipboard(self, item_ids):
        items = self.library.items(item_ids)
        if not items:
            return
        files = [Gio.File.new_for_path(self.library.file_path_for(item)) for item in items]
        uri_text = "\r\n".join(file.get_uri() for file in files) + "\r\n"
        providers = [
            Gdk.ContentProvider.new_for_value(GObject.Value(Gdk.FileList, Gdk.FileList.new_from_array(files))),
            Gdk.ContentProvider.new_for_bytes("text/uri-list", GLib.Bytes.new(uri_text.encode("utf-8"))),
            Gdk.ContentProvider.new_for_bytes("x-special/gnome-copied-files", GLib.Bytes.new(("copy\n" + "\n".join(file.get_uri() for file in files)).encode("utf-8"))),
        ]
        clipboard = self.window.get_clipboard()
        if len(items) == 1 and not items[0].is_video:
            item = items[0]

            def load():
                return load_texture(self.library.file_path_for(item), item.extension, 4096)

            def loaded(texture, _error):
                content = list(providers)
                if texture is not None:
                    content.insert(0, Gdk.ContentProvider.new_for_value(GObject.Value(Gdk.Texture, texture)))
                clipboard.set_content(Gdk.ContentProvider.new_union(content))
                self._toast("Copied to clipboard")

            run_in_thread(load, loaded, name="picnest-clipboard")
            return
        clipboard.set_content(Gdk.ContentProvider.new_union(providers))
        self._toast(f"Copied {describe_count(len(items))} to clipboard")

    def open_with(self, item_ids):
        items = self.library.items(item_ids[:1])
        if not items:
            return
        launcher = Gtk.FileLauncher(file=Gio.File.new_for_path(self.library.file_path_for(items[0])))
        if hasattr(launcher, "set_always_ask"):
            launcher.set_always_ask(True)

        def finished(launcher_object, result):
            try:
                launcher_object.launch_finish(result)
            except GLib.Error as error:
                if not error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                    logger.info("Open with failed: %s", error.message)

        launcher.launch(self.window, None, finished)

    def email(self, item_ids):
        items = self.library.items(item_ids)
        try:
            file_ops.email_files([self.library.file_path_for(item) for item in items])
        except file_ops.FileOperationError as error:
            show_error(self.window, "Could Not Share", [("", str(error))])

    def remove_from_album(self, album_id, item_ids):
        self.library.remove_from_album(album_id, item_ids)
        self._toast(f"Removed {describe_count(len(item_ids))} from the album", "Undo", lambda: self.library.add_to_album(album_id, item_ids))

    def set_album_cover(self, album_id, item_ids):
        if item_ids:
            self.library.set_album_cover(album_id, item_ids[0])
            self._toast("Album cover updated")

def build_item_menu(collection, single, album_id=None):
    menu = Gio.Menu()
    primary = Gio.Menu()
    if collection == "trash":
        primary.append("Restore", "item.restore")
        primary.append("Delete Permanently", "item.delete-permanently")
        menu.append_section(None, primary)
        return menu
    primary.append("Favorite", "item.toggle-favorite")
    primary.append("Add to Album…", "item.add-to-album")
    if collection == "album" and album_id is not None:
        primary.append("Remove from Album", "item.remove-from-album")
        if single:
            primary.append("Use as Album Cover", "item.set-album-cover")
    menu.append_section(None, primary)
    share = Gio.Menu()
    share.append("Copy to Clipboard", "item.copy-to-clipboard")
    share.append("Open With…", "item.open-with")
    share.append("Send by Email…", "item.email")
    share.append("Export…", "item.export")
    menu.append_section(None, share)
    files = Gio.Menu()
    if single:
        files.append("Edit", "item.edit")
        files.append("Rename…", "item.rename")
        files.append("Set as Wallpaper", "item.wallpaper")
    files.append("Move To…", "item.move")
    files.append("Copy To…", "item.copy")
    files.append("Show in Files", "item.show-in-files")
    menu.append_section(None, files)
    danger = Gio.Menu()
    danger.append("Unhide" if collection == "hidden" else "Hide", "item.unhide" if collection == "hidden" else "item.hide")
    danger.append("Move to Trash", "item.trash")
    menu.append_section(None, danger)
    return menu


def new_toast(message, button_label=None, callback=None, timeout=4):
    toast = Adw.Toast(title=GLib.markup_escape_text(message), timeout=timeout)
    if button_label and callback is not None:
        toast.set_button_label(button_label)
        toast.connect("button-clicked", lambda _toast: callback())
    return toast
