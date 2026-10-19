---
name: anki
description: >
  Create, browse, edit, and manage Anki flashcards and decks via AnkiConnect,
  or build an importable .apkg deck file when Anki is not running. Use whenever
  the user mentions Anki, flashcards, spaced repetition, cloze cards, or .apkg
  files, or wants to turn notes, docs, or a conversation into study cards, even
  if they do not name Anki. Also use to review, edit, tag, or reorganize
  existing decks. Applies evidence-based flashcard science automatically.
argument-hint: "[text, file path, or deck name]"
allowed-tools: Bash(curl *) Bash(uv run *) Read Write
---

# Anki Skill

Manage Anki flashcards and decks through AnkiConnect's REST API. This skill applies evidence-based flashcard science to every card it creates.

## Prerequisites

- Anki desktop must be running
- AnkiConnect plugin installed (Tools > Add-ons > Get Add-ons > code `2055492159`)
- AnkiConnect listens on `http://localhost:8765`

## AnkiConnect Communication

All API calls use this pattern:

```bash
curl -s localhost:8765 -X POST -d '{"action":"<action>","version":6,"params":{...}}'
```

Every response has `{"result": ..., "error": ...}`. Check `error` first — if non-null, the operation failed.

### Health Check

Before any operation, verify AnkiConnect is reachable:

```bash
curl -s localhost:8765 -X POST -d '{"action":"version","version":6}'
```

If this fails, tell the user: "Anki desktop needs to be open with AnkiConnect installed (add-on code 2055492159)." Then offer two choices: start Anki and retry, or build the cards as an `.apkg` file (see [.apkg Fallback](#apkg-fallback)). The fallback also applies when the skill runs in a sandbox that cannot reach `localhost`.

## Entry Points

Determine the mode from how the user invokes the skill:

| Invocation | Mode |
|------------|------|
| `/anki` | Ask what they want to do |
| `/anki <text or topic>` | Card creation from that material |
| `/anki review <deck>` | Browse existing cards in that deck |
| `/anki add <deck>` | Create new cards targeting that deck |

## Workflows

### Card Creation Session

This is the primary workflow. Read `reference/card-science.md` before generating any cards.

1. **Health check** — verify AnkiConnect is reachable
2. **Determine target deck** — ask user or infer from arguments
   - Use hierarchical naming: `Parent::Child::Grandchild`
   - If deck doesn't exist, offer to create it (including parent decks)
3. **Ingest source material** — read file, accept pasted text, or build from conversation
4. **Determine note type** — check what models exist in Anki:
   ```bash
   curl -s localhost:8765 -X POST -d '{"action":"modelNames","version":6}'
   ```
   Use the user's existing note types when possible. Default to `Basic`, `Basic (and reversed card)`, or `Cloze` as appropriate.
5. **Get model fields** — before creating notes, check the field names:
   ```bash
   curl -s localhost:8765 -X POST -d '{"action":"modelFieldNames","version":6,"params":{"modelName":"Basic"}}'
   ```
6. **Generate candidate cards** — apply the 7 Laws from card-science.md. Produce 5-10 cards per batch.
7. **Present cards for review** — display clearly:
   ```
   Card 1 [Basic] — Deck: Python::Data Structures
   Q: What is the time complexity of Python dict lookup?
   A: O(1) average case — dict uses a hash table internally
   Tags: python, data-structures, complexity

   Card 2 [Cloze] — Deck: Python::Data Structures
   {{c1::Hash collisions}} cause dict lookup to degrade to O(n) in the worst case
   Tags: python, data-structures, complexity
   ```
8. **Process user feedback** — accept these response patterns:
   - "accept all" / "looks good" / "send them" → add all cards
   - "reject 3" / "drop 3 and 5" → remove specific cards
   - "edit 2: change question to ..." → modify before adding
   - Per-card feedback in any natural format
9. **Push approved cards to Anki**:
   ```bash
   curl -s localhost:8765 -X POST -d '{
     "action": "addNote",
     "version": 6,
     "params": {
       "note": {
         "deckName": "Python::Data Structures",
         "modelName": "Basic",
         "fields": {"Front": "...", "Back": "..."},
         "tags": ["python", "data-structures"]
       }
     }
   }'
   ```
   For bulk adds, use `addNotes` with an array.
10. **Report results** — "Added 8 cards to Python::Data Structures. 1 duplicate skipped."
11. **Continue or finish** — "More cards from this material, or done?"

### Deck Management

- **List decks**: `deckNames`
- **Create deck**: `createDeck` with params `{"deck": "Parent::Child"}`
  - AnkiConnect auto-creates parent decks
- **Delete deck**: `deleteDecks` with params `{"decks": ["name"], "cardsToo": true}`
  - ALWAYS confirm with user before deleting
- **Move cards**: `changeDeck` with params `{"cards": [id,...], "deck": "target"}`

### Browse & Search

- **Find notes**: `findNotes` with query params (uses Anki's search syntax)
  - `"deck:Python"` — all notes in deck
  - `"tag:algorithms"` — by tag
  - `"front:*hash*"` — by content
- **Get note details**: `notesInfo` with array of note IDs
- **Find cards**: `findCards` with query, then `cardsInfo` for details
- Present results in a readable table format

### Edit & Maintain

- **Update fields**: `updateNoteFields` with note ID and new field values
- **Add/remove tags**: `addTags` / `removeTags` with note IDs and space-separated tags
- **Suspend/unsuspend**: `suspend` / `unsuspend` with card IDs
- When editing, show the current card content first, then apply changes

### Media

Read `reference/card-templates.md` for media formatting details.

- **Store media**: `storeMediaFile` with filename and base64-encoded data
- **Reference in cards**:
  - Images: `<img src="filename.jpg">`
  - Audio: `[sound:filename.mp3]`
- Media filenames must be unique across the collection

### .apkg Fallback

Use this path when the health check fails or the user asks for a file. The card creation workflow stays the same up to step 8. Only the push step changes. AnkiConnect calls stay curl-only. Python runs only here, through `uv`, so nothing is installed globally.

1. Write the approved cards to a JSON spec. Relative `media` paths resolve against the spec's folder:
   ```json
   {
     "deck": "Python::Data Structures",
     "notes": [
       {"model": "Basic", "fields": {"Front": "...", "Back": "..."}, "tags": ["python"]},
       {"model": "Basic (and reversed card)", "fields": {"Front": "...", "Back": "..."}, "tags": []},
       {"model": "Cloze", "fields": {"Text": "{{c1::...}}", "Extra": "..."}, "tags": []}
     ],
     "media": ["python-bst-diagram.png"]
   }
   ```
2. Build and validate the package. The script is in `scripts/` next to this file:
   ```bash
   uv run <skill-dir>/scripts/build_apkg.py cards.json cards.apkg
   ```
   The script stops with an error if a field references an `<img src>` or `[sound:]` file that is not in `media`. It also stops on media filenames with characters other than ASCII letters, digits, `.`, `_`, and `-`. After the build, it opens the zip, reads the media mapping, and counts notes with sqlite.
3. Tell the user how to import the file:
   - Anki desktop: File > Import, then select the `.apkg`.
   - AnkiConnect, when Anki is running: `importPackage` with an absolute path, then `sync`.

Deck IDs, note type IDs, and note GUIDs come from hashes of the deck name, the model name, and the first field. If the user imports the same spec again, Anki updates the notes and does not add duplicates. Note types import as `Basic (claude-anki)`, `Basic (and reversed card) (claude-anki)`, and `Cloze (claude-anki)`. They do not change the user's own note types.

## Error Handling

| Error | Response |
|-------|----------|
| Connection refused | "Anki desktop needs to be running with AnkiConnect installed." Offer the .apkg fallback. |
| Deck not found | "Deck 'X' doesn't exist. Create it?" |
| Duplicate note | Report which cards were skipped, continue with the rest |
| Model not found | List available models, ask user to pick one |
| Empty fields | Don't send — fix the card first |

## Quality Rules (Summary)

When generating cards, always apply these rules. Full details in `reference/card-science.md`:

1. **Understand First** — parse the material fully before creating cards
2. **One Atom Per Card** — one fact, one concept, one connection
3. **No Orphans** — every card connects to something the user cares about
4. **Precise Cues** — one unambiguous answer per card
5. **No Enumerations** — break lists into individual cards
6. **Context-Free** — each card stands alone, use topic prefixes
7. **Cloze When Possible** — prefer cloze for factual knowledge

Self-review every card against these rules before presenting to the user.
