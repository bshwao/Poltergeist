# Poltergeist

Poltergeist is a driving simulation and evaluation platform for exploring how
vehicles respond to dangerous or unusual road situations. Built around CARLA,
it brings scenario creation, simulation, and evidence review into one workspace.

Its central goal is to help developers and researchers test autonomous driving
agents under controlled conditions: define the situation, let the agent choose
how to respond, and examine what happened.

## Why Poltergeist exists

An autonomous vehicle needs to handle more than ordinary driving. A pedestrian
may step into its path, a leading vehicle may brake suddenly, or another car
may enter an intersection at the wrong moment. These situations are difficult
to reproduce consistently on real roads.

Poltergeist makes it possible to construct these encounters in simulation,
adjust their conditions, and repeat them. The aim is to turn a driving failure
into something that can be inspected, reproduced, and used to improve a system.

## How it works

1. **Create a scenario.** Start from an intersection, pedestrian crossing,
   sudden braking, or occluded pedestrian template, or define a custom recipe.
2. **Set the conditions.** Choose initial speeds, actor placement, hazard timing,
   weather, and experiment duration.
3. **Run the experiment.** Execute in CARLA and collect vehicle motion, events,
   safety measurements, and optional sensor recordings.
4. **Review the evidence.** Inspect results, telemetry, failures, and reports;
   change the conditions and run again.

For autonomous agent experiments, the scenario defines the environment and
initial conditions while the agent chooses the ego vehicle's evolving steering,
throttle, and braking. Driving goals and route assistance are recorded so that
results can be interpreted in context.

## What is available today

- A browser dashboard for building, saving, validating, and executing scenarios,
  then inspecting run history, safety metrics, telemetry, and recordings.
- CARLA execution and a synthetic test mode for exercising application workflows
  without running the simulator.
- A private [Windows companion prototype](docs/companion.md) with its own runtime,
  local configuration/diagnostics, durable execution and report import/export.
  Installer, hosted pairing and independent clean-machine release validation
  remain open.
- Repeatable experiment batches, failure reproduction, parameter search,
  scenario minimization, and report exports.
- An Experiments page for registered agent/version comparisons, matched seeds,
  repeated trials and source-aware search/reproduction/minimization, with
  frozen settings, explicit unknowns and downloadable comparison evidence.
- A Python agent SDK and external worker for trusted local packages, with
  command-line execution and a persistent registry for queued experiments.
- An Agents page for registering local packages, inspecting their saved
  identity, checking static readiness, and selecting a controller when launching
  a scenario.
- A maintained pretrained PlanT 2.0 integration using structured simulator state,
  plus synchronized RGB camera and LiDAR inputs for compatible sensor agents.
- A saved decision timeline linking each agent input to its proposed/applied
  controls, diagnostics, interventions and following measured frame, with exact
  sensor-input inspection in the dashboard.
- A read-only behavior-analysis API for event-relative control/motion response,
  braking/stopping episodes and recorded route measurements, with frame evidence,
  explicit coverage and preserved historical safety scores. See the
  [analysis guide](docs/behavior-analysis.md). Finished runs include a dashboard
  behavior review with event markers and links to supporting decision/telemetry
  frames. JSON, CSV and printable HTML behavior reports preserve the same
  measurements, qualified descriptions and separately labelled agent diagnostics.

Registered agents can
be launched through the dashboard or API. The included sensor pilot is a
rule-based teaching example. PlanT is the trained reference model and uses
structured state rather than camera-based perception.

## Where the project is going

The longer-term goal is a workspace where users can connect their own autonomous
driving agents, test them against challenging scenarios, and compare their
responses across versions and conditions.

The current workflow includes synchronized observation/action review, measured
behavior analysis and agent-aware comparisons and failure search. Future work
can build on these recorded experiments and broader model/perception support.
Reports help explain observed behavior using recorded inputs,
controls, outcomes, and any diagnostics supplied by the agent. They should
distinguish measured evidence from inferred explanations of a model's reasoning.

Simulation results depend on the scenario, available observations, simulator,
and evaluation assumptions. They support investigation and comparison; a
successful run does not establish real-world driving safety.

## Getting started

Follow the [first-run walkthrough](docs/first-run.md) for installation, diagnostics,
an isolated end-to-end check and your first agent comparison. The
[setup guide](docs/setup.md) covers detailed backend/dashboard configuration.
Then use the [dashboard guide](docs/dashboard.md) to create and
run your first scenario. Synthetic test mode lets you try the workflow before
setting up CARLA.

For autonomous driving experiments, start with the
[reference AI guide](docs/reference-ai-pilot.md), the
[agent SDK](docs/agent-sdk.md), or the
[camera and LiDAR agent guide](docs/sensor-agents.md).
Use the [agent registry guide](docs/agent-registry.md) to register a trusted local
package and select it for a queued dashboard or API experiment.
Review its recorded behavior using the [decision timeline guide](docs/decision-timeline.md).

## Learn more

- [Scenario creation](docs/scenarios.md) and [simulation execution](docs/simulation.md)
- [Collision stress presets and unchanged-baseline benchmark](docs/collision-stress.md)
- [Driving goals and control ownership](docs/driving-tasks.md)
- [Safety evaluation methodology](docs/methodology.md) and [report exports](docs/reports.md)
- [Agent comparisons and repeated trials](docs/agent-comparisons.md), [experiment batches](docs/experiments.md), and [failure search and reproduction](docs/optimization.md)
- [Architecture](docs/architecture.md), [API](docs/api.md), and [Docker setup](docs/docker.md)
- [Autonomous driving roadmap](docs/autonomous-agents-plan.md)
- [Distribution and scalability roadmap: hosted services and companion app](docs/scalability-plan.md)
- [Hardware profiles](docs/hardware.md), [troubleshooting](docs/troubleshooting.md), and [Phase 10 validation](docs/phase10-validation.md)
