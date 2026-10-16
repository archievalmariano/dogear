#!/usr/bin/env node
// One-time test of the DOGEAR watchdog alert channel (DOGEAR-WATCHDOG-ALERTS-PLAN rev 2,
// §4 step 6), run by the owner before the watchdog is enabled. It opens ONE issue in the
// private archievalmariano/dogear-alerts repository through the watchdog's own delivery
// code (notify.js: the App JWT, the scoped token, the issue assigned to the owner), and
// prints only the issue URL and whether GitHub kept the assignee: never the key, a JWT,
// a token or a response body. It reads nothing from dogear.
//
//   ALERT_APP_ID=... ALERT_APP_INSTALLATION_ID=... ALERT_APP_PRIVATE_KEY="$(cat app-pkcs8.pem)" \
//     node tools/watchdog_alert_test.mjs
//
// Pass: "assignee retained: yes" AND the owner confirms the issue arrived on each intended
// channel (email and/or GitHub Mobile push). Then close the test issue.
// Exit: 0 retained, 1 dropped or not created, 2 a missing value.

import { pathToFileURL } from "node:url";
import { buildAlert } from "../hosting/dogear-scheduler/src/alert.js";
import { ALERT_REPO, createAlertIssue } from "../hosting/dogear-scheduler/src/notify.js";
import { manilaDate } from "../hosting/dogear-scheduler/src/time.js";

const REQUIRED = ["ALERT_APP_ID", "ALERT_APP_INSTALLATION_ID", "ALERT_APP_PRIVATE_KEY"];

export async function main({ env = process.env, fetchImpl = fetch, now = new Date(), print = console.log } = {}) {
  const missing = REQUIRED.filter((name) => !env[name]);
  if (missing.length) {
    print(`refused: set ${missing.join(", ")} in this terminal's environment (nothing was sent)`);
    return 2;
  }
  const alert = buildAlert({ check: "test", target: "none", owner: "archievalmariano", repo: "dogear" },
    { code: "WATCHDOG_TEST", week: manilaDate(now), runIds: [] });
  try {
    const issue = await createAlertIssue({ ...pick(env), ALERT_REPO }, alert, { fetchImpl, nowMs: now.getTime() });
    print(`issue: ${issue.url}`);
    print(`assignee retained: ${issue.assigneeRetained ? "yes" : "no"}`);
    return issue.assigneeRetained ? 0 : 1;
  } catch (err) {
    print(`not created: ${err && err.code ? err.code : "error"}`);
    return 1;
  }
}

const pick = (env) => Object.fromEntries(REQUIRED.map((name) => [name, env[name]]));

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await main();
}
