# Exam Forge release history

v0.1.0 (2026-10-30, phase 1 bank editor): Create and review deck questions and export them to Moodle XML.

NEW: Exam Forge provides a private question bank for each deck.
- Open a reusable, non-modal Exam Forge window from the Deck menu, with status, use, topic, source-change and AI-draft filters.
- Create and edit questions, attach bank-stored images and link source slides by view and language. Edits create draft revisions and preserve reviewed history.
- Check sources on open, on demand and before status changes; retain Source changed and permanent AI-assisted labels.
- Show compact bank counts in the sidebar and start a question from the slide in focus.
- Recover save conflicts by keeping edits or saving a separate question. Export filtered, reviewed revision questions with verified images to Moodle XML.
- Choose or relocate a private bank folder on first use, with confirmation for folders that may be shared.

v0.2.0 (2026-10-30, phase 2 interface): Assemble, approve and issue ELP exams from the existing bank.

NEW: Bank, Exam and Sessions tabs connect the phase 2 modules in the existing window.
- Record presentations after activation, label sessions from an optional cached schedule, attach recordings and transcripts, read parsed transcripts, and open recordings in the system player.
- Save the deck-default Slides filter, confirm estimated cut-offs, inspect exclusions and infeasible blueprints, swap questions and approve after fresh source checks.
- Issue immutable manifests in the bank folder, reopen them, save exam and private key HTML through dialogs, and record corrections in a separate sheet with one journal writer at a time.
- Edit a teaching-assistant note for shared stems, exclusions and fixed option order. Freeze it with the question and mark it in the exam entry copy; Moodle XML ignores it.

v0.3.0 (2026-10-30, AI drafting): Draft questions from explicitly chosen teaching sources through the host's optional AI chat service.

NEW: Enable AI support in Settings to show Draft with AI in the Bank tab.
- Choose slides; transcripts are not sent. Preview source text and explicitly review image descriptions for exam use before including them; the review tick starts off.
- Build one text-only request without bank keys, rationales, recordings, student records or attachment metadata. The host handles route selection and cloud confirmation; decline and cancel quietly add nothing.
- Refuse malformed or truncated replies in full. Save valid replies as drafts with permanent route/model provenance, retain the amber AI draft label until review, and use the existing source-check and review lifecycle.
- Preserve an empty model name when the host reports a provider's default model.

v0.3.1 (2026-10-30, drafting guidance): Customize AI drafting guidance in Settings.

NEW: Customize AI drafting guidance.
- Edit or reset question drafting guidance while retaining the required answer format and source evidence rule.

v0.3.2 (2026-10-30, presentation logs): Simplify Sessions to logged presentations.

NEW: Sessions lists logged presentations.
- Remove manual sessions and recording and transcript attachments. Presentation logs continue to support the Slides filter and cached schedules provide labels and fallback coverage.


v0.3.3 (2026-10-30, focused workflows): Streamline question writing, revision quizzes and ELP exams.

NEW: Exam proposals use topic counts, fixed ELP rules and an automatically generated seed.
- Keep the seed and scoring rules in the frozen manifest. Slides filtering starts on each time.
- Remove deck defaults, manual seed and scoring controls, objective counting, duplicate manifest and sidebar open buttons, bank search, and drafting image descriptions.


v0.4.0 (2026-10-30, focused exam assembly): Assemble exams from all reviewed ELP questions.

NEW: The window has Bank and Exam tabs.
- Remove presentation logging and the taught-only filter; retain blueprint exclusions and source checks.


v0.4.1 (2026-10-30, AI host contract): Use the direct host AI chat call.

NEW: AI drafting uses the host AI chat interface.
- Request AI chat through the host and handle AiChatError.
