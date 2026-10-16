// What devices fetch, read as a device would: the public manifest and the issue it
// names, by plain GET with no credentials. The host is fixed per target here, never
// taken from configuration. Each read, body included, is bounded in time and size.
import { CannotRead, fetchBounded } from "./github.js";

export const HOSTS = Object.freeze({
  production: "https://dogear.archievalmariano.com",
  staging: "https://dogear-staging.pages.dev",
});

const ISSUE_PATH = /^issues\/dogear-(\d{4}-\d{2}-\d{2})\.([0-9a-f]{16})\.json$/;
const MAX_ISSUE_BYTES = 32 * 1024;
const MAX_MANIFEST_BYTES = 64 * 1024;

async function getBytes(url, fetchImpl, timeoutMs, maxBytes) {
  try {
    const res = await fetchBounded(fetchImpl, url, {}, maxBytes, timeoutMs);
    return res.status === 200 ? { status: 200, bytes: res.bytes } : { status: res.status };
  } catch (err) {
    if (!(err instanceof CannotRead)) throw err;
    return { status: 0 }; // timed out, stalled, oversized or unreachable: not served
  }
}

export async function sha256Hex(bytes) {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return [...digest].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// { readable, week, issueServed } for the target's host. readable is false when the
// manifest cannot be fetched or is not a valid manifest (its issue path must match
// its own week and hash, as the registry writes it).
export async function readServed(target, { fetchImpl = fetch, timeoutMs = 10000 } = {}) {
  const host = HOSTS[target];
  if (!host) throw new Error(`dogear-scheduler: unknown target ${target}`);
  const manifest = await getBytes(`${host}/current.json`, fetchImpl, timeoutMs, MAX_MANIFEST_BYTES);
  if (manifest.status !== 200) return { readable: false };
  let m;
  try {
    m = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(manifest.bytes));
  } catch {
    return { readable: false };
  }
  const week = m && m.week && m.week.start;
  const path = m && typeof m.issuePath === "string" ? m.issuePath.match(ISSUE_PATH) : null;
  if (typeof week !== "string" || typeof m.issueSha256 !== "string" || !/^[0-9a-f]{64}$/.test(m.issueSha256)
      || !path || path[1] !== week || path[2] !== m.issueSha256.slice(0, 16)) {
    return { readable: false };
  }
  const issue = await getBytes(`${host}/${m.issuePath}`, fetchImpl, timeoutMs, MAX_ISSUE_BYTES);
  const issueServed = issue.status === 200 && (await sha256Hex(issue.bytes)) === m.issueSha256;
  return { readable: true, week, issueServed };
}
