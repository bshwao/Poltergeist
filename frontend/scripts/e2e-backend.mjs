import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const repository = fileURLToPath(new URL("../../", import.meta.url));
const python = process.env.POLTERGEIST_TEST_PYTHON || "python";
const child = spawn(python, ["-m", "scripts.browser_test_server"], {
  cwd: repository, stdio: "inherit", windowsHide: true, shell: false,
});
child.on("error", error => { process.stderr.write(`Could not start test Python: ${error.message}\n`); process.exitCode = 1; });
child.on("exit", code => { process.exitCode = code ?? 1; });
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => child.kill(signal));
