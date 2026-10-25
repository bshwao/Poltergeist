import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const repository = fileURLToPath(new URL("../../", import.meta.url));
const child = spawn(process.env.POLTERGEIST_TEST_PYTHON || "python", ["-u", "tests/hosted_browser_server.py"], {
  cwd: repository, stdio: ["pipe", "inherit", "inherit"], windowsHide: true, shell: false,
});
child.on("error", () => { process.stderr.write("Could not start hosted browser fixture.\n"); process.exitCode = 1; });
child.on("exit", code => { process.exitCode = code ?? 1; });
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => {
  child.stdin.end("stop\n");
  const deadline = setTimeout(() => child.kill(), 15_000);
  deadline.unref();
});
