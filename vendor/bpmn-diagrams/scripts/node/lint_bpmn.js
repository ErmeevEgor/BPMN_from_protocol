#!/usr/bin/env node
// OPP-CLASS: B1
// Слой B валидации методологии BPMN: bpmnlint на готовом XML (программно, не CLI).
// Конфиг — .bpmnlintrc рядом (extends bpmnlint:recommended + наши правки). exit: 0 чисто,
// 1 есть error (невалидный BPMN — вызывающий Python делает die + cleanup), 2 сбой линтера.
const fs = require("fs");
const path = require("path");

async function main() {
  const [xmlPath, rcPathArg] = process.argv.slice(2);
  if (!xmlPath) { console.error("usage: lint_bpmn.js <file.bpmn> [.bpmnlintrc]"); process.exit(2); }

  const { BpmnModdle } = require("bpmn-moddle");
  const { Linter } = require("bpmnlint");
  const NodeResolver = require("bpmnlint/lib/resolver/node-resolver");

  const xml = fs.readFileSync(xmlPath, "utf-8");
  const moddle = new BpmnModdle();
  const { rootElement, warnings: parseWarnings } = await moddle.fromXML(xml);
  if (parseWarnings && parseWarnings.length) {
    for (const w of parseWarnings) console.error("  parse-warning:", w.message || w);
  }

  const rcPath = rcPathArg || path.join(__dirname, ".bpmnlintrc");
  const config = JSON.parse(fs.readFileSync(rcPath, "utf-8"));

  // NodeResolver резолвит bpmnlint:recommended и отдельные правила из node_modules
  const resolver = new NodeResolver({
    require: (id) => require(id),
  });
  const linter = new Linter({ config, resolver });

  const reports = await linter.lint(rootElement);
  let errors = 0, warnings = 0;
  const lines = [];
  for (const elementId of Object.keys(reports)) {
    for (const r of reports[elementId]) {
      const sev = r.category === "error" ? "error" : "warning";
      if (sev === "error") errors++; else warnings++;
      lines.push(`  ${elementId.padEnd(16)} ${sev.padEnd(7)} ${r.message}  (${r.rule})`);
    }
  }
  if (lines.length) console.error(lines.join("\n"));
  console.error(`bpmnlint: ${errors} error(s), ${warnings} warning(s)`);
  process.exit(errors > 0 ? 1 : 0);
}

main().catch((e) => { console.error("lint_bpmn:", (e && e.message) || e); process.exit(2); });
