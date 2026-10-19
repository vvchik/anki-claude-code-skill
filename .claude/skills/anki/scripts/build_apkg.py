# /// script
# requires-python = ">=3.11"
# dependencies = ["genanki==0.13.1"]
# ///
"""Build an Anki .apkg from a JSON spec, then validate the result.

Usage: uv run build_apkg.py SPEC.json OUT.apkg

Spec format:
  {"deck": "Parent::Child",
   "notes": [{"model": "Basic" | "Basic (and reversed card)" | "Cloze",
              "fields": {...}, "tags": [...]}],
   "media": ["relative/or/absolute/path.png", ...]}

Relative media paths resolve against the spec file's directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

import genanki

# Anki matches note types by ID on import. The suffix keeps these apart from the
# user's own "Basic"/"Cloze" so an import never silently reuses a changed schema.
MODEL_SUFFIX = " (claude-anki)"

CSS = """.card {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 18px;
  line-height: 1.5;
  text-align: center;
  color: #1a1a1a;
  background-color: #fafafa;
}
.nightMode .card { color: #e0e0e0; background-color: #1a1a1a; }
img { max-width: 100%; height: auto; }
.cloze { font-weight: bold; color: #0a5fd8; }
"""

BASIC_TEMPLATE = {
    "name": "Card 1",
    "qfmt": "{{Front}}",
    "afmt": "{{FrontSide}}<hr id=answer>{{Back}}",
}
REVERSE_TEMPLATE = {
    "name": "Card 2",
    "qfmt": "{{Back}}",
    "afmt": "{{FrontSide}}<hr id=answer>{{Front}}",
}
CLOZE_TEMPLATE = {
    "name": "Cloze",
    "qfmt": "{{cloze:Text}}",
    "afmt": "{{cloze:Text}}<br>{{Extra}}",
}

MODELS = {
    "Basic": (["Front", "Back"], [BASIC_TEMPLATE], genanki.Model.FRONT_BACK),
    "Basic (and reversed card)": (
        ["Front", "Back"],
        [BASIC_TEMPLATE, REVERSE_TEMPLATE],
        genanki.Model.FRONT_BACK,
    ),
    "Cloze": (["Text", "Extra"], [CLOZE_TEMPLATE], genanki.Model.CLOZE),
}

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
IMG_SRC = re.compile(
    r"""<img\b[^>]*?\bsrc\s*=\s*(?:"([^"]+)"|'([^']+)'|([^\s"'>]+))""", re.IGNORECASE
)
SOUND = re.compile(r"\[sound:([^\]]+)\]")
CLOZE = re.compile(r"\{\{c\d+::")


class SpecError(Exception):
    pass


def stable_id(kind: str, name: str) -> int:
    # genanki recommends IDs in [2**30, 2**31); hashing the name keeps re-imports
    # pointing at the same deck/model instead of creating duplicates.
    digest = hashlib.sha256(f"{kind}:{name}".encode()).digest()
    return (1 << 30) + int.from_bytes(digest[:8], "big") % (1 << 30)


def build_model(key: str) -> genanki.Model:
    fields, templates, model_type = MODELS[key]
    name = key + MODEL_SUFFIX
    return genanki.Model(
        stable_id("model", name),
        name,
        fields=[{"name": f} for f in fields],
        templates=templates,
        css=CSS,
        model_type=model_type,
    )


def media_refs(text: str) -> set[str]:
    refs = set(SOUND.findall(text))
    for groups in IMG_SRC.findall(text):
        src = next(g for g in groups if g)
        if not re.match(r"^(https?:|data:)", src):
            refs.add(src)
    return refs


def load_spec(spec_path: Path) -> dict:
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SpecError(f"{spec_path}: invalid JSON: {exc}") from exc
    if not isinstance(spec.get("deck"), str) or not spec["deck"].strip():
        raise SpecError("spec.deck must be a non-empty string")
    if not isinstance(spec.get("notes"), list) or not spec["notes"]:
        raise SpecError("spec.notes must be a non-empty list")
    spec.setdefault("media", [])
    if not isinstance(spec["media"], list):
        raise SpecError("spec.media must be a list of paths")
    return spec


def resolve_media(spec: dict, base: Path) -> dict[str, Path]:
    by_name: dict[str, Path] = {}
    for raw in spec["media"]:
        path = Path(raw)
        if not path.is_absolute():
            path = base / path
        name = path.name
        if not SAFE_NAME.match(name):
            raise SpecError(
                f"unsafe media filename {name!r}: use only ASCII letters, digits, '.', '_', '-'"
            )
        if name in by_name:
            raise SpecError(f"duplicate media filename {name!r}")
        if not path.is_file():
            raise SpecError(f"media file not found: {path}")
        by_name[name] = path.resolve()
    return by_name


def build(spec_path: Path, out_path: Path) -> dict:
    spec = load_spec(spec_path)
    media = resolve_media(spec, spec_path.parent)
    deck_name = spec["deck"]
    deck = genanki.Deck(stable_id("deck", deck_name), deck_name)
    models: dict[str, genanki.Model] = {}
    referenced: set[str] = set()
    errors: list[str] = []
    seen_guids: dict[str, int] = {}

    for i, note in enumerate(spec["notes"], 1):
        if not isinstance(note, dict):
            errors.append(f"note {i}: must be an object")
            continue
        key = note.get("model")
        if key not in MODELS:
            errors.append(f"note {i}: unknown model {key!r}; expected one of {list(MODELS)}")
            continue
        field_names = MODELS[key][0]
        fields = note.get("fields") or {}
        if not isinstance(fields, dict):
            errors.append(f"note {i}: fields must be an object")
            continue
        extra = set(fields) - set(field_names)
        if extra:
            errors.append(f"note {i}: unknown fields {sorted(extra)} for {key!r}")
            continue
        values = [str(fields.get(f, "")) for f in field_names]
        if not values[0].strip():
            errors.append(f"note {i}: first field {field_names[0]!r} is empty")
            continue
        if key == "Cloze" and not CLOZE.search(values[0]):
            errors.append(f"note {i}: Cloze Text has no {{{{c1::...}}}} deletion")
            continue
        tags = note.get("tags") or []
        if not isinstance(tags, list):
            errors.append(f"note {i}: tags must be a list")
            continue
        bad_tags = [t for t in tags if not isinstance(t, str) or not t or re.search(r"\s", t)]
        if bad_tags:
            errors.append(f"note {i}: tags must be non-empty and contain no spaces: {bad_tags}")
            continue

        refs = set().union(*(media_refs(v) for v in values))
        missing = sorted(refs - set(media))
        if missing:
            errors.append(f"note {i}: references media not listed in spec.media: {missing}")
            continue
        referenced |= refs

        # Same deck + same first field = same note on re-import.
        guid = genanki.guid_for(deck_name, values[0])
        if guid in seen_guids:
            errors.append(f"note {i}: same first field as note {seen_guids[guid]}")
            continue
        seen_guids[guid] = i

        if key not in models:
            models[key] = build_model(key)
        deck.add_note(genanki.Note(model=models[key], fields=values, tags=tags, guid=guid))

    if errors:
        raise SpecError("\n".join(errors))

    unused = sorted(set(media) - referenced)
    if unused:
        print(f"warning: media not referenced by any note: {unused}", file=sys.stderr)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    package = genanki.Package(deck)
    package.media_files = [str(p) for p in media.values()]
    package.write_to_file(str(out_path))
    return {"deck": deck_name, "notes": len(deck.notes), "media": sorted(media)}


def validate(apkg_path: Path) -> dict:
    """Open the package the way Anki does and report what is inside."""
    with zipfile.ZipFile(apkg_path) as zf:
        names = set(zf.namelist())
        collection = next(
            (c for c in ("collection.anki21", "collection.anki2") if c in names), None
        )
        if collection is None:
            raise SpecError(f"{apkg_path}: no collection.anki2/anki21 in package")
        if "media" not in names:
            raise SpecError(f"{apkg_path}: no media mapping in package")
        mapping = json.loads(zf.read("media"))
        absent = sorted(k for k in mapping if k not in names)
        if absent:
            raise SpecError(f"{apkg_path}: media mapping points at missing entries {absent}")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / collection
            db_path.write_bytes(zf.read(collection))
            con = sqlite3.connect(db_path)
            try:
                notes = con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
                cards = con.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
                flds = [row[0] for row in con.execute("SELECT flds FROM notes")]
            finally:
                con.close()
    packaged = set(mapping.values())
    dangling = sorted(set().union(*(media_refs(f) for f in flds)) - packaged) if flds else []
    if dangling:
        raise SpecError(f"{apkg_path}: notes reference media not in package: {dangling}")
    return {
        "collection": collection,
        "notes": notes,
        "cards": cards,
        "media": sorted(packaged),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spec", type=Path, help="JSON spec file")
    parser.add_argument("out", type=Path, help="output .apkg path")
    args = parser.parse_args(argv)
    try:
        built = build(args.spec, args.out)
    except SpecError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        checked = validate(args.out)
        if checked["notes"] != built["notes"]:
            raise SpecError(f"built {built['notes']} notes but package holds {checked['notes']}")
    except SpecError as exc:
        # Never leave a package that failed validation where it could be imported.
        args.out.unlink(missing_ok=True)
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {args.out.resolve()}")
    print(f"  deck:  {built['deck']}")
    print(f"  notes: {checked['notes']} ({checked['cards']} cards) in {checked['collection']}")
    print(f"  media: {len(checked['media'])} file(s) {', '.join(checked['media'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
