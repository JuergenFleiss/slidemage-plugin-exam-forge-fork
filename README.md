# Exam Forge

Exam Forge is a SlideMage plugin for creating and managing exam questions for a deck. The **Exam Forge…** command in the Deck menu opens a non-modal question-bank window that stays available when you switch decks. It requires SlideMage 9.5 or later.

## Question bank

Choose a private bank folder before saving anything. SlideMage remembers it by the deck's absolute path; a moved deck or a missing bank folder prompts you to choose again. Folders inside deck output or an unignored project directory require confirmation because questions and answers could be shared accidentally.

Create questions with answers and rationales, a learning objective, topic, difficulty and use. Phase 1 review requires at least two answers and exactly one correct answer. Source slides are selected by view and language. The sidebar's **Write a question for this slide…** uses the slide in focus when pressed.

Every saved edit creates a draft revision, including edits to images or their alternative text. Reviewed revisions and their media stay available in the bank. Conflicts keep your typed fields and offer to save them as a separate question.

Source checks run in the background on open, on **Check now**, and before status changes. A changed, missing or unreadable source sets the separate **Source changed** marker; confirmation to continue does not silently replace its original fingerprint. **AI-assisted** provenance stays in every revision; **AI draft** applies while a question remains a draft.

**Export Moodle XML…** exports reviewed revision-quiz questions matching the current list filters. It excludes ELP questions, drafts and retired questions. Images are read from the bank and verified against their SHA-256 hashes. Question and answer text is plain text; inline mathematics uses `\( ... \)`. In Moodle, import using the Moodle XML format with its MathJax filter enabled. A real Moodle import and desktop light/dark-theme check remain release checks.

The Bank view leaves room for future phases; exam, sessions and quiz tabs are not implemented.

## Development

The plugin uses API 1 and imports only `slidemage.plugin_api`, the standard library, the supported PySide6 modules, and its own modules via relative imports. Register contributions in `activate(host)` without network access.

Run `QT_QPA_PLATFORM=offscreen python -m pytest -q` with SlideMage's plugin API installed. Run `python build.py --check` to validate the manifest, package, imports and changelog, or `python build.py` to create the `.smplugin` archive and SHA-256 in `dist/`. Set `SLIDEMAGE_PLUGIN_PATH` to this checkout to load it in SlideMage.

The release workflow builds an archive from a version tag and derives release notes from `CHANGELOG.md` using `release_notes.py`.
