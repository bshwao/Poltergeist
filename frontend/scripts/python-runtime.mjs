import { existsSync } from "node:fs";
import { join } from "node:path";

export function browserTestPython(repository) {
  const local = join(repository, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  return process.env.POLTERGEIST_TEST_PYTHON || (existsSync(local) ? local : "python");
}
