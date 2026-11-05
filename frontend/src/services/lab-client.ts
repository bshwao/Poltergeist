import type { api } from "./api";

export const GUIDED_SUBMISSION_PREFIX = "poltergeist-guided-submission-v1/";

/** Shared UI boundary for the local API and an owner-scoped companion relay. */
export type LabClient = Pick<typeof api,
  "getGallery" | "listSuites" | "getSuite" | "saveSuite" | "updateSuite" | "deleteSuite" | "runSuite" | "getSuiteReport" | "listSuiteRuns"
  | "proposeDiscovery" | "listDiscoveryPlans" | "getDiscoveryPlan" | "approveDiscovery" | "getDiscoveryReport"
  | "getReplayHighlights" | "saveGuidanceFeedback" | "getOptimization" | "cancelOptimization" | "resumeOptimization"
  | "launchGuidedSimulation" | "getSimulation" | "getSimulationProgress" | "getSimulationResult" | "cancelSimulation" | "listAgents"
  | "getEvidenceAnalysis" | "getRegressionReport">;
