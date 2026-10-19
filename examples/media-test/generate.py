# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow==13.0.0"]
# ///
"""Generate media for the ClaudeSkillTest::Media deck and a buildable spec.

Usage: uv run generate.py [OUT_DIR]   (default: ./build next to this file)

Writes OUT_DIR/deck.json plus its media. Build the package with:
  uv run ../../.claude/skills/anki/scripts/build_apkg.py OUT_DIR/deck.json OUT_DIR/media-test.apkg

Audio, best available first: macOS `say` -> mp3 (ffmpeg) or m4a (afconvert);
without `say`, a sine tone as mp3 (ffmpeg) or wav (stdlib). The video note is
dropped when ffmpeg is missing.
"""

from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "deck.template.json"
PREFIX = "claude-skill-test"
PHRASE = "Anki media test"


def run(*cmd: str) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def make_image(out_dir: Path) -> str:
    name = f"{PREFIX}-shapes.png"
    img = Image.new("RGB", (320, 200), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((110, 30, 210, 130), fill="#f28c28")
    draw.ellipse((130, 50, 190, 110), fill="#1f6fd1")
    draw.text((160, 165), PHRASE, fill="black", anchor="mm", font=ImageFont.load_default(18))
    img.save(out_dir / name)
    return name


def write_tone_wav(path: Path, seconds: float = 1.0, hz: float = 440.0) -> None:
    rate = 22050
    frames = b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * hz * i / rate)))
        for i in range(int(rate * seconds))
    )
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)


def make_audio(out_dir: Path) -> str:
    ffmpeg = shutil.which("ffmpeg")
    with tempfile.TemporaryDirectory() as tmp:
        if shutil.which("say"):
            src = Path(tmp) / "voice.aiff"
            run("say", "-o", str(src), PHRASE)
        else:
            src = Path(tmp) / "tone.wav"
            write_tone_wav(src)
        if ffmpeg:
            name = f"{PREFIX}-voice.mp3"
            try:
                run(ffmpeg, "-y", "-loglevel", "error", "-i", str(src), "-ac", "1",
                    "-b:a", "64k", str(out_dir / name))
                return name
            except subprocess.CalledProcessError:
                pass  # e.g. an ffmpeg build without an mp3 encoder
        if src.suffix == ".aiff" and shutil.which("afconvert"):
            name = f"{PREFIX}-voice.m4a"
            run("afconvert", "-f", "m4af", "-d", "aac", str(src), str(out_dir / name))
            return name
        if src.suffix == ".aiff":
            write_tone_wav(src := Path(tmp) / "tone.wav")
        name = f"{PREFIX}-voice.wav"
        shutil.copyfile(src, out_dir / name)
    return name


def make_video(out_dir: Path) -> str | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    name = f"{PREFIX}-clip.mp4"
    base = [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
            "-i", "color=c=green:s=320x180:d=2", "-pix_fmt", "yuv420p"]
    # libx264 plays everywhere Anki runs; mpeg4 is the fallback for builds without it.
    for codec in ("libx264", "mpeg4"):
        try:
            run(*base, "-c:v", codec, str(out_dir / name))
            return name
        except subprocess.CalledProcessError:
            continue
    return None


def substitute(value, tokens: dict[str, str]):
    if isinstance(value, str):
        for token, name in tokens.items():
            value = value.replace(token, name)
        return value
    if isinstance(value, list):
        return [substitute(v, tokens) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, tokens) for k, v in value.items()}
    return value


def generate(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    tokens = {"@IMAGE@": make_image(out_dir), "@AUDIO@": make_audio(out_dir)}
    video = make_video(out_dir)
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    if video:
        tokens["@VIDEO@"] = video
    else:
        print("ffmpeg not found or failed: skipping the video note", file=sys.stderr)
        template["notes"] = [n for n in template["notes"] if "@VIDEO@" not in json.dumps(n)]
        template["media"] = [m for m in template["media"] if m != "@VIDEO@"]
    spec = substitute(template, tokens)
    spec_path = out_dir / "deck.json"
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return spec_path


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "build"
    path = generate(out)
    spec = json.loads(path.read_text(encoding="utf-8"))
    print(f"wrote {path} ({len(spec['notes'])} notes, media: {', '.join(spec['media'])})")
