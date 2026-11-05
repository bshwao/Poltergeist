from simulator.agents.aim import AIMCameraAgent
from simulator.agent_sensors import resolve_sensor_specs
from simulator.agents.transfuser.rig import manifest
from simulator.agents.sdk import AgentCompatibilityError
class AIMMatchedRig(AIMCameraAgent):
    def reset(self, context):
        expected=resolve_sensor_specs(manifest('unused')['sensors'])
        if sorted(context.sensors,key=lambda s:s.id)!=sorted(expected,key=lambda s:s.id):
            raise AgentCompatibilityError('Matched model comparison requires the exact common camera/LiDAR rig')
        super().reset(context.model_copy(update={'sensors':[s for s in context.sensors if s.id=='front-camera']}))
    def step(self, observation):
        samples={s.sensor_id:s for s in observation.sensors}
        if len(observation.sensors)!=2 or set(samples)!={'front-camera','lidar'}:
            raise AgentCompatibilityError('Matched model comparison requires both declared sensors')
        lidar=samples['lidar']
        if lidar.frame!=observation.frame or abs(lidar.timestamp_s-observation.time_s)>1e-6:
            raise AgentCompatibilityError('Ignored LiDAR must still belong to the exact current input frame')
        result=super().step(observation.model_copy(update={'sensors':[samples['front-camera']]}))
        result.diagnostics.metadata.update(comparison_rig='cvpr2021-camera-lidar',
            ignored_lidar_sha256=lidar.sha256,ignored_lidar_frame=lidar.frame,
            model_input_note='AIM uses RGB and explicitly ignores the additional declared LiDAR; no LiDAR-derived controls')
        return result
