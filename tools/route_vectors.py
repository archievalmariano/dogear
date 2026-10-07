"""Shared routing test vectors: what the public host returns, from the reference
router (``dogear.publish.routes.respond``). The Pages Function's tests run the
same file, so the two cannot drift apart.

    python3 tools/route_vectors.py           # rewrite hosting/dogear-site/test/vectors.json
    python3 tools/route_vectors.py --check   # exit 1 if it is out of date

The stores are synthetic and small: the router never reads object contents, so
the registry names hashes the objects do not have. Deterministic by construction.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dogear.publish import registry as regmod  # noqa: E402
from dogear.publish import routes  # noqa: E402
from dogear.publish.store import MemoryStore  # noqa: E402

OUT = ROOT / "hosting" / "dogear-site" / "test" / "vectors.json"
W0, W1, W2 = "2026-11-02", "2026-11-09", "2026-11-16"


def _sha(tag: str) -> str:
    return regmod.sha256(tag.encode("utf-8"))


def _rev(week: str, n: int, op: str, txn: str, reason: str | None = None) -> dict:
    issue_sha, web_sha = _sha(f"issue {week} {n}"), _sha(f"web {week} {n}")
    rev = {"rev": n, "op": op, "txn": txn, "issueId": f"dogear-{week}",
           "issueKey": regmod.issue_key(f"dogear-{week}", issue_sha), "issueSha256": issue_sha, "issueBytes": 4000 + n,
           "webKey": regmod.web_key(dt.date.fromisoformat(week), web_sha), "webSha256": web_sha,
           "provenanceSha256": _sha(f"prov {week} {n}"), "datasetSha256": _sha("dataset"),
           "generator": "dogear@vectors", "generatedAt": f"{week}T06:00:00+08:00",
           "publishedAt": f"{week}T06:02:00+08:00"}
    if reason:
        rev["reason"] = reason
    return rev


def registries() -> dict:
    t0, t1, t2 = "20261102T060200-launch-000000000001", "20261109T060200-promote-000000000002", \
        "20261110T090000-correct-000000000003"
    published = {"schemaVersion": 1, "txn": t2, "current": W1, "hold": None, "weeks": {
        W0: {"active": 0, "revisions": [_rev(W0, 0, "launch", t0)]},
        W1: {"active": 1, "revisions": [_rev(W1, 0, "promote", t1), _rev(W1, 1, "correct", t2, "headline")]}}}
    held = copy.deepcopy(published)
    held.update(current=W0, txn="20261111T090000-rollback-000000000004",
                hold={"reason": "withdrawn", "since": "2026-11-11T09:00:00+08:00",
                      "txn": "20261111T090000-rollback-000000000004"})
    for reg in (published, held):
        regmod.validate(reg)
    return {"published": published, "held": held}


def corrupt_registries(good: dict) -> dict:
    """Each must be refused (503 on /current.json), exactly as regmod.parse refuses it."""
    text = regmod.dumps(good).decode("utf-8")
    out = {"not-json": "{not json", "empty": "", "array": "[]", "null": "null"}

    def variant(name, change):
        reg = copy.deepcopy(good)
        change(reg)
        out[name] = regmod.dumps(reg).decode("utf-8")

    variant("extra-top-level-field", lambda r: r.update(extra=1))
    variant("schema-version-2", lambda r: r.update(schemaVersion=2))
    variant("schema-version-true", lambda r: r.update(schemaVersion=True))
    variant("bad-txn", lambda r: r.update(txn="not-a-txn"))
    variant("current-unpublished", lambda r: r.update(current=W2))
    variant("not-a-monday", lambda r: r["weeks"].update({"2026-11-03": r["weeks"].pop(W0)}))
    variant("no-weeks", lambda r: r.update(weeks={}))
    variant("active-out-of-range", lambda r: r["weeks"][W1].update(active=2))
    variant("active-true", lambda r: r["weeks"][W0].update(active=True))
    variant("rev-mismatch", lambda r: r["weeks"][W0]["revisions"][0].update(rev=1))
    variant("issue-key-mismatch", lambda r: r["weeks"][W0]["revisions"][0].update(
        issueKey="issues/dogear-2026-11-02.0000000000000000.json"))
    variant("web-key-mismatch", lambda r: r["weeks"][W0]["revisions"][0].update(webKey="web/x.html"))
    variant("issue-id-other-week", lambda r: r["weeks"][W0]["revisions"][0].update(issueId="dogear-2026-11-09"))
    variant("short-sha", lambda r: r["weeks"][W0]["revisions"][0].update(webSha256="abc"))
    variant("issue-bytes-too-large", lambda r: r["weeks"][W0]["revisions"][0].update(issueBytes=32 * 1024 + 1))
    variant("issue-bytes-zero", lambda r: r["weeks"][W0]["revisions"][0].update(issueBytes=0))
    variant("unknown-revision-field", lambda r: r["weeks"][W0]["revisions"][0].update(extra="x"))
    variant("correction-without-reason", lambda r: r["weeks"][W1]["revisions"][1].pop("reason"))
    variant("bad-op", lambda r: r["weeks"][W0]["revisions"][0].update(op="rollback"))
    variant("empty-generator", lambda r: r["weeks"][W0]["revisions"][0].update(generator=""))
    variant("bad-hold", lambda r: r.update(hold={"reason": "x"}))
    variant("hold-empty-reason", lambda r: r.update(hold={"reason": "", "since": "s", "txn": "t"}))
    # Python's json reads 4000.0 as a float, which the schema rejects; the Worker
    # must reject a non-integer literal too even though JavaScript has one number type.
    out["float-issue-bytes"] = text.replace('"issueBytes": 4000,', '"issueBytes": 4000.0,', 1)
    out["exponent-active"] = text.replace('"active": 0,', '"active": 0e0,', 1)
    for name, body in out.items():
        try:
            regmod.parse(body.encode("utf-8"))
        except regmod.CorruptRegistry:
            continue
        raise SystemExit(f"corrupt registry {name!r} is accepted by regmod.parse")
    return out


def stores() -> dict:
    regs = registries()
    objects = {}
    for week, entry in regs["published"]["weeks"].items():
        for rev in entry["revisions"]:
            objects[rev["issueKey"]] = f'{{"issueId":"{rev["issueId"]}","rev":{rev["rev"]}}}\n'
            objects[rev["webKey"]] = f"<!doctype html><title>{week} r{rev['rev']}</title>\n"
    missing_web = regs["published"]["weeks"][W0]["revisions"][0]["webKey"]
    objects.pop(missing_web)  # registered, but absent from storage: a plain 404
    objects["issues/dogear-2026-11-16.0123456789abcdef.json"] = "{}\n"  # uploaded, never registered
    objects["web/2026-11-16/0123456789abcdef.html"] = "<html>stray</html>\n"
    objects["fonts/Inter-Regular.woff2"] = "font-bytes"
    objects["fonts/LICENSE-Inter.txt"] = "OFL\n"
    out = {"empty": {"fonts/Inter-Regular.woff2": "font-bytes"}}
    for name, reg in regs.items():
        out[name] = dict(objects, **{regmod.REGISTRY_KEY: regmod.dumps(reg).decode("utf-8")})
    for name, body in corrupt_registries(regs["published"]).items():
        out[f"corrupt:{name}"] = dict(objects, **{regmod.REGISTRY_KEY: body})
    return out


def requests(store_names: list) -> list:
    rev = registries()["published"]["weeks"]
    w1r0, w1r1, w0r0 = rev[W1]["revisions"][0], rev[W1]["revisions"][1], rev[W0]["revisions"][0]
    paths = ["/current.json", "/", f"/{W1}/", f"/{W1}", "/2026-11-12/", "/2026-11-12", f"/{W0}/",
             "/2026-11-08/", f"/{W2}/", "/2026-11-17/", "/2026-02-30/", "/2026-13-01/", "/1999-01-04/",
             "/" + w1r1["issueKey"], "/" + w1r0["issueKey"], "/" + w0r0["issueKey"],
             "/issues/dogear-2026-11-16.0123456789abcdef.json", "/issues/dogear-2026-11-09.json",
             "/" + w1r1["webKey"], "/web/2026-11-16/0123456789abcdef.html", "/publication.json",
             "/fonts/Inter-Regular.woff2", "/fonts/LICENSE-Inter.txt", "/fonts/missing.woff2",
             "/fonts/../publication.json", "/fonts/Inter_Regular.woff2", "/staged/2026-11-09/issue.json",
             "/current.json/", "/CURRENT.JSON", "/index.html", "/robots.txt", "/2026-11-09/index.html"]
    out = []
    for store in store_names:
        if store.startswith("corrupt:"):
            chosen = ["/current.json", "/", f"/{W1}/", "/" + w1r1["issueKey"], "/fonts/Inter-Regular.woff2"]
        elif store == "empty":
            chosen = ["/current.json", "/", f"/{W1}/", "/" + w1r1["issueKey"], "/fonts/Inter-Regular.woff2"]
        else:
            chosen = paths
        for path in chosen:
            out.append({"store": store, "method": "GET", "path": path})
    for method in ("HEAD", "POST", "PUT", "DELETE", "OPTIONS"):
        for path in ("/current.json", f"/{W1}/", "/" + w1r1["issueKey"]):
            out.append({"store": "published", "method": method, "path": path})
    return out


def build() -> dict:
    all_stores = stores()
    cases = []
    for req in requests(list(all_stores)):
        store = MemoryStore()
        for key, body in all_stores[req["store"]].items():
            store.put(key, body.encode("utf-8"), content_type="application/octet-stream")
        r = routes.respond(store, req["method"], req["path"])
        cases.append(dict(req, expect={"status": r.status, "headers": dict(sorted(r.headers.items())),
                                       "body": r.body.decode("utf-8")}))
    return {"comment": "Generated by tools/route_vectors.py from dogear.publish.routes; do not edit.",
            "stores": all_stores, "cases": cases}


def render() -> str:
    return json.dumps(build(), indent=1, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv: list) -> int:
    text = render()
    if "--check" in argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT} is out of date; run python3 tools/route_vectors.py", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(build()['cases'])} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
