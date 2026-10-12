#!/usr/bin/python3
import re
import sys
from pathlib import Path

BINARY_FIELDS = ("Package", "Version", "Architecture", "Maintainer", "Installed-Size", "Depends", "Recommends", "Suggests", "Section", "Priority", "Description")


def parse_paragraphs(text):
    paragraphs = []
    current = {}
    last_key = None
    for line in text.splitlines():
        if not line.strip():
            if current:
                paragraphs.append(current)
                current = {}
                last_key = None
            continue
        if line[0] in " \t" and last_key is not None:
            current[last_key] += "\n" + line
            continue
        key, _, value = line.partition(":")
        last_key = key.strip()
        current[last_key] = value.strip()
    if current:
        paragraphs.append(current)
    return paragraphs


def clean_relations(value):
    parts = [part.strip() for part in value.replace("\n", " ").split(",")]
    parts = [part for part in parts if part and not re.match(r"^\$\{.*\}$", part)]
    return ", ".join(parts)


def main():
    control_path, version, installed_size = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    source, binary = parse_paragraphs(control_path.read_text(encoding="utf-8"))[:2]
    fields = dict(binary)
    fields["Version"] = version
    fields["Installed-Size"] = installed_size
    fields["Maintainer"] = source["Maintainer"]
    fields.setdefault("Section", source.get("Section", "misc"))
    fields.setdefault("Priority", source.get("Priority", "optional"))
    for relation in ("Depends", "Recommends", "Suggests"):
        if relation in fields:
            fields[relation] = clean_relations(fields[relation])
    lines = []
    for name in BINARY_FIELDS:
        value = fields.get(name)
        if value:
            lines.append(f"{name}: {value}")
    sys.stdout.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
