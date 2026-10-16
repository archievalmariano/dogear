// Alert delivery (DOGEAR-WATCHDOG-ALERTS-PLAN rev 2): a GitHub App opens one issue per
// finding in the private repository archievalmariano/dogear-alerts, assigned to the
// owner. This is the only module that sends anything but a GET, and only to two
// endpoints. Its credential is the App's key, separate from the detection token: the
// App is installed only on dogear-alerts and has Issues read/write there, nothing else.
//
//   1. sign an RS256 App JWT (PKCS#8 key)        -> sent only to the token exchange
//   2. POST /app/installations/{id}/access_tokens, scoped to dogear-alerts, issues: write
//   3. POST /repos/archievalmariano/dogear-alerts/issues -> the installation token only here

import { fetchBounded } from "./github.js";

export const ALERT_OWNER = "archievalmariano";
export const ALERT_REPO = "dogear-alerts";
export const ALERT_ASSIGNEE = "archievalmariano";
const API = "https://api.github.com";
const MAX_RESPONSE_BYTES = 1024 * 1024;

export class DeliveryError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

const b64url = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes)))
  .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const utf8 = (text) => new TextEncoder().encode(text);

// The App's JWT: RS256 over a PKCS#8 key, issued 60 s in the past (clock drift), valid 9 min.
export async function appJwt(appId, pem, nowMs) {
  if (!/^[0-9]+$/.test(String(appId || ""))) throw new DeliveryError("CONFIG", "ALERT_APP_ID must be the numeric App ID");
  const text = String(pem || "");
  if (text.includes("BEGIN RSA PRIVATE KEY")) {
    throw new DeliveryError("CONFIG", "the App key must be PKCS#8 (BEGIN PRIVATE KEY): "
      + "convert it once with openssl pkcs8 -topk8 -nocrypt");
  }
  const m = text.match(/-----BEGIN PRIVATE KEY-----([A-Za-z0-9+/=\s]+)-----END PRIVATE KEY-----/);
  if (!m) throw new DeliveryError("CONFIG", "ALERT_APP_PRIVATE_KEY is not a PKCS#8 PEM key");
  let key;
  try {
    const der = Uint8Array.from(atob(m[1].replace(/\s+/g, "")), (c) => c.charCodeAt(0));
    key = await crypto.subtle.importKey("pkcs8", der, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"]);
  } catch {
    throw new DeliveryError("CONFIG", "ALERT_APP_PRIVATE_KEY could not be read as an RSA key");
  }
  const now = Math.floor(nowMs / 1000);
  const signing = `${b64url(utf8(JSON.stringify({ alg: "RS256", typ: "JWT" })))}.`
    + b64url(utf8(JSON.stringify({ iat: now - 60, exp: now + 540, iss: Number(appId) })));
  const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, utf8(signing));
  return `${signing}.${b64url(signature)}`;
}

const headersWith = (bearer) => ({ Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
  "User-Agent": "dogear-scheduler", "Content-Type": "application/json", Authorization: `Bearer ${bearer}` });

async function send(fetchImpl, url, bearer, payload, timeoutMs) {
  const res = await fetchBounded(fetchImpl, url, headersWith(bearer), MAX_RESPONSE_BYTES, timeoutMs,
    { method: "POST", body: JSON.stringify(payload) });
  let json = null;
  try {
    json = JSON.parse(new TextDecoder("utf-8").decode(res.bytes));
  } catch { /* judged by the caller */ }
  return { status: res.status, json };
}

// Creates one alert issue. Returns { number, url, assigneeRetained }; throws DeliveryError
// (or CannotRead, from the deadline) when the issue could not be created at all.
export async function createAlertIssue(env, alert, { fetchImpl = fetch, nowMs = Date.now(), timeoutMs = 10000 } = {}) {
  const { ALERT_APP_ID: appId, ALERT_APP_INSTALLATION_ID: installation, ALERT_APP_PRIVATE_KEY: pem } = env;
  if (!appId || !installation || !pem) {
    throw new DeliveryError("CONFIG", "ALERT_APP_ID, ALERT_APP_INSTALLATION_ID and ALERT_APP_PRIVATE_KEY are required");
  }
  if (!/^[0-9]+$/.test(String(installation))) throw new DeliveryError("CONFIG", "ALERT_APP_INSTALLATION_ID must be numeric");
  if (env.ALERT_REPO !== ALERT_REPO) throw new DeliveryError("CONFIG", `ALERT_REPO must be ${ALERT_REPO}`);

  const jwt = await appJwt(appId, pem, nowMs);
  const exchange = await send(fetchImpl, `${API}/app/installations/${installation}/access_tokens`, jwt,
    { repositories: [ALERT_REPO], permissions: { issues: "write" } }, timeoutMs);
  const token = exchange.json && exchange.json.token;
  if (exchange.status !== 201 || typeof token !== "string" || !token) {
    throw new DeliveryError("TOKEN_REFUSED", `the token exchange answered ${exchange.status}`);
  }

  const created = await send(fetchImpl, `${API}/repos/${ALERT_OWNER}/${ALERT_REPO}/issues`, token,
    { title: alert.subject, body: alert.text, assignees: [ALERT_ASSIGNEE] }, timeoutMs);
  const issue = created.json;
  if (created.status !== 201 || !issue || !Number.isSafeInteger(issue.number)) {
    throw new DeliveryError("ISSUE_REFUSED", `issue creation answered ${created.status}`);
  }
  const retained = Array.isArray(issue.assignees) && issue.assignees.some((a) => a && a.login === ALERT_ASSIGNEE);
  return { number: issue.number, url: `https://github.com/${ALERT_OWNER}/${ALERT_REPO}/issues/${issue.number}`,
    assigneeRetained: retained };
}
