#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

PACKAGE="picnest"
ARCHITECTURE="amd64"
VERSION="$(dpkg-parsechangelog -S Version)"
OUTPUT_DIRECTORY="$PROJECT_ROOT/dist"
DEB_NAME="${PACKAGE}_${VERSION}_${ARCHITECTURE}.deb"
METHOD="${1:-auto}"

log() {
    printf '\033[1;35m==>\033[0m %s\n' "$*"
}

clean_sources() {
    find "$PROJECT_ROOT/picnest" -type d -name __pycache__ -prune -exec rm -rf {} +
}

build_with_dpkg_buildpackage() {
    log "Building with dpkg-buildpackage"
    dpkg-buildpackage -b -us -uc --no-sign
    mv "../${DEB_NAME}" "$OUTPUT_DIRECTORY/"
    rm -f "../${PACKAGE}_${VERSION}_${ARCHITECTURE}.buildinfo" "../${PACKAGE}_${VERSION}_${ARCHITECTURE}.changes"
    dh_clean >/dev/null 2>&1 || true
}

install_entry() {
    local source="$1" destination="$2" stage="$3"
    mkdir -p "$stage/$destination"
    if [ -d "$source" ]; then
        cp -a "$source" "$stage/$destination/"
    else
        install -m 0644 "$source" "$stage/$destination/"
    fi
}

build_with_dpkg_deb() {
    log "Building with dpkg-deb"
    local stage
    stage="$(mktemp -d)"
    trap 'rm -rf "$stage"' EXIT
    while read -r source destination; do
        [ -z "${source:-}" ] && continue
        install_entry "$source" "$destination" "$stage"
    done < debian/install
    find "$stage" -type d -name __pycache__ -prune -exec rm -rf {} +

    install -d "$stage/usr/share/man/man1" "$stage/usr/share/doc/$PACKAGE"
    gzip -9nc debian/picnest.1 > "$stage/usr/share/man/man1/picnest.1.gz"
    gzip -9nc debian/changelog > "$stage/usr/share/doc/$PACKAGE/changelog.gz"
    install -m 0644 debian/copyright "$stage/usr/share/doc/$PACKAGE/copyright"

    find "$stage" -type d -exec chmod 0755 {} +
    find "$stage" -type f -exec chmod 0644 {} +
    chmod 0755 "$stage/usr/bin/picnest"

    install -d -m 0755 "$stage/DEBIAN"
    local installed_size
    installed_size="$(du -sk --apparent-size "$stage" | cut -f1)"
    python3 -I tools/binary-control.py debian/control "$VERSION" "$installed_size" > "$stage/DEBIAN/control"
    for script in postinst prerm postrm; do
        if [ -f "debian/${PACKAGE}.${script}" ]; then
            sed '/^#DEBHELPER#$/d' "debian/${PACKAGE}.${script}" > "$stage/DEBIAN/${script}"
            chmod 0755 "$stage/DEBIAN/${script}"
        fi
    done
    (cd "$stage" && find etc -type f -printf '/%p\n' 2>/dev/null | sort) > "$stage/DEBIAN/conffiles"
    [ -s "$stage/DEBIAN/conffiles" ] || rm -f "$stage/DEBIAN/conffiles"
    [ -f "$stage/DEBIAN/conffiles" ] && chmod 0644 "$stage/DEBIAN/conffiles"
    (cd "$stage" && find . -path ./DEBIAN -prune -o -type f -printf '%P\n' | LC_ALL=C sort | xargs -d '\n' md5sum) > "$stage/DEBIAN/md5sums"
    chmod 0644 "$stage/DEBIAN/control" "$stage/DEBIAN/md5sums"

    dpkg-deb --root-owner-group -Zxz --build "$stage" "$OUTPUT_DIRECTORY/$DEB_NAME" >/dev/null
    rm -rf "$stage"
    trap - EXIT
}

run_lintian() {
    if command -v lintian >/dev/null 2>&1; then
        log "Running lintian"
        lintian --info --display-info --pedantic "$OUTPUT_DIRECTORY/$DEB_NAME" || true
    else
        log "lintian is not installed, skipping the lint check (sudo apt install lintian)"
    fi
}

main() {
    mkdir -p "$OUTPUT_DIRECTORY"
    rm -f "$OUTPUT_DIRECTORY/$DEB_NAME"
    clean_sources
    case "$METHOD" in
        dpkg-buildpackage)
            build_with_dpkg_buildpackage
            ;;
        dpkg-deb)
            build_with_dpkg_deb
            ;;
        auto)
            if command -v dh >/dev/null 2>&1 && dpkg-checkbuilddeps >/dev/null 2>&1; then
                build_with_dpkg_buildpackage
            else
                build_with_dpkg_deb
            fi
            ;;
        *)
            echo "Usage: $0 [auto|dpkg-buildpackage|dpkg-deb]" >&2
            exit 2
            ;;
    esac
    log "Package created: $OUTPUT_DIRECTORY/$DEB_NAME"
    dpkg-deb --info "$OUTPUT_DIRECTORY/$DEB_NAME" | sed -n '/Package:/,$p'
    run_lintian
    log "Install with: sudo apt install $OUTPUT_DIRECTORY/$DEB_NAME"
}

main
