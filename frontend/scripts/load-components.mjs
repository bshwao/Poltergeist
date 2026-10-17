import { access, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url));
const cacheRoot = path.resolve(fileURLToPath(new URL("../node_modules/.cache/", import.meta.url)));

/** Compile the real component graph for Node tests, retaining the same React instance. */
export async function loadComponents(entry) {
  await mkdir(cacheRoot, { recursive: true });
  const directory = await mkdtemp(path.join(cacheRoot, "poltergeist-components-"));
  await writeFile(path.join(directory, "package.json"), '{"type":"module"}');
  const compiled = new Map();

  async function compile(filename) {
    const absolute = path.resolve(filename);
    if (compiled.has(absolute)) return compiled.get(absolute);
    const relative = path.relative(sourceRoot, absolute);
    if (relative.startsWith("..") || path.isAbsolute(relative)) throw new Error("Component source must remain inside src");
    const destination = path.join(directory, relative.replace(/\.tsx?$/, ".js"));
    compiled.set(absolute, destination);
    const source = await readFile(absolute, "utf8");
    let { outputText } = ts.transpileModule(source, {
      fileName: absolute,
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    });
    // The renderer checks behavior. Vite checks stylesheet imports separately.
    outputText = outputText.replace(/^\s*import\s+["'][^"']+\.css["'];?\s*$/gm, "");
    for (const specifier of new Set([...outputText.matchAll(/\bfrom\s+["'](\.[^"']+)["']/g)].map(match => match[1]))) {
      const base = path.resolve(path.dirname(absolute), specifier);
      let dependency;
      for (const candidate of [base, `${base}.ts`, `${base}.tsx`]) {
        try { await access(candidate); dependency = candidate; break; } catch { /* Try the next source extension. */ }
      }
      if (!dependency) throw new Error(`Could not resolve component dependency ${specifier}`);
      const target = await compile(dependency);
      let replacement = path.relative(path.dirname(destination), target).split(path.sep).join("/");
      if (!replacement.startsWith(".")) replacement = `./${replacement}`;
      outputText = outputText.replaceAll(`"${specifier}"`, `"${replacement}"`).replaceAll(`'${specifier}'`, `'${replacement}'`);
    }
    await mkdir(path.dirname(destination), { recursive: true });
    await writeFile(destination, outputText);
    return destination;
  }

  const filename = await compile(path.join(sourceRoot, entry));
  return {
    module: await import(pathToFileURL(filename).href),
    moduleAt: async (relative) => import(pathToFileURL(await compile(path.join(sourceRoot, relative))).href),
    cleanup: async () => {
      if (path.dirname(path.resolve(directory)) !== cacheRoot) throw new Error("Unexpected component test directory");
      await rm(directory, { recursive: true, force: true });
    },
  };
}
