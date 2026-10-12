import functools
import importlib
import logging

import gi

logger = logging.getLogger(__name__)


def _require(namespace, version):
    try:
        gi.require_version(namespace, version)
        return importlib.import_module(f"gi.repository.{namespace}")
    except (ValueError, ImportError) as error:
        logger.info("Optional component %s %s unavailable: %s", namespace, version, error)
        return None


@functools.cache
def gexiv2():
    module = _require("GExiv2", "0.10")
    if module is not None:
        try:
            module.initialize()
        except Exception as error:
            logger.info("GExiv2 failed to initialize: %s", error)
            return None
    return module


@functools.cache
def gstreamer():
    module = _require("Gst", "1.0")
    if module is not None and not module.is_initialized():
        module.init(None)
    return module


@functools.cache
def gstreamer_pbutils():
    if gstreamer() is None:
        return None
    return _require("GstPbutils", "1.0")


@functools.cache
def has_paintable_sink():
    gst = gstreamer()
    return gst is not None and gst.ElementFactory.find("gtk4paintablesink") is not None


@functools.cache
def pillow():
    try:
        return importlib.import_module("PIL.Image")
    except ImportError:
        return None


@functools.cache
def pillow_ops():
    try:
        return importlib.import_module("PIL.ImageOps")
    except ImportError:
        return None


@functools.cache
def opencv():
    try:
        return importlib.import_module("cv2")
    except ImportError:
        return None
