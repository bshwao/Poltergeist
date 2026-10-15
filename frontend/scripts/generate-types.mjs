import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { compile } from "json-schema-to-typescript";

const schemaPath = new URL("../../contracts/models.schema.json", import.meta.url);
const outputPath = new URL("../src/types/generated.ts", import.meta.url);
const checkOnly = process.argv.includes("--check");
const unknownArguments = process.argv.slice(2).filter((argument) => argument !== "--check");

async function main() {
  if (unknownArguments.length > 0) {
    throw new Error(`Unknown arguments: ${unknownArguments.join(", ")}`);
  }

  const schema = JSON.parse(await readFile(schemaPath, "utf8"));
  const generated = await compile(schema, "PoltergeistContracts", {
    bannerComment: [
      "/* This file is generated. Do not edit it directly.",
      " * Source: contracts/models.schema.json (exported from shared.models).",
      " * Regenerate with: npm run contracts:generate",
      " */",
    ].join("\n"),
    unreachableDefinitions: true,
    additionalProperties: false,
  });

  if (checkOnly) {
    let existing;
    try {
      existing = await readFile(outputPath, "utf8");
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
      throw new Error("Generated types are missing. Run npm run contracts:generate.");
    }
    // Git may check out CRLF on Windows; line endings do not change the contract.
    if (existing.replace(/\r\n/g, "\n") !== generated.replace(/\r\n/g, "\n")) {
      throw new Error("Generated types are stale. Run npm run contracts:generate.");
    }
    console.log("Generated TypeScript contracts are up to date.");
    return;
  }

  await mkdir(dirname(fileURLToPath(outputPath)), { recursive: true });
  await writeFile(outputPath, generated, "utf8");
  console.log("Generated src/types/generated.ts from contracts/models.schema.json.");
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
