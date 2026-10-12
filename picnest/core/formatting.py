import datetime

from gi.repository import GLib


def human_size(size_bytes):
    if size_bytes is None:
        return "Unknown size"
    return GLib.format_size(int(size_bytes))


def human_duration(seconds):
    if not seconds or seconds < 0:
        return "0:00"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def local_datetime(timestamp):
    try:
        return datetime.datetime.fromtimestamp(timestamp)
    except (OverflowError, OSError, ValueError, TypeError):
        return datetime.datetime.fromtimestamp(0)


def day_title(timestamp, today=None):
    moment = local_datetime(timestamp).date()
    today = today or datetime.date.today()
    if moment == today:
        return "Today"
    if moment == today - datetime.timedelta(days=1):
        return "Yesterday"
    if moment.year == today.year:
        return moment.strftime("%A, %-d %B")
    return moment.strftime("%A, %-d %B %Y")


def month_title(timestamp):
    return local_datetime(timestamp).strftime("%B %Y")


def year_title(timestamp):
    return str(local_datetime(timestamp).year)


def full_date(timestamp):
    if timestamp is None:
        return "Unknown date"
    return local_datetime(timestamp).strftime("%A, %-d %B %Y at %H:%M")


def short_date(timestamp):
    if timestamp is None:
        return "Unknown date"
    return local_datetime(timestamp).strftime("%-d %b %Y")


def item_count_label(photos, videos):
    parts = []
    if photos:
        parts.append(f"{photos:,} photo" + ("" if photos == 1 else "s"))
    if videos:
        parts.append(f"{videos:,} video" + ("" if videos == 1 else "s"))
    return ", ".join(parts) if parts else "No items"


def plural(count, singular, plural_form=None):
    word = singular if count == 1 else (plural_form or f"{singular}s")
    return f"{count:,} {word}"


def coordinate_label(latitude, longitude):
    if latitude is None or longitude is None:
        return ""
    north_south = "N" if latitude >= 0 else "S"
    east_west = "E" if longitude >= 0 else "W"
    return f"{abs(latitude):.5f}° {north_south}, {abs(longitude):.5f}° {east_west}"


def exposure_label(exposure):
    if not exposure:
        return ""
    return exposure if exposure.endswith("s") else f"{exposure} s"
