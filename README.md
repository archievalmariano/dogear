# DOGEAR

*a limited weekly reading digest* for CrossPoint e-readers (XTEINK X4 / X4 Pro).

Each Monday (Asia/Manila), a handful of literary dates from the week ahead:
author births and deaths, publication anniversaries, Philippine literary
history and reading observances. Every issue is a deterministic view over a
curated, sourced dataset. No model or web research runs in the weekly path.
A global reading digest with a Philippine / Southeast Asian point of view and a
recognizable human editor.

This repository holds the code: the selector, the device issue and web
edition, the publisher, the Cloudflare Worker that serves it, and the
workflows. **The canonical dataset is private.** It lives in the editorial
repository and is checked out (read-only) at `dataset/` only where it's
needed. Everything here runs and tests on synthetic fixtures.

```
dogear/              stdlib-only Python: dataset, affinity, week, select, issue, web, balance, cli
dogear/publish/      the weekly publisher: registry, transactions, R2, verification (docs/PUBLISHING.md)
hosting/dogear-site/ Pages Worker serving the registry (shared route vectors with dogear/publish/routes.py)
hosting/dogear-scheduler/  Cron Triggers that dispatch the stage and promote workflows
tests/               unittest (Python 3.9+), synthetic data only
fixtures/            synthetic staging datasets, affinity and policy (never editorial records)
data/                the publication policy, the firmware contract and the device's trust roots
mock/                480x800 screen mock and --fitcheck, with the device fonts (SIL OFL)
web-assets/fonts/    the web edition's self-hosted Inter and Newsreader (SIL OFL)
tools/               route vectors, firmware contract sync, chain check, workflow entry point
dataset/             (not committed) the editorial checkout: dataset/data/literary-dates.json, affinity.json
```

```bash
python3 -m unittest discover -s tests
python3 -m dogear.cli --dataset fixtures/staging-s2.json validate
python3 -m dogear.cli --dataset fixtures/staging-s2.json --affinity fixtures/staging-affinity.json \
  issue --week 2027-03-01 --out out --issues out
python3 -m venv mock/.venv && mock/.venv/bin/pip install pillow==12.3.0 segno==1.6.6
mock/.venv/bin/python mock/render_dogear.py --fitcheck fixtures/staging-s2.json
(cd hosting/dogear-site && node --test test/*.test.js)
```

With the editorial checkout at `dataset/`, the same commands run against the
canonical dataset by default: `validate`, `review`, `approve`, `balance`,
`issue`, and `--fitcheck dataset/data/literary-dates.json`.

Docs:
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the editorial model,
  selection, the device and the web edition.
- [docs/PUBLISHING.md](docs/PUBLISHING.md): weekly publication and hosting,
  its reviews, and the staging trials.

`out/`, `out-dev/`, `stage/`, `dev-builds/`, `dataset/` and `mock/.venv/` are not committed.
