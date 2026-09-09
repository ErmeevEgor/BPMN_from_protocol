# Contributing

This repository publishes the portable `bpmn-from-protocol` Agent Skill.

The canonical implementation is maintained in `arman-bpmn-generator` and is
synchronized into its `portable-skills/bpmn-from-protocol` package before a
release. Avoid editing generated copies independently: submit a tested change
to the canonical generator first, run its portable sync and test suite, then
publish the resulting package here.

Before proposing a release-mirror update, run:

```text
python scripts/setup_runtime.py --install
python scripts/check_runtime.py
python tests/smoke_test.py
```

Do not commit source protocols, generated process artifacts, local tooling
paths, credentials, `node_modules`, caches, or logs.
