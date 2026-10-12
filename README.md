<p align="center">
  <img src="data/icons/picnest-wordmark.svg" alt="Picnest" height="96">
</p>

<p align="center"><b>A fast, beautiful home for every photo and video on your computer.</b></p>

Picnest is a native GNOME gallery for Ubuntu. It automatically finds every photo and video on your internal SSDs, hard drives and connected external drives and shows them in one organized library, so you never have to dig through folders again.

Created by **Junaid Ul Hassan, Software Engineer**.

---

## Features

**Automatic discovery**
- Scans every internal drive, external drive and mounted partition, or only the folders you choose
- JPEG, PNG, WebP, HEIC/HEIF, AVIF, TIFF, BMP, GIF, SVG, JPEG XL and RAW (CR2, CR3, NEF, ARW, DNG, ORF, RW2, RAF and more)
- MP4, MKV, AVI, MOV, WebM, FLV, WMV, MPEG, M2TS, 3GP and more
- Real-time file watching: new, moved and deleted files appear or disappear without a restart
- Hot-plugged drives appear when connected and are hidden (not forgotten) when removed
- Turn individual drives off and on in Preferences without losing favorites or albums
- Incremental background indexing, so the interface never blocks

**Library**
- One mixed grid for photos and videos, virtualized for libraries of 100,000+ items
- Ctrl + scroll and pinch to zoom the grid, with animated density changes
- Muted video previews on hover and duration badges
- Days, months and years with sticky, collapsible headers, or a flat view
- Sort by date taken (EXIF), date modified, name, size or type
- Blurred placeholders while thumbnails load, generated in background threads and cached on disk

**Organization**
- Library, Memories ("On This Day", "This Week", "Best of"), Years, Favorites, Recents, Videos, Map
- Albums and Folders (grouped by drive)
- Smart collections: Screenshots, Panoramas, Live Photos, Duplicates and People (optional, local face detection)
- Hidden album with optional password protection
- Trash with restore and automatic deletion after 30 days (configurable)
- Storage insights per drive, year and file type

**Search**
- Instant search by name, folder, camera, lens, album, year and type
- Filters for media type, year, camera, album, orientation, file size, resolution, location and folder
- Search history and suggestions

**Viewer**
- Smooth open and close transition from the grid thumbnail
- Zoom, pan, rotate and flip, with swipe, arrow key and filmstrip navigation
- Info panel with camera, lens, exposure, resolution, size, GPS and date
- Video player with seek bar, volume, playback speed, fullscreen and picture-in-picture
- Slideshow mode

**Actions**
- Favorite, hide, move to trash, restore and delete permanently, with undo
- Multi-select with rubber-band drag, Shift and Ctrl selection, or per-section select
- Add to album, move, copy, rename, show in Files, export with format conversion and resizing
- Share by clipboard, Open With, or email, and set as wallpaper
- Basic editor: crop with aspect presets, rotate, flip, filters, brightness, contrast, saturation and warmth
- Drag and drop to and from other apps
- Keyboard shortcuts for every main action (press Ctrl + ? in the app)

## Requirements

- Ubuntu 24.04 LTS or newer, amd64
- GTK 4.14+, libadwaita 1.5+, Python 3.12+ (installed automatically by apt)

## Building the .deb package

```bash
sudo apt install dpkg-dev
./build-deb.sh
```

The package is written to `dist/picnest_1.0.4_amd64.deb`.

`build-deb.sh` uses `dpkg-buildpackage` with the `debian/` directory when debhelper is installed, and otherwise assembles the same layout with `dpkg-deb`. You can choose explicitly:

```bash
./build-deb.sh dpkg-buildpackage
./build-deb.sh dpkg-deb
```

To run the lint check as part of the build, install lintian first:

```bash
sudo apt install lintian debhelper
./build-deb.sh
```

## Installing

Double-click `dist/picnest_1.0.4_amd64.deb` in Files or Ubuntu Software, or run:

```bash
sudo apt install ./dist/picnest_1.0.4_amd64.deb
```

Picnest then appears in the app grid. On first launch, the welcome screen offers to scan all drives or only your Pictures and Videos folders.

## Removing

```bash
sudo apt remove picnest
sudo apt purge picnest
```

`remove` uninstalls the application. `purge` also removes the system-wide configuration in `/etc/picnest`. Your library database (`~/.local/share/picnest`) and thumbnail cache (`~/.cache/picnest`) are never deleted by the package. Remove them yourself if you no longer need them.

## Running from source

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gstreamer-1.0 \
    gir1.2-gst-plugins-base-1.0 gir1.2-gexiv2-0.10 python3-pil gstreamer1.0-libav
./bin/picnest
```

Set `PICNEST_DEBUG=1` for verbose logging. Set `PICNEST_SCAN_ROOTS=/path/one:/path/two` to scan only specific folders while developing.

## Project structure

```
picnest/
  main.py, application.py, config.py    entry point, application and actions
  core/                                 engine, no UI code
    database.py, models.py, query.py    SQLite index, item cache, view queries
    scanner.py, watcher.py, volumes.py  discovery, real-time watching, drives
    metadata.py, metadata_service.py    EXIF and video metadata in background threads
    thumbnails.py, video_frames.py      thumbnail generation, caching and loading
    library.py                          facade used by the UI
    trash.py, file_ops.py, editing.py   trash, file operations, editor pipeline
    duplicates.py, faces.py, security.py
  ui/
    window.py, context.py               main window, sidebar and navigation
    media_grid.py, grid_page.py         virtualized grid and the pages built on it
    viewer.py, image_canvas.py          viewer, zoom and transitions
    video_player.py, filmstrip.py, info_panel.py
    editor.py                           photo editor
    pages/                              library, albums, folders, years, memories, map, storage
    dialogs/                            preferences, welcome, shortcuts, common dialogs
data/
  icons/                                app icon (SVG, symbolic, PNG sizes) and wordmarks
  ui-icons/                             symbolic UI icon set
  style.css, *.desktop, *.metainfo.xml, *.gschema.xml, picnest.conf
debian/                                 Debian packaging
tools/                                  icon rendering and packaging helpers
build-deb.sh                            one-step package build
```

## Icons and logo

The Picnest logo is a colorful parrot perched on a branch inside a rounded-square icon. It ships as a full-color SVG, a symbolic SVG (`picnest-symbolic.svg`), PNG exports from 16 to 512 px, and light and dark wordmarks. Regenerate the PNGs and wordmarks with `tools/render-icons.py`.

Interface icons use the GNOME Adwaita symbolic set for standard window controls, plus a bundled set of symbolic icons from [Phosphor Icons](https://phosphoricons.com) (MIT) with a uniform weight and corner style. `tools/fetch-icons.py` regenerates them.

## License

MIT. See [LICENSE](LICENSE).

Picnest is created by **Junaid Ul Hassan, Software Engineer**.
