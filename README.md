# anki-claude-code-skill

A Claude Code skill for creating and managing Anki flashcards via [AnkiConnect](https://github.com/FooSoft/anki-connect). Applies evidence-based flashcard science (Wozniak's 20 Rules, Matuschak's prompt-writing principles) to every card it generates.

## Setup

### 1. Install AnkiConnect

1. Open Anki desktop ([download](https://apps.ankiweb.net/))
2. Go to **Tools > Add-ons > Get Add-ons**
3. Enter code: `2055492159`
4. Restart Anki

### 2. Use the skill

Make sure Anki is running, then invoke the skill in Claude Code from this repo:

```
/anki                          # interactive — asks what you want to do
/anki <text or topic>          # generate cards from material
/anki review Python::Basics    # browse existing cards
/anki add Python::Algorithms   # add cards to a specific deck
```

## What it does

- **Creates flashcards** from any source material with automatic quality checks
- **Interactive review** — approve, edit, or reject cards before they're added
- **Full deck management** — create, list, delete, reorganize decks with hierarchical naming
- **Browse & search** — find cards by deck, tag, or content
- **Edit & maintain** — update cards, manage tags, suspend/unsuspend
- **Media support** — images and audio on cards
- **Syncs naturally** — cards go directly into your Anki desktop app via AnkiConnect
- **.apkg fallback** — if Anki is not running, or the skill runs in a sandbox without `localhost`, it builds an importable `.apkg` file instead

## How it works

The skill talks to Anki's REST API (AnkiConnect on `localhost:8765`) using `curl`. This path needs no Python packages and no dependencies beyond Anki and AnkiConnect.

The `.apkg` fallback runs `scripts/build_apkg.py` with [uv](https://docs.astral.sh/uv/). The script declares its own dependency ([genanki](https://github.com/kerrickstaley/genanki)), so `uv run` installs nothing globally:

```bash
uv run .claude/skills/anki/scripts/build_apkg.py cards.json cards.apkg
```

To import the file, use File > Import in Anki desktop, or the AnkiConnect `importPackage` action followed by `sync`. The spec format is in `SKILL.md`. Deck IDs, note type IDs, and note GUIDs are hashes of their names, so a second import of the same spec updates notes and does not add duplicates.

Cards are generated following evidence-based principles:

- **One atom per card** — each card tests exactly one thing
- **Precise cues** — every question has one unambiguous answer
- **Cloze-first** — cloze deletions for factual knowledge, basic Q&A for conceptual
- **Context-free** — every card stands alone with topic tags
- **No enumerations** — lists are broken into individual connected cards

## Skill structure

```
.claude/skills/anki/
├── SKILL.md                     # Workflow logic and entry points
├── scripts/
│   └── build_apkg.py            # JSON spec -> validated .apkg (fallback path)
└── reference/
    ├── ankiconnect-api.md       # Full AnkiConnect API reference
    ├── card-science.md          # Flashcard quality rules and principles
    └── card-templates.md        # HTML/CSS templates and media handling
```

## Media test deck

`examples/media-test/` makes a `ClaudeSkillTest::Media` deck with an image, an audio clip, and an optional video. Use it to confirm that media survives the trip from the package to AnkiWeb:

```bash
uv run examples/media-test/generate.py
uv run .claude/skills/anki/scripts/build_apkg.py examples/media-test/build/deck.json examples/media-test/build/media-test.apkg
```

On macOS the audio is speech from `say`. On other systems it is a sine tone. The video note needs `ffmpeg`. If `ffmpeg` is missing, the generator skips that note.

## Tests

```bash
uv run pytest
```

The tests build the example deck and inspect the zip and sqlite contents. They do not need Anki.

## Requirements

- [Anki desktop](https://apps.ankiweb.net/) (running)
- [AnkiConnect](https://ankiweb.net/shared/info/2055492159) plugin installed
- [Claude Code](https://claude.ai/claude-code)
- [uv](https://docs.astral.sh/uv/), only for the `.apkg` fallback

## License

MIT
