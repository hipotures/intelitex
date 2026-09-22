# Terminology Review

B: current main-server Review API, application ReviewRepository/ReviewService,
ApproveCommand.expected_revision and existing execute_approval. R: v33 full-page
2-panel Review. For detailed geometry/actions read supplied contract section 11.

Prepare is an explicit idempotent POST. GET loads the draft, not an instruction to
regenerate it every poll. No page-lifetime Store, application session or writer lock.
Individual mutations acquire the project lock; a concurrent writer can return busy.

Load `_revision`; mutations send `revision` and return the new `revision`.
PATCH accepts select,custom,reviewed,user_notes only. Do not turn category/meaning/
aliases into editable fields without a documented application command.

Select actual candidate.number, not blindly array index. Effective form is nonempty
custom, otherwise selected candidate. Clicking candidate clears custom when changing
choice. Keep source writes exact source as custom. Use candidate clears custom. These
do not auto-review or auto-approve. Changing a choice invalidates reviewed/confirmation
according to the application response; don't write a parallel frontend authority.

Search/status/category intersect, selection remains when still visible. Use backend
term IDs and order. Next/Previous without review do not mark the term reviewed.
Review & next flushes the form, marks reviewed with the correct returned revision,
then advances only after success. Use the prior filtered order, not an index into a
newly shortened Unreviewed list. Disable unavailable boundary actions; never auto-confirm.

Keep source text, candidates, translations and evidence in their actual language.
Meaning/aliases/evidence/confidence/attributes are actual data; absent is not high
confidence and React does not infer gender from names/pronouns. Render supplied gender/
species safely and accessibly when present. Don't invent relationships/evidence.

Evidence is term_id,entries,warnings,choice_pending_approval. Entries include verified
source/Polish content and canonical locations, not arbitrary files/raw exceptions.
Late response for term A must never populate term B. Handle loading/empty/error distinctly.
Whole-book Review evidence is not safe as Reader known-so-far evidence.

## Confirm versus approve

POST confirmation with revision and confirmed:true validates draft choices/review flags.
POST approve with the exact resulting revision commits terminology, validating inside
OperationScope before backup/commit. No model call, no API accept-defaults bypass.
The visible Confirm glossary action must not show committed success if only confirmation
succeeded. If approval fails, keep the distinct draft-confirmed/not-committed state.
Never auto-start translation after confirmation or after P1 finishes.

Current `approved` can remain true after editing an already committed draft, while
`review.current` refers to source/analysis compatibility. Do not assert that either
proves all current choices are committed. The target stronger reapproval gate is D08:
change/test the application policy explicitly if authorized; don't silently implement
it only in a React boolean. Existing dependent-chunk staleness belongs to approval logic.

On 409 busy or revision conflict, preserve draft and reload before explicit reapply.
Do not silently replace user's custom value or auto-approve a newer revision. Concurrent
requests, two tabs, and standalone/CLI writers must be tested. GET/read actions must not
claim editability merely because no SSE job is visible; real locks still decide.

The API has bulk-review, but v33 has no new bulk toolbar requirement. Don't invent a UI
just because an endpoint exists. Preserve standalone bulk behavior independently.
