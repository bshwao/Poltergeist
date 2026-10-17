import { readFile } from "node:fs/promises";
import ts from "typescript";

/** Load an isolated TS module with no runtime local imports, without build files. */
export async function loadTypeScript(sourceUrl) {
  const source = await readFile(sourceUrl, "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
    fileName: sourceUrl.pathname,
  });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
}
