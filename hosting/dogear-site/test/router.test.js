// Runs the shared vectors (written by tools/route_vectors.py from the Python
// reference router) against router.js: status, every header, and the body.
// Plain `node --test`; no Workers runtime, no network.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { respond } from "../site/_worker.js/router.js";
import { parse, CorruptRegistry } from "../site/_worker.js/registry.js";

const vectors = JSON.parse(readFileSync(new URL("./vectors.json", import.meta.url), "utf8"));

function memoryStore(objects, { failOn } = {}) {
  return {
    async get(key) {
      if (failOn && failOn(key)) throw new Error("injected storage failure");
      if (!Object.hasOwn(objects, key)) return null;
      const text = objects[key];
      return { text: async () => text, body: text };
    },
  };
}

test("vectors are present", () => {
  assert.ok(vectors.cases.length > 200);
});

for (const c of vectors.cases) {
  test(`${c.store} ${c.method} ${c.path}`, async () => {
    const r = await respond(memoryStore(vectors.stores[c.store]), c.method, c.path);
    assert.equal(r.status, c.expect.status);
    assert.deepEqual(Object.fromEntries(Object.entries(r.headers).sort()), c.expect.headers);
    assert.equal(r.body, c.expect.body);
  });
}

test("every corrupt registry in the vectors is refused by parse", () => {
  const names = Object.keys(vectors.stores).filter((s) => s.startsWith("corrupt:"));
  assert.ok(names.length >= 20);
  for (const name of names) {
    assert.throws(() => parse(vectors.stores[name]["publication.json"]), CorruptRegistry, name);
  }
});

test("storage failure is 503, never a guess", async () => {
  const store = memoryStore(vectors.stores.published, { failOn: (k) => k === "publication.json" });
  assert.equal((await respond(store, "GET", "/current.json")).status, 503);
  const issue = vectors.cases.find((c) => c.store === "published" && c.path.startsWith("/issues/") && c.expect.status === 200).path;
  const objects = memoryStore(vectors.stores.published, { failOn: (k) => k.startsWith("issues/") });
  const r = await respond(objects, "GET", issue);
  assert.equal(r.status, 503);
  assert.equal(r.headers["Cache-Control"], "no-store");
});

test("strings containing number-like text are not mistaken for floats", () => {
  const reg = JSON.parse(vectors.stores.published["publication.json"]);
  reg.weeks["2026-11-09"].revisions[1].reason = "fix 1.5e3 and -0.0 in the text \"quoted 2.0\"";
  assert.doesNotThrow(() => parse(JSON.stringify(reg, null, 2)));
});

test("indexing can be enabled per environment", async () => {
  const r = await respond(memoryStore(vectors.stores.published), "GET", "/current.json", { noindex: false });
  assert.equal(r.headers["X-Robots-Tag"], undefined);
});
