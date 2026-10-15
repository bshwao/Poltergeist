# Architecture

Poltergeist generates CARLA scenarios, runs agents through them, evaluates telemetry,
and exposes results through the backend API and dashboard.

The shared boundary is `shared.models`: Pydantic models validate scenarios,
telemetry, run lifecycle records, results, and failures. A deterministic schema
export generates the frontend types. See [data contracts](data-contracts.md).

The API factory `backend.main.create_app()` builds the application with validated
settings and CORS. `python -m backend` uses configured host/port. Bootstrapping
does not open a CARLA connection, initialize a database, or create storage.

The section 2 scenario engine safely loads and normalizes YAML/JSON, validates
template parameter bindings, and produces seeded configurations with typed
events and symbolic actor-relative geometry. It is independent of CARLA. See
[scenario engine usage](scenarios.md).

The section 3 evaluator consumes validated telemetry, groups ego events, computes
3D clearance/TTC and braking/stopping metrics, and returns coverage-aware
`SafetyMetrics` with versioned evaluation settings. It has no CARLA dependency.
Explicit per-frame rule coverage keeps unavailable map checks from appearing as
compliance. See [methodology](methodology.md).

The section 4 `SimulationRunner` owns one synchronous run: scenario snapshot,
fixed-step event dispatch, driving controls, cooperative cancellation, sensor
finalization, resource cleanup, and evaluation. It returns a terminal result plus
the finalized telemetry in memory. Frame/progress/raw sensor callbacks let future
orchestration and storage subscribe without putting database work in adapters.
Callback failures fail the run and still execute cleanup.

The adapter boundary in `simulator.protocols` separates the runner and agents from
CARLA. `CarlaSimulationAdapter` imports its optional client lazily, checks 0.9.16
versions, manages a synchronous world, validates/spawns actors, and collects native
snapshot and sensor observations. It restores prior world settings/weather and
destroys only resources it owns. The `TestSimulationAdapter` supplies deterministic
synthetic kinematics without a server. Test results retain `mode: "test"`; its
virtual geometry does not certify CARLA map validity or physics behavior.

`BaselineAgent` regulates speed/heading and brakes for observed hazards or a red
light. `BaseAgent` and `CustomAgent` support injected controllers. This development
baseline has no perception pipeline or complete route/intersection planner.
Runtime metadata records versions, actual map, seed, step, and resolved spawns.
See [simulation usage and limitations](simulation.md).

The database, artifact persistence, worker queue, and feature routes remain for
their respective [implementation sections](implementation-plan.md). The runner is
currently invoked from Python or its local CLI; `/health` does not start it.
