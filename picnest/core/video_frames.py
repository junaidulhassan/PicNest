import logging
import threading
from dataclasses import dataclass

from gi.repository import GdkPixbuf, Gio, GLib

from picnest.core import capabilities

logger = logging.getLogger(__name__)

STATE_TIMEOUT_SECONDS = 8
ROTATED_ORIENTATIONS = ("rotate-90", "rotate-270", "flip-rotate-90", "flip-rotate-270")
_pipeline_semaphore = threading.BoundedSemaphore(3)


@dataclass
class VideoProbe:
    duration: float
    width: int
    height: int
    created_at: float | None = None
    latitude: float | None = None
    longitude: float | None = None


def _uri_for(path):
    return Gio.File.new_for_path(path).get_uri()


def _probe_with_discoverer(path):
    gst = capabilities.gstreamer()
    pbutils = capabilities.gstreamer_pbutils()
    if gst is None or pbutils is None:
        return None
    try:
        discoverer = pbutils.Discoverer.new(10 * gst.SECOND)
        info = discoverer.discover_uri(_uri_for(path))
    except GLib.Error as error:
        logger.debug("Discoverer failed for %s: %s", path, error.message)
        return None
    duration = max(0, info.get_duration()) / gst.SECOND
    width = height = 0
    video_streams = info.get_video_streams()
    if video_streams:
        stream = video_streams[0]
        width, height = stream.get_width(), stream.get_height()
        numerator, denominator = stream.get_par_num(), stream.get_par_denom()
        if numerator and denominator and numerator != denominator:
            width = int(width * numerator / denominator)
    probe = VideoProbe(duration, width, height)
    tags = info.get_tags()
    if tags is not None:
        found, orientation = tags.get_string("image-orientation")
        if found and orientation in ROTATED_ORIENTATIONS:
            probe.width, probe.height = probe.height, probe.width
        found, date_time = tags.get_date_time("datetime")
        if found and date_time is not None and date_time.has_year():
            try:
                glib_date_time = date_time.to_g_date_time()
                if glib_date_time is not None:
                    probe.created_at = float(glib_date_time.to_unix())
            except Exception:
                probe.created_at = None
        found_latitude, latitude = tags.get_double("geo-location-latitude")
        found_longitude, longitude = tags.get_double("geo-location-longitude")
        if found_latitude and found_longitude and (latitude or longitude):
            probe.latitude, probe.longitude = latitude, longitude
    if probe.created_at is not None and probe.created_at < 86400 * 365:
        probe.created_at = None
    return probe


def _build_frame_pipeline(path):
    gst = capabilities.gstreamer()
    playbin = gst.ElementFactory.make("playbin", None)
    if playbin is None:
        return None, None
    sink_bin = gst.parse_bin_from_description(
        "videoflip video-direction=auto ! videoconvert ! videoscale ! "
        "video/x-raw,format=RGB,pixel-aspect-ratio=1/1 ! appsink name=frame_sink sync=false max-buffers=1 drop=true",
        True,
    )
    playbin.set_property("uri", _uri_for(path))
    playbin.set_property("video-sink", sink_bin)
    playbin.set_property("audio-sink", gst.ElementFactory.make("fakesink", None))
    playbin.set_property("mute", True)
    return playbin, sink_bin.get_by_name("frame_sink")


def _wait_for_state(gst, element):
    result, _state, _pending = element.get_state(STATE_TIMEOUT_SECONDS * gst.SECOND)
    return result in (gst.StateChangeReturn.SUCCESS, gst.StateChangeReturn.NO_PREROLL)


def _sample_to_pixbuf(gst, sample):
    caps = sample.get_caps()
    structure = caps.get_structure(0)
    width = structure.get_value("width")
    height = structure.get_value("height")
    buffer = sample.get_buffer()
    success, map_info = buffer.map(gst.MapFlags.READ)
    if not success:
        return None
    try:
        data = bytes(map_info.data)
    finally:
        buffer.unmap(map_info)
    stride = (width * 3 + 3) & ~3
    if len(data) < stride * height:
        stride = width * 3
    return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, False, 8, width, height, stride)


def grab_frame(path, position_fraction=0.1, maximum_seek_seconds=4.0):
    gst = capabilities.gstreamer()
    if gst is None:
        return None, None
    with _pipeline_semaphore:
        playbin, sink = _build_frame_pipeline(path)
        if playbin is None or sink is None:
            return None, None
        try:
            playbin.set_state(gst.State.PAUSED)
            if not _wait_for_state(gst, playbin):
                return None, None
            found, duration = playbin.query_duration(gst.Format.TIME)
            duration_seconds = duration / gst.SECOND if found and duration > 0 else 0.0
            if duration_seconds > 0:
                target = min(duration_seconds * position_fraction, maximum_seek_seconds)
                playbin.seek_simple(gst.Format.TIME, gst.SeekFlags.FLUSH | gst.SeekFlags.KEY_UNIT, int(target * gst.SECOND))
                _wait_for_state(gst, playbin)
            sample = sink.emit("pull-preroll")
            if sample is None:
                return None, duration_seconds
            return _sample_to_pixbuf(gst, sample), duration_seconds
        except GLib.Error as error:
            logger.debug("Frame extraction failed for %s: %s", path, error.message)
            return None, None
        finally:
            playbin.set_state(gst.State.NULL)


def probe_video(path):
    probe = _probe_with_discoverer(path)
    if probe is not None and (probe.width or probe.duration):
        return probe
    pixbuf, duration = grab_frame(path, 0.0, 0.0)
    if pixbuf is None and not duration:
        return None
    width = pixbuf.get_width() if pixbuf else 0
    height = pixbuf.get_height() if pixbuf else 0
    return VideoProbe(duration or 0.0, width, height)
