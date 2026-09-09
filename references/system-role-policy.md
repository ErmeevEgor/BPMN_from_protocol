# ROLE ≠ SYSTEM — process_model 2.1

The current process is one white-box Participant without an internal LaneSet.

- `user_task` and `manual_task`: `performer_kind=human`, non-empty `role`, one
  purple `#6366F1` role overlay above the Activity.
- `service_task`, `script_task`, `business_rule_task`:
  `performer_kind=automated`, `role=null`; never infer a nearby human role.
- send/receive/call/subprocess: determine `performer_kind` from evidence.
- Every Activity has one system overlay below it. `manual_task` uses only
  `Вне ИС`; an unknown system is `OPEN` plus an information gap and blocks PASS.
- Role/system overlays contain only their respective value and are never a
  source or target of Sequence Flow.

External organizations may be black-box Participants connected by Message
Flow. Software systems are not Participants solely because they execute work.
