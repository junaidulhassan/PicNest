import logging
import os
from dataclasses import dataclass
from pathlib import Path

from picnest.config import SYSTEM_CONFIG_PATH

logger = logging.getLogger(__name__)

REAL_FILESYSTEMS = frozenset(
    {
        "ext2", "ext3", "ext4", "btrfs", "xfs", "zfs", "f2fs", "jfs", "reiserfs", "bcachefs",
        "ntfs", "ntfs3", "fuseblk", "vfat", "msdos", "exfat", "hfs", "hfsplus", "apfs", "udf",
        "iso9660", "nfs", "nfs4", "cifs", "smb3", "fuse.sshfs",
    }
)

SYSTEM_MOUNT_PREFIXES = (
    "/boot", "/efi", "/proc", "/sys", "/dev", "/run/user", "/run/snapd", "/snap", "/var",
    "/usr", "/etc", "/tmp", "/opt", "/srv", "/lib", "/bin", "/sbin", "/recovery",
)

DEFAULT_EXCLUDED_ROOT_DIRECTORIES = (
    "/bin", "/boot", "/dev", "/efi", "/etc", "/lib", "/lib32", "/lib64", "/libx32", "/lost+found",
    "/opt", "/proc", "/run", "/sbin", "/snap", "/srv", "/sys", "/tmp", "/usr", "/var", "/cdrom",
    "/root",
)

DEFAULT_EXCLUDED_NAMES = frozenset(
    {
        "node_modules", "__pycache__", "site-packages", "dist-packages", "venv", "virtualenv",
        "miniconda3", "anaconda3", "miniforge3", "snap", "lost+found", "$RECYCLE.BIN",
        "System Volume Information", "Windows", "Program Files", "Program Files (x86)",
        "ProgramData", "AppData", "$WinREAgent", "Recovery", "bower_components", "target",
        "DerivedData", "Pods", "vendor", "thumbnails",
    }
)


@dataclass(frozen=True)
class MountPoint:
    path: str
    device: str
    filesystem: str
    removable: bool
    label: str


def _decode_mount_field(value):
    return value.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")


def _is_removable_device(device):
    if not device.startswith("/dev/"):
        return False
    name = os.path.basename(os.path.realpath(device))
    block_name = name
    for candidate in (name, name.rstrip("0123456789"), name.split("p")[0] if name.startswith("nvme") else name):
        if os.path.exists(f"/sys/block/{candidate}"):
            block_name = candidate
            break
    try:
        with open(f"/sys/block/{block_name}/removable", encoding="ascii") as handle:
            if handle.read().strip() == "1":
                return True
    except OSError:
        pass
    try:
        device_link = os.path.realpath(f"/sys/block/{block_name}")
        return "/usb" in device_link
    except OSError:
        return False


def _label_for_mount(path):
    if path == "/":
        return "Computer"
    return os.path.basename(path.rstrip("/")) or path


def list_mount_points():
    mounts = []
    seen_paths = set()
    try:
        with open("/proc/self/mounts", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError as error:
        logger.warning("Cannot read mount table: %s", error)
        return [MountPoint("/", "", "", False, "Computer")]
    for line in lines:
        fields = line.split()
        if len(fields) < 3:
            continue
        device, mount_path, filesystem = fields[0], _decode_mount_field(fields[1]), fields[2]
        if filesystem not in REAL_FILESYSTEMS:
            continue
        if mount_path != "/" and any(mount_path == prefix or mount_path.startswith(prefix + "/") for prefix in SYSTEM_MOUNT_PREFIXES):
            continue
        if mount_path in seen_paths:
            continue
        if mount_path.startswith("/home") and mount_path != "/home" and not os.access(mount_path, os.R_OK):
            continue
        seen_paths.add(mount_path)
        mounts.append(MountPoint(mount_path, device, filesystem, _is_removable_device(device), _label_for_mount(mount_path)))
    if not any(mount.path == "/" for mount in mounts):
        mounts.insert(0, MountPoint("/", "", "", False, "Computer"))
    return mounts


def root_for_path(path, root_paths):
    best = "/"
    for root in root_paths:
        if (path == root or path.startswith(root.rstrip("/") + "/")) and len(root) > len(best):
            best = root
    return best


def read_system_configuration():
    excluded_paths = list(DEFAULT_EXCLUDED_ROOT_DIRECTORIES)
    excluded_names = set(DEFAULT_EXCLUDED_NAMES)
    config_path = Path(SYSTEM_CONFIG_PATH)
    if not config_path.exists():
        return excluded_paths, excluded_names
    section = None
    try:
        for raw_line in config_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip().lower()
                continue
            if section == "excluded-paths":
                excluded_paths.append(line.rstrip("/") or "/")
            elif section == "excluded-names":
                excluded_names.add(line)
    except OSError as error:
        logger.warning("Cannot read %s: %s", config_path, error)
    return excluded_paths, excluded_names
