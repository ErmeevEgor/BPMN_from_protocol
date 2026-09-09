# Canonical process_model 2.1

Machine-readable schema: `schemas/process-model-2.1.schema.json`. Version 2.0
remains an input format and is deterministically migrated by
`scripts/normalize_model.py` without losing IDs, provenance, or `source_refs`.

Each Activity stores one atomic action, object, `performer_kind`, nullable
human `role`, optional sourced `responsible_owner`, required `system`, multiple `display_codes`, short `name`, full
render contract, knowledge status, and primary-source references. Full contract
is exported as `bpmn:documentation`, never as HTML in `name`.

Artifacts are occurrences with `logical_object_id`, business `name`, optional
`state`, `kind`, metadata/system hints, explicit `bpmn_representation`, and
`source_refs`. Allowed kinds are `system_object`, `system_report`,
`print_form`, `external_document`, `physical_object`, `message`, and `other`.
`data_store` requires explicit persistent-store evidence. Several occurrences
may share one logical identity and show different states.
When a state is consumed after a split/merge, the same
`logical_object_id + state` must have been explicitly emitted by an Activity
on every incoming path. An output state becomes available only after its
producing Activity; a merge cannot invent a state produced on another branch.

Every Activity has `artifact_assessment.input/output` with `identified`,
`not_applicable`, or `open`; `open` references an information gap. A mutating
Activity without output is invalid.

Gateways have type `xor|or|and|event_based`, role `split|merge`, and optional
`paired_gateway_id`. A merge requires at least two actual incoming branches;
split/merge pairing alone is not a reason to create one.
Message Flow crosses Participant boundaries; Call Activity represents a called
reusable Process and requires `called_process_id`, `performer_kind=process`, and
no human role. At most one XOR/OR branch is `default`; the default reference is
preserved in draw.io and BPMN XML. Linked-process URI requires provenance.
An external Message Flow targeting the process start requires
`event_definition=message` and a BPMN `messageEventDefinition`. Automated and
process Activities never receive an invented human role; a missing sourced
owner is recorded as an information gap.
