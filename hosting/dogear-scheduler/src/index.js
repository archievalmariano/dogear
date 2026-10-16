// dogear-scheduler: DOGEAR's read-only watchdog (review-by-exception plan §6;
// DOGEAR-WATCHDOG-PLAN). It never starts, stages, promotes, publishes, holds or
// resumes anything: it reads GitHub (GET only, read-only token) and the public
// host, decides (checks.js) and emails an alert. NOT DEPLOYED; inert until
// WATCH_ENABLED is "true".
import { CannotRead, createClient } from "./github.js";
import { readServed } from "./served.js";
import { daily, friday, monday } from "./checks.js";
import { buildAlert, sendAlert } from "./alert.js";
import { currentMonday, dayStart, fridayStart, mondayRollover } from "./time.js";

// Cron (UTC) -> check. Manila = UTC+8.
export const CHECKS = Object.freeze({
  "0 6 * * 5": { check: "friday", final: false }, // Fri 14:00 Manila: first look
  "0 12 * * 5": { check: "friday", final: true }, // Fri 20:00 Manila: final
  "0 1 * * 1": { check: "monday", final: false }, // Mon 09:00 Manila: first look
  "0 4 * * 1": { check: "monday", final: true }, // Mon 12:00 Manila: final
  "0 5 * * *": { check: "daily", final: true }, // daily 13:00 Manila: today's run, deadline
});
export const DECLARED_CRONS = Object.freeze(Object.keys(CHECKS));
const TARGETS = ["staging", "production"];

const log = (fields) => console.log(JSON.stringify(fields));
const iso = (d) => d.toISOString().replace(/\.\d{3}Z$/, "Z");
// Only these fields of a run or job are kept: no titles, messages or branch names
// beyond the one compared with "main".
const pickRun = (r) => ({ id: r.id, event: r.event, head_branch: r.head_branch, status: r.status,
  conclusion: r.conclusion, run_attempt: r.run_attempt, created_at: r.created_at });
const pickJob = (j) => ({ id: j.id, name: j.name, status: j.status, conclusion: j.conclusion });

async function runsSince(client, workflow, since, event) {
  const query = [event ? `event=${event}` : null, "branch=main", `created=${encodeURIComponent(`>=${iso(since)}`)}`,
    "per_page=30"].filter(Boolean).join("&");
  const body = await client.getJson(`actions/workflows/${workflow}/runs?${query}`);
  if (!body || !Array.isArray(body.workflow_runs)) throw new CannotRead("malformed run list");
  const runs = body.workflow_runs.map(pickRun);
  const jobsById = {};
  for (const run of runs) {
    const jobs = await client.getJson(`actions/runs/${run.id}/jobs?per_page=50`);
    if (!jobs || !Array.isArray(jobs.jobs)) throw new CannotRead("malformed job list");
    jobsById[run.id] = jobs.jobs.map(pickJob);
  }
  return { runs, jobsById };
}

export async function runCheck({ check, final }, client, now, target, fetchImpl = fetch) {
  if (check === "friday") {
    const { runs, jobsById } = await runsSince(client, "dogear-stage.yml", fridayStart(now), "schedule");
    const results = {};
    for (const run of runs) {
      if (run.status !== "completed" || run.conclusion !== "success") continue;
      const publish = jobsById[run.id].find((j) => j.name === "stage / publish");
      if (!publish) continue;
      try {
        results[publish.id] = await client.jobResult(publish.id, "stage-next");
      } catch (err) {
        if (!(err instanceof CannotRead)) throw err;
        results[publish.id] = null;
      }
    }
    return friday({ runs, jobsById, results, now, final, target });
  }
  if (check === "monday") {
    const { runs, jobsById } = await runsSince(client, "dogear-promote.yml", mondayRollover(now), null);
    const served = await readServed(target, { fetchImpl });
    return monday({ runs, jobsById, served, now, final, target });
  }
  const workflows = {};
  for (const wf of ["dogear-stage.yml", "dogear-promote.yml"]) {
    const body = await client.getJson(`actions/workflows/${wf}`);
    workflows[wf] = body && typeof body.state === "string" ? body.state : "unknown";
  }
  const { runs, jobsById } = await runsSince(client, "dogear-promote.yml", dayStart(now), "schedule");
  const commits = await client.getJson("commits?sha=main&per_page=1");
  const lastCommitDate = Array.isArray(commits) && commits[0] && commits[0].commit && commits[0].commit.committer
    ? commits[0].commit.committer.date : null;
  return daily({ workflows, runs, jobsById, lastCommitDate, now, target });
}

export default {
  async scheduled(event, env) {
    const plan = Object.hasOwn(CHECKS, event.cron) ? CHECKS[event.cron] : null;
    if (!plan) {
      log({ level: "error", msg: "unmapped cron; nothing checked", cron: event.cron });
      return;
    }
    if (env.WATCH_ENABLED !== "true") {
      log({ level: "info", msg: "watchdog not enabled; nothing checked", cron: event.cron });
      return;
    }
    const target = env.WATCH_TARGET;
    if (!TARGETS.includes(target)) throw new Error("dogear-scheduler: WATCH_TARGET must be staging or production");
    const now = new Date(event.scheduledTime);
    const context = { check: plan.check, target, owner: env.GITHUB_OWNER, repo: env.GITHUB_REPO };
    let findings, failure = null;
    try {
      if (!env.GITHUB_READ_TOKEN) throw new CannotRead("GITHUB_READ_TOKEN is not set");
      const client = createClient({ owner: env.GITHUB_OWNER, repo: env.GITHUB_REPO, token: env.GITHUB_READ_TOKEN });
      findings = await runCheck(plan, client, now, target);
    } catch (err) {
      if (!(err instanceof CannotRead)) failure = err;
      findings = [{ code: failure ? "WATCHDOG_ERROR" : "WATCHDOG_CANNOT_READ", week: currentMonday(now), runIds: [] }];
    }
    log({ level: findings.length ? "warn" : "info", msg: "check done", check: plan.check, final: plan.final, target,
      findings: findings.map((f) => ({ code: f.code, week: f.week, runIds: f.runIds })) });
    let sendError = null;
    for (const f of findings) {
      try {
        await sendAlert(env, buildAlert(context, f));
      } catch (err) {
        sendError = sendError || err;
        log({ level: "error", msg: "alert not sent", code: f.code });
      }
    }
    if (failure) throw failure;
    if (sendError) throw new Error("dogear-scheduler: an alert could not be sent");
  },

  // The HTTP entry point only reports liveness: no check runs and nothing is sent from it.
  async fetch() {
    return new Response("dogear-scheduler: alive (watch-only). Checks run from Cron Triggers only.\n",
      { headers: { "content-type": "text/plain; charset=utf-8" } });
  },
};
