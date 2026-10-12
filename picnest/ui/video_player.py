import logging

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from picnest.core import capabilities, formatting
from picnest.ui import icons
from picnest.ui.widgets import icon_button, set_accessible_label, unparent_children

logger = logging.getLogger(__name__)

PLAYBACK_RATES = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
CONTROLS_HIDE_DELAY = 2600


class VideoEngine(GObject.Object):
    __gsignals__ = {
        "state-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "ended": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "error": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    supports_rate = False

    @property
    def paintable(self):
        raise NotImplementedError


class PlaybinEngine(VideoEngine):
    supports_rate = True

    def __init__(self):
        super().__init__()
        gst = capabilities.gstreamer()
        self._gst = gst
        self._playbin = gst.ElementFactory.make("playbin3", None) or gst.ElementFactory.make("playbin", None)
        sink = gst.ElementFactory.make("gtk4paintablesink", None)
        self._paintable = sink.get_property("paintable")
        video_sink = sink
        try:
            if self._paintable.get_property("gl-context") is not None:
                gl_bin = gst.ElementFactory.make("glsinkbin", None)
                if gl_bin is not None:
                    gl_bin.set_property("sink", sink)
                    video_sink = gl_bin
        except TypeError:
            pass
        self._playbin.set_property("video-sink", video_sink)
        self._rate = 1.0
        self._loop = False
        self._playing = False
        self._duration = 0.0
        bus = self._playbin.get_bus()
        bus.add_signal_watch()
        self._bus = bus
        self._bus_handler = bus.connect("message", self._on_message)

    @property
    def paintable(self):
        return self._paintable

    def load(self, path):
        self._playbin.set_state(self._gst.State.NULL)
        self._duration = 0.0
        self._rate = 1.0
        self._playbin.set_property("uri", Gio.File.new_for_path(path).get_uri())
        self._playbin.set_state(self._gst.State.PAUSED)

    def play(self):
        self._playbin.set_state(self._gst.State.PLAYING)
        self._playing = True
        self.emit("state-changed")

    def pause(self):
        self._playbin.set_state(self._gst.State.PAUSED)
        self._playing = False
        self.emit("state-changed")

    @property
    def playing(self):
        return self._playing

    @property
    def duration(self):
        if not self._duration:
            found, value = self._playbin.query_duration(self._gst.Format.TIME)
            if found and value > 0:
                self._duration = value / self._gst.SECOND
        return self._duration

    @property
    def position(self):
        found, value = self._playbin.query_position(self._gst.Format.TIME)
        return value / self._gst.SECOND if found and value >= 0 else 0.0

    def seek(self, seconds):
        self._seek(seconds, self._rate)

    def _seek(self, seconds, rate):
        gst = self._gst
        position = int(max(0.0, seconds) * gst.SECOND)
        flags = gst.SeekFlags.FLUSH | gst.SeekFlags.ACCURATE
        if rate > 0:
            self._playbin.seek(rate, gst.Format.TIME, flags, gst.SeekType.SET, position, gst.SeekType.NONE, -1)
        else:
            self._playbin.seek(rate, gst.Format.TIME, flags, gst.SeekType.SET, 0, gst.SeekType.SET, position)

    def set_rate(self, rate):
        self._rate = rate
        self._seek(self.position, rate)

    @property
    def rate(self):
        return self._rate

    def set_volume(self, volume):
        self._playbin.set_property("volume", max(0.0, min(1.0, volume)))

    @property
    def volume(self):
        return self._playbin.get_property("volume")

    def set_muted(self, muted):
        self._playbin.set_property("mute", muted)

    @property
    def muted(self):
        return self._playbin.get_property("mute")

    def set_loop(self, loop):
        self._loop = loop

    def _on_message(self, _bus, message):
        gst = self._gst
        if message.type == gst.MessageType.EOS:
            if self._loop:
                self._seek(0.0, self._rate)
            else:
                self._playing = False
                self._playbin.set_state(gst.State.PAUSED)
                self.emit("state-changed")
                self.emit("ended")
        elif message.type == gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            self._playing = False
            self.emit("error", error.message)
        elif message.type == gst.MessageType.DURATION_CHANGED:
            self._duration = 0.0

    def shutdown(self):
        self._playbin.set_state(self._gst.State.NULL)
        if self._bus_handler:
            self._bus.disconnect(self._bus_handler)
            self._bus.remove_signal_watch()
            self._bus_handler = 0


class MediaFileEngine(VideoEngine):
    supports_rate = False

    def __init__(self):
        super().__init__()
        self._media = None
        self._loop = False
        self._volume = 1.0
        self._muted = False

    @property
    def paintable(self):
        return self._media

    def load(self, path):
        self.shutdown()
        media = Gtk.MediaFile.new_for_filename(path)
        media.set_loop(self._loop)
        media.set_volume(self._volume)
        media.set_muted(self._muted)
        media.connect("notify::playing", lambda *_args: self.emit("state-changed"))
        media.connect("notify::ended", self._on_ended)
        media.connect("notify::error", self._on_error)
        self._media = media

    def _on_ended(self, media, _param):
        if media.get_ended():
            self.emit("state-changed")
            self.emit("ended")

    def _on_error(self, media, _param):
        error = media.get_error()
        if error is not None:
            self.emit("error", error.message)

    def play(self):
        if self._media is not None:
            if self._media.get_ended():
                self._media.seek(0)
            self._media.play()

    def pause(self):
        if self._media is not None:
            self._media.pause()

    @property
    def playing(self):
        return self._media is not None and self._media.get_playing()

    @property
    def duration(self):
        return self._media.get_duration() / 1_000_000 if self._media is not None else 0.0

    @property
    def position(self):
        return self._media.get_timestamp() / 1_000_000 if self._media is not None else 0.0

    def seek(self, seconds):
        if self._media is not None and self._media.is_seekable():
            self._media.seek(int(max(0.0, seconds) * 1_000_000))

    def set_rate(self, rate):
        return

    @property
    def rate(self):
        return 1.0

    def set_volume(self, volume):
        self._volume = volume
        if self._media is not None:
            self._media.set_volume(volume)

    @property
    def volume(self):
        return self._volume

    def set_muted(self, muted):
        self._muted = muted
        if self._media is not None:
            self._media.set_muted(muted)

    @property
    def muted(self):
        return self._muted

    def set_loop(self, loop):
        self._loop = loop
        if self._media is not None:
            self._media.set_loop(loop)

    def shutdown(self):
        if self._media is not None:
            self._media.pause()
            self._media = None


def create_engine():
    if capabilities.has_paintable_sink():
        try:
            return PlaybinEngine()
        except Exception as error:
            logger.warning("Falling back to GtkMediaFile: %s", error)
    return MediaFileEngine()


class PictureInPictureWindow(Adw.Window):
    __gtype_name__ = "PicnestPictureInPictureWindow"

    def __init__(self, engine, title, on_closed):
        super().__init__(title=f"{title} — Picture in Picture", default_width=480, default_height=270, resizable=True)
        self._engine = engine
        self._on_closed = on_closed
        overlay = Gtk.Overlay()
        picture = Gtk.Picture(paintable=engine.paintable, content_fit=Gtk.ContentFit.CONTAIN)
        picture.add_css_class("pn-video-surface")
        overlay.set_child(picture)
        controls = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER, valign=Gtk.Align.END, margin_bottom=10)
        controls.add_css_class("osd")
        controls.add_css_class("pn-pip-controls")
        self._play_button = icon_button(icons.PAUSE if engine.playing else icons.PLAY, "Play or Pause", self._toggle, css_classes=("flat", "circular"))
        close_button = icon_button(icons.CLOSE, "Close Picture in Picture", self.close, css_classes=("flat", "circular"))
        controls.append(self._play_button)
        controls.append(close_button)
        overlay.add_overlay(controls)
        handle = Gtk.WindowHandle(child=overlay)
        self.set_content(handle)
        self._state_handler = engine.connect("state-changed", self._sync)
        self.connect("close-request", self._on_close_request)

    def _toggle(self):
        if self._engine.playing:
            self._engine.pause()
        else:
            self._engine.play()

    def _sync(self, _engine):
        self._play_button.set_icon_name(icons.PAUSE if self._engine.playing else icons.PLAY)

    def _on_close_request(self, _window):
        self._engine.disconnect(self._state_handler)
        self._engine.pause()
        self._engine.shutdown()
        self._on_closed()
        return False


class VideoPlayer(Gtk.Widget):
    __gtype_name__ = "PicnestVideoPlayer"
    __gsignals__ = {
        "fullscreen-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "controls-visibility": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self, settings):
        super().__init__(hexpand=True, vexpand=True, focusable=True)
        self.set_layout_manager(Gtk.BinLayout())
        self._settings = settings
        self._engine = create_engine()
        self._engine_handlers = []
        self._path = None
        self._title = ""
        self._seeking = False
        self._position_source = 0
        self._hide_source = 0
        self._pip_window = None
        overlay = Gtk.Overlay()
        overlay.set_parent(self)
        self._picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, hexpand=True, vexpand=True)
        self._picture.add_css_class("pn-video-surface")
        overlay.set_child(self._picture)
        self._status = Adw.StatusPage(icon_name=icons.WARNING, title="Cannot Play Video", visible=False)
        self._status.add_css_class("pn-video-error")
        overlay.add_overlay(self._status)
        self._big_play = Gtk.Button(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, tooltip_text="Play")
        self._big_play.set_child(Gtk.Image(icon_name=icons.PLAY, pixel_size=36))
        self._big_play.add_css_class("osd")
        self._big_play.add_css_class("circular")
        self._big_play.add_css_class("pn-big-play")
        set_accessible_label(self._big_play, "Play")
        self._big_play.connect("clicked", lambda _button: self.toggle_playback())
        overlay.add_overlay(self._big_play)
        self._controls_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE, transition_duration=200, reveal_child=True, valign=Gtk.Align.END)
        self._controls_revealer.set_child(self._build_controls())
        overlay.add_overlay(self._controls_revealer)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)
        click = Gtk.GestureClick()
        click.connect("released", self._on_surface_click)
        self._picture.add_controller(click)
        self._connect_engine()

    def set_bottom_inset(self, inset):
        self._controls_bar.set_margin_bottom(inset)

    def _build_controls(self):
        bar = Gtk.Box(spacing=8, margin_start=16, margin_end=16, margin_bottom=16)
        self._controls_bar = bar
        bar.add_css_class("osd")
        bar.add_css_class("pn-video-controls")
        self._play_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=150)
        self._play_stack.add_named(Gtk.Image(icon_name=icons.PLAY), "play")
        self._play_stack.add_named(Gtk.Image(icon_name=icons.PAUSE), "pause")
        self._play_button = Gtk.Button(child=self._play_stack, tooltip_text="Play (Space)", valign=Gtk.Align.CENTER)
        self._play_button.add_css_class("flat")
        self._play_button.add_css_class("circular")
        self._play_button.add_css_class("pn-play-toggle")
        set_accessible_label(self._play_button, "Play or Pause")
        self._play_button.connect("clicked", lambda _button: self.toggle_playback())
        bar.append(self._play_button)
        self._position_label = Gtk.Label(label="0:00", width_chars=5)
        self._position_label.add_css_class("numeric")
        bar.append(self._position_label)
        self._seek_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, draw_value=False)
        self._seek_scale.set_range(0, 1)
        self._seek_scale.add_css_class("pn-seek")
        set_accessible_label(self._seek_scale, "Seek")
        self._seek_scale.connect("change-value", self._on_seek_change)
        bar.append(self._seek_scale)
        self._duration_label = Gtk.Label(label="0:00", width_chars=5)
        self._duration_label.add_css_class("numeric")
        bar.append(self._duration_label)
        volume_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6)
        self._volume_scale = Gtk.Scale(orientation=Gtk.Orientation.VERTICAL, inverted=True, draw_value=False, height_request=140)
        self._volume_scale.set_range(0, 1)
        self._volume_scale.set_value(1.0)
        set_accessible_label(self._volume_scale, "Volume")
        self._volume_scale.connect("value-changed", self._on_volume_changed)
        self._mute_button = Gtk.ToggleButton(icon_name=icons.VOLUME_MUTED, tooltip_text="Mute (M)")
        self._mute_button.add_css_class("flat")
        set_accessible_label(self._mute_button, "Mute")
        self._mute_button.connect("toggled", self._on_mute_toggled)
        volume_box.append(self._volume_scale)
        volume_box.append(self._mute_button)
        self._volume_button = Gtk.MenuButton(icon_name=icons.VOLUME_HIGH, tooltip_text="Volume", popover=Gtk.Popover(child=volume_box), direction=Gtk.ArrowType.UP)
        self._volume_button.add_css_class("flat")
        set_accessible_label(self._volume_button, "Volume")
        bar.append(self._volume_button)
        self._speed_button = Gtk.MenuButton(label="1×", tooltip_text="Playback Speed", direction=Gtk.ArrowType.UP)
        self._speed_button.add_css_class("flat")
        self._speed_button.add_css_class("pn-speed-button")
        set_accessible_label(self._speed_button, "Playback Speed")
        speed_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        group_leader = None
        for rate in PLAYBACK_RATES:
            button = Gtk.ToggleButton(label=f"{rate:g}×", active=rate == 1.0)
            button.add_css_class("flat")
            if group_leader is None:
                group_leader = button
            else:
                button.set_group(group_leader)
            button.connect("toggled", self._on_rate_toggled, rate)
            speed_box.append(button)
        self._speed_button.set_popover(Gtk.Popover(child=speed_box))
        self._speed_button.set_visible(self._engine.supports_rate)
        bar.append(self._speed_button)
        bar.append(icon_button(icons.PICTURE_IN_PICTURE, "Picture in Picture", self.open_picture_in_picture))
        self._fullscreen_button = icon_button(icons.FULLSCREEN, "Fullscreen (F11)", lambda: self.emit("fullscreen-requested"))
        bar.append(self._fullscreen_button)
        return bar

    def _connect_engine(self):
        self._engine_handlers = [
            self._engine.connect("state-changed", self._on_state_changed),
            self._engine.connect("ended", self._on_ended),
            self._engine.connect("error", self._on_error),
        ]

    def _disconnect_engine(self):
        for handler in self._engine_handlers:
            self._engine.disconnect(handler)
        self._engine_handlers = []

    def do_dispose(self):
        if hasattr(self, "_engine"):
            self.stop()
        unparent_children(self)
        Gtk.Widget.do_dispose(self)

    def set_fullscreen_state(self, fullscreen):
        self._fullscreen_button.set_icon_name(icons.FULLSCREEN_EXIT if fullscreen else icons.FULLSCREEN)
        label = "Exit Fullscreen (F11)" if fullscreen else "Fullscreen (F11)"
        self._fullscreen_button.set_tooltip_text(label)
        set_accessible_label(self._fullscreen_button, label)

    def load(self, path, title, autoplay=True):
        self._path = path
        self._title = title
        self._status.set_visible(False)
        self._status.set_icon_name(icons.WARNING)
        self._status.set_title("Cannot Play Video")
        self._status.set_description("")
        self._engine.set_loop(self._settings.get("loop-videos"))
        self._engine.load(path)
        self._engine.set_volume(self._volume_scale.get_value())
        self._engine.set_muted(self._mute_button.get_active())
        self._picture.set_paintable(self._engine.paintable)
        self._seek_scale.set_value(0)
        self._position_label.set_label("0:00")
        self._duration_label.set_label("0:00")
        self._speed_button.set_label("1×")
        if autoplay:
            self._engine.play()
        self._start_position_updates()
        self._on_state_changed(self._engine)
        self._show_controls()

    def stop(self):
        if self._position_source:
            GLib.source_remove(self._position_source)
            self._position_source = 0
        if self._hide_source:
            GLib.source_remove(self._hide_source)
            self._hide_source = 0
        if self._engine is not None:
            self._engine.pause()
            self._engine.shutdown()
        self._picture.set_paintable(None)
        self._path = None

    def toggle_playback(self):
        if self._path is None:
            return
        if self._engine.playing:
            self._engine.pause()
        else:
            self._engine.play()
        self._show_controls()

    @property
    def playing(self):
        return self._engine.playing

    def seek_relative(self, seconds):
        self._engine.seek(max(0.0, self._engine.position + seconds))
        self._show_controls()

    def adjust_volume(self, delta):
        self._volume_scale.set_value(max(0.0, min(1.0, self._volume_scale.get_value() + delta)))
        self._show_controls()

    def toggle_mute(self):
        self._mute_button.set_active(not self._mute_button.get_active())

    def _start_position_updates(self):
        if not self._position_source:
            self._position_source = GLib.timeout_add(200, self._update_position)

    def _update_position(self):
        if self._path is None:
            self._position_source = 0
            return GLib.SOURCE_REMOVE
        duration = self._engine.duration
        position = self._engine.position
        if duration > 0:
            self._seek_scale.set_range(0, duration)
            self._duration_label.set_label(formatting.human_duration(duration))
        if not self._seeking:
            self._seek_scale.set_value(position)
        self._position_label.set_label(formatting.human_duration(position))
        return GLib.SOURCE_CONTINUE

    def _on_seek_change(self, _scale, _scroll_type, value):
        self._seeking = True
        self._engine.seek(value)
        self._position_label.set_label(formatting.human_duration(value))
        GLib.timeout_add(250, self._end_seeking)
        self._show_controls()
        return False

    def _end_seeking(self):
        self._seeking = False
        return GLib.SOURCE_REMOVE

    def _on_volume_changed(self, scale):
        volume = scale.get_value()
        self._engine.set_volume(volume)
        self._update_volume_icon()

    def _on_mute_toggled(self, button):
        self._engine.set_muted(button.get_active())
        self._update_volume_icon()

    def _update_volume_icon(self):
        volume = self._volume_scale.get_value()
        if self._mute_button.get_active() or volume <= 0.001:
            icon_name = icons.VOLUME_MUTED
        elif volume < 0.5:
            icon_name = icons.VOLUME_LOW
        else:
            icon_name = icons.VOLUME_HIGH
        self._volume_button.set_icon_name(icon_name)

    def _on_rate_toggled(self, button, rate):
        if button.get_active():
            self._engine.set_rate(rate)
            self._speed_button.set_label(f"{rate:g}×")

    def _on_state_changed(self, engine):
        playing = engine.playing
        self._play_stack.set_visible_child_name("pause" if playing else "play")
        self._play_button.set_tooltip_text("Pause (Space)" if playing else "Play (Space)")
        self._big_play.set_visible(not playing and self._path is not None and not self._status.get_visible())
        if playing:
            self._schedule_hide()
        else:
            self._show_controls(auto_hide=False)

    def _on_ended(self, _engine):
        self._show_controls(auto_hide=False)

    def _on_error(self, _engine, message):
        self._status.set_description(GLib.markup_escape_text(message))
        self._status.set_visible(True)
        self._big_play.set_visible(False)

    def _on_motion(self, _controller, _x, _y):
        self._show_controls()

    def _on_surface_click(self, gesture, n_press, _x, _y):
        if n_press == 2:
            self.emit("fullscreen-requested")
        elif n_press == 1:
            self.toggle_playback()

    def _show_controls(self, auto_hide=True):
        if not self._controls_revealer.get_reveal_child():
            self._controls_revealer.set_reveal_child(True)
            self.emit("controls-visibility", True)
        self.set_cursor(None)
        if auto_hide and self._engine.playing:
            self._schedule_hide()
        elif self._hide_source:
            GLib.source_remove(self._hide_source)
            self._hide_source = 0

    def _schedule_hide(self):
        if self._hide_source:
            GLib.source_remove(self._hide_source)
        self._hide_source = GLib.timeout_add(CONTROLS_HIDE_DELAY, self._hide_controls)

    def _hide_controls(self):
        self._hide_source = 0
        if self._engine.playing:
            self._controls_revealer.set_reveal_child(False)
            self.set_cursor(Gdk.Cursor.new_from_name("none", None))
            self.emit("controls-visibility", False)
        return GLib.SOURCE_REMOVE

    def open_picture_in_picture(self):
        if self._path is None or self._pip_window is not None:
            return
        engine = self._engine
        self._disconnect_engine()
        if self._position_source:
            GLib.source_remove(self._position_source)
            self._position_source = 0
        self._picture.set_paintable(None)
        title = self._title
        self._path = None
        self._engine = create_engine()
        self._connect_engine()
        if not engine.playing:
            engine.play()

        def closed():
            self._pip_window = None

        window = PictureInPictureWindow(engine, title, closed)
        self._pip_window = window
        window.present()
        self._status.set_visible(True)
        self._status.set_icon_name(icons.PICTURE_IN_PICTURE)
        self._status.set_title("Playing in Picture in Picture")
        self._status.set_description(GLib.markup_escape_text(title))
        self._big_play.set_visible(False)
