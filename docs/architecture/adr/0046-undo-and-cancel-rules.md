# ADR 0046: Undo and cancel rules

## Status

Accepted, 2026-11-17. Supersedes the "Cancel mechanisms" paragraph of [ADR 0043](0043-local-gui.md) (end of "Run statuses") for capture, build and export, and records undo as built.

## Context

[ADR 0043](0043-local-gui.md) gives the cancel mechanisms in one paragraph. It says export "checks a cancel flag between files" and that build is cancelled "by an exception raised in the worker at a safe point". Only the export wording was wrong: export checks the caller's signal at four named safe points, not between files. The build wording was incomplete. It does not name the error, which is `OperationCancelled`, nor the mechanism, which is a signal the caller sets and the library checks at the safe points it declares, nor the removal of the temporary file. Capture did match: it checks the signal between pages. ADR 0043 does not cover undo, so the undo rule is recorded here.

## Decision

### Undo

Undo withdraws the Reviewer's own Decision on the Record on screen. It does so by appending an Unsure Decision that supersedes it. The Record then counts as Unsure, not Unscreened. The earlier Decision stays in the Decision log.

- Undo never reaches another Reviewer's Decision, or a Decision from an earlier window. Records decided in an earlier window are not in the Queue.
- A read-only session refuses undo and writes nothing.
- With the cursor on an Unsure or Unscreened Record, or past the last Record, undo withdraws this Reviewer's Include or Exclude on the previous Record and moves the cursor to it.
- If the cursor is on an Unsure or Unscreened first Record in the Queue, there is no previous Record: nothing moves and nothing is written.
- If the previous Record is also Unsure or Unscreened, the cursor moves back one Record and nothing is written.

Changing any part of this rule requires a new ADR.

### One cancel rule

A cancel is a signal the caller sets. The operation checks it at its declared safe points. When the signal is set, the operation stops with the error in the table and leaves the state in the last column.

| Operation | Error | Safe points | State left behind |
|---|---|---|---|
| Capture | `CaptureCancelledError` | between pages | an unsealed run, which can be resumed |
| Build | `OperationCancelled` | each declared build safe point | the temporary file is removed; the previous Store is untouched |
| Export | `OperationCancelled` | start, counts, render, write | the previous bundle is untouched; a first export writes nothing |

No cancel alters a Sealed run, and build and export leave Layer 0 unchanged. A cancelled capture keeps the pages it already wrote in its unsealed run. Changing any part of this rule requires a new ADR.

## Consequences

Undo never edits or deletes a line. The Decision log stays append-only, and each undo adds at most one event; an undo that finds nothing to withdraw appends none. Because undo reaches only this Reviewer's Decisions in the current window, a second Reviewer's Decision on the same Record is left alone by any undo.

## Evidence

- Undo withdraws the Reviewer's own Decision on the Record on screen, appending an Unsure whose note names the superseded event — tests/integration/gui/test_screening_undo.py::test_undo__on_a_record_on_screen_with_own_exclude__withdraws_it_and_stays
- At queue level, undo appends an Unsure for the same Record and Reviewer, and its note names the original event — tests/integration/screening/test_queue.py::test_undo__last_decision__appends_reversal_and_steps_back
- The original Decision stays in the Decision log and its line is not edited — tests/integration/gui/test_screening_undo.py::test_undo__after_include__appends_unsure_and_restores_flow_and_progress
- After an Exclude, undo appends exactly one Unsure and restores the decided and remaining counts — tests/integration/gui/test_screening_undo.py::test_undo__after_exclude__restores_decided_and_remaining_counts
- After an Include, undo appends one line and takes the Record back out of the flow counts — tests/integration/gui/test_screening_undo.py::test_undo__after_include__appends_unsure_and_restores_flow_and_progress
- An undone Record is shown as Unsure, not Unscreened — tests/integration/gui/test_screening_undo.py::test_undo__screen_text__says_the_record_is_unsure_not_unscreened
- Undo never withdraws another Reviewer's Decision on the same Record — tests/integration/gui/test_screening_undo.py::test_undo__covers_only_the_researchers_own_decision_in_the_window_that_made_it
- Records decided in an earlier window are not in the Queue, so undo cannot reach their Decisions — tests/integration/screening/test_queue.py::test_queue__20_decided__resumes_at_21
- In a reopened window, undo returns nothing and the Decision log is unchanged, so an earlier window's Decision is not withdrawn — tests/integration/gui/test_screening_undo.py::test_undo__covers_only_the_researchers_own_decision_in_the_window_that_made_it
- A read-only session refuses undo and appends nothing — tests/integration/gui/test_screening_undo.py::test_undo__read_only_session__is_refused_and_logs_nothing
- With the cursor on an Unscreened or Unsure Record, or past the last Record, undo withdraws the previous Include or Exclude and moves to it — tests/integration/gui/test_screening_undo.py::test_undo__cursor_not_on_a_decided_record__withdraws_the_previous_one_and_moves_to_it
- At queue level, an Unsure under the cursor falls back to the previous Record, and the Unsure is not doubled — tests/integration/screening/test_queue.py::test_undo__on_an_unsure_record_under_the_cursor__falls_back_to_the_previous_one
- With the cursor on a decided Record, undo withdraws that Record's Decision and the cursor stays on it — tests/integration/screening/test_queue.py::test_undo__after_stepping_back__reverses_the_record_on_screen
- With the cursor on the first Record in the Queue and it unresolved, nothing moves and nothing is written — tests/integration/gui/test_screening_undo.py::test_undo__unresolved_first_record__appends_nothing_and_moves_nothing
- With the cursor on the first Record and it Unsure, nothing moves and nothing is written — tests/integration/gui/test_screening_undo.py::test_undo__unsure_first_record_on_screen__appends_nothing_and_moves_nothing
- When the previous Record is also Unsure or Unscreened, the cursor moves back one Record and nothing is written — tests/integration/gui/test_screening_undo.py::test_undo__previous_record_also_unresolved__moves_back_one_and_appends_nothing
- At queue level, stepping back onto an unresolved Record appends no event — tests/integration/screening/test_queue.py::test_undo__unresolved_previous_record__steps_back_without_appending
- Capture: a cancel set between pages stops before the next page is requested, and the run is left unsealed — tests/integration/capture/test_capture_control.py::test_scopus_cancel__between_pages__stops_before_next_page_and_resumes
- Capture (OpenAlex): a cancel set between pages stops before the next page is requested, and the run is left unsealed — tests/integration/capture/test_capture_control.py::test_openalex_cancel__between_pages__stops_before_next_page_and_resumes
- A cancelled Scopus capture is resumed under the same run id and fetches all three pages — tests/integration/capture/test_capture_control.py::test_scopus_cancel__between_pages__stops_before_next_page_and_resumes
- A cancelled OpenAlex capture is resumed under the same run id and sealed — tests/integration/capture/test_capture_control.py::test_openalex_cancel__between_pages__stops_before_next_page_and_resumes
- A cancel during a Scopus capture never alters a Sealed run — tests/integration/capture/test_capture_control.py::test_scopus_cancel_and_discard__never_alter_a_sealed_run
- A cancel during an OpenAlex capture never alters a Sealed run — tests/integration/capture/test_capture_control.py::test_openalex_cancel_and_discard__never_alter_a_sealed_run
- Build: a cancel at each declared safe point leaves the previous Store byte-identical, with no temporary file and Layer 0 unchanged — tests/integration/test_cancel_build_export.py::test_build_cancel__at_each_safe_point__keeps_the_previous_store
- Build with no previous Store: a cancel at any safe point leaves the store folder empty — tests/integration/test_cancel_build_export.py::test_build_cancel__with_no_previous_store__leaves_no_partial_store
- Export: a cancel at each of the four named safe points leaves the previous bundle byte-identical and Layer 0 unchanged — tests/integration/test_cancel_build_export.py::test_export_cancel__at_each_safe_point__keeps_the_previous_bundle
- Export: a cancelled first export writes no file at any of the four safe points — tests/integration/test_cancel_build_export.py::test_export_cancel__first_export__writes_nothing
- Export reports its four named safe points in order — tests/integration/test_cancel_build_export.py::test_export__reports_every_safe_point_and_table_progress
- In the GUI, a cancel during the render step leaves no file in the exports folder — tests/integration/gui/test_export.py::test_cancel__during_the_export__leaves_no_partial_bundle
- In the GUI, a cancelled second export leaves the previous bundle's files unchanged — tests/integration/gui/test_export.py::test_cancel__second_export__keeps_the_previous_bundle
