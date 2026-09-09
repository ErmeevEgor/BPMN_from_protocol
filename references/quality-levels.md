# BPMN quality levels

Select exactly one level per request. The level controls modeling granularity,
validation depth, and correction effort; it does not relax source truth, graph
validity, ROLE != SYSTEM, or the requirement to expose missing information.

## Selection

| Level | User intent | Target elapsed time | Correction budget |
|---|---|---:|---:|
| L1 — Express draft | `быстро`, `черновик`, `эскиз`, early discussion | 2–4 minutes | 0 layout corrections |
| L2 — Working diagram | ordinary generation, workshop, working documentation | 7–10 minutes | 1 evidence-backed correction |
| L3 — Audit-ready diagram | `детально`, `эталон`, `для регламента`, acceptance or independent audit | 12–20 minutes | up to 2 evidence-backed corrections |

Use L2 when the user does not name or imply a level. Do not ask the user to
choose when the wording already indicates one. State the selected level and
expected trade-off in the first progress update.

Elapsed-time targets are operating budgets, not promises about external tool
latency. Never extend a run merely to achieve cosmetic perfection.

## L1 — Express draft

Purpose: expose the process structure quickly so people can discuss scope and
logic.

- Extract the source once and model the happy path plus every business decision
  that materially changes the route.
- Prefer 6–12 operational Activities. Collapse repetitive micro-actions only
  when role, system, object, and business outcome remain the same. Never hide a
  real decision, exception that changes the outcome, or cross-system exchange.
- Show confirmed human roles, execution systems, and only process-defining
  inputs/outputs. Record unknowns in `information_gaps`; do not research or
  infer them extensively.
- Generate editable draw.io and PNG. Generate BPMN XML only when requested.
- Require model/schema/graph validation and a complete PNG inspection. Run the
  draw.io validator, but do not spend a correction round on cosmetic routing.
- The highest permitted completion status is `DRAFT / NEEDS_REVIEW`. Report
  visible arrow, spacing, or artifact issues explicitly.
- If the renderer withholds the normal output because of a visual gate, publish
  the generated candidate under a `-draft` filename and return it with the
  failing rules. Never return nothing after a model and candidate exist.

## L2 — Working diagram

Purpose: produce the normal working artifact used in analysis sessions. This
matches the current standard workflow.

- Model operational Activities with actionable names, confirmed roles and
  systems, meaningful gateways, principal business objects, observable results,
  and source references.
- Include the main exception paths and repetitions that affect execution;
  document secondary gaps instead of expanding speculative branches.
- Generate requested draw.io, PNG, and BPMN XML artifacts plus semantic and gap
  registries.
- Require semantic validation, corporate draw.io validation against the
  candidate SVG, BPMN XML validation, BPMN-DI validation when BPMN is requested,
  and one full visual inspection.
- Allow one evidence-backed correction that fixes the largest semantic or visual
  problem. Re-run only the affected generation and validation stages.
- Target `PASS`; use `NEEDS_REVIEW` when source gaps or remaining visible issues
  prevent an honest PASS. Deliver the files in either case.

## L3 — Audit-ready diagram

Purpose: create a reference artifact suitable for regulation, acceptance, or
independent review.

- Cover all source-backed routes, exceptions, controls, state transitions, and
  cross-system exchanges. Keep confirmed facts, assumptions, and recommendations
  visibly distinct.
- Preserve atomic Activities and detailed artifact states. Verify 1C metadata
  only when the source or an available authoritative repository provides it;
  otherwise record a gap.
- Split a dense process into an overview and detailed subprocess diagrams before
  shrinking text or creating long tangled routes.
- Generate the full requested package: draw.io, PNG, BPMN XML with BPMN-DI,
  bpmn-js previews, semantic registry, information gaps, and validation report.
- Require all L2 gates, inspect every complete draw.io and bpmn-js PNG, and
  compare cross-format node/flow/artifact coverage.
- Allow at most two evidence-backed correction rounds. Each round must address
  named validator findings or visible defects; do not polish by intuition.
- By 10 minutes, preserve and expose the first semantically valid candidate
  before continuing the audit pass. If the 20-minute budget or practical context
  budget is reached, stop and deliver the best valid candidate with `NEEDS_REVIEW`
  plus the remaining findings. Never consume the full run and return no artifact.

## Shared stopping rules

1. Extraction happens once unless the source itself changes.
2. Do not regenerate unaffected processes or formats.
3. Prefer deterministic validators and targeted fixes over repeated visual
   experimentation.
4. When the correction budget is exhausted, stop. A precise `NEEDS_REVIEW`
   result with usable files is better than an unbounded attempt at PASS.
5. Always return direct artifact links, the selected level, validation status,
   unresolved findings, and any intentionally omitted detail.
