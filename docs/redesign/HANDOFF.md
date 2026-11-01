# Exam Forge UI redesign — handoff for Claude Code

The interactive prototype is `docs/redesign/prototype.dc.html`; its `initialState()` holds sample data, and its `renderVals()` and handlers hold the behaviour. The prototype is a reference for layout, wording and flows. **Do not port its colours or HTML.** Build it in PySide6 using the host widgets this plugin already uses: `host.Page`, `host.Field`, `host.Button(kind, compact)`, `host.Chip(tone)`. Keep inheriting the host theme, including dark mode.

The goal: make it as simple and clean as possible. Questions belong to slides, slides belong to topics, and everything else follows from that.

## Constraints that do not change

- Plugin API 1. Imports are limited to `slidemage.plugin_api`, the stdlib, the supported PySide6 modules, and relative imports. `network = []`.
- Keep the bank's revision lifecycle exactly: edits create draft revisions; the status cycle is draft → reviewed → retired → draft; source checks run on the host worker; conflicts keep the typed fields.
- Keep every privacy rule in README.md: a private bank folder; AI requests carry only the text of slides the user ticked; the host confirms each request; replies are refused in full when invalid; AI provenance (`Origin`) is permanent; the TA note warning stays.
- Keep `accent()` semantics: **violet = source changed**, **amber = AI draft**. Text colour is the only explicit colour; surfaces inherit the theme.
- Run `QT_QPA_PLATFORM=offscreen python -m pytest -q` and `python build.py --check` after each step. Update tests rather than deleting coverage. Add a CHANGELOG entry (new minor version, with `NEW:` bullets).

## Information architecture

One window with two tabs: **Questions** and **Exam**. The window title is `Exam Forge · <deck>`. The top bar holds **Check slides** (was "Check now") and **Export to Moodle** (was "Export Moodle XML…"). A thin footer shows a lock icon, the private bank folder path, and "Slides checked HH:MM · N changed".

### Questions tab: three columns (QSplitter)

1. **Topics** (~250 px)
   - *All slides*, then one row per topic. Each row has a colour dot, the name, "N slides · M questions", a dark badge counting items that need attention, and a pencil button that opens the topic editor.
   - *No topic* appears only when some slides have no topic.
   - **+ New topic**.
   - Under the label "Review": **Needs attention**, which lists every question whose source changed and every unreviewed AI draft. Its subtitle reads "N changed slides · M AI drafts".
2. **Slides** (~320 px)
   - The slides of the selected topic, in deck order (`host.deck_slides`). Each row shows a small thumbnail box with the slide position and a topic dot, the title, "N questions" (or "No questions yet"), a violet *Changed* chip, and an amber *N AI drafts* chip.
3. **Questions for the selected slide** (flex)
   - Header: topic · slide N, then the title, plus an **Open slide** button (`host.open_slide`).
   - A violet banner when questions on the slide have changed sources: "This slide changed after N questions were written. [Review changes]".
   - **Draft with AI** (sparkle icon, amber) and **Write question** (primary). Both start from this slide.
   - Question cards, with retired questions last and dimmed. A card shows the stem, then chips for status (Reviewed in green, Draft neutral, Retired muted), *Revision quiz*/*ELP exam*, *Easy*/*Hard*, *AI draft*, *Slide changed*, and "Also on slide 6, 8". The answer summary sits on the right: "3 answers", or for ELP "5 answers · 3 correct".
   - The empty state offers both actions.

Clicking a card replaces column 3 with the **editor**, which has a back link: "‹ Slide 7 · Bayes' theorem".

### Editor (replaces `ItemEditor`)

Fields, top to bottom:

- **Question**: plain text with the placeholder `\( … \)`.
- **Answers**: one row per option.
  - A round tick button marks the option correct. A revision quiz acts like a radio group; ELP lets you tick several.
  - The answer text, then a remove button.
  - A second, quieter line for the rationale, with the placeholder "Why it is right or wrong · only in the private key".
  - The rule hint sits beside the header: "Revision quiz: exactly one correct answer" or "ELP exam: exactly five answers, one to four correct".
  - Switching to ELP pads the list to five empty answers. ELP caps answers at five; revision quizzes at eight.
- **Used for**: a two-option switch, Revision quiz | ELP exam.
- **Difficulty**: a two-option switch, Easy | Hard. This replaces the free-text difficulty field; existing values that are neither easy nor hard show as unselected.
- **Learning objective**.
- **Linked slides**: chips "Slide N · title" with a remove button each. **+ Link another slide** opens an inline grid of the deck's slides. Linking still captures a fingerprint through `link_slide` on the worker, using the window's current view and language; see Open decisions. Beside the label: "Topic from first slide: ● Probability". The **topic field is removed**.
- Folded **Note for teaching assistants**, keeping the warning text.
- Images (not in the prototype): keep attach, alt text and remove as a small folded section under the TA note.

The sticky footer has three parts:

- The status line: "Draft · saved", "Unsaved edits", "Unsaved edits · saving creates a new draft revision", "Reviewed · ready to use" or "Retired · …".
- When the question isn't reviewable, the red-text list of what is missing replaces the status line: "Before marking reviewed: write the question · mark exactly one correct answer · …". Use `check_phase1` / `check_elp` plus the existing text and answer checks.
- **Retire** (only when reviewed and unchanged), **Restore as draft** (only when retired), **Save draft** (disabled while there are no changes), and **Mark reviewed** (primary, disabled while issues remain; it reads "Reviewed" when already reviewed).

Header chips: *AI draft* (amber) or *AI-assisted* (muted, permanent), the status chip, and "rev N".

When `source_changed` is set, a violet banner reads "Slide N changed after this question was written. [Compare] [Still correct]".

Navigating away with unsaved edits currently asks Save/Discard/Cancel. The prototype auto-saves as a draft revision and shows a toast. **Keep the existing dialog** unless Jürgen decides otherwise; see Open decisions.

### Review changes dialog (new)

Opens from the slide banner or from **Compare** in the editor. It has two parts:

- **Old and new slide text, side by side.** Removed words are struck through on the old side; added words are highlighted on the new side. Use `difflib` on words.
- **One row per affected question**, with three actions:
  - **Still correct**: re-links the question to the current slide version.
  - **Edit**: opens the editor.
  - **Update with AI**: sends the question plus the new slide text to the host AI; the result is a draft.

  A resolved row shows a chip: "Kept · matches the current slide" or "Updated by AI · review the draft".

**Model gap:** showing the old slide text means storing it. Today `SourceLink` stores only a fingerprint. Add an optional `text` snapshot to `SourceLink` and write it in `link_slide` and in AI drafting. This is a schema change: bump `SCHEMA_VERSION` and keep reading v1 files. For links without a snapshot, fall back to showing only the current text, with the note "Earlier text not stored".

### Draft with AI dialog (rework of `DraftDialog`)

- **Slides to draft from**: a checkbox grid of the current topic's slides, with "Show all slides" to widen it. The slide the dialog was opened from is ticked. The current dialog starts with nothing ticked, and README says so explicitly. Opening from a slide is an explicit choice, but confirm with Jürgen and update README if it changes.
- **How many** (stepper, 1–30), **For** (Revision quiz | ELP exam), **Read slides as** (view), **Language**.
- A note: "Only the text of the N ticked slides is sent, and SlideMage shows it to you before anything leaves your computer. Images, other questions, answer keys and student data are never sent. Drafts arrive labelled AI draft and wait for your review."
- Buttons: Cancel and **Draft N questions**, which reads "Drafting…" while busy. On success, select the first source slide and show a toast: "N AI drafts added · review before use".
- The AI reply's `topic` field is no longer authoritative, because topic comes from the slide. Drop it from the request format or ignore it in `parse_reply`.

### Topics (new concept, replaces free-text `Item.topic`)

- A topic is a named, coloured group of slides. **Each slide is in at most one topic.** A question's topic is the topic of its **first** linked slide.
- The topic dialog has a name field and a grid of slide toggles; a slide that belongs to another topic shows that topic's dot. Below it: **Delete topic**, after which its slides have no topic, and **Done**.
- **Storage:** add `topics.json` to the bank folder: `{schema_version, topics: [{id, name, color, slides: [slide_id…]}]}`. Write it atomically, like `bank.json`. Colours come from a fixed palette of six and need dark-theme variants.
- **Compatibility:**
  - `assembly`, `manifest` and `moodle_xml` read `item.topic`. Resolve each item's topic name from the map before assembly and freeze it into the manifest, so issued exams keep working.
  - Decide whether `Item.topic` is still written on save, as a snapshot, or only derived; see Open decisions.
  - **Migration:** on first open, create topics from the distinct existing `item.topic` values. Assign each slide to the topic most of its questions use, and report conflicts once in the notice line.

### Exam tab (rework of `ExamTab`)

The step indicator shows three steps.

1. **Counts.**
   - One row per topic: dot and name, "N easy · M hard reviewed", an amber "N ELP drafts waiting for review" when there are any, and **Easy** and **Hard** steppers. When a count exceeds the reviewed questions available, the row shows red text "Only N available" under that stepper.
   - Last row: "Skip questions used in the last [N] exams".
   - A summary panel shows **questions / points / minutes**, computed with `ElpRules` (12 points and 2.5 min easy; 24 points and 5 min hard), the reason the exam can't be proposed when blocked, and **Propose exam**.
   - **Model gap:** `Blueprint` has topic counts plus a global `difficulty_mix`, but the prototype sets easy and hard **per topic**. Extend `Blueprint` (for example `counts: {(topic, difficulty): n}`, or a per-topic mix), keep `assemble` exact and never relax constraints, and freeze the new shape in the manifest. Minutes are derived, so drop the manual minutes field.
2. **Review & swap.**
   - A header with "N questions · P points · M min", the seed in monospace, **Change counts**, **New pick** and **Approve and freeze**.
   - Questions are grouped by topic. Each row shows the number, the stem, a chip "Easy · 12 pts" or "Hard · 24 pts", the slide reference, a violet *Slide changed* chip with a **Review** button that jumps to the editor, and **Swap**, which is disabled when no alternative exists.
   - Approval is blocked while any picked question has a changed source: "1 picked question is linked to a changed slide. Review or swap before freezing." The fresh source check before approval still runs.
3. **Freeze & save.**
   - A confirmation reading "Exam frozen", with totals, the issue time, the manifest file name, and "Saved in your private bank folder. Later edits to questions do not change this exam."
   - Two cards: **Exam copy** (Save exam HTML…) and **Private key**, with an amber card, a lock icon and "Answers, rationales and the scoring sheet. Keep this file private." (Save key HTML…).
   - **Record a correction…** opens the existing sheet. **Start another exam**.
   - **Open issued manifest…** stays available as a secondary button in the step-1 header.

### Sidebar box

Keep the counts and **Write a question for this slide…**. That button should open the window with that slide selected in column 2 and the editor open on a new question.

## Suggested order

1. Add the topics store, migration and topic resolution; adapt `assembly` and `manifest` to resolved topics, with tests.
2. Build the Questions tab's three columns, read-only, using the existing editor.
3. Build the new editor.
4. Add the `SourceLink` text snapshot (schema v2) and the Review changes dialog.
5. Rework the Draft with AI dialog.
6. Add the per-topic easy/hard `Blueprint` and the Exam tab steps.
7. Update the README, CHANGELOG, version and tests.

## Open decisions for Jürgen

1. **"Still correct" status.** Today, accepting a new baseline is an edit that creates a draft revision, which then needs re-review. The prototype keeps the question *reviewed*, because the user just compared old and new text. Choose one.
2. **"Update with AI" on an author-written question.** `Origin` is permanent, so the result can't become an AI-origin item. Options: create it as a new AI draft that replaces the old question (retire the old one), or keep author origin and add a per-revision "AI-assisted edit" flag.
3. **Requiring a linked slide.** The prototype requires at least one linked slide before review; today it is optional.
4. **Leaving with unsaved edits.** Auto-save as a draft (prototype) or keep the Save/Discard/Cancel prompt (current).
5. **The AI dialog's pre-tick.** Pre-tick the slide the dialog was opened from (prototype) or start empty (current README rule).
6. **`Item.topic`.** Keep writing it on save as a snapshot, or derive it only from `topics.json`.
7. **View and language for "+ Link another slide".** Use the deck default, the last used, or a small selector in the slide grid.
