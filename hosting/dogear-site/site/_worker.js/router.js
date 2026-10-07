// What the public host returns for each request: a port of
// dogear/publish/routes.py `respond`. Publication is decided by the registry
// alone; an object in storage that no revision names is never served. The
// shared vectors (test/vectors.json, written from the Python module) pin the two
// together, headers and bodies included.
//
// `store.get(key)` returns null, or { text(): Promise<string>, body } for the
// Worker (a stream) and tests alike. It may throw: storage is unavailable.

import { REGISTRY_KEY, CorruptRegistry, activeRevision, isMonday, isoDate, manifestText, parse, parseDate } from "./registry.js";

export const NO_STORE = "no-store";
export const IMMUTABLE = "public, max-age=31536000, immutable";
export const PAGE = "public, max-age=60";
const DATE_PATH = /^\/(\d{4}-\d{2}-\d{2})\/?$/;
const ISSUE_PATH = /^\/(issues\/dogear-\d{4}-\d{2}-\d{2}\.[0-9a-f]{16}\.json)$/;
const FONT_PATH = /^\/(fonts\/[A-Za-z0-9-]+\.(?:woff2|txt))$/;
const TEXT = "text/plain; charset=utf-8";

function headers(cache, ctype, noindex, extra = {}) {
  const h = { "Cache-Control": cache, "X-Content-Type-Options": "nosniff" };
  if (ctype) h["Content-Type"] = ctype;
  if (noindex) h["X-Robots-Tag"] = "noindex"; // indexing is an open editorial decision
  return Object.assign(h, extra);
}

const reply = (status, h, body = "") => ({ status, headers: h, body });
const notFound = () => reply(404, headers(NO_STORE, TEXT, true), "not found\n");
const unavailable = () => reply(503, headers(NO_STORE, TEXT, true), "unavailable\n");

// Route without reading objects: { status, headers, body } or { status, headers, key }.
export async function resolve(store, method, path, noindex = true) {
  if (method !== "GET" && method !== "HEAD") return reply(405, headers(NO_STORE, null, noindex, { Allow: "GET, HEAD" }));
  if (FONT_PATH.test(path)) {
    const ctype = path.endsWith(".woff2") ? "font/woff2" : TEXT;
    return { status: 200, headers: headers(IMMUTABLE, ctype, noindex), key: path.slice(1) };
  }
  let reg;
  try {
    const obj = await store.get(REGISTRY_KEY);
    reg = obj === null ? null : parse(await obj.text());
  } catch (err) {
    return unavailable(); // storage error or CorruptRegistry: never serve a guess
  }
  if (reg === null) {
    return reply(path === "/current.json" ? 503 : 404, headers(NO_STORE, TEXT, true), "nothing published\n");
  }
  if (path === "/current.json") return reply(200, headers(NO_STORE, "application/json", noindex), manifestText(reg));
  if (path === "/") return reply(302, headers(NO_STORE, null, true, { Location: `/${reg.current}/` }));
  let m = ISSUE_PATH.exec(path);
  if (m) {
    // Only each week's ACTIVE revision is addressable (PUBLISHING.md §7).
    for (const week of Object.keys(reg.weeks)) {
      if (activeRevision(reg, week).issueKey === m[1]) {
        return { status: 200, headers: headers(IMMUTABLE, "application/json", noindex), key: m[1] };
      }
    }
    return notFound();
  }
  m = DATE_PATH.exec(path);
  if (m) {
    const day = parseDate(m[1]);
    if (!day) return notFound();
    const monday = isoDate(new Date(day.getTime() - ((day.getUTCDay() + 6) % 7) * 86400000));
    if (!Object.hasOwn(reg.weeks, monday)) return notFound();
    if (m[1] !== monday || !path.endsWith("/")) return reply(301, headers(PAGE, null, true, { Location: `/${monday}/` }));
    const rev = activeRevision(reg, monday);
    return { status: 200, headers: headers(PAGE, "text/html; charset=utf-8", noindex), key: rev.webKey };
  }
  return notFound();
}

// The complete response. `readBody(obj)` turns a stored object into the body to send.
export async function respond(store, method, path, { noindex = true, readBody = (o) => o.text() } = {}) {
  let r = await resolve(store, method, path, noindex);
  if (r.key !== undefined) {
    let obj;
    try {
      obj = await store.get(r.key);
    } catch (err) {
      return unavailable();
    }
    r = obj === null ? notFound() : reply(r.status, r.headers, method === "HEAD" ? "" : await readBody(obj));
  }
  if (method === "HEAD") r = reply(r.status, r.headers, "");
  return r;
}

export { CorruptRegistry, isMonday };
