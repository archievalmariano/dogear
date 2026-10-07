import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { CRON_WORKFLOWS, DECLARED_CRONS, buildDispatchRequest, dispatchWorkflow, workflowForCron } from "../src/dispatch.js";
import worker from "../src/index.js";

const jsonc = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8").replace(/^\s*\/\/.*$/gm, ""));
const config = jsonc("../wrangler.jsonc");
const ENV = { GITHUB_OWNER: "archievalmariano", GITHUB_REPO: "dogear", GITHUB_REF: "main", GITHUB_TOKEN: "tok",
  PUBLICATION_TARGET: "staging", PUBLICATION_ENABLED: "true" };

test("wrangler crons equal the mapped crons", () => {
  assert.deepEqual([...config.triggers.crons].sort(), [...DECLARED_CRONS].sort());
});

test("Manila times: Friday stage, Monday promote (Sunday UTC), daily catch-up", () => {
  assert.equal(workflowForCron("2 2 * * 5"), "dogear-stage.yml");
  assert.equal(workflowForCron("2 22 * * 0"), "dogear-promote.yml");
  assert.equal(workflowForCron("32 22 * * *"), "dogear-promote.yml");
  assert.equal(workflowForCron("2 22 * * 1"), null);
  assert.equal(workflowForCron("constructor"), null);
  for (const wf of Object.values(CRON_WORKFLOWS)) {
    readFileSync(new URL(`../../../.github/workflows/${wf}`, import.meta.url)); // the workflow exists
  }
});

test("deployed defaults are inert and staging", () => {
  assert.equal(config.vars.PUBLICATION_ENABLED, "false");
  assert.equal(config.vars.PUBLICATION_TARGET, "staging");
  assert.equal(config.vars.GITHUB_REPO, "dogear");
  assert.ok(!JSON.stringify(config).includes("GITHUB_TOKEN\":"), "no token in config");
});

test("dispatch request: token only in the header, explicit target input", () => {
  const { url, init } = buildDispatchRequest({ owner: "o", repo: "r", workflow: "dogear-promote.yml", ref: "main",
    token: "secret-token", target: "staging" });
  assert.ok(!url.includes("secret-token"));
  assert.ok(!init.body.includes("secret-token"));
  assert.deepEqual(JSON.parse(init.body), { ref: "main", inputs: { target: "staging" } });
  assert.throws(() => buildDispatchRequest({ owner: "o", repo: "r", workflow: "w", token: "t", target: "prod" }));
});

test("disabled or unmapped crons dispatch nothing", async () => {
  const calls = [];
  globalThis.fetch = async (...a) => { calls.push(a); return new Response(null, { status: 204 }); };
  await worker.scheduled({ cron: "2 22 * * 0", scheduledTime: 0 }, { ...ENV, PUBLICATION_ENABLED: "false" });
  await worker.scheduled({ cron: "9 9 * * *", scheduledTime: 0 }, ENV);
  assert.equal(calls.length, 0);
  await worker.scheduled({ cron: "2 22 * * 0", scheduledTime: 0 }, ENV);
  assert.equal(calls.length, 1);
  assert.match(calls[0][0], /dogear-promote\.yml\/dispatches$/);
});

test("a failed dispatch fails the invocation", async () => {
  const fetchImpl = async () => new Response("Bad credentials", { status: 401 });
  const r = await dispatchWorkflow(ENV, "dogear-stage.yml", { fetchImpl });
  assert.equal(r.ok, false);
  globalThis.fetch = fetchImpl;
  await assert.rejects(worker.scheduled({ cron: "2 2 * * 5", scheduledTime: 0 }, ENV));
});

test("the HTTP entry point never dispatches", async () => {
  let called = false;
  globalThis.fetch = async () => { called = true; return new Response(null, { status: 204 }); };
  const res = await worker.fetch(new Request("https://x/dispatch", { method: "POST" }), ENV);
  assert.equal(res.status, 200);
  assert.equal(called, false);
});
