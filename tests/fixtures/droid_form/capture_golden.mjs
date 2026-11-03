// Fills each case's "droid" in golden.json with js-yaml 4.3.2's dump of its fields, in Droid's key
// order and with the options that reproduce Droid's droid files (no line folding, double quotes),
// followed by the trimmed body. Run: node tests/fixtures/droid_form/capture_golden.mjs
import { readFileSync, writeFileSync } from "node:fs";
import { dump } from "./js-yaml-4.3.2.mjs";

const path = new URL("./golden.json", import.meta.url);
const golden = JSON.parse(readFileSync(path, "utf-8"));
const order = ["name", "description", "model", "reasoningEffort", "tools", "mcpServers", "version"];
for (const entry of golden.cases) {
  const a = {};
  for (const key of order) if (entry.fields[key] !== undefined) a[key] = entry.fields[key];
  entry.droid = `---\n${dump(a, { lineWidth: -1, quotingType: '"', forceQuotes: false })}---\n${golden.body.trim()}`;
}
writeFileSync(path, JSON.stringify(golden, null, 2) + "\n");
