# DOGEAR hosting

| Piece | What | Tests |
|---|---|---|
| `dogear-site/` | Pages advanced-mode Worker: reads `publication.json` from the R2 binding `ISSUES` and serves only what it names (`router.js`, `registry.js`, ports of `dogear/publish/routes.py` and `registry.py`). `wrangler.jsonc` = production (`dogear-site` → `dogear-issues`); `staging/wrangler.jsonc` = `dogear-staging` → `dogear-issues-staging`. | `node --test test/*.test.js`: the 224 shared vectors from `tools/route_vectors.py` (status, every header, body) plus storage-failure and parsing cases |
| `dogear-scheduler/` | Cron Triggers → `workflow_dispatch` of `dogear-stage.yml` / `dogear-promote.yml` with an explicit `target`. Deployed defaults are inert: `PUBLICATION_ENABLED=false`, target `staging`. | `node --test test/*.test.js` |
| `../.github/workflows/` | `_dogear-publish.yml` (the production editorial gate and the one publish job) and its callers: stage, promote, operate, the weekly chain check, and `dogear-ci.yml` (tests, no secrets). | `tests/test_hosting.py` (no expression inside a shell command; only `tools/run_publish.py` reaches the publisher) |

Both packages pin `wrangler` 4.138.0 and need no `npm install` for their tests.

## Rules for every Cloudflare step (each needs the user's approval)

- Run Wrangler from a directory holding only the intended config, and read the
  API calls in its log afterwards. Wrangler 4.138 silently turned a Pages
  create into a Workers deploy when it detected an AI agent (PUBLISHING.md §16).
- `pages project create` needs `--force`; `dogear-staging` already exists (S1).
- No secret is ever an argument, a committed file or a log line. The publisher
  reads `DOGEAR_R2_ACCOUNT_ID`, `DOGEAR_R2_ACCESS_KEY_ID` and
  `DOGEAR_R2_SECRET_ACCESS_KEY` from its environment.

## Copies kept in step with the firmware

The device fonts (`mock/fonts/`), the glyph ranges the fit check uses, the size
limits (`data/firmware-contract.json`) and the trust roots
(`data/dogear-trust-roots.pem`) are copies from the firmware. Refresh them with
`python3 tools/firmware_contract.py --sync` and
`python3 tools/chain_check.py --sync-roots` while a `crosspoint-reader`
checkout sits beside this repository; `tests/test_vendored.py` and
`tests/test_chain_check.py` then check every copy against it.
