# bpmn-from-protocol

Portable Agent Skill for turning source documents and protocol text into
editable draw.io diagrams, optional BPMN 2.0 XML with BPMN-DI, previews, and
validation reports.

## Install

Keep the top-level `bpmn-from-protocol` directory intact. Copy it to the skill
directory supported by the host agent, or give the agent the path to its
`SKILL.md` explicitly. Skill discovery and invocation syntax are host-specific;
the workflow and scripts are not tied to one LLM provider.

The host must be able to read local files and run shell commands. A text-only
chat client without filesystem and process execution cannot run the complete
pipeline.

## Runtime setup

Required for normal source extraction and draw.io generation:

- Python 3.11 or newer;
- the packages in `scripts/requirements.txt`.

Required when BPMN XML preview/validation is requested:

- Node.js 18 or newer;
- npm dependencies under `vendor/bpmn-diagrams/scripts/node`.

Required for draw.io PNG export and strict validation against actual SVG
routes:

- draw.io Desktop CLI.

Legacy binary `.doc` extraction additionally requires LibreOffice.

Inspect the current host without changing it:

```text
python scripts/check_runtime.py
```

Print installation commands without running them:

```text
python scripts/setup_runtime.py
```

Install Python and Node dependencies explicitly:

```text
python scripts/setup_runtime.py --install
```

Set `DRAWIO_CLI` to the draw.io executable when it is not on `PATH`. An
optional `config/tooling.json` in the working directory may instead contain a
`drawio_cli` path. Set `LIBREOFFICE_CLI` similarly for a non-standard
LibreOffice installation.

## Inputs

The deterministic extractor supports `.txt`, `.md`, `.html`, `.docx`, `.doc`,
`.pdf`, `.xlsx`, `.xlsm`, `.csv`, and `.tsv`. Spreadsheet extraction emits
stable cell locators such as `'План'!A12`. OCR is not automatic.

## Quality levels

- `L1` — express draft, normally 2–4 minutes and no cosmetic correction loop.
- `L2` — working diagram, normally 7–10 minutes and the default.
- `L3` — audit-ready diagram, normally 12–20 minutes with deeper review.

Example prompts:

```text
Use bpmn-from-protocol. Create an L1 express draft from the attached protocol.
Use bpmn-from-protocol. Create a working L2 BPMN diagram from this DOCX.
Use bpmn-from-protocol. Create an audit-ready L3 package with BPMN 2.0 XML.
```

## Outputs

The pipeline writes its deliverables under the selected workspace:

- `output/models/` — canonical semantic model;
- `output/registries/` — semantic/render registries and information gaps;
- `output/drawio/` — editable draw.io file;
- `output/preview/` — draw.io PNG when draw.io CLI is available;
- `output/bpmn/` and `output/bpmn-preview/` — requested BPMN XML and preview;
- `output/validation/` — machine-readable and human-readable reports.

If draw.io Desktop is unavailable, the editable `.drawio` remains deliverable
and the result is marked `NEEDS_REVIEW`; PNG and actual-SVG route validation are
reported as unavailable. Missing optional tooling must not delete a generated
candidate.

## Known limits

- Different LLMs may produce different semantic models from the same ambiguous
  source. Rendering and validation are deterministic after `process_model.json`
  is fixed.
- Scanned PDFs and images require OCR supplied by the host.
- Automatic skill discovery, attachment access, and image inspection differ by
  agent host.
