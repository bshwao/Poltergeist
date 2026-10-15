# Safety evaluation methodology

The section 3 evaluator consumes measured `TelemetryFrame` records and returns
`SafetyMetrics`. It runs without CARLA or numerical packages. Its public API is
exported from `evaluation`; the simulator adapter will supply observations in
section 4. The results describe the supplied observation window, which may be
shorter than a complete run.

## Usage

```python
from evaluation import evaluate_safety
from shared.models import ActorState, BoundingBoxExtent, TelemetryFrame, Transform, Vector3

def vehicle(actor_id, x, speed):
    return ActorState(
        actor_id=actor_id,
        transform=Transform(position=Vector3(x=x)),
        bounding_box_extent=BoundingBoxExtent(x=2, y=1, z=1),
        velocity_mps=Vector3(x=speed),
        acceleration_mps2=Vector3(),
    )

metrics = evaluate_safety([
    TelemetryFrame(
        frame=0, time_s=0,
        ego=vehicle("ego", 0, 10),
        actors=[vehicle("lead", 14, 5)],
        collisions=[], traffic_violations=[],
    ),
])
assert metrics.min_clearance_m == 10
assert metrics.min_ttc_s == 2
assert metrics.coverage.complete
assert abs(metrics.severity - 15) < 1e-9
```

`evaluate_safety(frames, settings=EvaluationSettings(...))` aggregates a trace.
Pair helpers are `bounding_box_clearance` and `time_to_collision`; detailed
episode summaries are `summarize_collisions`, `summarize_traffic_rules`, and
`summarize_braking`. `calculate_severity` recomputes the score from aggregate
metrics. Inputs are revalidated and copied, including mutable model instances.

## Observation coverage

Pose, velocity, acceleration, and event/actor feeds can be unobserved. Missing
values remain `null`. For `actors`, `collisions`, and `traffic_violations`, an empty
list means the source was observed and found none; `null` means the source was
not observed. A producer must send an empty rule list only after performing its
configured ego rule checks. A collision feed need not contain an event every tick,
but its producer must distinguish a functioning sensor with no callback from a
missing/unavailable sensor.

Unobserved collision/rule counts and braking metrics are `null`, not zero. Null
clearance/TTC can also mean there were no relevant actor pairs or no predicted
contact within the TTC horizon. `coverage` records observed frame/pair counts so
these cases can be distinguished. A derived speeding count alone is partial rule
coverage; it does not establish red-light, stop-sign, wrong-way, or sidewalk
compliance. Counts from partially observed traces cover observed episodes only.

Frame numbers and elapsed times must strictly increase and the ego ID must stay
the same. Duplicate or reordered samples are rejected. Skipped frame IDs or time
gaps above `max_sample_gap_s` mark the window incomplete. Gaps also break braking
continuity and prevent fabricated stopping distances. A nonempty window has
complete coverage only when collision, full rule, braking, clearance, and TTC
observations cover every frame and every listed actor pair, with no gaps.

## Collision episodes

Collision counts use sensor events against ego, including static-world actors.
Geometric overlap alone is reported as zero clearance/TTC, and does not create a
sensor collision. CARLA can produce collision callbacks in each frame and against
multiple actors; see its [sensor reference](https://carla.readthedocs.io/en/0.9.16/ref_sensors/#collision-detector).

Repeated contacts with the same other actor are one episode while successive
callbacks are no more than `collision_episode_gap_s` apart (default 1 second).
Empty collision frames do not immediately end a contact: intermittent callbacks
may describe the same contact. A larger gap starts another episode. Separate
other-actor IDs remain separate, even in one frame. Episode summaries retain
first/last observation times and maximum impulse in newton-seconds.

Optional `event_id` values identify individual observations globally within the
trace. Replays with the same actor/impulse are ignored without extending an
episode; conflicting payloads for an ID are rejected. Distinct observation IDs
can still belong to one contact episode. A gap threshold of zero merges only
contacts occurring at the same sample time.

## Clearance and predicted TTC

Clearance is the shortest Euclidean distance between two three-dimensional
oriented bounding-box surfaces. Overlap or touching is zero. It uses yaw, pitch,
and roll and therefore handles vertical separation as well as adjacent lanes.
Missing positive half-extents or box pose produce `null`; the evaluator never
substitutes a point-sized vehicle.

An explicit `bounding_box_transform` is the world pose of the actual box center.
Otherwise the box is assumed centered on/aligned with `ActorState.transform`.
The CARLA adapter must account for local bounding-box offsets and rotations before
setting the world box pose. Rotation axes follow the
[CARLA rotation implementation](https://carla.org/Doxygen/html/d0/d8f/Rotation_8h_source.html).

Static intersection uses the separating-axis test; separation distance checks
vertex/box and edge/edge feature pairs. Contact has a 1e-9 metre arithmetic
tolerance. Non-finite inputs and arithmetic overflow are rejected.

TTC is the first predicted simultaneous box contact within an inclusive finite
time horizon (default 10 seconds). Swept separating-axis intervals assume fixed
orientation and constant world linear velocity. Acceleration, angular velocity,
and future steering are excluded. This handles following, head-on, crossing, and
parallel miss cases without dividing clearance by radial closing speed alone.
See Eberly's [oriented-box collision method](https://www.geometrictools.com/Documentation/DynamicCollisionDetection.pdf).
The assumption is a surrogate prediction, consistent with using current motion
to project conflicts; FHWA describes related TTC assumptions in its
[SSAM methodology](https://www.fhwa.dot.gov/publications/research/safety/08051/02.cfm).

Current overlap yields TTC 0 even if velocity is missing. Otherwise missing
velocity/geometry or no predicted contact within the horizon yields `null`.
`can_compute_ttc` identifies whether a pair has sufficient observed data;
`geometry_available` checks geometry availability. Aggregates take minima over
observed pairs; actor IDs must be unique within each frame.

## Traffic-rule episodes

Red-light, stop-sign, wrong-way, and sidewalk events come from the future map-aware
detectors. The evaluator counts reported ego events and does not infer those
violations from actor coordinates alone. `source_id` identifies a light, sign,
lane, or speed zone. Rule/source combinations are separate episodes. An observed
clear rule list closes absent episodes immediately; an unobserved list leaves
them pending, subject to a 1 second maximum gap between active samples.

Speeding can additionally be derived from ego velocity magnitude and a measured
positive `speed_limit_mps`. Speed must strictly exceed limit plus the configured
tolerance (default 0.5 m/s). Reported and derived speeding are merged when they
refer to the same ongoing incident; fresh named source observations preserve
distinct zones. Stable event IDs deduplicate replayed rule observations, and
conflicting actor/rule/source/details payloads are rejected. Historical replays
cannot reassign the current speed zone.

A sole continuous anonymous speeding episode acquires its first named source
without increasing the count; anonymous callbacks can continue a sole known zone.
When multiple named zones make attribution ambiguous, anonymous evidence remains
anonymous rather than choosing a zone. Replay-only speeding lists without current
velocity evidence do not hold a zone open. Counts therefore represent these
explicit grouping rules, not independently verified discrete road maneuvers.

## Braking and stopping

With measured velocity `v` and acceleration `a`, deceleration magnitude is:

```text
deceleration = max(0, -dot(a, v / |v|))
```

At speeds at or below `stopped_speed_mps` (default 0.1 m/s), it is zero when both
measurements exist. The projection supports reversing and avoids counting purely
perpendicular cornering acceleration as braking. If acceleration is absent,
consecutive velocity samples can estimate interval-average deceleration:
`max(0, (previous_speed - speed) / dt)`. Skipped frames, unavailable speed, or
excessive time gaps prevent this inference. The first velocity-only sample has no
deceleration measurement, so it leaves that frame's braking coverage incomplete.

Hard braking is a contiguous sequence with calculated deceleration at or above
`hard_braking_threshold_mps2` (default 4 m/s²). Missing measurements and gaps break
the sequence. Summaries report maximum observed deceleration and episode count.

A stopping episode begins while moving with a positive brake/handbrake command
or calculated deceleration. Measured acceleration/commands begin at the current
sample; inferred deceleration begins at the preceding sample because it describes
that interval. Distance integrates speed by the trapezoid rule until speed reaches
the stopped threshold. A moving sample without braking evidence, missing speed,
or a gap abandons the pending stop. Unfinished episodes are omitted. Aggregate
stopping time/distance are the maxima of completed measured stops, not theoretical
reaction or stopping-distance predictions.

## Severity version 1.0

Thresholds and weights are configurable and saved with the metrics in
`evaluation_settings`, including `method_version`. These are initial project
search heuristics, not calibrated crash probabilities or regulatory pass/fail
limits. A sensor collision sets severity to 100 even under partial coverage.
Otherwise incomplete coverage produces `null`, so missing data cannot appear as
a reassuring zero score.

For a completely observed window, let `clip(x)` clamp to [0, 1]:

```text
TTC risk       = clip(1 - minimum_TTC / 3 seconds)
clearance risk = clip(1 - minimum_clearance / 2 metres)
braking risk   = clip((maximum_deceleration - 4) / (8 - 4))
rule risk      = clip(violation_episode_count / 3)
severity       = min(99, 45*TTC risk + 25*clearance risk
                       + 15*braking risk + 15*rule risk)
```

Under complete coverage, null TTC means no predicted contact in the horizon;
null clearance means no other actors were observed. Their contributions are zero.
Scoring uses no completed-stop penalty. The noncollision cap reserves 100 for
sensor-confirmed collisions. Weights must sum to 100, the TTC horizon must cover
the warning threshold, and full braking severity must exceed the hard-braking
threshold. Numeric outputs are finite and JSON-safe.

Later optimization should reject or separately surface unscored windows rather
than converting null severity to zero. Actual map-aware detection, callback/frame
synchronization, and simulator behavior are section 4 work.
