---
name: bpmn-from-protocol
description: >-
  Create editable BPMN process diagrams from an attached file, local path,
  or pasted protocol text when draw.io, PNG, or BPMN 2.0 XML is requested.
  Not for UML, ER diagrams, charts, or timelines.
---

# BPMN from protocol

Build evidence-based BPMN artifacts using the canonical ARMAN semantic model
schema 2.1. This package is agent-neutral: use the current host's filesystem,
shell, document extraction, and image inspection capabilities.

Before the first run, read [runtime.md](references/runtime.md) and run
`python scripts/check_runtime.py`. For modeling,
read [process-model-schema.md](references/process-model-schema.md) and enforce
[ROLE != SYSTEM](references/system-role-policy.md).

## Quality level

Choose one level before modeling and state it in the first progress update:

- **L1 — Express draft**: fastest structural draft for discussion.
- **L2 — Working diagram**: normal operational detail and the default when the
  user does not specify a level.
- **L3 — Audit-ready diagram**: deeper source coverage and stricter review for
  a regulation, acceptance, or reference artifact.

Infer an explicit request such as `быстро`, `рабочая схема`, or `аудитная`
without asking a follow-up question. A plain request to create a diagram uses
L2. Read [quality-levels.md](references/quality-levels.md) after selecting the
level and follow its scope, validation, correction, and stopping budget.
Quality level changes depth and review effort, never BPMN truth: source
traceability, ROLE != SYSTEM, valid graph semantics, and explicit information
gaps remain mandatory at every level.

## Workflow

1. Resolve the supplied attachment/path and create an isolated writable workspace.
2. Extract once with `scripts/extract_protocol.py`.
3. Separate independent processes without mixing their facts.
4. Create one atomic schema-2.1 `process_model.json` per process. One Activity
   has one action, role, system, main business object, basis, observable result
   or verification criterion, knowledge status, and source trace. Never invent
   missing facts or source locators.
5. Run `scripts/normalize_model.py`, then `scripts/validate_model.py`.
   Semantic validation must pass before either registry or renderer runs.
6. Build both registries plus `information-gaps.md`: use
   `build_semantic_registry.py`, `build_information_gaps.py`, and
   `build_registry.py` for the deterministic renderer.
7. Run `scripts/run_pipeline.py --model <model> --workspace <workspace>`;
   add `--bpmn` when BPMN XML is requested. Use `--no-png` only when PNG was
   not requested. If draw.io Desktop is unavailable, preserve the editable
   draw.io candidate and report `NEEDS_REVIEW` instead of failing the run.
   The `--bpmn` path must publish BPMN-DI from the final draw.io geometry,
   render it through bpmn-js, and pass the independent BPMN-DI visual gate.
8. Apply the selected level's validation and correction budget. When SVG
   validation is required, use the SVG exported from the same draw.io
   candidate. Inspect the complete PNG at a readable scale.
9. Only after inspection, record the decision with
   `scripts/post_render_review.py <id> --workspace <workspace> --status PASS|FAIL --reviewer <name>`.
10. Rebuild the final report with `scripts/build_validation_report.py` and
    return direct links to requested artifacts plus validation results.

## Invariants

- Protocol decisions and scenario are the source of business truth.
- The current white-box Process has no internal Lane. Human roles and systems
  are per-Activity overlays; overlays are never Flow Nodes.
- `system` names one execution system; devices/interfaces belong in
  `execution_channel`; physical manual work uses `system: "Вне ИС"`.
- `CONFIRMED` Activity requires `source_refs`. Recommendations,
  assumptions, open questions, and 1C metadata checks remain visibly distinct.
- A regression fixture may index evidence but cannot be the only source for a
  `CONFIRMED` fact; retain a primary DOCX/transcript reference and locator.
- A Task displays only a short operational `name` (maximum 72 characters and
  2–3 rendered lines). Full action/object/interface/basis/result/check data
  stays in the model and semantic registry and is exported to
  `bpmn:documentation`, never encoded into `name` or HTML.
- Never fill unknown contract fields with generic completion phrases. Mark the
  Activity `OPEN` and add an `information_gaps[]` row.
- Use `send_task` only across separate participants/pools with a Message Flow.
  Model automatic in-system exchange as `service_task`.
- Place a same-type merge only when at least two branches really reconverge.
  A one-input merge is invalid and split/merge symmetry alone is insufficient.
- Route Sequence Flows from explicit ports on the actual Flow Node bounds,
  never from an enclosing Activity Group. Keep the happy path left-to-right;
  reserve distinct outer corridors for alternatives and cyclic returns. Two
  flows may not share a segment, including flows with a common endpoint.
- Calculate ports on the actual perimeter: rectangles for Activity,
  Participant, and the safe Data Object contract; diamonds for Gateway;
  ellipses for Event. Validate both endpoints of Sequence Flow, Message Flow,
  and Data Association in draw.io and the rendered SVG.
- Keep Tasks on one baseline whether a role overlay exists or not. Human
  Activities require a role; automated Activities have no invented role.
  Render a system as a compact rounded badge and route Data Associations
  around it. Size each Activity Group from its artifact count and keep all
  children contained without intersections with neighboring groups.
- Render Start/End Event and Gateway captions as separate wrapped label cells.
  Put each branch condition beside its own outgoing flow. Preserve a selected
  default flow in draw.io and BPMN XML.
- Use `call_activity` only with `called_process_id` so BPMN XML can emit
  `calledElement`; otherwise choose the real Task type and performer.
- A Message Flow that launches the Process requires `event_definition:
  message` and a BPMN `messageEventDefinition`. Never invent a human role for
  automated/process Activities; record a missing sourced owner as an OPEN
  information gap.
- Place external Participants near the nodes they communicate with. Message
  Flows must avoid Flow Nodes, Sequence Flows, and data artifacts.
- Keep Data Association semantic endpoints as `Artifact ↔ Activity`. Give
  every input/output occurrence its own bottom Activity port and corridor;
  it may not overlap or cross Sequence Flow, another Association, Activity,
  Artifact, role badge, or system badge.
- Treat `logical_object_id + state` as path-sensitive: a consumed state must
  have been explicitly produced on every incoming path. A state change exists
  only after the Activity that emits that state as an output.
- Source adapters extract facts only. Excel, table, and text adapters must not
  emit layout coordinates, bands, ports, or corridors; normalize every source
  to one `process_model` and use this renderer.
- Visual PASS is blocked by edge-edge intersections, label overlaps, invalid
  backward routing, edge-through-node paths, Message Flow obstruction,
  Data Association crossing a system badge, text overflow, excessive width,
  or large empty intervals. Validate actual SVG routes, not only model points.
- Do not store `visual_review` in the model or predeclare it before render.
  Critical OPEN/ASSUMPTION items force `NEEDS_REVIEW`; overall `PASS` requires
  a separate hash-bound post-render review. For BPMN requests the hashes also
  include the `.bpmn` and its bpmn-js SVG/PNG.
- Split processes above 22 Tasks into an overview and readable detailed
  subprocess diagrams. Use Task geometry 120–160 × 70–90 and a 14–16 px marker.
- Never preserve legacy `source_status` after normalization.
- Do not combine different actions, roles, systems, controls, alternatives,
  knowledge statuses, or source requirements into one Activity.
- Follow [corporate-bpmn-standard.md](references/corporate-bpmn-standard.md)
  and [color-system.md](references/color-system.md).
- Do not run indefinite correction loops.
- Never spend the whole run polishing without a deliverable. Follow the
  progressive-delivery and hard-stop rules in `quality-levels.md`; return the
  best generated candidate with an honest status when its budget expires.

## Completion

The task is complete when every requested process has a valid schema-2.1
model, semantic registry, requested artifact files, and validation reports.
The portable package is generated from canonical repository sources; run the
repository sync checker before distributing it.
