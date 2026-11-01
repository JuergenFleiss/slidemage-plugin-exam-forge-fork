# Exam Forge

Exam Forge is a SlideMage plugin for creating and managing exam questions for a deck. The **Exam Forge…** command in the Deck menu opens a non-modal question-bank window that stays available when you switch decks. It requires SlideMage 0.13.17 or later.

## Question bank

**The bank contains answer keys and rationales.** Keep its folder private and back it up securely. Choose a private bank folder before saving anything. SlideMage remembers it by the deck's absolute path; a moved deck or a missing bank folder prompts you to choose again. Folders inside deck output or an unignored project directory require confirmation because questions and answers could be shared accidentally.

The window has **Questions** and **Exam** tabs. On Questions, choose a topic, then a slide, to see the questions written for it. **Needs attention** lists questions whose slide changed and AI drafts still awaiting review.

Create questions with answers and rationales, a learning objective, difficulty (**Easy** or **Hard**) and use (**Revision quiz** or **ELP exam**). Revision-quiz review requires at least two answers and exactly one correct answer. ELP review requires five answers with one to four correct. Link source slides by view and language. The sidebar's **Write a question for this slide…** uses the slide in focus when pressed.

**Topics** are groups of slides, kept in `topics.json` in the bank folder. Each slide belongs to at most one topic, and a question's topic is the topic of its first linked slide. When you first open a bank that has older free-text topics, Exam Forge creates topics from them automatically and reports any conflicts once.

Marking a question reviewed requires at least one linked slide; questions that were already reviewed keep their status. Questions without a linked slide appear under **Unlinked questions** and are left out of exam counts until you link one.

Every saved edit creates a draft revision, including edits to images or their alternative text. Reviewed revisions and their media stay available in the bank. Conflicts keep your typed fields and offer to save them as a separate question. Leaving a question with unsaved edits asks whether to save, discard or cancel.

The bank file format is version 2. Version 1 files stay readable and are upgraded the next time they are written. **Older versions of Exam Forge cannot read an upgraded bank**, so update the plugin on every machine that uses the same bank folder. Exam manifests issued by older versions still open.

Source checks run in the background on open, on **Check slides**, and before status changes. A changed, missing or unreadable source sets the separate **Source changed** marker; confirmation to continue does not silently replace its original fingerprint.

**Review changes** (from the banner on a changed slide, or **Compare** in the editor) shows the old and new slide text side by side and one row for each affected question. **Still correct** keeps the question's status and records a new revision with the current slide as its baseline. **Edit** opens the question. **Update with AI** asks the AI for a revised version, which arrives as a draft; the question keeps its author origin, and the revision is permanently marked **AI-assisted**. For links made before slide text was stored, the dialog shows only the current text, with "Earlier text not stored". **AI-assisted** provenance stays in every revision; **AI draft** applies while a question remains a draft.

**Export to Moodle** exports every reviewed revision-quiz question. It excludes ELP questions, drafts and retired questions. Images are read from the bank and verified against their SHA-256 hashes. Question and answer text is plain text; inline mathematics uses `\( ... \)`. In Moodle, import using the Moodle XML format with its MathJax filter enabled. **Moodle import has yet to be verified**, including media, mathematics and grading.

## ELP exams

The **Exam** tab has three steps: counts, review and swap, freeze and save.

**Counts.** For each topic, set how many easy and how many hard questions you want, and choose how many recent exams to avoid. Proposals use all reviewed ELP questions and report eligibility exclusions with reasons. Easy questions use 12 points and 2.5 minutes; hard questions use 24 points and 5 minutes. The total questions, points and minutes follow from your counts. Each proposal gets an automatic seed. Infeasible blueprints keep their reported constraints; they are never relaxed.

**Review and swap.** Swap questions from the eligible pool. Approval is blocked while any picked question is linked to a changed slide; review or swap it first, there is no override. A fresh source check also runs when you approve.

**Freeze and save.** **Approve and freeze** checks fresh sources again; changed sources stop issuing. Each issue creates a unique `*.manifest.json` in the bank folder. The frozen blueprint includes the seed and selection constraints. Use **Open issued manifest…** to inspect it or save its documents again. Later bank edits do not alter it.

The save buttons write two self-contained HTML files, separately: an exam entry copy and a **private key with answers, rationales and the scoring sheet**. Math remains copyable source, not typeset. The item editor's teaching-assistant note carries shared stems, exclusions or fixed option order. **This note appears in the exam file: never put answers in it.** It is frozen verbatim and ignored by Moodle XML. The exam copy and the private key cannot be saved to the same file. Nothing is inferred from the key into the note.

**Record a correction…** opens a separate sheet that appends item, change, reason and time to the manifest's `*.corrections.jsonl` journal. It leaves the manifest and generated documents unchanged. One sheet per journal and a file lock serialize writers; a busy journal asks you to retry. No student scoring, in-app media player or Moodle quiz generator is included.

Real-window dark-theme and player checks, frozen-build end-to-end operation, Moodle import (J2) and teaching-assistant document approval (J4) remain manual release checks.

## AI drafting

AI support is off by default in Exam Forge Settings. Enable it with an available chat route (for example Claude or Ollama) to show **Draft with AI** on the Questions tab. Choose the slide view, language and question use, and set the number of questions (1–30). The slide you opened it from is ticked; you can tick others. Every slide that will be sent is visibly ticked, and only ticked slides are used. Select a row to inspect its text.

Image descriptions are never included in drafting requests. Image files, other bank questions and their answer keys or rationales, and student records are never added to the request. Use teaching sources free of student information; source text is sent verbatim.

The host handles the current chat route and model, confirms the full text before each cloud request, and never silently switches routes. Cancel or decline adds nothing. The AI does not propose a topic; a question's topic comes from its slide. A malformed or truncated JSON reply, a wrong question count, or a question without a supplied slide is refused in full with a message. Valid replies enter the bank only as **Draft**, with the captured route/model and an amber **AI draft** label. Edit and **Mark reviewed** (which needs a linked slide) through the normal source check before using them. The permanent **AI-assisted** provenance survives acceptance and later edits; those edits start another draft revision. Each draft links to the supplied slide identified in the reply.

**Update with AI** in Review changes is different: it sends one question's text, its answers, which answers are correct, its rationales, its learning objective and the current text of its linked slides. That is the answer key for that question, unlike Draft with AI, which never sends answer keys. The host shows the full request before anything is sent. The result is a draft for you to review.

## Slide text stored in the bank

Each linked slide stores a text snapshot of the slide, in the private bank folder and in issued manifests, so that Review changes can show what the slide said before. Treat these files like the rest of the bank. Links made earlier show "Earlier text not stored".

## Development

The plugin uses API 1 and imports only `slidemage.plugin_api`, the standard library, the supported PySide6 modules, and its own modules via relative imports. Register contributions in `activate(host)` without network access. This plugin declares no network contacts (`network = []`).

Run `QT_QPA_PLATFORM=offscreen python -m pytest -q` with SlideMage's plugin API installed. Run `python build.py --check` to validate the manifest, package, imports and changelog, or `python build.py` to create the `.smplugin` archive and SHA-256 in `dist/`. Set `SLIDEMAGE_PLUGIN_PATH` to this checkout to load it in SlideMage.

The build check validates the import allowlist but does not prove that every standard-library module is available in a frozen SlideMage. The frozen app excludes `tkinter`, `turtle`, `turtledemo`, `idlelib`, `test`, `ensurepip`, `lib2to3`, `pydoc_data`, `this`, `antigravity`, and private top-level modules except `_thread`. `slidemage --self-test --plugins` checks bundled imports and loads a minimal test plugin, not Exam Forge. Test Exam Forge separately in the frozen app.

Keep `CHANGELOG.md` in SlideMage's format, with increasing versions and a `NEW:` paragraph with bullets for each entry; a major version increase also needs a `BREAKING:` paragraph. The release workflow builds an archive from a matching version tag and derives release notes using `release_notes.py`. Published assets must not be replaced; publish fixes as a new version. Drafts and prereleases are not listed. After release, submit the plugin through the [SlideMage plugin submission page](https://slidemage.org/plugins/submit/).
