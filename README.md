# Exam Forge

Exam Forge is a SlideMage plugin for creating and managing exam questions for a deck. The **Exam Forge…** command in the Deck menu opens a non-modal question-bank window that stays available when you switch decks. It requires SlideMage 9.6 or later.

## Question bank

**The bank contains answer keys and rationales.** Keep its folder private and back it up securely. Choose a private bank folder before saving anything. SlideMage remembers it by the deck's absolute path; a moved deck or a missing bank folder prompts you to choose again. Folders inside deck output or an unignored project directory require confirmation because questions and answers could be shared accidentally.

Create questions with answers and rationales, a learning objective, topic, difficulty and use. Revision-quiz review requires at least two answers and exactly one correct answer. ELP review requires five options with one to four correct. Use difficulty `easy` or `hard` for ELP assembly. Source slides are selected by view and language. The sidebar's **Write a question for this slide…** uses the slide in focus when pressed.

Every saved edit creates a draft revision, including edits to images or their alternative text. Reviewed revisions and their media stay available in the bank. Conflicts keep your typed fields and offer to save them as a separate question.

Source checks run in the background on open, on **Check now**, and before status changes. A changed, missing or unreadable source sets the separate **Source changed** marker; confirmation to continue does not silently replace its original fingerprint. **AI-assisted** provenance stays in every revision; **AI draft** applies while a question remains a draft.

**Export Moodle XML…** exports reviewed revision-quiz questions matching the current list filters. It excludes ELP questions, drafts and retired questions. Images are read from the bank and verified against their SHA-256 hashes. Question and answer text is plain text; inline mathematics uses `\( ... \)`. In Moodle, import using the Moodle XML format with its MathJax filter enabled. **Moodle import has yet to be verified**, including media, mathematics and grading.

## ELP exams and sessions

The window has **Bank**, **Exam** and **Sessions** tabs. In Sessions, refresh after a presentation ends. Presentation logging starts on first opening a bank, after plugin activation, and continues while the window is hidden. Switching bank folders or shutting down closes its subscription. Without a `presenter.events` provider the Sessions tab explains that logging is unavailable. An optional cached `course.sessions` schedule labels presentations and supplies estimated slide coverage.

Tick sessions to include their presentation logs in taught evidence; all are included initially.

On Exam, set exact topic counts, easy/hard counts, minutes and recent-exam avoidance. The Slides taught-only filter starts on. Easy questions use 12 points and 2.5 minutes; hard questions use 24 points and 5 minutes. Each proposal gets an automatic seed. Turn it off to remove the taught-only limit. Without a log, slide coverage uses the schedule and asks you to confirm or adjust the estimated last taught slide position. Both exclusion lists show their reasons. Infeasible blueprints keep their reported constraints; they are never relaxed. Swap from the eligible pool, then approve after the source check and changed-question list.

Approval opens the second issuing step. **Freeze approved exam** checks fresh sources again; changed sources stop issuing. Each issue creates a unique `*.manifest.json` in the bank folder. The frozen blueprint includes the seed, taught options, session IDs and confirmed cut-off. Open an issued manifest to inspect it or save its documents again. Later bank edits do not alter it.

The document save dialogs write two self-contained HTML files: an exam entry copy and a **private key with answers, rationales and the scoring sheet**. Math remains copyable source, not typeset. The item editor's teaching-assistant note carries shared stems, exclusions or fixed option order. **This note appears in the exam file: never put answers in it.** It is frozen verbatim and ignored by Moodle XML. Nothing is inferred from the key into the note.

**Corrections…** opens a separate sheet that appends item, change, reason and time to the manifest's `*.corrections.jsonl` journal. It leaves the manifest and generated documents unchanged. One sheet per journal and a file lock serialize writers; a busy journal asks you to retry. No student scoring, in-app media player or Moodle quiz generator is included.

Real-window dark-theme and player checks, frozen-build end-to-end operation, Moodle import (J2) and teaching-assistant document approval (J4) remain manual release checks.

## AI drafting

AI support is off by default in Exam Forge Settings. Enable it with an available chat route (for example Claude or Ollama) to show **Draft with AI…** in the Bank tab. Choose the slide view, language and question use, then explicitly tick slides. Nothing is selected initially. Select a row to inspect its text.

Image descriptions are never included in drafting requests. Image files, other bank questions and their keys or rationales, session names and student records are never added to the request. Use teaching sources free of student information; source text is sent verbatim.

The host handles the current chat route and model, confirms the full text before each cloud request, and never silently switches routes. Cancel or decline adds nothing. A malformed or truncated JSON reply is refused in full with a message. Valid replies enter the bank only as **Draft**, with the captured route/model and an amber **AI draft** label. Edit and **Mark reviewed** through the normal source check before using them. The permanent **AI-assisted** provenance survives acceptance and later edits; those edits start another draft revision. Chosen slide links are retained.

## Development

The plugin uses API 1 and imports only `slidemage.plugin_api`, the standard library, the supported PySide6 modules, and its own modules via relative imports. Register contributions in `activate(host)` without network access. This plugin declares no network contacts (`network = []`).

Run `QT_QPA_PLATFORM=offscreen python -m pytest -q` with SlideMage's plugin API installed. Run `python build.py --check` to validate the manifest, package, imports and changelog, or `python build.py` to create the `.smplugin` archive and SHA-256 in `dist/`. Set `SLIDEMAGE_PLUGIN_PATH` to this checkout to load it in SlideMage.

The build check validates the import allowlist but does not prove that every standard-library module is available in a frozen SlideMage. The frozen app excludes `tkinter`, `turtle`, `turtledemo`, `idlelib`, `test`, `ensurepip`, `lib2to3`, `pydoc_data`, `this`, `antigravity`, and private top-level modules except `_thread`. `slidemage --self-test --plugins` checks bundled imports and loads a minimal test plugin, not Exam Forge. Test Exam Forge separately in the frozen app.

Keep `CHANGELOG.md` in SlideMage's format, with increasing versions and a `NEW:` paragraph with bullets for each entry; a major version increase also needs a `BREAKING:` paragraph. The release workflow builds an archive from a matching version tag and derives release notes using `release_notes.py`. Published assets must not be replaced; publish fixes as a new version. Drafts and prereleases are not listed. After release, submit the plugin through the [SlideMage plugin submission page](https://slidemage.org/plugins/submit/).
