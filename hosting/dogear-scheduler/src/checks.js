// The watchdog's decisions (PLAN §2). Pure: every input is fetched elsewhere and
// passed in, so each table row is a test. A finding is { code, week, runIds }:
// numeric run ids only, never names, titles or log text.

import { runState, runTarget } from "./github.js";
import { currentMonday, dayStart, fridayStart, mondayRollover, nextMonday, DAY_MS } from "./time.js";

const finding = (code, week, runIds = []) => ({ code, week, runIds });
const newestFirst = (runs) => [...runs].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));

// Split runs by target (PLAN §2.0a). Wrong-target runs are reported and never used
// as evidence. Not-completed runs whose target is not known yet count as the
// watched target's (pending); completed ones of unknown target are evidence only
// as an emergency stop, otherwise unreadable.
function byTarget(runs, jobsById, caller, target, week) {
  const evidence = [], wrong = [], findings = [];
  for (const run of newestFirst(runs)) {
    const jobs = jobsById[run.id] || [];
    const t = runTarget(jobs, caller);
    if (t === "unknown") {
      if (run.status !== "completed" || runState(run, jobs, caller) === "stop") evidence.push(run);
      else findings.push(finding("WATCHDOG_CANNOT_READ", week, [run.id]));
    } else if (t === target) {
      evidence.push(run);
    } else {
      wrong.push(run.id);
    }
  }
  if (wrong.length) findings.push(finding("WRONG_TARGET", week, wrong));
  return { evidence, findings };
}

const job = (jobs, name) => jobs.find((j) => j.name === name);
const succeeded = (j) => !!j && j.status === "completed" && j.conclusion === "success";

// PLAN §2.1. `results`: { [publishJobId]: {status, target} | null } for the publish
// jobs of completed, green runs (null: the log or its result line could not be read).
export function friday({ runs, jobsById, results, now, final, target }) {
  const week = nextMonday(now);
  const since = fridayStart(now).getTime();
  const scheduled = runs.filter((r) => r.event === "schedule" && r.head_branch === "main"
    && Date.parse(r.created_at) >= since && Date.parse(r.created_at) <= now.getTime());
  const { evidence, findings } = byTarget(scheduled, jobsById, "stage", target, week);
  const run = evidence[0];
  if (!run) return final ? [...findings, finding("STAGE_RUN_MISSING", week)] : findings;
  const jobs = jobsById[run.id] || [];
  const state = runState(run, jobs, "stage");
  if (state === "pending") return final ? [...findings, finding("STAGE_RUN_PENDING", week, [run.id])] : findings;
  if (state === "stop") return [...findings, finding("EMERGENCY_STOP", week, [run.id])];
  if (state === "failed") return [...findings, finding("STAGE_RUN_FAILED", week, [run.id])];
  const publish = job(jobs, "stage / publish");
  if (run.run_attempt !== 1 || !succeeded(job(jobs, "stage / route")) || !succeeded(publish)
      || (target === "production" && !succeeded(job(jobs, "stage / editorial-gate")))) {
    return [...findings, finding("STAGE_NOT_UNATTENDED", week, [run.id])];
  }
  const result = results[publish.id];
  if (!result || result.target !== target) return [...findings, finding("WATCHDOG_CANNOT_READ", week, [run.id])];
  if (result.status === "noop") return [...findings, finding("STAGE_NOOP", week, [run.id])];
  if (result.status !== "staged") return [...findings, finding("WATCHDOG_CANNOT_READ", week, [run.id])];
  return findings;
}

// PLAN §2.2. `served`: { readable, week, issueServed } from served.js.
export function monday({ runs, jobsById, served, now, final, target }) {
  const week = currentMonday(now);
  const since = mondayRollover(now).getTime();
  const promotes = runs.filter((r) => (r.event === "schedule" || r.event === "workflow_dispatch")
    && r.head_branch === "main" && Date.parse(r.created_at) >= since && Date.parse(r.created_at) <= now.getTime());
  const { evidence, findings } = byTarget(promotes, jobsById, "promote", target, week);
  if (!served.readable) return [...findings, finding("HOST_UNREADABLE", week)];
  const run = evidence[0];
  const state = run ? runState(run, jobsById[run.id] || [], "promote") : "missing";
  const ids = run ? [run.id] : [];
  const later = (code) => (final ? [...findings, finding(code, week, ids)] : findings);
  if (served.week === week) {
    if (!served.issueServed) return [...findings, finding("ISSUE_UNSERVED", week, ids)];
    if (state === "stop") return [...findings, finding("EMERGENCY_STOP", week, ids)];
    if (state === "failed") return [...findings, finding("MONDAY_UNVERIFIED", week, ids)];
    if (state === "missing") return later("MONDAY_UNVERIFIED");
    if (state === "pending") return later("MONDAY_NOT_PUBLISHED");
    return findings;
  }
  if (state === "stop") return [...findings, finding("EMERGENCY_STOP", week, ids)];
  if (state === "failed") return [...findings, finding("MONDAY_HELD", week, ids)];
  if (state === "healthy") return [...findings, finding("MONDAY_NOT_PUBLISHED", week, ids)];
  return later("MONDAY_NOT_PUBLISHED");
}

// PLAN §2.3, at 16:00 Manila: the deadline for today's run.
export function daily({ workflows, runs, jobsById, lastCommitDate, now, target }) {
  const week = currentMonday(now);
  const out = [];
  const disabled = ["dogear-stage.yml", "dogear-promote.yml"].filter((w) => !workflows[w] || workflows[w] !== "active");
  if (disabled.length) out.push(finding("WORKFLOW_DISABLED", week));
  const since = dayStart(now).getTime();
  const today = runs.filter((r) => r.event === "schedule" && r.head_branch === "main"
    && Date.parse(r.created_at) >= since && Date.parse(r.created_at) <= now.getTime());
  const { evidence, findings } = byTarget(today, jobsById, "promote", target, week);
  out.push(...findings);
  const run = evidence[0];
  if (!run) {
    if (!disabled.length) out.push(finding("SCHEDULE_DROPPED", week));
  } else {
    const state = runState(run, jobsById[run.id] || [], "promote");
    if (state === "stop") out.push(finding("EMERGENCY_STOP", week, [run.id]));
    else if (state === "failed") out.push(finding("PROMOTE_RUN_FAILED", week, [run.id]));
    else if (state === "pending") out.push(finding("PROMOTE_RUN_PENDING", week, [run.id]));
  }
  const last = Date.parse(lastCommitDate);
  if (!Number.isFinite(last)) out.push(finding("WATCHDOG_CANNOT_READ", week));
  else if (now.getTime() - last > 50 * DAY_MS) out.push(finding("INACTIVITY_SOON", week));
  return out;
}

