// Public-safe alerts (PLAN §3): a fixed sentence per code, the week, the watched
// target and links built here from numeric run ids. Nothing GitHub returns as text
// (titles, commit messages, branch names, log lines) is ever put in an alert.
// Delivered as one issue per finding in the private dogear-alerts repository, assigned
// to the owner (notify.js; DOGEAR-WATCHDOG-ALERTS-PLAN rev 2).
import { DeliveryError, createAlertIssue } from "./notify.js";

export const MESSAGES = Object.freeze({
  STAGE_RUN_MISSING: "No scheduled Friday stage run started. If you already re-staged manually, ignore this.",
  STAGE_RUN_PENDING: "The scheduled Friday stage run has not finished.",
  STAGE_RUN_FAILED: "The scheduled Friday stage run did not succeed: there is no usable proof for next week.",
  STAGE_NOT_UNATTENDED: "The Friday stage run was not the unattended first attempt, or its publish job did not run.",
  STAGE_NOOP: "The Friday stage run found next week already published; it staged no proof.",
  WRONG_TARGET: "A publication run went to a different target than the one watched; it is not counted.",
  EMERGENCY_STOP: "Scheduled publication is skipped: the emergency stop (PUBLICATION_ENABLED) is active.",
  ISSUE_UNSERVED: "The manifest names this week, but its issue is not served with the recorded hash.",
  MONDAY_UNVERIFIED: "The manifest names this week, but no successful promote run confirms it.",
  MONDAY_NOT_PUBLISHED: "This week is not published yet.",
  MONDAY_HELD: "This week is held: the promote run did not succeed and the previous issue stays current.",
  HOST_UNREADABLE: "The public manifest could not be read or is not valid.",
  WORKFLOW_DISABLED: "The stage or promote workflow is disabled (manually, or by GitHub after inactivity).",
  SCHEDULE_DROPPED: "No scheduled promote run started today.",
  PROMOTE_RUN_FAILED: "Today's scheduled promote run did not succeed.",
  PROMOTE_RUN_PENDING: "Today's scheduled promote run has not finished.",
  INACTIVITY_SOON: "The repository has had no commit on main for over 50 days; GitHub will disable the schedules at 60.",
  WATCHDOG_CANNOT_READ: "The watchdog could not read GitHub or a run's result, so it cannot confirm this check.",
  WATCHDOG_ERROR: "The watchdog failed while running this check.",
  WATCHDOG_TEST: "One-time test of the DOGEAR watchdog alert channel. Confirm it arrived, then close this issue.",
});

export function buildAlert({ check, target, owner, repo }, { code, week, runIds }) {
  if (!Object.hasOwn(MESSAGES, code)) throw new Error(`dogear-scheduler: unknown alert code ${code}`);
  const links = runIds.filter((id) => Number.isSafeInteger(id))
    .map((id) => `https://github.com/${owner}/${repo}/actions/runs/${id}`);
  const lines = [`Check: ${check}`, `Target: ${target}`, `Week: ${week}`, `Code: ${code}`, "", MESSAGES[code],
    ...(links.length ? ["", ...links] : [])];
  return { subject: `DOGEAR watchdog: ${code} ${week}`, text: lines.join("\n") + "\n" };
}

// Delivered only if the issue was created AND the owner is still assigned: GitHub drops
// an assignee it cannot assign without an error, and the assignment is what makes the
// alert a participating (pushed) notification. A dropped assignee is a delivery failure.
export async function sendAlert(env, alert, options) {
  const issue = await createAlertIssue(env, alert, options);
  if (!issue.assigneeRetained) {
    console.log(JSON.stringify({ level: "error", msg: "ASSIGNEE_DROPPED", issue: issue.number }));
    throw new DeliveryError("ASSIGNEE_DROPPED", `issue ${issue.number}: the assignee was not retained`);
  }
  return issue;
}
