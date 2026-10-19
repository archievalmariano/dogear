import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { createHash } from "node:crypto";

import worker, { CHECKS, DECLARED_CRONS, runCheck } from "../src/index.js";
import { friday, monday, daily } from "../src/checks.js";
import { CannotRead, MAX_JSON_BYTES, allowedPath, createClient, parseResult, runState, runTarget } from "../src/github.js";
import { HOSTS, readServed } from "../src/served.js";
import { MESSAGES, buildAlert, sendAlert } from "../src/alert.js";
import { ALERT_ASSIGNEE, DeliveryError, appJwt, createAlertIssue } from "../src/notify.js";
import { main as alertTestMain } from "../../../tools/watchdog_alert_test.mjs";
import { currentMonday, fridayStart, mondayRollover, nextMonday, dayStart } from "../src/time.js";

const jsonc = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8").replace(/^\s*\/\/.*$/gm, ""));
const config = jsonc("../wrangler.jsonc");
const at = (iso) => new Date(iso);
const codes = (findings) => findings.map((f) => f.code).sort();

// ---- fixtures, shaped like GitHub's API (job names as GitHub shows them) ----------

let nextId = 1000;
function run({ event = "schedule", status = "completed", conclusion = "success", attempt = 1, created,
  branch = "main" } = {}) {
  return { id: nextId++, event, status, conclusion, run_attempt: attempt, created_at: created, head_branch: branch };
}
// Jobs of a run through _dogear-publish.yml. target: production | staging | none (caller skipped) | pending (no jobs yet)
function jobs(caller, { target = "production", route = "success", gate, publish = "success", status = "completed" } = {}) {
  if (target === "none") return [{ id: nextId++, name: caller, status: "completed", conclusion: "skipped" }];
  if (target === "pending") return [];
  const gateConclusion = gate || (target === "production" ? "success" : "skipped");
  return [
    { id: nextId++, name: `${caller} / route`, status: "completed", conclusion: route },
    { id: nextId++, name: `${caller} / editorial-gate`, status: "completed", conclusion: gateConclusion },
    { id: nextId++, name: `${caller} / publish`, status, conclusion: status === "completed" ? publish : null },
  ];
}
const publishId = (js, caller) => js.find((j) => j.name === `${caller} / publish`).id;

// Week of Monday 2026-10-19 (Manila). Friday stage 2026-10-23 11:07 Manila = 03:07Z.
const FRI_FIRST = at("2026-10-23T06:00:00Z"), FRI_FINAL = at("2026-10-23T12:00:00Z");
const MON_FIRST = at("2026-10-19T01:00:00Z"), MON_FINAL = at("2026-10-19T04:00:00Z");
const TUE_DAILY = at("2026-10-20T05:00:00Z"), WED_DAILY = at("2026-10-21T05:00:00Z");

// ---- time -----------------------------------------------------------------------------

test("Manila calendar: the Sunday-UTC Monday, the windows", () => {
  assert.equal(currentMonday(at("2026-10-18T22:00:00Z")), "2026-10-19"); // Mon 06:00 Manila is Sun 22:00 UTC
  assert.equal(currentMonday(at("2026-10-18T15:59:00Z")), "2026-10-12"); // Sun 23:59 Manila
  assert.equal(nextMonday(FRI_FIRST), "2026-10-26");
  assert.equal(fridayStart(FRI_FIRST).toISOString(), "2026-10-22T16:00:00.000Z");
  assert.equal(mondayRollover(MON_FIRST).toISOString(), "2026-10-18T22:00:00.000Z");
  assert.equal(dayStart(TUE_DAILY).toISOString(), "2026-10-19T16:00:00.000Z");
});

// ---- configuration ----------------------------------------------------------------------

test("wrangler: crons equal the checks; enabled on production; no secret or recipient in config", () => {
  assert.deepEqual([...config.triggers.crons].sort(), [...DECLARED_CRONS].sort());
  assert.deepEqual(Object.keys(CHECKS).sort(), [...DECLARED_CRONS].sort());
  assert.equal(config.vars.WATCH_ENABLED, "true");
  assert.equal(config.vars.WATCH_TARGET, "production");
  assert.equal(config.vars.GITHUB_REPO, "dogear");
  const text = JSON.stringify(config);
  for (const key of ["GITHUB_TOKEN", "GITHUB_READ_TOKEN", "ALERT_TO", "ALERT_APP", "PRIVATE", "token", "secret"]) {
    assert.ok(!text.includes(key), key);
  }
  assert.equal(config.send_email, undefined, "no email binding: alerts are GitHub issues");
  assert.equal(config.vars.ALERT_REPO, "dogear-alerts");
  assert.equal((text.match(/@/g) || []).length, 0, "no address in the config");
});

// ---- never mutates -------------------------------------------------------------------------

const SRC = new URL("../src/", import.meta.url);
const FORBIDDEN = /\b(POST|PUT|PATCH|DELETE)\b|method\s*:|dispatches|\/rerun|\/cancel|\/approve|pending_deployments/;
const scan = (text) => FORBIDDEN.test(text);

test("source: no request method, no mutating endpoint anywhere in the detection code", () => {
  const files = readdirSync(SRC);
  assert.ok(files.includes("notify.js"));
  for (const name of files.filter((f) => f !== "notify.js")) {
    assert.equal(scan(readFileSync(new URL(name, SRC), "utf8")), false, name);
  }
  // The scan itself catches a mutation (it is what fails if one is added).
  const github = readFileSync(new URL("github.js", SRC), "utf8");
  assert.equal(scan(github.replace('redirect: "manual"', 'method: "PO" + "ST", redirect: "manual"')), true);
  assert.equal(scan(github + "\n// actions/workflows/x/dispatches\n"), true);
});

test("notify.js, the only non-GET module, reaches exactly two endpoints and nothing of dogear", () => {
  const notify = readFileSync(new URL("notify.js", SRC), "utf8");
  const urls = [...notify.matchAll(/`\$\{API\}([^`]*)`/g)].map((m) => m[1]);
  assert.deepEqual(urls, ["/app/installations/${installation}/access_tokens", "/repos/${ALERT_OWNER}/${ALERT_REPO}/issues"]);
  assert.ok(notify.includes('const API = "https://api.github.com";'));
  assert.ok(notify.includes('export const ALERT_OWNER = "archievalmariano";'));
  assert.ok(notify.includes('export const ALERT_REPO = "dogear-alerts";'));
  assert.equal((notify.match(/method: "POST"/g) || []).length, 1, "one POST site, used for the two endpoints");
  for (const bad of ["PUT", "PATCH", "DELETE", "dispatches", "/actions/", "contents", "GITHUB_READ_TOKEN"]) {
    assert.ok(!notify.includes(bad), bad);
  }
  assert.ok(!/repos\/archievalmariano\/dogear[^-]/.test(notify), "never the dogear repository");
});

test("the client reaches only the five allowlisted path shapes", () => {
  for (const ok of ["actions/workflows/dogear-stage.yml", "actions/workflows/dogear-promote.yml",
    "actions/workflows/dogear-promote.yml/runs?branch=main&created=%3E%3D2026-10-18T22%3A00%3A00Z&per_page=30",
    "actions/runs/123/jobs?per_page=50", "actions/jobs/9/logs", "commits?sha=main&per_page=1"]) {
    assert.ok(allowedPath(ok), ok);
  }
  for (const bad of ["actions/workflows/dogear-promote.yml/dispatches", "actions/runs/1/rerun", "actions/runs/1/cancel",
    "actions/workflows/dogear-operate.yml", "actions/variables", "environments", "contents/x", "commits?sha=x&per_page=1",
    "../../orgs", "actions/runs/1/pending_deployments", "actions/workflows/dogear-stage.yml/runs?x=1;y"]) {
    assert.equal(allowedPath(bad), false, bad);
  }
  const client = createClient({ owner: "o", repo: "r", token: "t", fetchImpl: async () => { throw new Error("no"); } });
  return assert.rejects(client.getJson("actions/runs/1/rerun"), /not allowed/);
});

// ---- a fake GitHub and host, recording every request -----------------------------------

const SENTINELS = ["SENTINEL-TITLE-1f2e", "SENTINEL-COMMIT-9a8b", "SENTINEL-BRANCH-77c1", "SENTINEL-STEP-4d4d",
  "SENTINEL-LOG-5e5e"];

// The alert App: a key pair generated here, and a fake of its two endpoints.
const KEYS = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
  publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
const PEM = "-----BEGIN PRIVATE KEY-----\n" + Buffer.from(await crypto.subtle.exportKey("pkcs8", KEYS.privateKey))
  .toString("base64").match(/.{1,64}/g).join("\n") + "\n-----END PRIVATE KEY-----\n";
const APP_ENV = { ALERT_APP_ID: "123456", ALERT_APP_INSTALLATION_ID: "987654", ALERT_APP_PRIVATE_KEY: PEM,
  ALERT_REPO: "dogear-alerts" };
const INSTALL_TOKEN = "INSTALL-TOKEN-SECRET-5a5a";
const EXCHANGE_URL = "https://api.github.com/app/installations/987654/access_tokens";
const ISSUES_URL = "https://api.github.com/repos/archievalmariano/dogear-alerts/issues";

// Handles the alert App's two endpoints; anything else goes to `next`. Records the issues created.
function alertSink(next, { exchangeStatus = 201, issueStatus = 201, drop = () => false, otherLogin = null } = {}) {
  const issues = [];
  const json = (body, status) => new Response(JSON.stringify(body), { status });
  const fetchImpl = async (url, init = {}) => {
    if (String(url) === EXCHANGE_URL && init.method === "POST") return json({ token: INSTALL_TOKEN, expires_at: "x" }, exchangeStatus);
    if (String(url) === ISSUES_URL && init.method === "POST") {
      const body = JSON.parse(init.body);
      const number = issues.length + 1;
      issues.push({ ...body, number });
      const assignees = drop(number) ? (otherLogin ? [{ login: otherLogin }] : []) : body.assignees.map((login) => ({ login }));
      return json({ number, title: body.title, assignees, html_url: `https://github.com/x/${number}` }, issueStatus);
    }
    return next(url, init);
  };
  return { fetchImpl, issues };
}

// An ad-hoc world (a single fetch function) with the alert App in front of it.
function adhoc(next, alerts) {
  const requests = [];
  const sink = alertSink(async (url, init) => next(url, init), alerts);
  return { fetchImpl: async (url, init = {}) => { requests.push({ url: String(url), init }); return sink.fetchImpl(url, init); },
    requests, issues: sink.issues };
}

function fakeWorld({ stageRuns = [], promoteRuns = [], jobsById = {}, logs = {}, workflows = {}, lastCommit,
  manifest, issueBytes, status = {}, host = "production", alerts = {} } = {}) {
  const requests = [];
  const respond = (body, code = 200, headers = {}) => new Response(typeof body === "string" ? body : JSON.stringify(body),
    { status: code, headers });
  const decorate = (r) => ({ ...r, name: SENTINELS[0], display_title: SENTINELS[0], head_commit: { message: SENTINELS[1] },
    head_branch: r.head_branch, path: ".github/workflows/x.yml" });
  const fetchImpl = async (url, init = {}) => {
    requests.push({ url: String(url), init });
    const u = new URL(String(url));
    if (u.host === "api.github.com") {
      const path = u.pathname.replace("/repos/archievalmariano/dogear/", "") + u.search;
      if (status[path.split("?")[0]]) return respond({ message: "x" }, status[path.split("?")[0]]);
      let m;
      if ((m = path.match(/^actions\/workflows\/(dogear-stage|dogear-promote)\.yml$/))) {
        return respond({ state: workflows[`${m[1]}.yml`] || "active", name: SENTINELS[0] });
      }
      if ((m = path.match(/^actions\/workflows\/(dogear-stage|dogear-promote)\.yml\/runs\?(.*)$/))) {
        const q = new URLSearchParams(m[2]);
        const since = Date.parse(q.get("created").slice(2));
        const list = (m[1] === "dogear-stage" ? stageRuns : promoteRuns)
          .filter((r) => Date.parse(r.created_at) >= since && (!q.get("event") || r.event === q.get("event")));
        return respond({ total_count: list.length, workflow_runs: list.map(decorate) });
      }
      if ((m = path.match(/^actions\/runs\/(\d+)\/jobs/))) {
        return respond({ jobs: (jobsById[m[1]] || []).map((j) => ({ ...j, steps: [{ name: SENTINELS[3] }] })) });
      }
      if ((m = path.match(/^actions\/jobs\/(\d+)\/logs$/))) {
        if (!(m[1] in logs)) return respond("", 404);
        return respond("", 302, { location: `https://logs.example.net/signed/${m[1]}?sig=abc` });
      }
      if (path.startsWith("commits?")) {
        return respond(lastCommit === undefined ? [] : [{ commit: { committer: { date: lastCommit }, message: SENTINELS[1] } }]);
      }
      return respond({ message: "unexpected" }, 404);
    }
    if (u.host === "logs.example.net") return respond(logs[u.pathname.split("/").pop()]);
    if (`${u.protocol}//${u.host}` === HOSTS[host]) {
      if (u.pathname === "/current.json") return manifest ? respond(manifest) : respond("", 404);
      if (issueBytes && u.pathname === `/${manifest.issuePath}`) return new Response(issueBytes, { status: 200 });
      return respond("", 404);
    }
    return respond("", 404);
  };
  const sink = alertSink(fetchImpl, alerts);
  const recorded = async (url, init = {}) => {
    if (String(url) === EXCHANGE_URL || String(url) === ISSUES_URL) requests.push({ url: String(url), init });
    return sink.fetchImpl(url, init);
  };
  return { fetchImpl: recorded, requests, issues: sink.issues };
}

function servedIssue(week, body = `{"issue":"${week}"}`) {
  const bytes = new TextEncoder().encode(body);
  const sha = createHash("sha256").update(bytes).digest("hex");
  return { bytes, manifest: { issueId: `dogear-${week}`, issuePath: `issues/dogear-${week}.${sha.slice(0, 16)}.json`,
    issueSha256: sha, revision: 0, webPath: `${week}/`, week: { start: week, end: "x" } } };
}

const logFor = (status, target = "production", op = "stage-next") => [
  "2026-10-23T03:08:00.1Z staged: staged 2027-03-08: 4 items, issue b2918edc000d", // test-suite noise
  `2026-10-23T03:08:01.1Z noop: ${op}`, // noise that looks like a result, before the wrapper line
  `2026-10-23T03:09:00.1Z ${SENTINELS[4]}`,
  `2026-10-23T03:09:10.1Z running: dogear publish ${op} --target ${target}`,
  `2026-10-23T03:09:20.1Z ${status}: ${op}`,
  "2026-10-23T03:09:21.1Z   1 notice(s) saved in the private publication data (runs/)",
].join("\n");

async function invoke(cron, scheduledTime, world, envExtra = {}) {
  const logged = [];
  const env = { GITHUB_OWNER: "archievalmariano", GITHUB_REPO: "dogear", WATCH_TARGET: "production",
    WATCH_ENABLED: "true", GITHUB_READ_TOKEN: "TOKEN-SECRET-0000", ...APP_ENV, ...envExtra };
  const realFetch = globalThis.fetch, realLog = console.log;
  globalThis.fetch = world.fetchImpl;
  console.log = (line) => logged.push(String(line));
  let error = null;
  try {
    await worker.scheduled({ cron, scheduledTime: scheduledTime.getTime() }, env);
  } catch (err) {
    error = err;
  } finally {
    globalThis.fetch = realFetch;
    console.log = realLog;
  }
  const sent = (world.issues || []).map((i) => ({ subject: i.title, text: i.body, assignees: i.assignees }));
  return { sent, logged, error, codes: sent.map((m) => m.subject.split(" ")[2]).sort() };
}

function fridayWorld({ conclusion = "success", status = "completed", attempt = 1, target = "production", result = "staged",
  logTarget = target, extraRuns = [] } = {}) {
  const r = run({ created: "2026-10-23T03:07:00Z", conclusion, status, attempt });
  const js = jobs("stage", { target, status: status === "completed" ? "completed" : "in_progress" });
  const jobsById = { [r.id]: js };
  const logs = {};
  if (target !== "none" && target !== "pending") logs[publishId(js, "stage")] = logFor(result, logTarget);
  for (const [er, ej] of extraRuns) jobsById[er.id] = ej;
  return fakeWorld({ stageRuns: [r, ...extraRuns.map(([er]) => er)], jobsById, logs });
}

// ---- every check is GET-only, and the token stays home --------------------------------------

test("detection makes only GET requests; delivery only two POSTs; each credential reaches only its endpoint", async () => {
  const issue = servedIssue("2026-10-19");
  const pr = run({ created: "2026-10-18T23:07:00Z" });
  const worlds = [
    ["0 12 * * 5", FRI_FINAL, fridayWorld()],
    ["0 4 * * 1", MON_FINAL, fakeWorld({ promoteRuns: [pr], jobsById: { [pr.id]: jobs("promote") }, manifest: issue.manifest,
      issueBytes: issue.bytes })],
    ["0 5 * * *", TUE_DAILY, fakeWorld({ promoteRuns: [run({ created: "2026-10-20T00:07:00Z" })], lastCommit: "2026-10-15T00:00:00Z" })],
  ];
  for (const [cron, when, world] of worlds) {
    await invoke(cron, when, world);
    assert.ok(world.requests.length > 0, cron);
    for (const { url, init } of world.requests) {
      assert.ok(!url.includes("TOKEN-SECRET"), url);
      const auth = init.headers && init.headers.Authorization;
      if (url === EXCHANGE_URL) { // the App JWT, and only here
        assert.equal(init.method, "POST");
        assert.match(auth, /^Bearer [\w-]+\.[\w-]+\.[\w-]+$/);
        assert.ok(!auth.includes("TOKEN-SECRET") && !auth.includes(INSTALL_TOKEN));
        continue;
      }
      if (url === ISSUES_URL) { // the installation token, and only here
        assert.equal(init.method, "POST");
        assert.equal(auth, `Bearer ${INSTALL_TOKEN}`);
        continue;
      }
      assert.equal(init.method, undefined, `${cron} ${url}`); // detection: fetch's default, GET
      assert.equal(init.body, undefined);
      if (url.startsWith("https://api.github.com/repos/archievalmariano/dogear/")) {
        assert.equal(auth, "Bearer TOKEN-SECRET-0000");
      } else {
        assert.equal(auth, undefined, `no credential to ${url}`);
      }
      assert.equal(scan(url), false, url);
    }
  }
});

test("the job-log redirect is fetched without the token, and only over https", async () => {
  const seen = [];
  const fetchImpl = async (url, init) => {
    seen.push({ url, init });
    if (String(url).startsWith("https://api.github.com/")) {
      return new Response("", { status: 302, headers: { location: "https://blob.example/x?sig=1" } });
    }
    return new Response("2026-01-01T00:00:00Z running: dogear publish stage-next --target production\n"
      + "2026-01-01T00:00:01Z staged: stage-next\n");
  };
  const client = createClient({ owner: "archievalmariano", repo: "dogear", token: "TOKEN-SECRET-0000", fetchImpl });
  assert.deepEqual(await client.jobResult(5, "stage-next"), { status: "staged", target: "production" });
  assert.equal(seen[0].init.headers.Authorization, "Bearer TOKEN-SECRET-0000");
  assert.deepEqual(seen[1].init.headers, {});
  assert.equal(seen[1].init.method, undefined);
  const insecure = createClient({ owner: "a", repo: "b", token: "t",
    fetchImpl: async () => new Response("", { status: 302, headers: { location: "http://blob.example/x" } }) });
  await assert.rejects(insecure.jobResult(5, "stage-next"), CannotRead);
  const unauth = [];
  const anon = createClient({ owner: "a", repo: "b", fetchImpl: async (u, i) => { unauth.push(i); return new Response("{}"); } });
  await anon.getJson("actions/workflows/dogear-stage.yml");
  assert.equal(unauth[0].headers.Authorization, undefined);
});

// ---- the deadline covers the whole body (Codex implementation HOLD) --------------------------

// Headers arrive at once; the body sends `first` and then never completes. These tests carry
// their own timeout, so a regression fails here instead of hanging CI.
const stalled = (first = "{\"workflow_runs\": [", headers = {}) => {
  const cancelled = { value: false };
  const body = new ReadableStream({
    start(c) { c.enqueue(new TextEncoder().encode(first)); },
    cancel() { cancelled.value = true; },
  });
  return { response: () => new Response(body, { status: 200, headers }), cancelled };
};

async function within(ms, promise) {
  const started = Date.now();
  let error = null;
  try {
    await promise;
  } catch (err) {
    error = err;
  }
  return { error, elapsed: Date.now() - started };
}

test("a GitHub body that never completes is CannotRead within the deadline, and its stream is cancelled", { timeout: 5000 }, async () => {
  const s = stalled();
  const client = createClient({ owner: "archievalmariano", repo: "dogear", token: "t", timeoutMs: 50,
    fetchImpl: async () => s.response() });
  const { error, elapsed } = await within(50, client.getJson("actions/workflows/dogear-stage.yml"));
  assert.ok(error instanceof CannotRead, String(error));
  assert.ok(elapsed < 2000, `took ${elapsed} ms`);
  assert.equal(s.cancelled.value, true);
});

test("a redirected job log that never completes is CannotRead within the deadline", { timeout: 5000 }, async () => {
  const s = stalled("2026-01-01T00:00:00Z running: dogear publish stage-next --target production\n");
  const fetchImpl = async (url) => (String(url).startsWith("https://api.github.com/")
    ? new Response("", { status: 302, headers: { location: "https://blob.example/x" } })
    : s.response());
  const client = createClient({ owner: "archievalmariano", repo: "dogear", token: "t", timeoutMs: 50, fetchImpl });
  const { error, elapsed } = await within(50, client.jobResult(5, "stage-next"));
  assert.ok(error instanceof CannotRead, String(error));
  assert.ok(elapsed < 2000, `took ${elapsed} ms`);
});

test("a whole check over a stalled GitHub fails as CannotRead (the WATCHDOG_CANNOT_READ alert), not a hang", { timeout: 5000 }, async () => {
  for (const [check, when] of [["friday", FRI_FINAL], ["monday", MON_FINAL], ["daily", TUE_DAILY]]) {
    const client = createClient({ owner: "archievalmariano", repo: "dogear", token: "t", timeoutMs: 50,
      fetchImpl: async () => stalled().response() });
    const { error, elapsed } = await within(50, runCheck({ check, final: true }, client, when, "production",
      async () => stalled().response()));
    assert.ok(error instanceof CannotRead, `${check}: ${error}`);
    assert.ok(elapsed < 2000, `${check} took ${elapsed} ms`);
  }
});

test("a stalled or oversized manifest or issue is not served, within the deadline", { timeout: 5000 }, async () => {
  const issue = servedIssue("2026-10-19");
  const stallManifest = async () => stalled("{\"week\":").response();
  let r = await within(50, readServed("production", { fetchImpl: stallManifest, timeoutMs: 50 }));
  assert.equal(r.error, null);
  assert.ok(r.elapsed < 2000);
  assert.equal((await readServed("production", { fetchImpl: stallManifest, timeoutMs: 50 })).readable, false);
  const stallIssue = async (url) => (String(url).endsWith("/current.json")
    ? new Response(JSON.stringify(issue.manifest)) : stalled("{").response());
  r = await within(50, readServed("production", { fetchImpl: stallIssue, timeoutMs: 50 }));
  assert.ok(r.elapsed < 2000);
  assert.deepEqual(await readServed("production", { fetchImpl: stallIssue, timeoutMs: 50 }),
    { readable: true, week: "2026-10-19", issueServed: false });
  const huge = async () => new Response(new Uint8Array(70 * 1024));
  assert.equal((await readServed("production", { fetchImpl: huge })).readable, false);
});

test("an oversized GitHub body is refused, not read to the end", { timeout: 5000 }, async () => {
  let pulled = 0;
  const endless = () => new Response(new ReadableStream({
    pull(c) { pulled += 1; c.enqueue(new Uint8Array(256 * 1024)); },
  }));
  const client = createClient({ owner: "archievalmariano", repo: "dogear", token: "t", timeoutMs: 5000,
    fetchImpl: async () => endless() });
  await assert.rejects(client.getJson("actions/workflows/dogear-stage.yml"), /too large/);
  assert.ok(pulled * 256 * 1024 <= MAX_JSON_BYTES + 512 * 1024, `read ${pulled} chunks`);
});

// ---- the classifiers (§2.0, §2.0a) ----------------------------------------------------------

test("run state: only completed + success is healthy; every other conclusion fails; skipped caller is the stop", () => {
  const ran = jobs("promote");
  assert.equal(runState({ status: "completed", conclusion: "success" }, ran, "promote"), "healthy");
  for (const c of ["failure", "cancelled", "timed_out", "action_required", "stale", "neutral", "startup_failure",
    "skipped", "something_new", null]) {
    assert.equal(runState({ status: "completed", conclusion: c }, ran, "promote"), "failed", String(c));
  }
  for (const s of ["queued", "in_progress", "waiting", "pending", "requested", "something_new"]) {
    assert.equal(runState({ status: s, conclusion: null }, ran, "promote"), "pending", s);
  }
  const stopped = jobs("promote", { target: "none" });
  assert.equal(runState({ status: "completed", conclusion: "success" }, stopped, "promote"), "stop");
  assert.equal(runState({ status: "completed", conclusion: "skipped" }, stopped, "promote"), "stop");
  assert.equal(runState({ status: "completed", conclusion: "skipped" }, [], "promote"), "stop");
});

test("run target: the editorial gate runs only for production", () => {
  for (const c of ["success", "failure", "cancelled"]) {
    assert.equal(runTarget(jobs("promote", { gate: c }), "promote"), "production");
  }
  assert.equal(runTarget([{ name: "promote / editorial-gate", status: "queued", conclusion: null }], "promote"), "production");
  assert.equal(runTarget([{ name: "promote / editorial-gate", status: "in_progress", conclusion: null }], "promote"), "production");
  assert.equal(runTarget(jobs("promote", { target: "staging" }), "promote"), "staging");
  assert.equal(runTarget([], "promote"), "unknown");
  assert.equal(runTarget(jobs("promote", { target: "none" }), "promote"), "unknown");
  assert.equal(runTarget(jobs("stage"), "promote"), "unknown"); // another caller's jobs never count
});

test("the result line is anchored on the wrapper line, past the test suite's look-alikes", () => {
  assert.deepEqual(parseResult(logFor("staged"), "stage-next"), { status: "staged", target: "production" });
  assert.deepEqual(parseResult(logFor("noop", "staging"), "stage-next"), { status: "noop", target: "staging" });
  assert.equal(parseResult("2026Z staged: stage-next\n", "stage-next"), null); // no wrapper line
  const twice = logFor("staged") + "\n2026Z running: dogear publish stage-next --target production\n2026Z staged: stage-next";
  assert.equal(parseResult(twice, "stage-next"), null);
  assert.equal(parseResult(logFor("staged", "production", "promote"), "stage-next"), null);
});

// ---- Friday (§2.1) ----------------------------------------------------------------------------

test("Friday: a proof staged unattended is OK; noop, pending, missing, failed and wrong shapes alert", async () => {
  assert.deepEqual((await invoke("0 6 * * 5", FRI_FIRST, fridayWorld())).codes, []);
  assert.deepEqual((await invoke("0 12 * * 5", FRI_FINAL, fridayWorld())).codes, []);
  assert.deepEqual((await invoke("0 12 * * 5", FRI_FINAL, fridayWorld({ result: "noop" }))).codes, ["STAGE_NOOP"]);
  assert.deepEqual((await invoke("0 12 * * 5", FRI_FINAL, fridayWorld({ result: "held" }))).codes, ["WATCHDOG_CANNOT_READ"]);
  assert.deepEqual((await invoke("0 12 * * 5", FRI_FINAL, fridayWorld({ logTarget: "staging" }))).codes,
    ["WATCHDOG_CANNOT_READ"]);
  assert.deepEqual((await invoke("0 12 * * 5", FRI_FINAL, fridayWorld({ attempt: 2 }))).codes, ["STAGE_NOT_UNATTENDED"]);
  assert.deepEqual((await invoke("0 6 * * 5", FRI_FIRST, fridayWorld({ status: "in_progress", conclusion: null }))).codes, []);
  assert.deepEqual((await invoke("0 12 * * 5", FRI_FINAL, fridayWorld({ status: "queued", conclusion: null }))).codes,
    ["STAGE_RUN_PENDING"]);
  assert.deepEqual((await invoke("0 6 * * 5", FRI_FIRST, fakeWorld())).codes, []);
  assert.deepEqual((await invoke("0 12 * * 5", FRI_FINAL, fakeWorld())).codes, ["STAGE_RUN_MISSING"]);
  for (const c of ["failure", "cancelled", "timed_out", "action_required"]) {
    assert.deepEqual((await invoke("0 6 * * 5", FRI_FIRST, fridayWorld({ conclusion: c }))).codes, ["STAGE_RUN_FAILED"], c);
  }
  assert.deepEqual((await invoke("0 6 * * 5", FRI_FIRST, fridayWorld({ target: "none" }))).codes, ["EMERGENCY_STOP"]);
  // An in-progress run with no jobs yet is pending for the watched target.
  assert.deepEqual((await invoke("0 6 * * 5", FRI_FIRST, fridayWorld({ target: "pending", status: "queued",
    conclusion: null }))).codes, []);
});

test("Friday: a staging stage run while watching production is WRONG_TARGET, never a proof", async () => {
  assert.deepEqual((await invoke("0 6 * * 5", FRI_FIRST, fridayWorld({ target: "staging", logTarget: "staging" }))).codes,
    ["WRONG_TARGET"]);
  assert.deepEqual((await invoke("0 12 * * 5", FRI_FINAL, fridayWorld({ target: "staging", logTarget: "staging" }))).codes,
    ["STAGE_RUN_MISSING", "WRONG_TARGET"]);
  // Watching staging, the same run is the proof.
  assert.deepEqual((await invoke("0 12 * * 5", FRI_FINAL, fridayWorld({ target: "staging", logTarget: "staging" }),
    { WATCH_TARGET: "staging" })).codes, []);
});

// ---- Monday (§2.2) ----------------------------------------------------------------------------

function mondayCase({ runs: list, target = "production", manifestWeek = "2026-10-19", alterIssue = false, issue404 = false }) {
  const issue = servedIssue(manifestWeek);
  const runs = [], jobsById = {};
  for (const spec of list) {
    const r = run({ event: spec.event || "schedule", created: spec.created || "2026-10-18T23:07:00Z",
      status: spec.status || "completed", conclusion: spec.conclusion === undefined ? "success" : spec.conclusion });
    runs.push(r);
    jobsById[r.id] = jobs("promote", { target: spec.target || "production" });
  }
  return fakeWorld({ promoteRuns: runs, jobsById, manifest: issue.manifest, host: target,
    issueBytes: issue404 ? undefined : (alterIssue ? new TextEncoder().encode("tampered") : issue.bytes) });
}

test("Monday: current week, issue served with its hash and a successful run is the only OK", async () => {
  assert.deepEqual((await invoke("0 1 * * 1", MON_FIRST, mondayCase({ runs: [{}] }))).codes, []);
  assert.deepEqual((await invoke("0 4 * * 1", MON_FINAL, mondayCase({ runs: [{}], issue404: true }))).codes, ["ISSUE_UNSERVED"]);
  assert.deepEqual((await invoke("0 4 * * 1", MON_FINAL, mondayCase({ runs: [{}], alterIssue: true }))).codes, ["ISSUE_UNSERVED"]);
  for (const c of ["failure", "cancelled", "timed_out", "action_required"]) {
    assert.deepEqual((await invoke("0 1 * * 1", MON_FIRST, mondayCase({ runs: [{ conclusion: c }] }))).codes,
      ["MONDAY_UNVERIFIED"], c); // Codex: a cancelled run after the switch is not OK
  }
  assert.deepEqual((await invoke("0 1 * * 1", MON_FIRST, mondayCase({ runs: [] }))).codes, []);
  assert.deepEqual((await invoke("0 4 * * 1", MON_FINAL, mondayCase({ runs: [] }))).codes, ["MONDAY_UNVERIFIED"]);
  assert.deepEqual((await invoke("0 1 * * 1", MON_FIRST, mondayCase({ runs: [{ status: "queued", conclusion: null }] }))).codes, []);
  assert.deepEqual((await invoke("0 4 * * 1", MON_FINAL, mondayCase({ runs: [{ status: "queued", conclusion: null }] }))).codes,
    ["MONDAY_NOT_PUBLISHED"]);
  // A failure resolved by a later successful (here: manual recovery) production run.
  assert.deepEqual((await invoke("0 4 * * 1", MON_FINAL, mondayCase({ runs: [{ conclusion: "failure" },
    { event: "workflow_dispatch", created: "2026-10-19T01:30:00Z" }] }))).codes, []);
});

test("Monday: the previous week still current", async () => {
  const stale = { manifestWeek: "2026-10-12" };
  assert.deepEqual((await invoke("0 1 * * 1", MON_FIRST, mondayCase({ ...stale, runs: [{ conclusion: "failure" }] }))).codes,
    ["MONDAY_HELD"]);
  assert.deepEqual((await invoke("0 1 * * 1", MON_FIRST, mondayCase({ ...stale, runs: [] }))).codes, []);
  assert.deepEqual((await invoke("0 4 * * 1", MON_FINAL, mondayCase({ ...stale, runs: [] }))).codes, ["MONDAY_NOT_PUBLISHED"]);
  assert.deepEqual((await invoke("0 1 * * 1", MON_FIRST, mondayCase({ ...stale, runs: [{}] }))).codes, ["MONDAY_NOT_PUBLISHED"]);
  const r = run({ created: "2026-10-18T23:07:00Z" });
  const world = fakeWorld({ promoteRuns: [r], jobsById: { [r.id]: jobs("promote", { target: "none" }) },
    manifest: servedIssue("2026-10-12").manifest, issueBytes: servedIssue("2026-10-12").bytes });
  assert.deepEqual((await invoke("0 1 * * 1", MON_FIRST, world)).codes, ["EMERGENCY_STOP"]);
  assert.deepEqual((await invoke("0 1 * * 1", MON_FIRST, fakeWorld({ promoteRuns: [] }))).codes, ["HOST_UNREADABLE"]);
});

test("Monday target binding, both directions (Codex rev 4)", async () => {
  // A current, hash-valid production issue with a production success, then a staging success.
  const after = { target: "staging", created: "2026-10-19T00:07:00Z" };
  assert.deepEqual((await invoke("0 4 * * 1", MON_FINAL, mondayCase({ runs: [{}, after] }))).codes, ["WRONG_TARGET"]);
  // A production failure after the switch, then a staging success: not cleared.
  assert.deepEqual((await invoke("0 4 * * 1", MON_FINAL, mondayCase({ runs: [{ conclusion: "failure" }, after] }))).codes,
    ["MONDAY_UNVERIFIED", "WRONG_TARGET"]);
  // A manual staging run does not clear a production failure either.
  assert.deepEqual((await invoke("0 4 * * 1", MON_FINAL, mondayCase({ runs: [{ conclusion: "failure" },
    { ...after, event: "workflow_dispatch" }] }))).codes, ["MONDAY_UNVERIFIED", "WRONG_TARGET"]);
  // The mirror, watching staging: production runs are the wrong target.
  const prodAfter = { target: "production", created: "2026-10-19T00:07:00Z" };
  const env = { WATCH_TARGET: "staging" };
  assert.deepEqual((await invoke("0 4 * * 1", MON_FINAL, mondayCase({ target: "staging",
    runs: [{ target: "staging" }, prodAfter] }), env)).codes, ["WRONG_TARGET"]);
  assert.deepEqual((await invoke("0 4 * * 1", MON_FINAL, mondayCase({ target: "staging",
    runs: [{ target: "staging", conclusion: "failure" }, prodAfter] }), env)).codes, ["MONDAY_UNVERIFIED", "WRONG_TARGET"]);
});

test("served: the manifest must name its own week and hash; the issue must hash to it", async () => {
  const issue = servedIssue("2026-10-19");
  const serve = (manifest, bytes, status = 200) => async (url) => (String(url).endsWith("/current.json")
    ? new Response(JSON.stringify(manifest), { status })
    : new Response(bytes, { status: bytes ? 200 : 404 }));
  assert.deepEqual(await readServed("production", { fetchImpl: serve(issue.manifest, issue.bytes) }),
    { readable: true, week: "2026-10-19", issueServed: true });
  const otherWeek = { ...issue.manifest, week: { start: "2026-10-26" } };
  assert.equal((await readServed("production", { fetchImpl: serve(otherWeek, issue.bytes) })).readable, false);
  const otherSha = { ...issue.manifest, issueSha256: "0".repeat(64) };
  assert.equal((await readServed("production", { fetchImpl: serve(otherSha, issue.bytes) })).readable, false);
  assert.equal((await readServed("production", { fetchImpl: serve(issue.manifest, null) })).issueServed, false);
  assert.equal((await readServed("production", { fetchImpl: serve(issue.manifest, new Uint8Array(40000)) })).issueServed, false);
  assert.equal((await readServed("production", { fetchImpl: async () => new Response("<html>", { status: 200 }) })).readable, false);
  assert.equal((await readServed("production", { fetchImpl: async () => { throw new Error("down"); } })).readable, false);
});

// ---- daily (§2.3) ------------------------------------------------------------------------------

function dailyCase({ runs: list = [], workflows = {}, lastCommit = "2026-10-15T00:00:00Z", target = "production" } = {}) {
  const runs = [], jobsById = {};
  for (const spec of list) {
    const r = run({ created: spec.created, status: spec.status || "completed",
      conclusion: spec.conclusion === undefined ? "success" : spec.conclusion });
    runs.push(r);
    jobsById[r.id] = jobs("promote", { target: spec.target || target });
  }
  return fakeWorld({ promoteRuns: runs, jobsById, workflows, lastCommit });
}
const MON_RUN = { created: "2026-10-19T00:07:00Z" }, TUE_RUN = { created: "2026-10-20T00:07:00Z" };
const WED_RUN = { created: "2026-10-21T00:07:00Z" };

test("daily: Codex's Tuesday — Monday's run present, Tuesday's never starts", async () => {
  assert.deepEqual((await invoke("0 5 * * *", TUE_DAILY, dailyCase({ runs: [MON_RUN] }))).codes, ["SCHEDULE_DROPPED"]);
  // Wednesday's run does not change Tuesday's result, and Wednesday judges Wednesday alone.
  assert.deepEqual((await invoke("0 5 * * *", TUE_DAILY, dailyCase({ runs: [MON_RUN, WED_RUN] }))).codes, ["SCHEDULE_DROPPED"]);
  assert.deepEqual((await invoke("0 5 * * *", WED_DAILY, dailyCase({ runs: [MON_RUN, WED_RUN] }))).codes, []);
  // Yesterday's run, however late, never counts for today: Monday's run delayed to 12:30 Manila is
  // under 26 hours old at Tuesday 13:00, and Tuesday still has no run.
  assert.deepEqual((await invoke("0 5 * * *", TUE_DAILY, dailyCase({ runs: [{ created: "2026-10-19T04:30:00Z" }] }))).codes,
    ["SCHEDULE_DROPPED"]);
  // Grace: a run created late but before 13:00 counts; one created after the check time does not.
  assert.deepEqual((await invoke("0 5 * * *", TUE_DAILY, dailyCase({ runs: [{ created: "2026-10-20T04:59:00Z" }] }))).codes, []);
});

test("daily decision alone: yesterday's run is never today's, even if the query returned it", () => {
  const late = run({ created: "2026-10-19T04:30:00Z" }); // Monday, delayed: under 26 h old at Tuesday 13:00
  const input = { workflows: { "dogear-stage.yml": "active", "dogear-promote.yml": "active" }, runs: [late],
    jobsById: { [late.id]: jobs("promote") }, lastCommitDate: "2026-10-15T00:00:00Z", now: TUE_DAILY, target: "production" };
  assert.deepEqual(codes(daily(input)), ["SCHEDULE_DROPPED"]);
  const early = run({ created: "2026-10-20T05:00:01Z" }); // after the check time
  assert.deepEqual(codes(daily({ ...input, runs: [early], jobsById: { [early.id]: jobs("promote") } })), ["SCHEDULE_DROPPED"]);
  // Friday and Monday decisions apply their own windows too.
  const thursday = run({ created: "2026-10-22T15:59:00Z" });
  assert.deepEqual(codes(friday({ runs: [thursday], jobsById: { [thursday.id]: jobs("stage") }, results: {}, now: FRI_FINAL,
    final: true, target: "production" })), ["STAGE_RUN_MISSING"]);
  const sunday = run({ created: "2026-10-18T21:59:00Z" });
  assert.deepEqual(codes(monday({ runs: [sunday], jobsById: { [sunday.id]: jobs("promote") },
    served: { readable: true, week: "2026-10-19", issueServed: true }, now: MON_FINAL, final: true, target: "production" })),
  ["MONDAY_UNVERIFIED"]);
});

test("daily: today's run must have completed with success by 13:00", async () => {
  assert.deepEqual((await invoke("0 5 * * *", TUE_DAILY, dailyCase({ runs: [TUE_RUN] }))).codes, []);
  for (const c of ["failure", "cancelled", "timed_out", "action_required"]) {
    assert.deepEqual((await invoke("0 5 * * *", TUE_DAILY, dailyCase({ runs: [{ ...TUE_RUN, conclusion: c }] }))).codes,
      ["PROMOTE_RUN_FAILED"], c);
  }
  assert.deepEqual((await invoke("0 5 * * *", TUE_DAILY, dailyCase({ runs: [{ ...TUE_RUN, status: "queued",
    conclusion: null }] }))).codes, ["PROMOTE_RUN_PENDING"]); // Codex: a queued run is not present-and-healthy
  // Monday's two runs: the latest decides.
  const MON_DAILY = at("2026-10-19T05:00:00Z");
  assert.deepEqual((await invoke("0 5 * * *", MON_DAILY, dailyCase({ runs: [{ created: "2026-10-18T23:07:00Z",
    conclusion: "failure" }, MON_RUN] }))).codes, []);
});

test("daily: emergency stop, disabled workflows, wrong target, inactivity", async () => {
  const r = run({ created: TUE_RUN.created });
  const stopped = fakeWorld({ promoteRuns: [r], jobsById: { [r.id]: jobs("promote", { target: "none" }) },
    lastCommit: "2026-10-15T00:00:00Z" });
  assert.deepEqual((await invoke("0 5 * * *", TUE_DAILY, stopped)).codes, ["EMERGENCY_STOP"]);
  assert.deepEqual((await invoke("0 5 * * *", TUE_DAILY, dailyCase({ workflows: { "dogear-promote.yml": "disabled_inactivity" } }))).codes,
    ["WORKFLOW_DISABLED"]);
  assert.deepEqual((await invoke("0 5 * * *", TUE_DAILY, dailyCase({ runs: [TUE_RUN],
    workflows: { "dogear-stage.yml": "disabled_manually" } }))).codes, ["WORKFLOW_DISABLED"]);
  assert.deepEqual((await invoke("0 5 * * *", TUE_DAILY, dailyCase({ runs: [{ ...TUE_RUN, target: "staging" }] }))).codes,
    ["SCHEDULE_DROPPED", "WRONG_TARGET"]);
  assert.deepEqual((await invoke("0 5 * * *", TUE_DAILY, dailyCase({ runs: [TUE_RUN,
    { created: "2026-10-20T01:00:00Z", target: "staging" }] }))).codes, ["WRONG_TARGET"]);
  assert.deepEqual((await invoke("0 5 * * *", TUE_DAILY, dailyCase({ runs: [TUE_RUN], lastCommit: "2026-08-30T00:00:00Z" }))).codes,
    ["INACTIVITY_SOON"]); // 51 days
  assert.deepEqual((await invoke("0 5 * * *", TUE_DAILY, dailyCase({ runs: [TUE_RUN], lastCommit: "2026-09-01T00:00:00Z" }))).codes,
    []); // 49 days
});

// ---- inert, failures, liveness ----------------------------------------------------------------

test("inert unless enabled; unmapped crons and a bad target do nothing", async () => {
  for (const extra of [{ WATCH_ENABLED: "false" }, { WATCH_ENABLED: "" }, { WATCH_ENABLED: undefined }]) {
    const world = fridayWorld();
    const out = await invoke("0 12 * * 5", FRI_FINAL, world, extra);
    assert.equal(world.requests.length, 0);
    assert.equal(out.sent.length, 0);
  }
  const world = fridayWorld();
  assert.equal((await invoke("9 9 * * *", FRI_FINAL, world)).sent.length, 0);
  assert.equal(world.requests.length, 0);
  const bad = await invoke("0 12 * * 5", FRI_FINAL, world, { WATCH_TARGET: "prod" });
  assert.ok(bad.error);
  assert.equal(world.requests.length, 0);
});

test("fail loud: unreadable GitHub, a missing token, an error, an alert that cannot be sent", async () => {
  for (const code of [403, 429, 500]) {
    const world = fakeWorld({ status: { "actions/workflows/dogear-stage.yml/runs": code } });
    assert.deepEqual((await invoke("0 12 * * 5", FRI_FINAL, world)).codes, ["WATCHDOG_CANNOT_READ"], String(code));
  }
  const malformed = adhoc(async () => new Response("not json", { status: 200 }));
  assert.deepEqual((await invoke("0 5 * * *", TUE_DAILY, malformed)).codes, ["WATCHDOG_CANNOT_READ"]);
  const timeout = adhoc(async () => { throw Object.assign(new Error("t"), { name: "AbortError" }); });
  assert.deepEqual((await invoke("0 5 * * *", TUE_DAILY, timeout)).codes, ["WATCHDOG_CANNOT_READ"]);
  assert.deepEqual((await invoke("0 12 * * 5", FRI_FINAL, fridayWorld(), { GITHUB_READ_TOKEN: "" })).codes,
    ["WATCHDOG_CANNOT_READ"]);
  const crashed = await invoke("0 5 * * *", TUE_DAILY, adhoc(async (u) => (String(u).includes("/jobs")
    ? new Response(JSON.stringify({ jobs: [null] }))
    : new Response(JSON.stringify({ workflow_runs: [{ id: 1, created_at: null }] })))));
  assert.deepEqual(crashed.codes, ["WATCHDOG_ERROR"]);
  assert.ok(crashed.error, "the invocation fails after alerting");
  for (const alerts of [{ issueStatus: 403 }, { issueStatus: 422 }, { exchangeStatus: 401 }]) {
    const unsent = await invoke("0 12 * * 5", FRI_FINAL, fakeWorld({ alerts }));
    assert.ok(unsent.error, JSON.stringify(alerts));
    assert.ok(unsent.logged.some((l) => l.includes("alert not sent") && l.includes("STAGE_RUN_MISSING")));
    assert.ok(unsent.logged.some((l) => l.includes("check done")), "detection is still logged");
  }
  // Missing App secrets: nothing is sent, the error is logged, the invocation fails.
  const noApp = fakeWorld();
  const out = await invoke("0 12 * * 5", FRI_FINAL, noApp, { ALERT_APP_PRIVATE_KEY: "" });
  assert.ok(out.error);
  assert.equal(noApp.requests.filter((r) => r.url === EXCHANGE_URL || r.url === ISSUES_URL).length, 0);
  assert.ok(out.logged.some((l) => l.includes("alert not sent")));
});

test("alerts and logs are public-safe: fixed templates, no planted text from GitHub or the logs", async () => {
  const cases = [["0 12 * * 5", FRI_FINAL, fridayWorld({ result: "noop" })], ["0 12 * * 5", FRI_FINAL, fakeWorld()],
    ["0 5 * * *", TUE_DAILY, dailyCase({ runs: [{ ...TUE_RUN, conclusion: "failure" }] })],
    ["0 4 * * 1", MON_FINAL, mondayCase({ runs: [{ conclusion: "cancelled" }] })]];
  for (const [cron, when, world] of cases) {
    const out = await invoke(cron, when, world);
    assert.ok(out.sent.length > 0, cron);
    const everything = JSON.stringify(out.sent) + out.logged.join("\n");
    for (const s of [...SENTINELS, "TOKEN-SECRET", INSTALL_TOKEN, "PRIVATE KEY"]) {
      assert.ok(!everything.includes(s), `${cron}: ${s}`);
    }
    for (const m of out.sent) {
      assert.deepEqual(m.assignees, ["archievalmariano"]);
      assert.match(m.subject, /^DOGEAR watchdog: [A-Z_]+ \d{4}-\d{2}-\d{2}$/);
      for (const line of m.text.split("\n").filter(Boolean)) {
        assert.ok(/^(Check|Target|Week|Code): [a-z0-9_ -]+$/i.test(line) || Object.values(MESSAGES).includes(line)
          || /^https:\/\/github\.com\/archievalmariano\/dogear\/actions\/runs\/\d+$/.test(line), line);
      }
    }
  }
  assert.throws(() => buildAlert({ check: "x", target: "staging", owner: "o", repo: "r" }, { code: "NOPE", week: "w", runIds: [] }));
});

// ---- alert delivery: the GitHub App issue path (DOGEAR-WATCHDOG-ALERTS-PLAN rev 2) ------------

const ALERT = buildAlert({ check: "daily", target: "production", owner: "archievalmariano", repo: "dogear" },
  { code: "SCHEDULE_DROPPED", week: "2026-10-19", runIds: [] });
const fromB64url = (part) => Buffer.from(part.replace(/-/g, "+").replace(/_/g, "/"), "base64");

test("the App JWT: RS256, iss = the App ID, iat 60 s back, exp 9 min ahead, and it verifies", async () => {
  const now = Date.parse("2026-10-20T05:00:00Z");
  const jwt = await appJwt("123456", PEM, now);
  const [h, p, sig] = jwt.split(".");
  assert.deepEqual(JSON.parse(fromB64url(h)), { alg: "RS256", typ: "JWT" });
  assert.deepEqual(JSON.parse(fromB64url(p)), { iat: now / 1000 - 60, exp: now / 1000 + 540, iss: 123456 });
  const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", KEYS.publicKey, fromB64url(sig), new TextEncoder().encode(`${h}.${p}`));
  assert.equal(ok, true);
  await assert.rejects(appJwt("123456", PEM.replace(/PRIVATE KEY/g, "RSA PRIVATE KEY"), now),
    (e) => e instanceof DeliveryError && /PKCS#8/.test(e.message));
  await assert.rejects(appJwt("abc", PEM, now), DeliveryError);
  await assert.rejects(appJwt("1", "not a key", now), DeliveryError);
});

test("delivery: the token is scoped to dogear-alerts issues; the issue is assigned to the owner", async () => {
  const calls = [];
  const sink = alertSink(async () => new Response("", { status: 404 }));
  const fetchImpl = async (url, init) => { calls.push({ url: String(url), init }); return sink.fetchImpl(url, init); };
  const issue = await sendAlert(APP_ENV, ALERT, { fetchImpl, nowMs: Date.now() });
  assert.deepEqual(calls.map((c) => [c.url, c.init.method]), [[EXCHANGE_URL, "POST"], [ISSUES_URL, "POST"]]);
  assert.deepEqual(JSON.parse(calls[0].init.body), { repositories: ["dogear-alerts"], permissions: { issues: "write" } });
  assert.deepEqual(JSON.parse(calls[1].init.body),
    { title: ALERT.subject, body: ALERT.text, assignees: [ALERT_ASSIGNEE] });
  assert.equal(ALERT_ASSIGNEE, "archievalmariano");
  assert.equal(calls[1].init.headers.Authorization, `Bearer ${INSTALL_TOKEN}`);
  assert.deepEqual(issue, { number: 1, url: "https://github.com/archievalmariano/dogear-alerts/issues/1",
    assigneeRetained: true });
});

test("delivery: a dropped or substituted assignee is ASSIGNEE_DROPPED, logged with the issue number only", async () => {
  for (const otherLogin of [null, "someone-else"]) {
    const sink = alertSink(async () => new Response("", { status: 404 }), { drop: () => true, otherLogin });
    const logged = [];
    const realLog = console.log;
    console.log = (line) => logged.push(String(line));
    try {
      await assert.rejects(sendAlert(APP_ENV, ALERT, { fetchImpl: sink.fetchImpl }),
        (e) => e instanceof DeliveryError && e.code === "ASSIGNEE_DROPPED");
    } finally {
      console.log = realLog;
    }
    assert.deepEqual(logged.map((l) => JSON.parse(l)), [{ level: "error", msg: "ASSIGNEE_DROPPED", issue: 1 }]);
    assert.equal(sink.issues.length, 1, "the issue exists, but it is not counted as delivered");
  }
});

test("delivery: a dropped assignee on one finding still delivers the others, then fails the invocation", async () => {
  // Daily with no run today and an old main: SCHEDULE_DROPPED and INACTIVITY_SOON, two findings.
  const world = fakeWorld({ promoteRuns: [], lastCommit: "2026-08-01T00:00:00Z", alerts: { drop: (n) => n === 1 } });
  const out = await invoke("0 5 * * *", TUE_DAILY, world);
  assert.equal(world.issues.length, 2, "both findings were sent");
  assert.deepEqual(world.issues.map((i) => i.assignees), [["archievalmariano"], ["archievalmariano"]]);
  assert.ok(out.error, "the invocation fails");
  const drops = out.logged.filter((l) => l.includes("ASSIGNEE_DROPPED")).map((l) => JSON.parse(l));
  assert.deepEqual(drops, [{ level: "error", msg: "ASSIGNEE_DROPPED", issue: 1 }]);
  assert.equal(out.logged.filter((l) => l.includes("alert not sent")).length, 1, "only the first is undelivered");
});

test("delivery: a refused or malformed creation is a delivery failure", async () => {
  const sinks = [alertSink(null, { issueStatus: 200 }), alertSink(null, { issueStatus: 422 }),
    alertSink(null, { exchangeStatus: 200 })];
  for (const sink of sinks) await assert.rejects(sendAlert(APP_ENV, ALERT, { fetchImpl: sink.fetchImpl }), DeliveryError);
  const notJson = async (url) => (String(url) === EXCHANGE_URL
    ? new Response(JSON.stringify({ token: INSTALL_TOKEN }), { status: 201 }) : new Response("<html>", { status: 201 }));
  await assert.rejects(sendAlert(APP_ENV, ALERT, { fetchImpl: notJson }), (e) => e.code === "ISSUE_REFUSED");
  for (const env of [{ ...APP_ENV, ALERT_REPO: "dogear" }, { ...APP_ENV, ALERT_APP_INSTALLATION_ID: "1/../2" },
    { ...APP_ENV, ALERT_APP_ID: "" }]) {
    let called = false;
    await assert.rejects(sendAlert(env, ALERT, { fetchImpl: async () => { called = true; } }), (e) => e.code === "CONFIG");
    assert.equal(called, false, "nothing is sent with a bad configuration");
  }
});

test("delivery: a stalled token or issue response fails within the deadline", { timeout: 5000 }, async () => {
  for (const which of [EXCHANGE_URL, ISSUES_URL]) {
    const fetchImpl = async (url) => (String(url) === which ? stalled("{").response()
      : new Response(JSON.stringify({ token: INSTALL_TOKEN }), { status: 201 }));
    const { error, elapsed } = await within(50, createAlertIssue(APP_ENV, ALERT, { fetchImpl, timeoutMs: 50 }));
    assert.ok(error instanceof CannotRead, `${which}: ${error}`);
    assert.ok(elapsed < 2000);
  }
});

test("the one-time alert test tool: one assigned issue through notify.js, and only the URL and verdict printed", async () => {
  const run = async (env, alerts) => {
    const sink = alertSink(async (url) => { throw new Error(`unexpected ${url}`); }, alerts);
    const calls = [], printed = [];
    const code = await alertTestMain({ env, fetchImpl: async (u, i) => { calls.push(String(u)); return sink.fetchImpl(u, i); },
      now: new Date("2026-10-20T05:00:00Z"), print: (l) => printed.push(l) });
    return { code, printed, calls, issues: sink.issues };
  };
  const ok = await run(APP_ENV);
  assert.equal(ok.code, 0);
  assert.deepEqual(ok.printed, ["issue: https://github.com/archievalmariano/dogear-alerts/issues/1", "assignee retained: yes"]);
  assert.deepEqual(ok.calls, [EXCHANGE_URL, ISSUES_URL]); // nothing of dogear is read
  assert.equal(ok.issues.length, 1);
  assert.equal(ok.issues[0].title, "DOGEAR watchdog: WATCHDOG_TEST 2026-10-20");
  assert.deepEqual(ok.issues[0].assignees, ["archievalmariano"]);
  assert.ok(ok.issues[0].body.includes(MESSAGES.WATCHDOG_TEST));
  const dropped = await run(APP_ENV, { drop: () => true });
  assert.equal(dropped.code, 1);
  assert.deepEqual(dropped.printed, ["issue: https://github.com/archievalmariano/dogear-alerts/issues/1", "assignee retained: no"]);
  const refused = await run(APP_ENV, { issueStatus: 403 });
  assert.equal(refused.code, 1);
  assert.deepEqual(refused.printed, ["not created: ISSUE_REFUSED"]);
  for (const name of ["ALERT_APP_ID", "ALERT_APP_INSTALLATION_ID", "ALERT_APP_PRIVATE_KEY"]) {
    const missing = await run({ ...APP_ENV, [name]: "" });
    assert.equal(missing.code, 2, name);
    assert.deepEqual(missing.calls, [], `${name}: nothing sent`);
  }
  for (const out of [ok, dropped, refused]) {
    const text = out.printed.join("\n");
    for (const secret of ["PRIVATE KEY", INSTALL_TOKEN, "eyJ", "123456", "987654"]) assert.ok(!text.includes(secret), secret);
  }
});

test("the HTTP entry point only reports liveness", async () => {
  let called = false;
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => { called = true; return new Response(""); };
  try {
    const res = await worker.fetch(new Request("https://x/check", { headers: { a: "b" } }), {});
    assert.equal(res.status, 200);
    assert.match(await res.text(), /watch-only/);
  } finally {
    globalThis.fetch = realFetch;
  }
  assert.equal(called, false);
});
