// Generates lib/types.ts from the backend's ChatResponse JSON Schema (GET /api/schema),
// so frontend and backend types can't drift apart. Run with the backend up: npm run gen:types
import { writeFile } from "node:fs/promises";
import { compile } from "json-schema-to-typescript";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const res = await fetch(`${apiUrl}/api/schema`);
if (!res.ok) {
  throw new Error(`GET ${apiUrl}/api/schema failed: HTTP ${res.status}`);
}
const schema = await res.json();

const ts = await compile(schema, "ChatResponse", {
  additionalProperties: false,
  bannerComment:
    "/* Generated from GET /api/schema by scripts/gen-types.mjs. Do not edit; run `npm run gen:types`. */",
});
await writeFile(new URL("../lib/types.ts", import.meta.url), ts);
console.log("wrote lib/types.ts");
