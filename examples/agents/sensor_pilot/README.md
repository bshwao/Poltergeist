# Camera and LiDAR teaching controller

This is a **rule-based integration demonstrator**, not a trained AI model or a
replacement for the privileged-state PlanT2 reference pilot. It demonstrates
that actual RGB/LiDAR bytes can change controls through SDK 1.3.

The controller transforms LiDAR XYZI points using the declared mount, removes
ground and its configured ego hull, then brakes for nearby forward corridor
returns. Very dark/black camera images stop the car as an explicit visibility
guard. Camera pixels do not provide object detection or traffic-light logic.
It follows the framework's finite route using declared ideal simulated ego
localization and uses an explicit deterministic goal speed controller.

Run from the repository root after starting CARLA and installing the project
in your CARLA Python environment:

```powershell
& $carlaPy -m simulator --scenario examples/scenarios/sensor_pilot_town03.json --mode carla --allow-map-reload --agent-package examples/agents/sensor_pilot --agent-python $carlaPy --output storage/reports/sensor-pilot.json
```

RGB requires rendering: do not use CARLA's `no_rendering_mode`. An offscreen
CARLA window can still render camera images. The example requests a 320 by 180
camera and 32-channel LiDAR. It requires no model download or GPU inference.

Synthetic test mode uses explicitly manufactured camera/LiDAR fixtures. It
checks the integration and decision rules only; it does not render the world
or establish perception quality. Real CARLA point clouds and pixel inputs
remain necessary for meaningful sensor testing.
