# BPMN quality levels

Select exactly one level per request. A quality level controls only validation
depth, visual-review depth, and the correction budget. It never changes the
business content, source coverage, modeling granularity, or requested output
formats.

## Shared content contract

The following contract is identical at L1, L2, and L3:

- extract the supplied source completely enough to identify every process in
  scope and every source-backed action, role, system, object, gateway,
  exception, transfer, input, and output;
- do not omit or merge a fact merely to meet a time target or reduce the number
  of diagram elements;
- keep Activities atomic whenever action, role, system, object, control,
  outcome, knowledge status, or source requirement differs;
- preserve all confirmed routes and meaningful exceptions from the source;
- never invent a missing fact; record it in `information_gaps`;
- build the same schema-2.1 model and all formats requested by the user,
  including draw.io, PNG, and BPMN 2.0 XML with BPMN-DI when requested;
- always run schema, source-traceability, ROLE != SYSTEM, and graph-validity
  checks before rendering.

Choosing a faster level is never permission to show only a happy path, remove
an Activity, omit a role/system/artifact, simplify a gateway, or skip a
requested deliverable. If the source is large, use overview plus detailed
subprocess diagrams at every level; do not silently discard content.

## Selection

| Level | Choose when | Target elapsed time | Visual correction budget |
|---|---|---:|---:|
| L1 — Basic validation | rapid review or first discussion; the user accepts visible layout findings | 3–7 minutes | 0 corrections |
| L2 — Standard validation | ordinary generation, analysis workshop, working documentation | 7–15 minutes | 1 targeted correction |
| L3 — Extended validation | regulation, acceptance, reference artifact, or independent audit | 20–40 minutes | up to 3 targeted corrections |

Use L2 when the user does not name or imply a level. Infer phrases such as
`быстро`, `минимальная проверка`, `рабочая схема`, `тщательная проверка`, or
`для приемки` without asking a follow-up question. State the selected level
and its validation trade-off in the first progress update.

Elapsed-time targets are operating budgets, not promises about external tool
latency. Preserve a first semantically valid candidate as soon as it exists.
Never extend a run merely to achieve cosmetic perfection.

## L1 — Basic validation

Purpose: return the complete source-backed model and requested files quickly,
with only the checks required to prove that the files are structurally usable.

Run:

- the complete shared content contract;
- schema, source-traceability, ROLE != SYSTEM, and graph validation;
- draw.io XML/structure validation and basic endpoint/reference checks;
- XML parse, BPMN semantic references, and BPMN-DI completeness when BPMN is
  requested;
- one quick inspection of the complete PNG for catastrophic clipping, an empty
  render, or an unreadable overall layout.

Do not run a correction cycle. Actual-SVG geometric auditing, exhaustive
edge-edge intersection review, detailed typography review, and pixel-level
layout tuning are intentionally outside L1. Publish the candidate even when a
visual validator reports findings, set `NEEDS_REVIEW`, and list those findings.
Structural or semantic model errors remain `FAIL` and must be fixed because
they would make the result unusable.

## L2 — Standard validation

Purpose: reproduce the established working workflow that normally completes
within 10–15 minutes while keeping the full business model.

Run everything in L1, plus:

- corporate draw.io validation against the SVG exported from the same draw.io
  candidate;
- endpoint-to-contour checks for Sequence Flow, Message Flow, and Data
  Association;
- automated checks for edge-through-node paths, shared edge segments,
  significant edge intersections, branch-label placement, text overflow,
  role/system/artifact placement, and artifact-to-Activity attachment;
- BPMN XML and BPMN-DI validation plus one complete bpmn-js preview inspection
  when BPMN is requested;
- one full visual inspection of the draw.io PNG and cross-format comparison of
  the main node/flow/artifact coverage.

Allow no more than one targeted correction. It must address named validator
findings or an obvious visible defect. Re-run only the affected stages. Do not
perform repeated manual coordinate tuning or continue solely to turn cosmetic
findings into `PASS`. Deliver the best usable files with `NEEDS_REVIEW` when
the correction budget is exhausted.

## L3 — Extended validation

Purpose: audit the same complete model and deliverables with the deeper review
used for acceptance or a reference artifact.

Run everything in L2, plus:

- element-by-element comparison of the model, semantic registry, draw.io, and
  BPMN XML, including roles, systems, object states, gateways, exception paths,
  Message Flows, and Data Associations;
- inspection of every complete draw.io and bpmn-js preview at both overview and
  readable detail scales;
- exhaustive review of connector geometry, labels, whitespace, typography,
  artifact ownership, and cross-format consistency;
- regression comparison when an approved reference or regression case exists;
- a detailed validation report that records every gate and remaining finding.

Allow up to three targeted correction rounds. Each round must address recorded
findings and re-run only affected generation and validation stages. Preserve
the first semantically valid candidate before extended polishing. If the
40-minute budget or practical context budget is reached, stop and deliver the
best valid candidate with `NEEDS_REVIEW`; never consume the run and return no
artifact.

## Shared stopping rules

1. Extract the unchanged source once.
2. Do not regenerate unaffected processes or formats.
3. Prefer deterministic validators and targeted fixes over repeated visual
   experimentation.
4. Stop when the selected correction budget is exhausted.
5. A time limit may stop validation or polishing, but it may not silently
   reduce the process model or requested deliverables.
6. Always return direct artifact links, the selected level, validation status,
   checks performed, checks intentionally not performed, and unresolved
   findings.
