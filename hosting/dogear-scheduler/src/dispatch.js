// Pure logic for the dogear-scheduler Worker (no runtime deps; `node --test`).
// Modelled on goto-scheduler, sharing no state with it.
//
// Cloudflare Cron Triggers fire on a scheduler independent of GitHub Actions'
// best-effort `on: schedule` (GOTO saw scheduled runs hours late or dropped) and
// call `workflow_dispatch`. Every DOGEAR operation they start is idempotent: stage
// re-stages the same week, promote no-ops once the week is in the registry at any
// revision, so a double fire, or one racing GitHub's fallback crons, is safe.
//
// Manila is UTC+8 with no DST; the UTC day matters (Mon 06:02 PHT is Sun 22:02 UTC).
export const CRON_WORKFLOWS = Object.freeze({
  "2 2 * * 5": "dogear-stage.yml", // Fri 02:02 UTC = Fri 10:02 PHT — stage W+1
  "17 2 * * 5": "dogear-stage.yml", // Fri 02:17 UTC = Fri 10:17 PHT — stage backup
  "2 22 * * 0": "dogear-promote.yml", // Sun 22:02 UTC = Mon 06:02 PHT — promote
  "17 22 * * 0": "dogear-promote.yml", // Sun 22:17 UTC = Mon 06:17 PHT — promote backup
  "32 22 * * *": "dogear-promote.yml", // 22:32 UTC = 06:32 PHT daily — catch-up (current week only)
});

export const DECLARED_CRONS = Object.freeze(Object.keys(CRON_WORKFLOWS));
const TARGETS = ["staging", "production"];

export function workflowForCron(cron) {
  return Object.hasOwn(CRON_WORKFLOWS, cron) ? CRON_WORKFLOWS[cron] : null;
}

// Build the workflow_dispatch request. Pure. The token travels only in the
// Authorization header. The target is an explicit input, so a scheduler bound to
// staging can never start a production run by default.
export function buildDispatchRequest({ owner, repo, workflow, ref, token, target }) {
  if (!owner || !repo) throw new Error("dogear-scheduler: missing GITHUB_OWNER/GITHUB_REPO");
  if (!workflow) throw new Error("dogear-scheduler: missing workflow");
  if (!token) throw new Error("dogear-scheduler: missing GITHUB_TOKEN");
  if (!TARGETS.includes(target)) throw new Error("dogear-scheduler: PUBLICATION_TARGET must be staging or production");
  return {
    url: `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow}/dispatches`,
    init: {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "dogear-scheduler",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: ref || "main", inputs: { target } }),
    },
  };
}

export async function dispatchWorkflow(env, workflow, { fetchImpl, timeoutMs = 10000 } = {}) {
  const doFetch = fetchImpl || fetch;
  const { url, init } = buildDispatchRequest({
    owner: env.GITHUB_OWNER, repo: env.GITHUB_REPO, workflow, ref: env.GITHUB_REF,
    token: env.GITHUB_TOKEN, target: env.PUBLICATION_TARGET,
  });
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await doFetch(url, { ...init, signal: controller.signal });
    const ok = res.status === 204; // GitHub answers 204 No Content
    let detail = "";
    if (!ok) {
      try { detail = (await res.text()).slice(0, 500); } catch { /* status is enough */ }
    }
    return { ok, status: res.status, workflow, detail };
  } finally {
    clearTimeout(timer);
  }
}
