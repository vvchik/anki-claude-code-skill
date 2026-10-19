import importlib.util
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_apkg = load("build_apkg", ROOT / ".claude/skills/anki/scripts/build_apkg.py")
generate = load("generate", ROOT / "examples/media-test/generate.py")


@pytest.fixture(scope="module")
def example(tmp_path_factory):
    out = tmp_path_factory.mktemp("media-test")
    spec_path = generate.generate(out)
    apkg = out / "media-test.apkg"
    assert build_apkg.main([str(spec_path), str(apkg)]) == 0
    return json.loads(spec_path.read_text()), apkg


def read_collection(apkg: Path, tmp_path: Path):
    collection = build_apkg.validate(apkg)["collection"]
    with zipfile.ZipFile(apkg) as zf:
        mapping = json.loads(zf.read("media"))
        payload = {name: zf.read(key) for key, name in mapping.items()}
        db = tmp_path / collection
        db.write_bytes(zf.read(collection))
    con = sqlite3.connect(db)
    try:
        notes = con.execute("SELECT guid, flds, tags FROM notes").fetchall()
        decks = json.loads(con.execute("SELECT decks FROM col").fetchone()[0])
    finally:
        con.close()
    return payload, notes, decks


def test_example_deck_contents(example, tmp_path):
    spec, apkg = example
    payload, notes, decks = read_collection(apkg, tmp_path)

    assert len(notes) == len(spec["notes"]) >= 6
    assert sorted(payload) == sorted(spec["media"])
    assert all(len(data) > 0 for data in payload.values())
    assert "ClaudeSkillTest::Media" in {d["name"] for d in decks.values()}
    assert all("claude-skill-test" in tags.split() for _, _, tags in notes)

    fields = "\x1f".join(f for _, f, _ in notes)
    for name in spec["media"]:
        assert name in fields


def test_example_has_mixed_media_field(example):
    spec, _ = example
    mixed = [
        v
        for n in spec["notes"]
        for v in n["fields"].values()
        if "<img" in v and "[sound:" in v
    ]
    assert mixed


def test_ids_are_stable_across_builds(tmp_path, example):
    spec, first = example
    spec_path = tmp_path / "deck.json"
    spec["media"] = [str(first.parent / m) for m in spec["media"]]
    spec_path.write_text(json.dumps(spec))
    second = tmp_path / "again.apkg"
    assert build_apkg.main([str(spec_path), str(second)]) == 0

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _, notes_a, decks_a = read_collection(first, tmp_path / "a")
    _, notes_b, decks_b = read_collection(second, tmp_path / "b")
    assert sorted(g for g, _, _ in notes_a) == sorted(g for g, _, _ in notes_b)
    assert set(decks_a) == set(decks_b)


def write_spec(tmp_path: Path, notes, media=()):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps({"deck": "T", "notes": notes, "media": list(media)}))
    return path


def basic(front, back="b"):
    return {"model": "Basic", "fields": {"Front": front, "Back": back}, "tags": []}


@pytest.mark.parametrize(
    "notes, media_files, message",
    [
        ([basic('<img src="nope.png">')], [], "nope.png"),
        ([basic("[sound:nope.mp3]")], [], "nope.mp3"),
        ([basic("q")], ["has space.png"], "unsafe media filename"),
        ([basic("q")], ["café.png"], "unsafe media filename"),
        ([{"model": "Cloze", "fields": {"Text": "no deletion"}}], [], "Cloze"),
        ([{"model": "Fancy", "fields": {"Front": "q"}}], [], "unknown model"),
        ([{"model": "Basic", "fields": {"Question": "q"}}], [], "unknown fields"),
        ([basic("q"), basic("q")], [], "same first field"),
    ],
)
def test_invalid_specs_fail(tmp_path, notes, media_files, message):
    for name in media_files:
        (tmp_path / name).write_bytes(b"x")
    spec = write_spec(tmp_path, notes, media_files)
    with pytest.raises(build_apkg.SpecError, match=message):
        build_apkg.build(spec, tmp_path / "out.apkg")


def test_duplicate_media_names_fail(tmp_path):
    for sub in ("a", "b"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "img.png").write_bytes(b"x")
    spec = write_spec(tmp_path, [basic('<img src="img.png">')], ["a/img.png", "b/img.png"])
    with pytest.raises(build_apkg.SpecError, match="duplicate media filename"):
        build_apkg.build(spec, tmp_path / "out.apkg")


@pytest.mark.parametrize(
    "front", ["<img src=nope.png>", "<IMG class=x SRC='nope.png'>"]
)
def test_unquoted_and_uppercase_img_refs_are_checked(tmp_path, front):
    with pytest.raises(build_apkg.SpecError, match="nope.png"):
        build_apkg.build(write_spec(tmp_path, [basic(front)]), tmp_path / "out.apkg")


@pytest.mark.parametrize(
    "note, message",
    [
        ("not a note", "must be an object"),
        ({"model": "Basic", "fields": {"Front": "q"}, "tags": "python"}, "tags must be a list"),
        ({"model": "Basic", "fields": {"Front": "q"}, "tags": [1]}, "tags must be"),
    ],
)
def test_malformed_notes_fail_cleanly(tmp_path, note, message):
    with pytest.raises(build_apkg.SpecError, match=message):
        build_apkg.build(write_spec(tmp_path, [note]), tmp_path / "out.apkg")


def test_remote_images_are_not_media(tmp_path):
    spec = write_spec(tmp_path, [basic('<img src="https://example.com/x.png">')])
    assert build_apkg.build(spec, tmp_path / "out.apkg")["notes"] == 1
