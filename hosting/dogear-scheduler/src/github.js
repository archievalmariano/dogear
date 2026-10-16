// The watchdog's only way to GitHub: read-only. Every request is a plain GET (no
// request method is ever set), to one of five allowlisted path shapes on the
// configured repository. The token travels only in the Authorization header to
// api.github.com, and is never sent to the job-log redirect target.

export class CannotRead extends Error {}

// Upper bounds on what is read: GitHub's JSON lists and a publish job's log are far
// smaller; anything larger is unreadable, not read to the end.
export const MAX_JSON_BYTES = 2 * 1024 * 1024;
export const MAX_LOG_BYTES = 8 * 1024 * 1024;

// One fetch, its whole body included, under one deadline. The deadline runs until the
// last byte is read (not only until the headers arrive), and a body over `maxBytes`
// is refused. Throws CannotRead on a timeout, a stalled or oversized body, or any
// transport error; otherwise { status, location, bytes }.
export async function fetchBounded(fetchImpl, url, headers, maxBytes, timeoutMs) {
  const controller = new AbortController();
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new CannotRead("timed out"));
    }, timeoutMs);
  });
  let reader = null;
  const work = (async () => {
    const res = await fetchImpl(url, { headers, redirect: "manual", signal: controller.signal });
    const chunks = [];
    let size = 0;
    if (res.body) {
      reader = res.body.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > maxBytes) throw new CannotRead("response too large");
        chunks.push(value);
      }
    }
    const bytes = new Uint8Array(size);
    let at = 0;
    for (const c of chunks) {
      bytes.set(c, at);
      at += c.byteLength;
    }
    return { status: res.status, location: res.headers.get("location"), bytes };
  })();
  try {
    return await Promise.race([work, deadline]);
  } catch (err) {
    throw err instanceof CannotRead ? err : new CannotRead(`request failed (${err && err.name ? err.name : "error"})`);
  } finally {
    clearTimeout(timer);
    work.catch(() => {}); // the race is decided; a late rejection is not an error
    if (reader) reader.cancel().catch(() => {});
  }
}

const decode = (bytes) => new TextDecoder("utf-8").decode(bytes);

const API = "https://api.github.com";
const WORKFLOWS = "(dogear-stage|dogear-promote)\\.yml";
// Paths relative to /repos/<owner>/<repo>/ (PLAN §4: the five endpoints).
const ALLOWED = [
  new RegExp(`^actions/workflows/${WORKFLOWS}$`),
  new RegExp(`^actions/workflows/${WORKFLOWS}/runs\\?[a-z_]+=[A-Za-z0-9_:>=%.-]+(&[a-z_]+=[A-Za-z0-9_:>=%.-]+)*$`),
  /^actions\/runs\/[0-9]+\/jobs\?per_page=[0-9]+$/,
  /^actions\/jobs\/[0-9]+\/logs$/,
  /^commits\?sha=main&per_page=1$/,
];

export function allowedPath(path) {
  return typeof path === "string" && ALLOWED.some((re) => re.test(path));
}

export function createClient({ owner, repo, token, fetchImpl = fetch, timeoutMs = 10000 }) {
  if (!/^[A-Za-z0-9-]+$/.test(owner || "") || !/^[A-Za-z0-9._-]+$/.test(repo || "")) {
    throw new Error("dogear-scheduler: GITHUB_OWNER/GITHUB_REPO missing or malformed");
  }
  const base = `${API}/repos/${owner}/${repo}/`;

  const request = (url, headers, maxBytes) => fetchBounded(fetchImpl, url, headers, maxBytes, timeoutMs);

  function apiHeaders() {
    const h = { Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "dogear-scheduler" };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }

  async function getJson(path) {
    if (!allowedPath(path)) throw new Error(`dogear-scheduler: path not allowed: ${path}`);
    const res = await request(base + path, apiHeaders(), MAX_JSON_BYTES);
    if (res.status !== 200) throw new CannotRead(`GitHub answered ${res.status}`);
    try {
      return JSON.parse(decode(res.bytes));
    } catch {
      throw new CannotRead("GitHub answered malformed JSON");
    }
  }

  // The publish job's public result status for `op` (e.g. "staged"), and the target
  // its wrapper line names; only these two tokens leave this function.
  async function jobResult(jobId, op) {
    const path = `actions/jobs/${jobId}/logs`;
    if (!allowedPath(path)) throw new Error(`dogear-scheduler: path not allowed: ${path}`);
    let res = await request(base + path, apiHeaders(), MAX_JSON_BYTES);
    if ([301, 302, 303, 307, 308].includes(res.status)) {
      let location;
      try {
        location = new URL(res.location);
      } catch {
        throw new CannotRead("job log redirect without a usable location");
      }
      if (location.protocol !== "https:") throw new CannotRead("job log redirect is not https");
      res = await request(location.toString(), {}, MAX_LOG_BYTES); // a fresh GET: no Authorization, no token
    }
    if (res.status !== 200) throw new CannotRead(`job log answered ${res.status}`);
    return parseResult(decode(res.bytes), op);
  }

  return { getJson, jobResult };
}

// The publisher's own result line, anchored on tools/run_publish.py's wrapper line
// ("running: dogear publish <op> --target <t>"): the job log also holds the test
// suite's output, whose lines look alike. Null unless exactly one wrapper line is
// followed by a result line for the same operation.
export function parseResult(text, op) {
  const lines = String(text).split(/\r?\n/).map((l) => l.replace(/^\S+Z /, ""));
  const wrapper = new RegExp(`^running: dogear publish ${op} --target (staging|production)$`);
  const starts = lines.flatMap((l, i) => (wrapper.test(l) ? [i] : []));
  if (starts.length !== 1) return null;
  const target = lines[starts[0]].match(wrapper)[1];
  const result = new RegExp(`^([a-z][a-z ]*): ${op}(\\s|$)`);
  for (const line of lines.slice(starts[0] + 1)) {
    const m = line.match(result);
    if (m) return { status: m[1], target };
  }
  return null;
}

// PLAN §2.0: only completed + success is healthy. "stop" is the emergency stop: the
// caller job (the one that calls the reusable publisher) was skipped, so none of its
// jobs ran. Every other completed conclusion, known or not, is "failed".
export function runState(run, jobs, caller) {
  if (run.status !== "completed") return "pending";
  const ran = jobs.some((j) => j.name === `${caller} / publish` || j.name === `${caller} / route`);
  const callerSkipped = jobs.some((j) => j.name === caller && j.conclusion === "skipped");
  if (!ran && (callerSkipped || run.conclusion === "skipped")) return "stop";
  return run.conclusion === "success" ? "healthy" : "failed";
}

// PLAN §2.0a: the editorial gate runs only for production.
export function runTarget(jobs, caller) {
  const gate = jobs.find((j) => j.name === `${caller} / editorial-gate`);
  if (!gate) return "unknown";
  if (gate.status === "completed" && gate.conclusion === "skipped") return "staging";
  return "production";
}
