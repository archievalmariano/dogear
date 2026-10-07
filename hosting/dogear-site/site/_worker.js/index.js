// dogear-site: Cloudflare Pages Functions, advanced mode (_worker.js/ directory).
// Serves DOGEAR from the R2 bucket bound as ISSUES (wrangler.jsonc). Read-only:
// the Worker never writes; publication is one conditional write of
// publication.json by the publisher. Routing and headers live in router.js,
// which the shared vectors test against the Python reference router.
//
// Pages `_headers` rules do not apply to Function responses, so every header is
// set here. R2 reads are strongly consistent: a registry switch is visible to
// the next request.

import { respond } from "./router.js";

function r2Store(bucket) {
  return {
    async get(key) {
      const obj = await bucket.get(key);
      return obj === null ? null : obj; // R2ObjectBody: .text(), .body (a stream)
    },
  };
}

export default {
  /**
   * @param {Request} request
   * @param {{ ISSUES: R2Bucket, DOGEAR_INDEXABLE?: string }} env
   */
  async fetch(request, env) {
    const url = new URL(request.url);
    const r = await respond(r2Store(env.ISSUES), request.method, url.pathname, {
      noindex: env.DOGEAR_INDEXABLE !== "true", // editorial decision (PUBLISHING.md §14); unset = noindex
      readBody: (obj) => obj.body, // stream stored objects; generated bodies are strings
    });
    return new Response(r.body === "" ? null : r.body, { status: r.status, headers: r.headers });
  },
};
