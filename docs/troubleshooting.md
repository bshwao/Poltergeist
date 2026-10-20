# Troubleshooting a local installation

Start with `python -m scripts.doctor --frontend`. Add `--carla` only when a server
is running, and `--carla-python /absolute/path/to/python` if the client lives in
another environment. The optional probe is read-only and bounded in a subprocess.

| Symptom | Check / next action |
| --- | --- |
| PowerShell says the expression after `&` is invalid | The interpreter variable is unset in this terminal. Use an explicit `.venv\Scripts\python.exe` path, or assign the full path again. |
| `No module named backend` or a missing command | Run module commands from the checkout or install the package in that interpreter. After updates, `python -m pip install --no-deps -e .` refreshes console entry points. |
| CARLA install fails on Python 3.14 | Keep 3.14 for core-only development; use the documented Python 3.12 environment for the CARLA 0.9.16 client. |
| API is connected but CARLA execution fails | `/health` reports API liveness only. Run the explicit doctor probe; check the selected interpreter, host, port and both client/server versions. |
| CARLA times out or spawns lag severely | Confirm exactly one instance and one synchronous tick owner. Finish or cancel active runs before another client takes ownership. Check hardware contention and [profiles](hardware.md). |
| Low-quality Town03 crashes before actors spawn | This native shader failure has been seen on the tested Windows installation. Use the documented map/graphics workaround in [setup](setup.md#carla-and-docker); a dashboard restart cannot repair an engine crash. |
| Actor outside a driving lane / invalid placement | Regenerate a bundled template on the loaded map. Custom absolute coordinates and supplied routes must actually match that map; the adapter keeps strict lane/connectivity checks. |
| Starter rejects driving goals | Update to the maintained SDK 1.3 starter, rerun its smoke check and create a fresh registration. Old snapshots remain historical evidence. |
| Agent readiness passes but load/inference fails | Readiness checks files/declarations only. Inspect the run's load/reset/step error, worker Python and dependency environment. Run `pip check` in that environment. No baseline fallback occurs. |
| Registration differs / fingerprint changed | Code, model, interpreter or SDK files changed after registration. Create a new registration; do not edit the old snapshot to make it appear identical. Source-aware reproduction needs its original executable identity. |
| RGB launch rejected with no-rendering | Set `no_rendering_mode` to false and use a compatible rig/timestep. Off-screen rendering can still supply RGB; disabled rendering cannot. |
| Duplicate experiment after a network error | Refresh experiment history before resubmitting. Mutations are not automatically retried. Inspect the accepted ID and retained evidence when using the validator. |
| Interrupted experiment after restart | Inspect its frozen identity/settings and use explicit **Resume remaining trials**. Resume preserves its journal and budget; it cannot switch agent versions. |
| Unknown metric or no paired difference | Check observation coverage, completion and runtime-version exclusions. Missing data stays unknown and is never converted to zero. |
| Behavior export fails | Check that the run is finalized and its artifacts still exist. Retry explicitly. For metadata-only recovery, disable behavior analysis; do not invent missing evidence. |
| Browser tests cannot start | Run `npm ci` and `npx playwright install chromium`, set the test interpreter, and free ports 8049/5189. On Linux install browser system dependencies. The suite refuses to attach to an unrelated API. |

Keep the first failed evidence directory. It can contain an accepted-operation
ID, comparison journal, database, recordings and reports even when the final
validation status is false. Each retry uses a new directory; nothing is silently
overwritten. Source examples require a checkout; wheel users must supply
`--examples-dir` explicitly. Follow the [first-run guide](first-run.md) for the
complete sequence and the [validation record](phase10-validation.md) for what has
actually been checked.
