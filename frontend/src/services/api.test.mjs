import test, { after, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadComponents } from "../../scripts/load-components.mjs";

async function loadApiModule() {
  const loaded = await loadComponents("services/api.ts");
  after(() => loaded.cleanup());
  return loaded.module;
}

const { api, ApiError, API_BASE_URL, createApiClient } = await loadApiModule();
const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

test("same-origin API prefixes support reverse proxies without embedding a host", async () => {
  const calls = [];
  globalThis.fetch = async url => { calls.push(url); return jsonResponse(health); };
  await createApiClient("/api/").health();
  await createApiClient("/").health();
  assert.deepEqual(calls, ["/api/health", "/health"]);
  for (const invalid of ["//other.example/api", "/api/../secret", "/api/./health", "/api?key=value", "/api#fragment", "/api\\secret", "/%2e%2e", "/api path"]) assert.throws(() => createApiClient(invalid));
});

test("report links use owned IDs, validated formats, and an explicit JSON telemetry option", () => {
  const client = createApiClient("/api");
  const id = "00000000-0000-4000-8000-000000000001";
  assert.equal(client.reportUrl(id, "json", true), `/api/reports/simulations/${id}?format=json&include_telemetry=true`);
  assert.equal(client.reportUrl(id, "csv"), `/api/reports/simulations/${id}?format=csv`);
  assert.equal(client.reportUrl(id, "html"), `/api/reports/simulations/${id}?format=html`);
  for (const args of [["../../private", "json"], [id, "exe"], [id, "csv", true], [id, "html", "true"]]) assert.throws(() => client.reportUrl(...args), ApiError);
});

const recipe = {
  name: "Repeatable crossing", description: "A crossing pedestrian", map: "Town03", seed: 17, duration_s: 30,
  template: "pedestrian_crossing", fixed_delta_seconds: 0.05,
  actors: [{ id: "ego", type: "ego_vehicle", speed_mps: 10 }], events: [], weather: {}, parameters: {},
  driving_task: null,
};
const health = { status: "ok", service: "poltergeist" };
const jsonResponse = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});

test("health uses the configured default URL and forwards an AbortSignal without a content-type header", async () => {
  const controller = new AbortController();
  globalThis.fetch = async (url, options) => {
    assert.equal(url, `${API_BASE_URL}/health`);
    assert.equal(options.method, "GET");
    assert.equal(options.signal, controller.signal);
    assert.deepEqual(options.headers, { Accept: "application/json" });
    assert.equal("body" in options, false);
    return jsonResponse(health);
  };
  assert.deepEqual(await api.health({ signal: controller.signal }), health);
});

test("template metadata and generation preserve the canonical request and response", async () => {
  const input = { template: "pedestrian_crossing", sample: true, seed: 0, parameters: { ego_speed_mps: 10 }, ranges: { pedestrian_start_s: [1, 3] } };
  const templates = [{ template: input.template, parameters: [], scenario: recipe }];
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return jsonResponse(url.endsWith("/templates") ? templates : recipe);
  };
  assert.deepEqual(await api.listTemplates(), templates);
  assert.deepEqual(await api.generateScenario(input), recipe);
  assert.equal(calls[1].url, `${API_BASE_URL}/scenarios/generate`);
  assert.equal(calls[1].options.method, "POST");
  assert.equal(calls[1].options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[1].options.body), input);
});

test("validation sends the full edited recipe without normalizing it in the client", async () => {
  const edited = { ...recipe, events: [{ actor_id: "lead", parameters: { target_speed_mps: 0 } }] };
  globalThis.fetch = async (url, options) => {
    assert.equal(url, `${API_BASE_URL}/scenarios/validate`);
    assert.deepEqual(JSON.parse(options.body), edited);
    return jsonResponse(edited);
  };
  assert.deepEqual(await api.validateScenario(edited), edited);
});

test("FastAPI validation errors expose field paths while omitting input and context", async () => {
  globalThis.fetch = async () => jsonResponse({ detail: [
    { loc: ["body", "seed"], msg: "Input should be greater than or equal to 0", input: "private", ctx: { error: "private" } },
    { loc: ["body", "actors", 0, "speed_mps"], msg: "Input should be a valid number" },
    { loc: ["body"], msg: "Value error, exactly one ego actor is required" },
  ] }, 422);
  await assert.rejects(api.validateScenario(recipe), error => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 422);
    assert.deepEqual(error.issues, [
      { path: "seed", message: "Input should be greater than or equal to 0" },
      { path: "actors[0].speed_mps", message: "Input should be a valid number" },
      { path: "", message: "exactly one ego actor is required" },
    ]);
    assert.doesNotMatch(error.message, /private/);
    return true;
  });
});

test("nested Pydantic diagnostics keep field reasons without submitted input or documentation links", async () => {
  globalThis.fetch = async () => jsonResponse({ detail: [{ loc: ["body"], msg:
    "Value error, invalid scenario: 2 validation errors for Scenario\nname\n  Field required [type=missing, input_value={}, input_type=dict]\n    For further information visit https://errors.pydantic.dev/2.14/v/missing\nactors\n  List should have at least 1 item after validation, not 0 [type=too_short, input_value=[], input_type=list]\n    For further information visit https://errors.pydantic.dev/2.14/v/too_short",
  }] }, 422);
  await assert.rejects(api.validateScenario({}), error => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 422);
    assert.equal(error.issues.length, 1);
    assert.equal(error.issues[0].message, "invalid scenario: name: Field required; actors: List should have at least 1 item after validation, not 0");
    assert.doesNotMatch(error.issues[0].message, /input_value|input_type|type=|errors\.pydantic|For further information|Value error|https?:\/\//);
    return true;
  });
});

for (const [status, detail] of [[409, "Recipe revision changed"], [429, "Simulation queue is full"], [503, "Persistence is unavailable"]]) {
  test(`safe HTTP ${status} details remain actionable`, async () => {
    globalThis.fetch = async () => jsonResponse({ detail }, status);
    await assert.rejects(api.health(), error => error instanceof ApiError && error.status === status && error.message === detail);
  });
}

for (const body of ["<html><body>proxy stack</body></html>", { detail: "Traceback (most recent call last): private.py" }, { detail: "<script>secret</script>" }, { detail: { internal: "private" } }]) {
  test(`unexpected service failures do not expose HTML or tracebacks: ${typeof body === "string" ? "HTML" : JSON.stringify(body)}`, async () => {
    globalThis.fetch = async () => typeof body === "string" ? new Response(body, { status: 503 }) : jsonResponse(body, 503);
    await assert.rejects(api.health(), error => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, 503);
      assert.match(error.message, /temporarily unavailable/);
      assert.doesNotMatch(error.message, /secret|private|script|html|Traceback/);
      return true;
    });
  });
}

test("unexpected HTTP 500 details use a generic message even when they are plain text", async () => {
  globalThis.fetch = async () => jsonResponse({ detail: "database password private" }, 500);
  await assert.rejects(api.health(), error => error instanceof ApiError && error.status === 500 && !error.message.includes("private"));
});

test("network failures have a stable message without leaking a transport diagnostic", async () => {
  globalThis.fetch = async () => { throw new TypeError("private proxy diagnostic"); };
  await assert.rejects(api.health(), error => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 0);
    assert.match(error.message, /backend is running/);
    assert.doesNotMatch(error.message, /private proxy/);
    return true;
  });
});

test("fetch cancellation preserves the original AbortError", async () => {
  const controller = new AbortController();
  const abort = new DOMException("Cancelled", "AbortError");
  globalThis.fetch = async (_url, options) => {
    assert.equal(options.signal, controller.signal);
    controller.abort();
    throw abort;
  };
  await assert.rejects(api.health({ signal: controller.signal }), error => error === abort);
});

test("cancellation during response reading is not reported as malformed JSON", async () => {
  const controller = new AbortController();
  const abort = new DOMException("Cancelled", "AbortError");
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => { controller.abort(); throw abort; } });
  await assert.rejects(api.health({ signal: controller.signal }), error => error === abort);
});

test("malformed JSON and a response with the wrong shape fail before the UI consumes them", async () => {
  globalThis.fetch = async () => new Response("not-json", { status: 200 });
  await assert.rejects(api.health(), error => error instanceof ApiError && /invalid JSON/.test(error.message));
  for (const invalid of [null, 0, "ok", [], { status: "ok" }]) {
    globalThis.fetch = async () => jsonResponse(invalid);
    await assert.rejects(api.health(), error => error instanceof ApiError && /unexpected response/.test(error.message));
  }
  globalThis.fetch = async () => jsonResponse({ templates: [] });
  await assert.rejects(api.listTemplates(), ApiError);
  globalThis.fetch = async () => jsonResponse([{}]);
  await assert.rejects(api.listTemplates(), ApiError);
  globalThis.fetch = async () => jsonResponse({ name: "missing recipe fields" });
  await assert.rejects(api.validateScenario(recipe), ApiError);
});

test("nested malformed actor and recipe fields are rejected before recipe summaries render", async () => {
  const invalidRecipes = [
    { ...recipe, description: { text: "not renderable" } },
    { ...recipe, name: null },
    { ...recipe, seed: 0.5 },
    { ...recipe, duration_s: 0 },
    { ...recipe, fixed_delta_seconds: -0.1 },
    { ...recipe, actors: [null] },
    { ...recipe, actors: ["ego"] },
    { ...recipe, actors: [{ id: {}, type: "ego_vehicle", speed_mps: 10 }] },
    { ...recipe, actors: [{ id: "ego", type: null, speed_mps: 10 }] },
    { ...recipe, actors: [{ id: "ego", type: "unsupported", speed_mps: 10 }] },
    { ...recipe, actors: [{ id: "ego", type: "ego_vehicle", speed_mps: "10" }] },
    { ...recipe, actors: [{ id: "ego", type: "ego_vehicle", speed_mps: -1 }] },
    { ...recipe, actors: [] },
    { ...recipe, events: [null] },
    { ...recipe, parameters: null },
    { ...recipe, parameters: { speed: {} } },
  ];
  for (const invalid of invalidRecipes) {
    globalThis.fetch = async () => jsonResponse(invalid);
    await assert.rejects(api.validateScenario(recipe), error => error instanceof ApiError && /unexpected response/.test(error.message));
    globalThis.fetch = async () => jsonResponse([{ template: recipe.template, scenario: invalid, parameters: [] }]);
    await assert.rejects(api.listTemplates(), ApiError);
  }
});

test("template parameter metadata is validated before numeric fields and defaults render", async () => {
  const parameter = { name: "ego_speed_mps", minimum: 0, maximum: 30, default: 10, integer: false, unit: "m/s", description: "Initial speed" };
  const catalog = (parameters) => [{ template: recipe.template, scenario: recipe, parameters }];
  for (const invalid of [
    null, {}, { ...parameter, name: {} }, { ...parameter, name: " " },
    { ...parameter, unit: null }, { ...parameter, description: {} }, { ...parameter, integer: "false" },
    { ...parameter, minimum: "0" }, { ...parameter, maximum: null },
    { ...parameter, minimum: 40 }, { ...parameter, default: -1 }, { ...parameter, default: 31 },
    { ...parameter, integer: true, default: 1.5 },
  ]) {
    globalThis.fetch = async () => jsonResponse(catalog([invalid]));
    await assert.rejects(api.listTemplates(), error => error instanceof ApiError && /unexpected response/.test(error.message));
  }
  globalThis.fetch = async () => jsonResponse(catalog([parameter, parameter]));
  await assert.rejects(api.listTemplates(), ApiError);
  globalThis.fetch = async () => jsonResponse(catalog([parameter]));
  assert.deepEqual(await api.listTemplates(), catalog([parameter]));
});

test("catalogs reject unsupported, mismatched, and duplicate templates while allowing an empty catalog", async () => {
  for (const invalid of [
    [{ template: "unsupported", scenario: recipe, parameters: [] }],
    [{ template: "intersection", scenario: recipe, parameters: [] }],
    [{ template: recipe.template, scenario: recipe, parameters: [] }, { template: recipe.template, scenario: recipe, parameters: [] }],
  ]) {
    globalThis.fetch = async () => jsonResponse(invalid);
    await assert.rejects(api.listTemplates(), ApiError);
  }
  globalThis.fetch = async () => jsonResponse([]);
  assert.deepEqual(await api.listTemplates(), []);
});

test("numeric overflow in JSON responses is rejected", async () => {
  const overflowingRecipe = JSON.stringify(recipe).replace('"duration_s":30', '"duration_s":1e400');
  globalThis.fetch = async () => new Response(overflowingRecipe);
  await assert.rejects(api.validateScenario(recipe), ApiError);
  const overflowingCatalog = `[${JSON.stringify({ template: recipe.template, scenario: recipe, parameters: [
    { name: "speed", minimum: 0, maximum: 30, default: 10, integer: false, unit: "m/s", description: "Speed" },
  ] }).replace('"maximum":30', '"maximum":1e400')}]`;
  globalThis.fetch = async () => new Response(overflowingCatalog);
  await assert.rejects(api.listTemplates(), ApiError);
});

test("unserializable recipes fail locally rather than issuing a GET or masking a network failure", async () => {
  globalThis.fetch = async () => { assert.fail("No request should be made"); };
  const cyclic = {};
  cyclic.self = cyclic;
  for (const invalid of [undefined, cyclic, { seed: 1n }]) {
    await assert.rejects(api.validateScenario(invalid), error => error instanceof ApiError && error.status === 422 && /valid JSON/.test(error.message));
  }
});

test("base URL validation supports deployment prefixes and rejects ambiguous or unsafe URLs", async () => {
  const client = createApiClient(" https://example.test/api/// ");
  globalThis.fetch = async url => {
    assert.equal(url, "https://example.test/api/health");
    return jsonResponse(health);
  };
  await client.health();
  for (const invalid of ["relative/path", "file:///tmp/private", "javascript:alert(1)", "https://user:secret@example.test", "https://example.test?x=1", "https://example.test#part"]) {
    assert.throws(() => createApiClient(invalid), /VITE_API_URL/);
  }
});

const scenarioId = "1676a66c-4b04-4a6b-a2a4-37f5175cc52d";
const simulationId = "9b09ecda-202f-498f-a095-c2b2dfc8c760";
const timestamp = "2026-10-16T12:00:00Z";
const savedRecipe = {
  ...recipe, schema_version: "1.0", location: null, event: null, occlusion: false,
  weather: { cloudiness: 0, precipitation: 0, wetness: 0, fog_density: 0, sun_altitude_deg: 60 },
  actors: [{ ...recipe.actors[0], blueprint: null, relative_spawn: null, spawn: null, spawn_point_index: null }],
};
const savedScenario = { id: scenarioId, revision: 3, scenario: savedRecipe, created_at: timestamp, updated_at: timestamp };
const executionOptions = {
  allow_map_reload: false, camera: false, lidar: false, camera_width: 640, camera_height: 360,
  max_ticks: 100000, sensor_settle_s: 0.02, sensor_timeout_s: 2,
};
const evaluationSettings = {
  method_version: "1.0", collision_episode_gap_s: 1, violation_episode_gap_s: 1,
  speeding_tolerance_mps: 0.5, hard_braking_threshold_mps2: 4, stopped_speed_mps: 0.1,
  max_sample_gap_s: 0.5, ttc_horizon_s: 10, ttc_warning_s: 3, clearance_warning_m: 2,
  braking_full_severity_mps2: 8, violations_for_full_severity: 3,
  ttc_weight: 45, clearance_weight: 25, braking_weight: 15, traffic_rule_weight: 15,
};
const launch = {
  scenario: savedRecipe, scenario_id: null, mode: "test", options: executionOptions,
  evaluation_settings: evaluationSettings,
};
const acceptedRun = {
  simulation: {
    id: simulationId, scenario: savedRecipe, mode: "test", agent: "baseline", status: "queued",
    progress: 0, created_at: timestamp, started_at: null, finished_at: null, error: null,
  },
  scenario_id: null, revision: 1, created_at: timestamp, updated_at: timestamp, result: null,
  job: { cancel_requested: false, queue_position: 1, options: executionOptions, evaluation_settings: evaluationSettings },
};
const queue = { started: true, accepting: true, worker_alive: true, pending: 0, active_id: null, error: null };

test("saved scenario reads preserve records, paging, deployment prefixes, and cancellation", async () => {
  const client = createApiClient("https://example.test/api");
  const controller = new AbortController();
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    assert.equal(options.method, "GET");
    assert.equal(options.signal, controller.signal);
    assert.deepEqual(options.headers, { Accept: "application/json" });
    return jsonResponse(url.includes("?limit=") ? [savedScenario] : savedScenario);
  };
  assert.deepEqual(await client.listScenarios({ limit: 25, offset: 50 }, { signal: controller.signal }), [savedScenario]);
  assert.deepEqual(await client.getScenario(scenarioId, { signal: controller.signal }), savedScenario);
  assert.equal(calls[0].url, "https://example.test/api/scenarios?limit=25&offset=50");
  assert.equal(calls[1].url, `https://example.test/api/scenarios/${scenarioId}`);
});

test("default saved scenario paging leaves backend defaults intact and supports an empty library", async () => {
  globalThis.fetch = async url => {
    assert.equal(url, `${API_BASE_URL}/scenarios`);
    return jsonResponse([]);
  };
  assert.deepEqual(await api.listScenarios(), []);
});

test("create saves the entire recipe and update serializes the expected revision with the recipe", async () => {
  const controller = new AbortController();
  const edited = { ...savedRecipe, name: "Edited recipe", seed: 21 };
  const updated = { ...savedScenario, revision: 4, scenario: edited };
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return jsonResponse(options.method === "POST" ? savedScenario : updated, options.method === "POST" ? 201 : 200);
  };
  assert.deepEqual(await api.createScenario(savedRecipe, { signal: controller.signal }), savedScenario);
  assert.deepEqual(await api.updateScenario(scenarioId, edited, 3, { signal: controller.signal }), updated);
  assert.equal(calls[0].url, `${API_BASE_URL}/scenarios`);
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].options.body), savedRecipe);
  assert.equal(calls[1].url, `${API_BASE_URL}/scenarios/${scenarioId}`);
  assert.equal(calls[1].options.method, "PUT");
  assert.deepEqual(JSON.parse(calls[1].options.body), { scenario: edited, expected_revision: 3 });
  for (const { options } of calls) {
    assert.equal(options.signal, controller.signal);
    assert.equal(options.headers["Content-Type"], "application/json");
  }
});

test("delete includes the expected revision and accepts a 204 response without reading JSON", async () => {
  const controller = new AbortController();
  globalThis.fetch = async (url, options) => {
    assert.equal(url, `${API_BASE_URL}/scenarios/${scenarioId}?expected_revision=3`);
    assert.equal(options.method, "DELETE");
    assert.equal(options.signal, controller.signal);
    assert.deepEqual(options.headers, { Accept: "application/json" });
    assert.equal("body" in options, false);
    return { ok: true, status: 204, json: () => assert.fail("A successful deletion has no JSON body") };
  };
  assert.equal(await api.deleteScenario(scenarioId, 3, { signal: controller.signal }), undefined);
});

test("update and delete preserve revision conflicts without retrying or discarding the submitted recipe", async () => {
  const edited = { ...savedRecipe, name: "My unsaved edits" };
  const before = structuredClone(edited);
  let requests = 0;
  globalThis.fetch = async () => { requests++; return jsonResponse({ detail: "Scenario revision changed; reload before saving" }, 409); };
  await assert.rejects(api.updateScenario(scenarioId, edited, 3), error => error instanceof ApiError && error.status === 409 && /revision changed/.test(error.message));
  await assert.rejects(api.deleteScenario(scenarioId, 3), error => error instanceof ApiError && error.status === 409);
  assert.equal(requests, 2);
  assert.deepEqual(edited, before);
});

test("IDs, revisions, and paging bounds are validated before fetching", async () => {
  globalThis.fetch = async () => assert.fail("Invalid resource arguments must not issue requests");
  for (const id of ["", " ", "not-a-uuid", "../health", `${scenarioId}?expected_revision=1`]) {
    for (const invoke of [() => api.getScenario(id), () => api.updateScenario(id, savedRecipe, 3), () => api.deleteScenario(id, 3)]) {
      await assert.rejects(invoke(), error => error instanceof ApiError && error.status === 422);
    }
  }
  for (const revision of [0, -1, 1.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1, "3", null]) {
    await assert.rejects(api.updateScenario(scenarioId, savedRecipe, revision), ApiError);
    await assert.rejects(api.deleteScenario(scenarioId, revision), ApiError);
  }
  for (const paging of [{ limit: 0 }, { limit: 1001 }, { limit: 1.5 }, { limit: "25" }, { offset: -1 }, { offset: 0.5 }, { offset: Infinity }]) {
    await assert.rejects(api.listScenarios(paging), ApiError);
  }
});

test("saved record guards reject malformed nested fields consistently for list, get, create, and update", async () => {
  const malformed = [
    null, {}, { ...savedScenario, id: "not-a-uuid" }, { ...savedScenario, revision: 0 },
    { ...savedScenario, revision: 1.5 }, { ...savedScenario, created_at: "yesterday" },
    { ...savedScenario, updated_at: null }, { ...savedScenario, scenario: null },
    { ...savedScenario, scenario: { ...savedRecipe, actors: [null] } },
  ];
  for (const invalid of malformed) {
    globalThis.fetch = async () => jsonResponse(invalid);
    for (const invoke of [() => api.getScenario(scenarioId), () => api.createScenario(savedRecipe), () => api.updateScenario(scenarioId, savedRecipe, 3)]) {
      await assert.rejects(invoke(), error => error instanceof ApiError && /unexpected response/.test(error.message));
    }
    globalThis.fetch = async () => jsonResponse([invalid]);
    await assert.rejects(api.listScenarios(), ApiError);
  }
});

test("CRUD mutation serialization rejects cyclic or non-JSON values before fetching", async () => {
  globalThis.fetch = async () => assert.fail("An invalid JSON body must not issue requests");
  const cyclic = {};
  cyclic.self = cyclic;
  for (const invalid of [cyclic, { seed: 1n }]) {
    await assert.rejects(api.createScenario(invalid), ApiError);
    await assert.rejects(api.updateScenario(scenarioId, invalid, 3), ApiError);
  }
  await assert.rejects(api.createScenario(undefined), ApiError);
});

test("queue status keeps unavailable and idle states nullable and forwards the abort signal", async () => {
  const controller = new AbortController();
  globalThis.fetch = async (url, options) => {
    assert.equal(url, `${API_BASE_URL}/simulations/queue`);
    assert.equal(options.signal, controller.signal);
    return jsonResponse(queue);
  };
  assert.deepEqual(await api.getSimulationQueue({ signal: controller.signal }), queue);
  const unavailable = { ...queue, accepting: false, worker_alive: false, active_id: simulationId, error: "worker unavailable" };
  globalThis.fetch = async () => jsonResponse(unavailable);
  assert.deepEqual(await api.getSimulationQueue(), unavailable);
});

test("malformed queue responses fail before displaying capacity or availability", async () => {
  for (const invalid of [
    {}, { ...queue, accepting: "true" }, { ...queue, started: 1 }, { ...queue, worker_alive: null },
    { ...queue, pending: -1 }, { ...queue, pending: 0.5 }, { ...queue, active_id: {} }, { ...queue, error: {} },
  ]) {
    globalThis.fetch = async () => jsonResponse(invalid);
    await assert.rejects(api.getSimulationQueue(), error => error instanceof ApiError && /unexpected response/.test(error.message));
  }
});

test("launch sends explicit mode, full recipe, options, and scoring settings and preserves queue acceptance", async () => {
  const controller = new AbortController();
  globalThis.fetch = async (url, options) => {
    assert.equal(url, `${API_BASE_URL}/simulations`);
    assert.equal(options.method, "POST");
    assert.equal(options.signal, controller.signal);
    assert.equal(options.headers["Content-Type"], "application/json");
    assert.deepEqual(JSON.parse(options.body), launch);
    return jsonResponse(acceptedRun, 202);
  };
  const record = await api.launchSimulation(launch, { signal: controller.signal });
  assert.deepEqual(record, acceptedRun);
  assert.equal(record.result, null);
  assert.equal(record.simulation.status, "queued");
  assert.equal(record.simulation.progress, 0);
  assert.equal(record.job.queue_position, 1);
});

test("launch can reference a saved recipe and preserves an already running status", async () => {
  const input = { ...launch, mode: "carla", scenario: null, scenario_id: scenarioId };
  const running = {
    ...acceptedRun, scenario_id: scenarioId,
    simulation: { ...acceptedRun.simulation, mode: "carla", status: "running", progress: 0.25, started_at: timestamp },
    job: { ...acceptedRun.job, queue_position: null },
  };
  globalThis.fetch = async (_url, options) => {
    assert.deepEqual(JSON.parse(options.body), input);
    return jsonResponse(running, 202);
  };
  assert.deepEqual(await api.launchSimulation(input), running);
});

test("launch receipts preserve valid graphics preferences while older jobs may omit them", async () => {
  for (const carla_quality of [null, "Low", "Epic"]) {
    const received = { ...acceptedRun, job: { ...acceptedRun.job, options: { ...executionOptions, carla_quality } } };
    globalThis.fetch = async () => jsonResponse(received, 202);
    assert.equal((await api.launchSimulation(launch)).job.options.carla_quality, carla_quality);
  }
});

test("launch only reports acceptance for HTTP 202 and delete only succeeds for HTTP 204", async () => {
  for (const status of [200, 201]) {
    globalThis.fetch = async () => jsonResponse(acceptedRun, status);
    await assert.rejects(api.launchSimulation(launch), error => error instanceof ApiError && error.status === status && /unexpected response/.test(error.message));
    await assert.rejects(api.deleteScenario(scenarioId, 3), ApiError);
  }
});

test("malformed accepted runs, progress, execution options, and evaluation settings are rejected", async () => {
  const malformed = [
    {}, { ...acceptedRun, revision: 0 }, { ...acceptedRun, scenario_id: "unknown" },
    { ...acceptedRun, updated_at: null }, { ...acceptedRun, result: {} },
    ...[
      { id: "unknown" }, { mode: "synthetic" }, { mode: ["test"] }, { status: "accepted" },
      { progress: -1 }, { progress: 2 }, { scenario: null }, { started_at: "soon" }, { error: {} },
    ].map(fields => ({ ...acceptedRun, simulation: { ...acceptedRun.simulation, ...fields } })),
    ...[
      { cancel_requested: null }, { queue_position: 0 }, { options: null },
      { options: { ...executionOptions, max_ticks: "10" } },
      { options: { ...executionOptions, camera: "false" } },
      { options: { ...executionOptions, sensor_timeout_s: -1 } },
      { options: { ...executionOptions, carla_quality: "Medium" } },
      { options: { ...executionOptions, carla_quality: true } },
      { evaluation_settings: {} }, { evaluation_settings: { ...evaluationSettings, ttc_weight: "45" } },
      { evaluation_settings: { ...evaluationSettings, method_version: "future" } },
    ].map(fields => ({ ...acceptedRun, job: { ...acceptedRun.job, ...fields } })),
  ];
  for (const invalid of malformed) {
    globalThis.fetch = async () => jsonResponse(invalid, 202);
    await assert.rejects(api.launchSimulation(launch), error => error instanceof ApiError && /unexpected response/.test(error.message));
  }
});

test("launch capacity and availability failures remain errors and do not trigger retries", async () => {
  for (const status of [429, 503]) {
    let requests = 0;
    globalThis.fetch = async () => { requests++; return jsonResponse({ detail: status === 429 ? "Simulation queue is full" : "Worker unavailable" }, status); };
    await assert.rejects(api.launchSimulation(launch), error => error instanceof ApiError && error.status === status);
    assert.equal(requests, 1);
  }
});

test("all section 8 client functions are also available as named exports", async () => {
  const module = await loadApiModule();
  for (const name of ["listScenarios", "getScenario", "createScenario", "updateScenario", "deleteScenario", "getSimulationQueue", "launchSimulation"]) {
    assert.equal(module[name], module.api[name]);
  }
});

const coverage = {
  total_frames: 1, collision_frames: 1, traffic_rule_frames: 0, braking_frames: 0,
  clearance_frames: 0, ttc_frames: 0, actor_pairs: 0, clearance_pairs: 0, ttc_pairs: 0, has_gaps: true,
};
const unknownMetrics = {
  collision_count: null, traffic_violation_count: null, min_clearance_m: null, min_ttc_s: null,
  max_deceleration_mps2: null, hard_braking_count: null, max_stopping_distance_m: null, max_stopping_time_s: null,
  severity: null, coverage, evaluation_settings: null,
};
const completedSimulation = { ...acceptedRun.simulation, status: "completed", progress: 1, started_at: timestamp, finished_at: timestamp };
const finalizedResult = {
  simulation: completedSimulation, metrics: unknownMetrics, telemetry_frames: 1, measured_duration_s: 0,
  driving_outcome: null, control_audit: null, agent_inputs: null,
  artifacts: [{ kind: "telemetry", path: `telemetry/${simulationId}/frames.ndjson`, media_type: "application/x-ndjson" }],
  runtime: { adapter: "test", server_version: null, client_version: null, map_name: "Town03", fixed_delta_seconds: 0.05, seed: 17, resolved_spawns: {}, sensor_samples_count: 0 },
};
const completedRecord = { ...acceptedRun, simulation: completedSimulation, result: finalizedResult, job: null };
const progress = { simulation_id: simulationId, status: "queued", progress: 0, revision: 1, started_at: null, finished_at: null, cancel_requested: false, queue_position: 1, error: null };
const state = { actor_id: "ego", transform: null, velocity_mps: null, acceleration_mps2: null, bounding_box_extent: null, bounding_box_transform: null, speed_limit_mps: null };
const frame = { frame: 0, time_s: 0, ego: state, actors: null, collisions: null, traffic_violations: null, rule_checks: null,
  control: { throttle: 0, steer: 0, brake: 0, hand_brake: false, reverse: false } };
const failureId = "44444444-4444-4444-8444-444444444444";
const failure = { id: failureId, simulation_id: simulationId, scenario: recipe, kind: "collision", summary: "Observed contact", severity: 100, reproduced: null, created_at: timestamp };
const analyticsGroup = {
  mode: "test", total_runs: 1, status_counts: { queued: 0, running: 0, completed: 1, failed: 0, cancelled: 0 },
  result_count: 1, scored_runs: 0, unscored_results: 1, mean_severity: null, collision_runs: 0,
  collision_observed_runs: 0, traffic_violation_runs: 0, traffic_observed_runs: 0, failure_reports: 0, failure_kinds: {},
};
const artifactId = "55555555-5555-4555-8555-555555555555";
const cameraSample = { kind: "camera", frame: 5, timestamp: 0.25, file: "00000000-camera.bin", byte_length: 4, sha256: "a".repeat(64), metadata: { encoding: "bgra8", width: 1, height: 1 } };
const lidarSample = { kind: "lidar", frame: 5, timestamp: 0.25, file: "00000001-lidar.bin", byte_length: 16, sha256: "b".repeat(64), metadata: { encoding: "float32_xyzi", channels: 32 } };
const manifest = { format: "raw-sensor-v1", simulation_id: simulationId, artifact_id: artifactId, sample_count: 2, total_bytes: 20, samples: [cameraSample, lidarSample] };

test("simulation list filters and detail routes preserve IDs, defaults, nullable job metadata, and signals", async () => {
  const controller = new AbortController();
  const calls = [];
  const client = createApiClient("https://example.test/poltergeist");
  globalThis.fetch = async (url, options) => {
    calls.push(url);
    assert.equal(options.signal, controller.signal);
    if (url.endsWith("/progress")) return jsonResponse(progress);
    if (url.endsWith("/result")) return jsonResponse(finalizedResult);
    return jsonResponse(url.includes("?") || url.endsWith("/simulations") ? [completedRecord] : completedRecord);
  };
  assert.deepEqual(await client.listSimulations({ limit: 25, offset: 0, mode: "test", status: "completed" }, { signal: controller.signal }), [completedRecord]);
  assert.deepEqual(await client.listSimulations({}, { signal: controller.signal }), [completedRecord]);
  assert.deepEqual(await client.getSimulation(simulationId, { signal: controller.signal }), completedRecord);
  assert.deepEqual(await client.getSimulationProgress(simulationId, { signal: controller.signal }), progress);
  assert.deepEqual(await client.getSimulationResult(simulationId, { signal: controller.signal }), finalizedResult);
  assert.deepEqual(calls, [
    "https://example.test/poltergeist/simulations?limit=25&offset=0&mode=test&status=completed",
    "https://example.test/poltergeist/simulations", `https://example.test/poltergeist/simulations/${simulationId}`,
    `https://example.test/poltergeist/simulations/${simulationId}/progress`, `https://example.test/poltergeist/simulations/${simulationId}/result`,
  ]);
});

test("unknown result metrics remain null, observed zeros remain zero, and runtime provenance is preserved", async () => {
  const observed = { ...finalizedResult, metrics: { ...unknownMetrics, collision_count: 0, hard_braking_count: 0, severity: 0, min_clearance_m: 0, evaluation_settings: evaluationSettings },
    runtime: { ...finalizedResult.runtime, client_version: "0.9.15", server_version: "0.9.15", resolved_spawns: { ego: { position: { x: 0, y: -1, z: 1 }, rotation: { pitch_deg: 0, roll_deg: 0, yaw_deg: 90 } } } } };
  for (const result of [finalizedResult, observed]) {
    globalThis.fetch = async () => jsonResponse(result);
    assert.deepEqual(await api.getSimulationResult(simulationId), result);
  }
});

test("driving tasks and outcomes round trip without enabling dashboard agent launches", async () => {
  const destination = { position: { x: 10, y: 0, z: 1 }, rotation: { pitch_deg: 0, roll_deg: 0, yaw_deg: 0 } };
  const task = { route_owner: "framework", destination, route: [], target_speed_mps: 8,
    completion_radius_m: 2, completion_height_tolerance_m: 2, completion_speed_mps: null,
    completion_hold_s: 0, stop_on_completion: false };
  const scenario = { ...savedRecipe, driving_task: task };
  const outcome = { status: "timed_out", route_owner: "framework", control_mode: "scripted", destination,
    target_speed_mps: 8, resolved_route: [destination], initial_speed_mps: 10, final_distance_m: 5,
    final_height_error_m: 0, reached_at_s: null, route_progress_m: 0, route_length_m: 0,
    interventions: [], termination_reason: "duration_elapsed" };
  const result = { ...finalizedResult, simulation: { ...completedSimulation, scenario }, driving_outcome: outcome,
    control_audit: { mode: "scripted", controller: "baseline", navigation: "framework_route", interventions: [] } };
  globalThis.fetch = async () => jsonResponse(scenario);
  assert.deepEqual(await api.validateScenario(scenario), scenario);
  globalThis.fetch = async () => jsonResponse(result);
  assert.deepEqual(await api.getSimulationResult(simulationId), result);
  for (const badTask of [{ ...task, completion_hold_s: -1 }, { ...task, destination: {} }, { ...task, route_owner: "automatic" }]) {
    globalThis.fetch = async () => jsonResponse({ ...scenario, driving_task: badTask });
    await assert.rejects(api.validateScenario(scenario), ApiError);
  }
  for (const fields of [{ final_distance_m: -1 }, { status: "successful" }, { reached_at_s: "soon" }, { resolved_route: [{}] }]) {
    globalThis.fetch = async () => jsonResponse({ ...result, driving_outcome: { ...outcome, ...fields } });
    await assert.rejects(api.getSimulationResult(simulationId), ApiError);
  }
  globalThis.fetch = async () => jsonResponse({ ...result, control_audit: { ...result.control_audit, controller: "unknown" } });
  await assert.rejects(api.getSimulationResult(simulationId), ApiError);
});

test("agent input provenance preserves source, calibrated sensors, and localization assistance", async () => {
  const destination = { position: { x: 10, y: 0, z: 1 }, rotation: { pitch_deg: 0, roll_deg: 0, yaw_deg: 0 } };
  const task = { route_owner: "framework", destination, route: [], target_speed_mps: 8,
    completion_radius_m: 2, completion_height_tolerance_m: 2, completion_speed_mps: null,
    completion_hold_s: 0, stop_on_completion: false };
  const inputs = { observation_mode: "sensors", source: "deterministic_fixture", ego_state_source: "simulated_ego_kinematics",
    ground_truth_actor_inputs: false, assistance: ["ideal_ego_kinematics", "framework_navigation"],
    sensors: [{ id: "front", type: "sensor.camera.rgb", mount: destination,
      attributes: { image_size_x: 320, image_size_y: 180, fov: 90, sensor_tick: 0 } }] };
  const sensorResult = { ...finalizedResult, simulation: { ...completedSimulation, scenario: { ...savedRecipe, driving_task: task } },
    control_audit: { mode: "authoritative", controller: "sdk_agent", navigation: "framework_route", interventions: [] }, agent_inputs: inputs };
  const stateResult = { ...finalizedResult, agent_inputs: { observation_mode: "simulator_state", source: "synthetic_state",
    ego_state_source: "simulator_state", ground_truth_actor_inputs: true, sensors: [], assistance: [] } };
  for (const result of [sensorResult, stateResult, finalizedResult, { ...finalizedResult, agent_inputs: undefined }]) {
    globalThis.fetch = async () => jsonResponse(result);
    assert.deepEqual(await api.getSimulationResult(simulationId), JSON.parse(JSON.stringify(result)));
  }
  const invalidInputs = [
    { ...inputs, ground_truth_actor_inputs: true }, { ...inputs, source: "carla_sensors" },
    { ...inputs, ego_state_source: "simulator_state" }, { ...inputs, sensors: [] },
    { ...inputs, assistance: ["framework_navigation"] }, { ...inputs, assistance: ["ideal_ego_kinematics"] },
    { ...inputs, sensors: [...inputs.sensors, ...inputs.sensors] },
    { ...inputs, sensors: [{ ...inputs.sensors[0], attributes: { image_size_x: 320, image_size_y: 180, fov: 90 } }] },
    { ...inputs, sensors: [{ ...inputs.sensors[0], attributes: { ...inputs.sensors[0].attributes, image_size_x: "320" } }] },
    { ...inputs, sensors: [{ ...inputs.sensors[0], attributes: { ...inputs.sensors[0].attributes, fov: 180 } }] },
    { ...inputs, sensors: [{ ...inputs.sensors[0], attributes: { ...inputs.sensors[0].attributes, unknown: 0 } }] },
  ];
  for (const agent_inputs of invalidInputs) {
    globalThis.fetch = async () => jsonResponse({ ...sensorResult, agent_inputs });
    await assert.rejects(api.getSimulationResult(simulationId), ApiError);
  }
  for (const result of [
    { ...sensorResult, simulation: completedSimulation },
    { ...sensorResult, control_audit: { mode: "scripted", controller: "baseline", navigation: "framework_route", interventions: [] } },
    { ...stateResult, agent_inputs: { ...stateResult.agent_inputs, ground_truth_actor_inputs: false } },
  ]) {
    globalThis.fetch = async () => jsonResponse(result);
    await assert.rejects(api.getSimulationResult(simulationId), ApiError);
  }
});

test("results reject malformed metrics, coverage, artifacts, settings, and runtime fields", async () => {
  const invalidMetrics = [
    null, {}, { ...unknownMetrics, collision_count: "0" }, { ...unknownMetrics, collision_count: -1 },
    { ...unknownMetrics, severity: "0" }, { ...unknownMetrics, severity: -1 }, { ...unknownMetrics, severity: 101 },
    { ...unknownMetrics, min_ttc_s: -1 }, { ...unknownMetrics, hard_braking_count: 0.5 },
    { ...unknownMetrics, coverage: { ...coverage, collision_frames: 2 } },
    { ...unknownMetrics, coverage: { ...coverage, total_frames: null } },
    { ...unknownMetrics, coverage: { ...coverage, has_gaps: "false" } },
    { ...unknownMetrics, coverage: { ...coverage, ttc_pairs: 1 } },
    { ...unknownMetrics, evaluation_settings: { ...evaluationSettings, ttc_weight: 200 } },
    { ...unknownMetrics, evaluation_settings: { ...evaluationSettings, ttc_warning_s: 100 } },
  ];
  const invalid = [
    ...invalidMetrics.map(metrics => ({ ...finalizedResult, metrics })),
    { ...finalizedResult, telemetry_frames: 0.5 }, { ...finalizedResult, measured_duration_s: -1 },
    { ...finalizedResult, simulation: acceptedRun.simulation },
    ...[{ adapter: "carla" }, { seed: -1 }, { sensor_samples_count: "0" }, { resolved_spawns: { ego: {} } }, { map_name: {} }]
      .map(fields => ({ ...finalizedResult, runtime: { ...finalizedResult.runtime, ...fields } })),
    ...[{ kind: "remote", path: "x", media_type: "application/json" }, { kind: "report", path: "https://example.test", media_type: "application/json" },
      { kind: "report", path: "../report.json", media_type: "application/json" }, { kind: "report", path: "report.json", media_type: {} }]
      .map(artifact => ({ ...finalizedResult, artifacts: [artifact] })),
  ];
  for (const result of invalid) {
    globalThis.fetch = async () => jsonResponse(result);
    await assert.rejects(api.getSimulationResult(simulationId), ApiError);
    globalThis.fetch = async () => jsonResponse({ ...completedRecord, result });
    await assert.rejects(api.getSimulation(simulationId), ApiError);
  }
});

test("record, progress, result, failure and manifest IDs must belong to the requested resource", async () => {
  for (const [invoke, response] of [
    [() => api.getSimulation(simulationId), { ...completedRecord, simulation: { ...completedSimulation, id: scenarioId }, result: null }],
    [() => api.getSimulationProgress(simulationId), { ...progress, simulation_id: scenarioId }],
    [() => api.getSimulationResult(simulationId), { ...finalizedResult, simulation: { ...completedSimulation, id: scenarioId } }],
    [() => api.getFailure(failureId), { ...failure, id: scenarioId }],
    [() => api.getCaptureManifest(simulationId, 0), { ...manifest, simulation_id: scenarioId }],
    [() => api.getSimulation(simulationId), { ...completedRecord, result: { ...finalizedResult, simulation: { ...completedSimulation, id: scenarioId } } }],
  ]) {
    globalThis.fetch = async () => jsonResponse(response);
    await assert.rejects(invoke(), ApiError);
  }
});

test("cancel requests require HTTP202, keep cancel acknowledgement distinct from completion, and do not retry", async () => {
  const controller = new AbortController();
  let calls = 0;
  globalThis.fetch = async (url, options) => {
    calls++;
    assert.equal(url, `${API_BASE_URL}/simulations/${simulationId}/cancel`);
    assert.equal(options.method, "POST");
    assert.equal(options.signal, controller.signal);
    return jsonResponse({ ...acceptedRun, job: { ...acceptedRun.job, cancel_requested: true } }, 202);
  };
  const response = await api.cancelSimulation(simulationId, { signal: controller.signal });
  assert.equal(response.job.cancel_requested, true);
  assert.equal(response.simulation.status, "queued");
  assert.equal(calls, 1);
  for (const status of [200, 201, 409, 503]) {
    globalThis.fetch = async () => jsonResponse(acceptedRun, status);
    await assert.rejects(api.cancelSimulation(simulationId), error => error instanceof ApiError && error.status === status);
  }
});

test("progress guards reject invalid states without coercing false or unknown values", async () => {
  for (const fields of [{ status: "done" }, { progress: 2 }, { progress: -1 }, { progress: "0" }, { revision: 0 }, { cancel_requested: null }, { queue_position: 0 }, { started_at: "later" }, { finished_at: {} }, { error: {} }]) {
    globalThis.fetch = async () => jsonResponse({ ...progress, ...fields });
    await assert.rejects(api.getSimulationProgress(simulationId), ApiError);
  }
});

test("telemetry pages preserve absent feeds, empty observed feeds, numeric data and paging", async () => {
  const observed = { ...frame, frame: 1, time_s: 0.05, actors: [], collisions: [], traffic_violations: [], rule_checks: [] };
  const page = { frames: [frame, observed], total: 2, offset: 0, limit: 25 };
  const controller = new AbortController();
  globalThis.fetch = async (url, options) => {
    assert.equal(url, `${API_BASE_URL}/simulations/${simulationId}/telemetry?limit=25&offset=0`);
    assert.equal(options.signal, controller.signal);
    return jsonResponse(page);
  };
  assert.deepEqual(await api.getTelemetry(simulationId, { limit: 25, offset: 0 }, { signal: controller.signal }), page);
  globalThis.fetch = async () => jsonResponse({ frames: [], total: 0, offset: 0, limit: 1000 });
  assert.deepEqual(await api.getTelemetry(simulationId), { frames: [], total: 0, offset: 0, limit: 1000 });
});

test("nested telemetry guards reject unsafe numeric geometry, controls, contacts, and duplicate actor or rule identities", async () => {
  const invalidFrames = [
    { ...frame, frame: -1 }, { ...frame, time_s: "0" }, { ...frame, ego: {} },
    { ...frame, ego: { ...state, velocity_mps: { x: 0, y: {}, z: 0 } } },
    { ...frame, ego: { ...state, bounding_box_extent: { x: 0, y: 1, z: 1 } } },
    { ...frame, ego: { ...state, speed_limit_mps: -1 } },
    { ...frame, actors: [state] }, { ...frame, actors: [null] },
    { ...frame, control: { ...frame.control, throttle: 2 } }, { ...frame, control: { ...frame.control, steer: "0" } },
    { ...frame, collisions: [{ other_actor_id: "ego", impulse_n_s: 0, event_id: null }] },
    { ...frame, collisions: [{ other_actor_id: "world", impulse_n_s: -1, event_id: null }] },
    { ...frame, traffic_violations: [{ actor_id: "ego", rule: "unknown", details: "", source_id: null, event_id: null }] },
    { ...frame, rule_checks: ["speeding", "speeding"] }, { ...frame, rule_checks: ["unknown"] },
  ];
  for (const invalid of invalidFrames) {
    globalThis.fetch = async () => jsonResponse({ frames: [invalid], total: 1, offset: 0, limit: 1000 });
    await assert.rejects(api.getTelemetry(simulationId), ApiError);
  }
  for (const page of [
    { frames: [frame, frame], total: 2, offset: 0, limit: 1000 },
    { frames: [frame], total: 0, offset: 0, limit: 1000 },
    { frames: [frame], total: 1, offset: 1, limit: 1000 },
    { frames: [], total: 0, offset: 0, limit: 25 },
  ]) {
    globalThis.fetch = async () => jsonResponse(page);
    await assert.rejects(api.getTelemetry(simulationId), ApiError);
  }
});

test("failure filters retain true, false and unknown as distinct query values", async () => {
  const controller = new AbortController();
  const urls = [];
  globalThis.fetch = async (url, options) => { urls.push(url); assert.equal(options.signal, controller.signal); return jsonResponse([failure]); };
  for (const reproduced of ["all", "true", "false", "unknown"]) {
    assert.deepEqual(await api.listFailures({ simulation_id: simulationId, reproduced, limit: 10, offset: 0 }, { signal: controller.signal }), [failure]);
  }
  assert.deepEqual(urls, ["all", "true", "false", "unknown"].map(value => `${API_BASE_URL}/failures?limit=10&offset=0&simulation_id=${simulationId}&reproduced=${value}`));
  globalThis.fetch = async () => jsonResponse(failure);
  assert.deepEqual(await api.getFailure(failureId), failure);
});

test("manual reproduction updates PATCH only the explicit outcome and preserve false and null", async () => {
  const controller = new AbortController();
  for (const reproduced of [true, false, null]) {
    globalThis.fetch = async (url, options) => {
      assert.equal(url, `${API_BASE_URL}/failures/${failureId}/reproduction`);
      assert.equal(options.method, "PATCH");
      assert.equal(options.signal, controller.signal);
      assert.equal(options.headers["Content-Type"], "application/json");
      assert.deepEqual(JSON.parse(options.body), { reproduced });
      return jsonResponse({ ...failure, reproduced });
    };
    assert.equal((await api.updateReproduction(failureId, reproduced, { signal: controller.signal })).reproduced, reproduced);
  }
  globalThis.fetch = async () => jsonResponse({ ...failure, reproduced: true });
  await assert.rejects(api.updateReproduction(failureId, false), ApiError);
});

test("failure creation accepts only HTTP201 and verifies report ownership", async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, `${API_BASE_URL}/failures`);
    assert.equal(options.method, "POST");
    assert.deepEqual(JSON.parse(options.body), failure);
    return jsonResponse(failure, 201);
  };
  assert.deepEqual(await api.createFailure(failure), failure);
  for (const [response, status] of [[failure, 200], [{ ...failure, id: scenarioId }, 201], [{ ...failure, simulation_id: scenarioId }, 201]]) {
    globalThis.fetch = async () => jsonResponse(response, status);
    await assert.rejects(api.createFailure(failure), ApiError);
  }
});

test("failure guards reject malformed evidence summaries and keep nullable reproduction strict", async () => {
  for (const fields of [{ severity: null }, { severity: 101 }, { severity: "0" }, { reproduced: "false" }, { reproduced: 0 }, { kind: "unsafe" }, { simulation_id: "other" }, { summary: {} }, { summary: " " }, { scenario: null }]) {
    globalThis.fetch = async () => jsonResponse({ ...failure, ...fields });
    await assert.rejects(api.getFailure(failureId), ApiError);
    globalThis.fetch = async () => jsonResponse([{ ...failure, ...fields }]);
    await assert.rejects(api.listFailures(), ApiError);
  }
});

test("analytics keeps modes separate, validates counts, and preserves unknown severity", async () => {
  const controller = new AbortController();
  globalThis.fetch = async (url, options) => {
    assert.equal(url, `${API_BASE_URL}/analytics?mode=test`);
    assert.equal(options.signal, controller.signal);
    return jsonResponse({ groups: [analyticsGroup] });
  };
  assert.deepEqual(await api.getAnalytics("test", { signal: controller.signal }), { groups: [analyticsGroup] });
  for (const invalid of [
    { groups: [{ ...analyticsGroup, mode: "synthetic" }] }, { groups: [{ ...analyticsGroup, mean_severity: "0" }] },
    { groups: [{ ...analyticsGroup, total_runs: -1 }] }, { groups: [{ ...analyticsGroup, status_counts: { done: 1 } }] },
    { groups: [{ ...analyticsGroup, failure_kinds: { collision: 1.5 } }] }, { groups: [analyticsGroup, analyticsGroup] },
  ]) {
    globalThis.fetch = async () => jsonResponse(invalid);
    await assert.rejects(api.getAnalytics(), ApiError);
  }
  globalThis.fetch = async () => jsonResponse({ groups: [{ ...analyticsGroup, mode: "carla" }] });
  await assert.rejects(api.getAnalytics("test"), ApiError);
});

test("section9 IDs, indices, filters and pagination fail locally without coercing malformed inputs", async () => {
  globalThis.fetch = async () => assert.fail("Invalid arguments must not issue requests");
  for (const invalid of ["bad", "../health", null, undefined]) {
    for (const invoke of [() => api.getSimulation(invalid), () => api.getSimulationProgress(invalid), () => api.getSimulationResult(invalid), () => api.cancelSimulation(invalid), () => api.getTelemetry(invalid), () => api.getFailure(invalid), () => api.getCaptureManifest(invalid, 0), () => api.getCaptureSample(invalid, 0, 0)]) await assert.rejects(invoke(), ApiError);
    assert.throws(() => api.artifactUrl(invalid, 0), ApiError);
  }
  for (const filters of [{ mode: "synthetic" }, { mode: null }, { status: "done" }, { limit: 0 }, { offset: -1 }]) await assert.rejects(api.listSimulations(filters), ApiError);
  for (const filters of [{ reproduced: false }, { reproduced: null }, { reproduced: "null" }, { simulation_id: "bad" }, { limit: 1001 }]) await assert.rejects(api.listFailures(filters), ApiError);
  for (const value of ["false", 0, undefined, {}]) await assert.rejects(api.updateReproduction(failureId, value), ApiError);
  for (const value of [null, "synthetic", 0]) await assert.rejects(api.getAnalytics(value), ApiError);
  for (const value of [-1, 1.5, "0", NaN, Infinity]) {
    assert.throws(() => api.sampleUrl(simulationId, 0, value), ApiError);
    await assert.rejects(api.getCaptureManifest(simulationId, value), ApiError);
    await assert.rejects(api.getCaptureSample(simulationId, 0, value), ApiError);
  }
});

test("raw capture uses owned indexed routes and validates camera and LiDAR manifest metadata", async () => {
  const client = createApiClient("https://example.test/api");
  assert.equal(client.artifactUrl(simulationId, 2), `https://example.test/api/simulations/${simulationId}/artifacts/2`);
  assert.equal(client.sampleUrl(simulationId, 2, 1), `https://example.test/api/simulations/${simulationId}/artifacts/2/samples/1`);
  const controller = new AbortController();
  globalThis.fetch = async (url, options) => {
    assert.equal(url, client.artifactUrl(simulationId, 2));
    assert.equal(options.signal, controller.signal);
    return jsonResponse(manifest);
  };
  assert.deepEqual(await client.getCaptureManifest(simulationId, 2, { signal: controller.signal }), manifest);
});

test("raw capture manifest guards reject unsafe files, dimensions, checksums, ordering, totals and encodings", async () => {
  const withCamera = fields => ({ ...manifest, samples: [{ ...cameraSample, ...fields }, lidarSample] });
  for (const invalid of [
    { ...manifest, format: "images" }, { ...manifest, artifact_id: "other" }, { ...manifest, sample_count: 1 }, { ...manifest, total_bytes: 19 },
    withCamera({ file: "../../image.png" }), withCamera({ frame: -1 }), withCamera({ timestamp: "0.25" }), withCamera({ sha256: "bad" }),
    withCamera({ byte_length: 8 }), withCamera({ metadata: { encoding: "png", width: 1, height: 1 } }),
    withCamera({ metadata: { encoding: "bgra8", width: 4097, height: 1 } }), withCamera({ metadata: { encoding: "bgra8", width: "1", height: 1 } }),
    { ...manifest, samples: [cameraSample, { ...lidarSample, byte_length: 15 }] },
    { ...manifest, samples: [cameraSample, { ...lidarSample, metadata: { encoding: "float32_xyzi", channels: 0 } }] },
    { ...manifest, sample_count: 3, total_bytes: 24, samples: [cameraSample, lidarSample, { ...cameraSample, file: "00000002-camera.bin" }] },
    withCamera({ metadata: { ...cameraSample.metadata, notes: "x".repeat(17000) } }),
  ]) {
    globalThis.fetch = async () => jsonResponse(invalid);
    await assert.rejects(api.getCaptureManifest(simulationId, 0), ApiError);
  }
});

test("raw sample download preserves bytes and abort signal through an indexed octet-stream request", async () => {
  const controller = new AbortController();
  const bytes = new Uint8Array([3, 2, 1, 255]);
  globalThis.fetch = async (url, options) => {
    assert.equal(url, `${API_BASE_URL}/simulations/${simulationId}/artifacts/2/samples/0`);
    assert.equal(options.signal, controller.signal);
    assert.deepEqual(options.headers, { Accept: "application/octet-stream" });
    return new Response(bytes, { headers: { "Content-Type": "application/octet-stream", "Content-Length": "4" } });
  };
  assert.deepEqual(new Uint8Array(await api.getCaptureSample(simulationId, 2, 0, { signal: controller.signal })), bytes);
});

test("raw sample downloads reject wrong content types, partial responses, huge declarations and incomplete bodies", async () => {
  for (const response of [
    new Response("html", { headers: { "Content-Type": "text/html" } }),
    new Response(new Uint8Array(4), { status: 206, headers: { "Content-Type": "application/octet-stream" } }),
    new Response(new Uint8Array(4), { headers: { "Content-Type": "application/octet-stream", "Content-Length": "67108865" } }),
    new Response(new Uint8Array(4), { headers: { "Content-Type": "application/octet-stream", "Content-Length": "8" } }),
    new Response(new Uint8Array(4), { headers: { "Content-Type": "application/octet-stream", "Content-Length": "no" } }),
  ]) {
    globalThis.fetch = async () => response;
    await assert.rejects(api.getCaptureSample(simulationId, 0, 0), ApiError);
  }
  globalThis.fetch = async () => jsonResponse({ detail: "Raw sample does not exist" }, 404);
  await assert.rejects(api.getCaptureSample(simulationId, 0, 0), error => error instanceof ApiError && error.status === 404);
});

test("raw sample streaming enforces its byte bound when content length is absent", async () => {
  const chunk = new Uint8Array(16 * 1024 * 1024);
  let cancelled = false;
  const stream = new ReadableStream({ start(controller) { for (let i = 0; i < 5; i++) controller.enqueue(chunk); }, cancel() { cancelled = true; } });
  globalThis.fetch = async () => new Response(stream, { headers: { "Content-Type": "application/octet-stream" } });
  await assert.rejects(api.getCaptureSample(simulationId, 0, 0), error => error instanceof ApiError && /64 MiB/.test(error.message));
  assert.equal(cancelled, true);
});

test("raw sample fetch and body aborts retain the original AbortError", async () => {
  const aborted = new DOMException("Superseded", "AbortError");
  globalThis.fetch = async () => { throw aborted; };
  await assert.rejects(api.getCaptureSample(simulationId, 0, 0), error => error === aborted);
  globalThis.fetch = async () => new Response(new ReadableStream({ start(controller) { controller.error(aborted); } }), { headers: { "Content-Type": "application/octet-stream" } });
  await assert.rejects(api.getCaptureSample(simulationId, 0, 0), error => error === aborted);
});

test("section9 client functions are available as named exports", async () => {
  const module = await loadApiModule();
  for (const name of ["listSimulations", "getSimulation", "getSimulationProgress", "getSimulationResult", "cancelSimulation", "getTelemetry", "listFailures", "getFailure", "createFailure", "updateReproduction", "getAnalytics", "artifactUrl", "sampleUrl", "getCaptureManifest", "getCaptureSample"]) assert.equal(module[name], module.api[name]);
});

const registrySnapshot = {
  registration_id: scenarioId, package_path: 'C:/agents/pilot', python_executable: 'C:/agents/env/python.exe',
  python_sha256: 'a'.repeat(64), python_size_bytes: 100, package_sha256: 'b'.repeat(64),
  observation_mode: 'simulator_state', agent_control_mode: 'authoritative',
  manifest: {
    protocol_version: '1.3', agent_id: 'registered-pilot', name: 'Registered pilot', version: '1',
    entry_point: 'agent:Driver', observation_mode: 'simulator_state', control_mode: 'vehicle_control',
    navigation_mode: 'provided_route', sensors: [], assets: [], requirements_file: null,
    required_features: [], configuration: { brake: .5 },
  },
  configuration: { brake: .5 },
  files: [{ path: 'agent.json', sha256: 'c'.repeat(64), size_bytes: 100 }], model_files: [], runtime_files: [],
  process_options: { load_timeout_s: 30, reset_timeout_s: 10, step_timeout_s: 2, close_timeout_s: 2, terminate_timeout_s: 1, max_message_bytes: 4194304 },
};
const registeredAgent = { id: scenarioId, active: true, created_at: timestamp, snapshot: registrySnapshot };
const registeredRun = {
  ...acceptedRun, simulation: { ...acceptedRun.simulation, agent: 'custom', agent_snapshot: registrySnapshot },
  job: { ...acceptedRun.job, agent_snapshot: registrySnapshot },
};

test('registry client preserves registration configuration, identities and readiness scope', async () => {
  const controller = new AbortController();
  const requests = [];
  const readiness = { agent_id: scenarioId, ready: true, inspection: 'static_only', inference_checked: false, issues: [], limitations: ['Dependencies not executed'], checked_at: timestamp };
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    assert.equal(options.signal, controller.signal);
    if (url.endsWith('/readiness')) return jsonResponse(readiness);
    if (url.endsWith('/deactivate')) return jsonResponse({ ...registeredAgent, active: false });
    if (options.method === 'POST') return jsonResponse(registeredAgent, 201);
    if (url.includes('?')) return jsonResponse([registeredAgent]);
    return jsonResponse(registeredAgent);
  };
  const input = { package_path: 'C:/agents/pilot', trusted_local_code: true, configuration: { brake: .5 } };
  assert.deepEqual(await api.registerAgent(input, { signal: controller.signal }), registeredAgent);
  assert.deepEqual(JSON.parse(requests[0].options.body), input);
  assert.deepEqual(await api.listAgents({ limit: 10, offset: 0 }, { signal: controller.signal }), [registeredAgent]);
  assert.deepEqual(await api.getAgent(scenarioId, { signal: controller.signal }), registeredAgent);
  assert.deepEqual(await api.getAgentReadiness(scenarioId, { signal: controller.signal }), readiness);
  assert.equal((await api.deactivateAgent(scenarioId, { signal: controller.signal })).active, false);
});

test('registry list status filters reach the API and invalid filters fail before fetch', async () => {
  const requests = [];
  globalThis.fetch = async url => { requests.push(url); return jsonResponse([]); };
  await api.listAgents({ active: true, limit: 9, offset: 8 });
  await api.listAgents({ active: false });
  await api.listAgents({});
  assert.equal(requests[0], `${API_BASE_URL}/agents?limit=9&offset=8&active=true`);
  assert.equal(requests[1], `${API_BASE_URL}/agents?active=false`);
  assert.equal(requests[2], `${API_BASE_URL}/agents`);
  assert.throws(() => api.listAgents({ active: 'true' }), ApiError);
  assert.throws(() => api.listAgents({ active: null }), ApiError);
  assert.equal(requests.length, 3);
});

test('registered launch sends the selected UUID and retains the exact queued snapshot', async () => {
  globalThis.fetch = async (_url, options) => {
    assert.equal(JSON.parse(options.body).agent_id, scenarioId);
    return jsonResponse(registeredRun, 202);
  };
  assert.deepEqual(await api.launchSimulation({ ...launch, agent_id: scenarioId }), registeredRun);
});

for (const [label, modify] of [
  ['hash', value => { value.files[0].sha256 = 'unverified'; }],
  ['configuration', value => { value.manifest.configuration.brake = true; }],
  ['mode', value => { value.observation_mode = 'sensors'; }],
  ['deadline', value => { value.process_options.step_timeout_s = -1; }],
  ['duplicate file', value => { value.files.push({ ...value.files[0] }); }],
]) test(`registry client rejects invalid snapshot ${label}`, async () => {
  const item = structuredClone(registeredAgent);
  modify(item.snapshot);
  globalThis.fetch = async () => jsonResponse(item);
  await assert.rejects(api.getAgent(scenarioId), ApiError);
});

test('run reads reject a queued snapshot that differs from its durable job', async () => {
  const item = structuredClone(registeredRun);
  item.job.agent_snapshot = structuredClone(item.job.agent_snapshot);
  item.job.agent_snapshot.configuration.brake = .9;
  item.job.agent_snapshot.manifest.configuration.brake = .9;
  globalThis.fetch = async () => jsonResponse(item);
  await assert.rejects(api.getSimulation(simulationId), ApiError);
});

test('registry reads reject the wrong registration identity and inference claims', async () => {
  globalThis.fetch = async () => jsonResponse({ ...registeredAgent, id: simulationId });
  await assert.rejects(api.getAgent(scenarioId), ApiError);
  globalThis.fetch = async () => jsonResponse({ agent_id: scenarioId, ready: true, inspection: 'static_only', inference_checked: true, issues: [], limitations: [], checked_at: timestamp });
  await assert.rejects(api.getAgentReadiness(scenarioId), ApiError);
});

test('registered result controls and input mode must match the selected agent', async () => {
  const result = {
    ...finalizedResult,
    simulation: { ...completedSimulation, agent: 'custom', agent_snapshot: registrySnapshot },
    control_audit: { mode: 'authoritative', controller: 'sdk_agent', navigation: 'legacy_assisted', interventions: [] },
    agent_inputs: { observation_mode: 'simulator_state', source: 'synthetic_state', ego_state_source: 'simulator_state',
      sensors: [], ground_truth_actor_inputs: true, assistance: [] },
  };
  globalThis.fetch = async () => jsonResponse(result);
  assert.deepEqual(await api.getSimulationResult(simulationId), result);
  for (const field of ['mode', 'controller']) {
    const bad = structuredClone(result);
    bad.control_audit[field] = field === 'mode' ? 'scripted' : 'baseline';
    globalThis.fetch = async () => jsonResponse(bad);
    await assert.rejects(api.getSimulationResult(simulationId), ApiError);
  }
});

test('named cameras and legacy recording retain independent capture clocks', async () => {
  const sample = (id, frame, index) => ({ ...cameraSample, frame, timestamp: frame * .05,
    file: `${String(index).padStart(8, '0')}-camera.bin`,
    metadata: { ...cameraSample.metadata, ...(id === null ? {} : { agent_sensor_id: id }) },
  });
  const samples = [sample('front', 1, 0), sample('rear', 1, 1), sample(null, 1, 2), sample('front', 2, 3)];
  const capture = { ...manifest, samples, sample_count: samples.length, total_bytes: samples.reduce((sum, item) => sum + item.byte_length, 0) };
  globalThis.fetch = async () => jsonResponse(capture);
  assert.deepEqual(await api.getCaptureManifest(simulationId, 0), capture);
  const bad = structuredClone(capture); bad.samples[1].metadata.agent_sensor_id = 'front';
  globalThis.fetch = async () => jsonResponse(bad);
  await assert.rejects(api.getCaptureManifest(simulationId, 0), ApiError);
  for (const invalid of [null, '', '  ', 1]) {
    const malformed = structuredClone(capture); malformed.samples[0].metadata.agent_sensor_id = invalid;
    globalThis.fetch = async () => jsonResponse(malformed);
    await assert.rejects(api.getCaptureManifest(simulationId, 0), ApiError);
  }
});
