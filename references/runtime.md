# Runtime and cross-agent usage

The package uses the common `SKILL.md` directory format. Point an agent to
the directory or install/copy it into that host's skill location. Automatic
discovery is host-specific; the workflow and Python scripts are not tied to
an LLM vendor. Read the packaged `README.md` for installation examples.

## Required runtime

- Python 3.11+.
- For requested BPMN: Node.js 18+ and `npm ci` in
  `vendor/bpmn-diagrams/scripts/node`; `node_modules` is not packaged.
- Packages from `scripts/requirements.txt`.
- `.docx`: `python-docx`; `.pdf`: `pdfplumber`; `.xlsx`/`.xlsm`: `openpyxl`.
- Legacy binary `.doc`: local LibreOffice for conversion.

Check the host before modeling:

```text
python scripts/check_runtime.py
```

`scripts/setup_runtime.py` prints dependency installation commands and changes
nothing unless the user explicitly runs it with `--install`.

Editable `.drawio` does not require draw.io Desktop. Draw.io SVG export and
strict actual-SVG route validation use a configured draw.io Desktop CLI. BPMN
SVG is rendered by the vendor Node stack. If draw.io Desktop is missing,
preserve `.drawio`, skip its SVG preview, and report
`NEEDS_REVIEW`; never discard the candidate. OCR is not automatic.

The canonical model must be normalized to schema 2.1 and pass semantic
validation before registry or rendering. The portable package is assembled
by `scripts/sync_portable_skill.py`; never hand-maintain mapped copies.

`run_pipeline.py` accepts `--quality-level L1|L2|L3`, writes the selected
machine-readable profile to `output/validation/<process-id>-quality-profile.json`,
and never uses the level to reduce model contents or requested formats. It
renders artifacts and produces `NEEDS_REVIEW` until the review required by
that profile is recorded. Inspect the generated SVG, then run:

```text
python scripts/post_render_review.py <process-id> --workspace <workspace> --quality-level <L1|L2|L3> --status PASS --reviewer <name>
python scripts/build_validation_report.py <workspace>/output/models/<process-id>-model.json --workspace <workspace> --quality-level <L1|L2|L3>
```

The review file is bound to SHA-256 hashes of the current draw.io and SVG. If
BPMN was requested it is also bound to the `.bpmn` and its actual bpmn-js SVG.
Optional PNG files are hashed only when they were explicitly requested. A new
render invalidates the old review automatically.
