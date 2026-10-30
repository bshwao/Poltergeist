import type { api } from "./api";

/** Shared UI boundary for the local API and an owner-scoped companion relay. */
export type LabClient = Pick<typeof api,
  "getGallery" | "listSuites" | "getSuite" | "saveSuite" | "updateSuite" | "deleteSuite" | "runSuite" | "getSuiteReport" | "listSuiteRuns"
  | "proposeDiscovery" | "listDiscoveryPlans" | "getDiscoveryPlan" | "approveDiscovery" | "getDiscoveryReport"
  | "getReplayHighlights" | "saveGuidanceFeedback" | "getOptimization" | "cancelOptimization" | "resumeOptimization"
  | "launchSimulation" | "getSimulation" | "getSimulationProgress" | "getSimulationResult" | "cancelSimulation" | "listAgents">;
