# DOGEAR — weekly publication and hosting

*Design, reviews and status, 7 October 2026. Built: the publisher, the Worker,
the scheduler and the workflows (§18–§19). Staging exists and has passed: S1
(§10–§11) and the S2 publication cycle (§19c). Production resources, the
repositories' setup, schedules and the first public publication remain gated
(§13, §20); each needs the owner's approval.*

Builds on: [ARCHITECTURE.md](ARCHITECTURE.md) (selector, issue, web edition),
the editorial handoff (state, invariants; private, in `dogear-editorial`) and GOTO's live publication stack
(`docs/13-hosted-architecture.md`, `docs/14-production-deployment-runbook.md`,
`hosting/goto-scheduler/README.md` in this workspace).

**Launch blockers:**

1. The X4 must complete a real HTTPS fetch from the production host (G9);
   from staging it has (§11, §19c).
2. Slot-5 threshold: 42 or 40 (editorial, §14).
3. Whether a genuinely sparse 1–2-item week publishes (editorial, §14).
4. Records whose sourcing is held stay held until it is resolved (editorial; tracked privately).
5. Codex findings 7–8 stay deferred to the final release review.

---

## 1. Architecture

DOGEAR reuses GOTO's proven shape and shares none of its state:

```
canonical dataset (git, editor-approved records)
   │  Friday: STAGE next week          ── dogear-stage.yml   (GitHub Actions, Python stdlib)
   ▼
deterministic selector → device issue JSON + web edition + audit + validation
   │  committed to dogear-publication-data (private): weeks/<monday>/…  state=staged
   │  Monday 06:00 PHT: PROMOTE          ── dogear-promote.yml
   ▼
R2 bucket dogear-issues: upload immutable objects (unreachable until registered) → verify
   → conditional write of publication.json (the registry: the ONE publication boundary) → verify served
   ▼
Cloudflare Pages project dogear-site (Functions advanced mode, R2 binding)
   at https://dogear.archievalmariano.com   (one GoDaddy CNAME → dogear-site.pages.dev)
   ▼
X4 / X4 Pro: GET /current.json (built from the registry) → GET /issues/<content-addressed>.json
Phone (final QR): GET /2026-10-12/ (the week's active web revision, looked up in the registry)
```

A standalone `dogear-scheduler` Cloudflare Worker (cron triggers →
`workflow_dispatch`) triggers the workflows on time, as `goto-scheduler`
does for GOTO. GitHub's own `on: schedule` crons stay as a redundant
fallback. The reason is GOTO's experience: GitHub runs scheduled jobs hours
late and sometimes drops them silently.

**Kept separate from GOTO:**

| | GOTO (live) | DOGEAR (proposed) |
|---|---|---|
| Source repo | `project-goto` (`main`) | `dogear` (new, private; §15) |
| Publication data | `project-goto-publication-data` | `dogear-publication-data` (new, private) |
| Workflows | `goto-publish.yml`, `togo-publish.yml` | `dogear-stage.yml`, `dogear-promote.yml`, `dogear-correct.yml` |
| Concurrency group | `goto-publication` | `dogear-publication` |
| R2 bucket | `goto-editions` | `dogear-issues` (+ `dogear-issues-staging`) |
| Pages project / host | `goto-site` / `goto.archievalmariano.com` | `dogear-site` / `dogear.archievalmariano.com` (+ `dogear-staging`) |
| Scheduler Worker | `goto-scheduler` | `dogear-scheduler` |
| Credentials | its own PAT and R2 keys | its own PAT (DOGEAR repo only) and bucket-scoped R2 keys |

**Reused patterns, not code paths:**
- the pipeline order: write immutable objects first, flip the pointer last, then verify what's actually served;
- idempotent runs: a re-run no-ops once the pointer already names this week's issue;
- `PUBLICATION_ENABLED` gating scheduled runs;
- package-pinned Wrangler;
- the scheduler's allowlist-only design (its HTTP endpoint never publishes).

**Model-free and research-free:** a publication run reads only the
committed dataset, the affinity file and the previously *published* issues.
Its one network use is a reading-link check at staging time (§3), which can
block a run but never changes which items are selected.

---

## 2. Canonical dataset and reserve

### 2.1 What already holds

- **Records recur every year.** Every record is a fixed day-precision
  anniversary (`occurrence_in` matches month/day; 29 February only in leap
  years). Copy is written without the year ("*The Salt Orchard* is
  published"; the kicker carries "PUBLISHED 1850"), so one
  record serves every year with no duplication.
- **Approval persists.** It binds to a fingerprint of the record's
  material fields, so an approved record stays approved year after year
  until someone edits it. An edit voids the approval automatically.
- **The editorial dimensions stay separate fields:**
  - `significance`, `recognition`, `discoveryValue`, `phRelevance`, `seaRelevance`;
  - `area` (wider regional context), `domain`, the affinity file;
  - `links[].rights` and access tier;
  - `status`, `sources[].kind`, `verifiedBy`/`verifiedOn`.

  The selector combines them per issue and stores none of them as a single
  score. Nothing in this proposal changes that.

### 2.2 The reserve is the real launch constraint

Measured on the seed dataset, counting every verified record as if approved
and applying history week to week:

| Window | 5+ items | 3–4 | 1–2 | 0 |
|---|---|---|---|---|
| Next 12 weeks (12 Oct 2026 – 3 Jan 2027) | 2 | 3 | 1 | 6 |
| Next 52 weeks | 2 | 4 | 18 | 28 |

The seed dataset was built around the sample weeks. Weekly publication needs
roughly 10–12 eligible candidates per week to fill a 5–6-item digest after
day caps, slot bars and history. That means an annual corpus of about
**500–600 records**.

### 2.3 Proposed reserve strategy

1. **Launch runway:** at least 8 consecutive weeks from the launch week, each
   with a full digest **from approved records only**. 12 weeks preferred.
   Corpus work fills those weeks first.
2. **Annual corpus:** build the rest month by month in batches: research,
   verify, then the editor approves. This happens offline, never as part of
   a publication run.
3. **Two new local commands** (read-only, deterministic):
   - `dogear runway`: consecutive publishable weeks from a date, using
     approved records only. This is the number the launch gate checks.
   - `dogear coverage --year 2027`: a 52-week table of approved,
     awaiting-approval and held counts per week, so batches target the thin
     weeks.

   The weekly stage run also reports runway, so a shrinking reserve shows up
   weeks before it bites.
4. **One-off and movable events** (rare today): an optional
   `occurs: {"years": [2027]}` restricts a record to listed years, absent
   meaning every year. Rule-based dates such as "fourth Monday" are left out
   until a real record needs one.
5. **File layout:** keep one JSON file up to about 200 records, then split
   into `data/records/01.json`…`12.json`. The loader concatenates the months
   and the dataset hash covers the canonical concatenation. Monthly files keep
   editing batches reviewable.
6. **Link health:** a quarterly offline sweep of every reading link and
   source in the reserve, reported to the editor. The weekly link check
   (§3) covers only the issue being published.

Records that are held, disputed, provisional or unapproved stay in the
dataset. Production selection excludes them before composition, as it
already does, and §3 adds an independent check on the output.

---

## 3. Weekly generation

**Stage** (automatic on Fridays for next week, W+1; §4 lists the two explicit
current-week exceptions):

1. Resolve W+1 = the Monday after the current Asia/Manila week (`dogear.week`).
2. **Freeze the inputs**, recording each by hash:
   - the generator's git SHA;
   - the dataset and affinity file;
   - history: the `[monday, issueSha256]` pairs of *published* weeks, read
     from the publication registry (§6), never from staged artifacts;
   - `generatedAt`.
3. Select in **publication mode**: unapproved records excluded before
   composition.
4. Generate the device issue, web edition, audit and editor's report, plus a
   private **`provenance.json`**:
   - one entry for every rendered record (digest, ALSO THIS WEEK and any
     web-only item): `{recordId, fingerprint, section, position}`;
   - the frozen inputs;
   - the hashes of the device issue and the web page.

   Provenance lives only in the publication-data repo and is never uploaded.
   The device issue schema doesn't change: its `alsoThisWeek` entries carry
   no record IDs, which is why provenance is a separate file.
5. Validate. Every check fails the stage run and nothing is committed:

   | Check | Rule |
   |---|---|
   | Eligibility (independent) | A separate module, not the selector, checks every provenance entry against the canonical dataset: the record exists; the full `publishable` predicate holds (verified, approval fingerprint equals the current fingerprint, `sourcing_ok`); and its fingerprint equals the one in provenance. It rejects a missing provenance file, an entry count that doesn't match the artifact's items, and any proof artifact (`preview: true`). |
   | Content binding | Regenerating from the frozen inputs gives byte-identical files. That ties the rendered text to exactly the approved record versions in provenance. |
   | Not a proof | `preview` is false; the web page has no proof banner. Proof output goes to a separate directory that the production uploader cannot read. The uploader takes only files listed in a production artifact set that passed these checks. |
   | Source integrity | The dataset validates; every rendered item has visible sources under the source rules (de-duplicated, Wikidata hidden when a better source exists). |
   | External links | Each rendered item's CTA URL is fetched. **404, 410, DNS failure or a TLS error block the run**; a timeout or 5xx is a warning, re-checked at promotion. The check never changes selection. |
   | DOGEAR's own URL | The final QR target is **not** fetched, because next week's page doesn't exist publicly yet. Locally: the URL must equal `<base>/<monday>/` and the matching web artifact must be in the staged set. The public URL is checked after promotion (§6, step 8). |
   | Access hierarchy | CTA labels come only from READ ONLINE / BORROW / FIND THE BOOK / READ MORE / VIEW SOURCE; never READ FREE. |
   | Fit | `--fitcheck` passes on the rendered items (needs Pillow and the device's Noto TTFs vendored; §15). |
   | Device limits | Issue ≤ 32 KB, field lengths and entry count within `DogearLimits.h`, mirrored in Python with a test pinning the two. |
   | Shape | Slot bars and the sparse-week rule, both pending editorial decision (§14). |

6. Commit `weeks/<W+1>/staged/` (artifacts, provenance, validation report)
   to the publication-data repo, and post the proof link to the editor (GitHub
   job summary; email optional). The editor has the weekend to veto, by
   withdrawing an approval or asking for a re-stage. No weekly sign-off is
   needed.

**Promote** (Monday; §4 and §6). Normal promotion publishes **the staged
bytes exactly**, after re-running the eligibility check against the dataset
as it is at that moment. **Exception:** if an approval was withdrawn or a
record edited after staging, promotion re-stages from the current dataset.
The replacement selection may differ from Friday's proof; it is a new,
fully validated artifact with its own hashes, and the editor gets an
explicit "changed proof" notice. Approvals *added* after staging don't change
the issue unless someone deliberately re-stages.

**Correct** (manual, with a required reason) and **rollback** (manual) are
separate transitions with their own preconditions (§4).

**`occurs`** (§2.3): if the field is introduced, it joins the approval
fingerprint's material fields, so restricting a record's years voids its
approval like any other edit.

---

## 4. Scheduler and state transitions

**Triggers** (Philippine time; Manila is UTC+8 with no DST):

| When (PHT) | Cron (UTC) | Job |
|---|---|---|
| Fri 10:02 | `2 2 * * 5` | stage W+1 |
| Fri 10:17 | `17 2 * * 5` | stage W+1 (backup) |
| Mon 06:02 | `2 22 * * 0` | promote |
| Mon 06:17 | `17 22 * * 0` | promote (backup) |
| Daily 06:32 | `32 22 * * *` | promote (catch-up) |

The UTC day-of-week matters: Monday 06:02 PHT is **Sunday** 22:02 UTC.

**Transitions.** The CLI holds all window and state rules; the crons only
start runs.

| Transition | Who | Preconditions | Effect |
|---|---|---|---|
| **stage** | Friday cron | target = W+1 only | `weeks/<W+1>/staged/` |
| **promote** | Monday crons, daily catch-up, manual | target = the Manila week containing now, past Monday 06:00 PHT; the registry has **no** entry for that week, at any revision (otherwise it's a green no-op, whatever the hash); no `hold`; target later than the current week | Adds the week at revision 0 and makes it current. Uses the staged artifact, or stages the current week inline if none valid exists (exception 1). |
| **launch** | manual, once | empty registry and an explicit `--first-publication` flag | Stages and promotes the current week (exception 2; §9) |
| **correct** | manual | week W published; `--expect-rev` equals W's active revision; a reason | Appends a new revision and makes it active. Devices see it only if W is the current week; a past week's correction changes only its web page. |
| **rollback** | manual | a target revision that was published | Within a week: active = an earlier revision. To an earlier week: current = that week **and `hold` is set** with a reason. |
| **restore** | manual | `hold` set; an explicit published `--week` and `--rev` | Makes that revision current and clears `hold`. This is how "put this week back" is done after a rollback, since promote would skip a week already in the registry. |
| **resume** | manual | `hold` set | Clears `hold` only. The current week stays whatever the rollback left, and automatic promotion resumes with the **next unpublished** week. |

- **Catch-up publishes only the current Manila week, and only once.** After
  the following Monday it targets the new week, so a missed week is never
  published retroactively by automation. That week simply has no issue.
- **A correction can't be undone by catch-up:** catch-up recognises an
  already-published week whatever its revision hash.
- **A rollback can't be undone by catch-up:** `hold` stops automatic
  promotion (the daily run reports the hold) until a human resumes.
- **Week boundary recheck:** promote recomputes the Manila week immediately
  before the conditional pointer write (§6, step 5). A run delayed across a
  week boundary aborts instead of publishing the week it started for.
- **Late but valid:** a promotion that lands late is still correct, because
  the issue's dates come from the week, not the run time. Devices switch only
  when the pointer changes, never by their own clock.

---

## 5. Cloudflare Worker / R2 layout

**R2 `dogear-issues`.** Uploading an object never publishes it. Only the
registry does:

| Key | Contents | Mutability |
|---|---|---|
| `publication.json` | **the registry**: the single publication boundary (§6) | changed only by conditional write |
| `issues/<issueId>.<sha16>.json` | device issue, content-addressed | immutable |
| `web/<monday>/<sha16>.html` | web edition revision, content-addressed | immutable |
| `fonts/*` | Inter, Newsreader (OFL) | immutable |

**`dogear-site` `_worker.js`** (Pages advanced mode, modelled on `goto-site`).
On each request it reads `publication.json`; R2 reads are strongly
consistent.

| Route | Response |
|---|---|
| `GET /current.json` | built from the registry's current week and active revision (the device manifest, §6). `Cache-Control: no-store`. |
| `GET /issues/<key>.json` | served **only if** the key is the **active** revision of a published week (a correction withdraws the old URL; a rollback within the week brings it back); immutable caching |
| `GET /<monday>/` | the **active web revision of a published week**, looked up in the registry. `Cache-Control: public, max-age=60`, so a correction or rollback shows within a minute. |
| A day inside a published week | 301 to its Monday |
| `GET /` | 302 to the current week |
| `GET /fonts/*` | immutable |
| Anything else (unpublished, future, unknown) | 404 with `Cache-Control: no-store`, so an unpublished 404 is never cached past promotion |

Other rules:
- GET and HEAD only; strict key validation; no listing.
- The Function sets every header itself: Pages `_headers` rules don't apply
  to Function responses.
- If the registry is unreadable or corrupt, the Worker answers 503 on
  `/current.json`. The device then keeps its verified card copy.
- An uploaded but unregistered web or issue object is unreachable, so a
  failed promotion never exposes a half-published edition.

The staging pair is `dogear-staging` (Pages) and, from S2, `dogear-issues-staging` (R2), each with R2 credentials scoped to one bucket.

---

## 6. Registry, manifest and the publication transaction

**Registry** (`publication.json`, private to the Worker; about 0.6 KB per revision):

```json
{
  "schemaVersion": 1,
  "txn": "20261012T060211+0800-promote-7f3a",
  "current": "2026-10-12",
  "hold": null,
  "weeks": {
    "2026-10-12": {
      "active": 0,
      "revisions": [
        { "rev": 0, "op": "promote", "txn": "20261012T060211+0800-promote-7f3a",
          "issueId": "dogear-2026-10-12",
          "issueKey": "issues/dogear-2026-10-12.3f9a1c2b7d4e5f60.json",
          "issueSha256": "…", "issueBytes": 9876,
          "webKey": "web/2026-10-12/a41c09e2b7d3f501.html", "webSha256": "…",
          "provenanceSha256": "…", "datasetSha256": "…", "generator": "dogear@<sha>",
          "generatedAt": "2026-10-09T10:02:03+08:00", "publishedAt": "2026-10-12T06:02:11+08:00" }
      ]
    }
  }
}
```

**Device manifest** (`/current.json`, built by the Worker from the registry):

```json
{ "issueId": "dogear-2026-10-12",
  "issuePath": "issues/dogear-2026-10-12.3f9a1c2b7d4e5f60.json",
  "issueSha256": "…", "week": { "start": "2026-10-12", "end": "2026-10-18" },
  "webPath": "2026-10-12/", "revision": 0 }
```

The firmware is already compatible: `parseServerManifest` reads `issueId`,
`issuePath` and `issueSha256`, ignores other fields, and enforces the path
rules and the 4 KB cap.

**Transaction.** Every transition that changes publication runs these steps:

1. **Read** `publication.json` and its ETag:
   - **Missing:** allowed only for `launch` with `--first-publication` and
     no history in the publication-data repo.
   - **Unreadable or failing schema validation:** abort and alert. Never
     overwrite it.
2. **Plan:** compute the intended registry as a pure function of the current
   registry and the operation, and check the transition's preconditions
   (§4).
3. **Upload** the new immutable objects, then **verify** each by GET:
   complete bytes, length, SHA-256, and the issue ID inside.
4. **Record a pending transaction** in the publication-data repo and push it:
   `pending.json` = `{txn, op, week, expectedEtag, expectedRegistrySha,
   intendedRegistrySha, artifact hashes}`. If the push fails, abort; nothing
   has been switched.
5. **Recheck the Manila week** (promote) and abort if it changed. This
   recheck, and the step-2 preconditions, run again before **every** write
   attempt, retries included.
6. **Conditional write:** `PUT publication.json` with `If-Match:
   <expectedEtag>`, or `If-None-Match: *` for the first publication.
7. **Resolve the outcome:**
   - **Success:** committed.
   - **412 (precondition failed):** something else changed the registry.
     Abort and alert; never retry blindly.
   - **Ambiguous** (timeout, 5xx, connection lost): read `publication.json`
     back.
     - Equal to the intended registry: committed.
     - ETag still the expected one: not applied. A retry is allowed only
       after **re-running every precondition** (step 2's transition checks,
       step 5's Manila-week check, no `hold`) immediately before that attempt.
       A recovery that crosses Monday midnight therefore aborts instead of
       publishing the previous week.
     - Anything else: a concurrent change. Abort and alert.
8. **Verify what is served:** GET the public `/current.json` and
   `/<monday>/`. If the registry now carries a *later* transaction than ours
   (e.g. a correction), report it and don't touch it. Otherwise retry the
   read only; never re-PUT.
9. **Finalize exactly once:**
   - append to `history.jsonl`, keyed by `txn` (skipped if already present);
   - delete `pending.json`;
   - push.

**Reconciliation first.** Every run, before anything else, checks for a
leftover `pending.json`:
- the registry's `txn` equals the pending one → finalize it (step 9);
- the registry's ETag still equals the pending `expectedEtag` → it never
  applied; record it as aborted and remove it;
- anything else → stop and alert a human.

So a successful pointer switch followed by a failed git push is completed
on the next run, never repeated. The workflows share the
`dogear-publication` concurrency group, and the conditional write protects
against any writer outside that group.

**Production never carries a proof.** The uploader's allowlist accepts only
validated production artifact sets. As defence in depth, release firmware
could also treat `preview: true` as invalid (for the firmware review, §17).

---

## 7. Archive URLs

- Issue pages: `https://dogear.archievalmariano.com/2026-10-12/` (the issue's
  Monday, as in ARCHITECTURE).
- `/` → the current issue. A date inside a published week redirects to its
  Monday. Future, unpublished and missed weeks are 404.
- Readable and enumerable by design, the opposite of GOTO's opaque tokens.
  Publication is decided by the registry, not by guessable keys.
- Each URL always serves the week's active revision. Earlier revisions stay
  in R2 for rollback but are not publicly addressable.
- The final QR on the device already points to the issue's web page
  (`--base-url`); production sets the base to the custom host.
- An `/archive/` index page is optional and not in v1 (§14).

---

## 8. Staging and production

| | Staging | Production |
|---|---|---|
| Host | `https://dogear-staging.pages.dev` (no DNS) | `https://dogear.archievalmariano.com` |
| Content | **S1: synthetic fixture only** (`fixtures/staging-synthetic.json`, never the canonical dataset). S2: synthetic or proof, decided then. | Approved only; production refuses proofs |
| Crawlers | `X-Robots-Tag: noindex, nofollow`; robots.txt left permissive so crawlers can see the noindex. This is labelling, **not access control**. | per §14 |
| Storage | S1: static Direct Upload. S2: `dogear-issues-staging` + registry | `dogear-issues` + registry |
| Writes | Manual from the Mac (S1); staging workflows (S2) | Workflows only, through a GitHub Environment `production` with you as required reviewer for the first promotions |
| Firmware | Dev builds with `DOGEAR_SERVER_BASE` = staging; HTTPS only | Release builds; the compiled-in default is already `https://dogear.archievalmariano.com` |

**Certificates need ongoing checks, not a fixed issuer.** Observed on
7 October 2026:
- `goto-site.pages.dev`: Let's Encrypt YE1 → ISRG Root YE → **ISRG Root X2**
  (itself cross-signed by X1);
- **`dogear-staging.pages.dev`** (S1): Cloudflare's shared `pages.dev`
  certificate, GTS WE1 → **GTS Root R4** → GlobalSign cross-sign. So one
  provider's hosts can differ, and S1 exercised the GTS chain;
- `goto.archievalmariano.com`: GTS WE1 → **GTS Root R4**, cross-signed by
  GlobalSign.

Cloudflare rotates managed certificates and Let's Encrypt documents
alternative chains. A future chain ending only at ISRG Root YE, without the
X2 cross-sign, would fail on the device. So:
1. G9 tests the **exact production hostname** with the **intended firmware**
   and records the served chain.
2. A **weekly chain check** in the DOGEAR repo verifies each host's served
   chain with only the `DogearTrustRoots.h` roots and alerts on failure,
   before devices fail.
3. Before release firmware, decide whether to add ISRG Root YE (and its
   RSA counterpart) to the trust list. GlobalSign Root CA, which
   cross-signs GTS R4, expires in January 2028.

---

## 9. Mid-week launch

- The inaugural issue is the **Asia/Manila Monday–Sunday week containing the
  launch moment**: launch on Wednesday 14 October and the issue is
  12–18 October, with items from the days already past included.
- It's the `launch` transition (§4): stage the current week, then promote,
  with `generatedAt` = launch time and an empty history.
- The issue's NEXT ISSUE line names the following Monday as usual.
- That week's Friday stage run prepares the next issue normally. A launch on
  Friday–Sunday runs that stage immediately after the launch promotion.
- The week is never computed as "seven days from deployment".

---

## 10. First staging deploy (S1): **created 7 October 2026; PASS on both devices (§11)**

**Purpose:** close launch blocker 1 (the X4 doing real HTTPS) with the
smallest possible footprint. It doesn't test production TLS, R2, the Worker,
the registry or the publication transaction; those are S2 and G9.

**Exactly what would be created:**
- **One Cloudflare Pages project, `dogear-staging`**, in your existing
  Cloudflare account. Direct Upload, no Git connection, served only at
  `https://dogear-staging.pages.dev`.
- **No R2 bucket, Worker, custom domain, DNS record, secret, PAT, GitHub
  repo, workflow or scheduler.**

**What it would serve** (built locally by `tools/staging-build.sh` into
`site-staging/`; prepared, not uploaded). **Synthetic content only**, as
Codex recommended:
- **Issue `dogear-2027-02-01`** (1–7 February 2027): three items, all
  labelled "Staging test", all placeholder text, linking only to
  `example.org`.
  - Built by the real generator from `fixtures/staging-synthetic.json`.
  - Marked `preview: true`; 2,391 B; fit check clean.
  - Neither device has this week cached.
  - Issue SHA-256 `496c47bd8f066b3472284206c6d8ffa28ccc94d513a2d45a923513bb18819057`.
- `current.json` → that issue (SHA-256 `6972b3fa…f2dc5895`), its web
  edition, the fonts, and `_headers` (`X-Robots-Tag: noindex, nofollow`;
  `Cache-Control: no-cache, must-revalidate` on `current.json`).

**Firmware** (built and in `dev-builds/`; not installed). These are dev
envs; release envs are untouched. Each has `DOGEAR_SERVER_BASE` = `https://dogear-staging.pages.dev`
and **no** LAN-HTTP exception, from firmware commit `3d9d9e56`:

| File | SHA-256 |
|---|---|
| `DOGEAR_X4_STAGING.bin` | `25954daae17960c630c5cce11e830f115d1eba5651bf074176c3174a3ebe7021` |
| `DOGEAR_X4PRO_STAGING.bin` | `ffbea7d144a01c410b8e3cd310d8fe13e8b9689d6757f8dff6efd987c195e68d` |

**Commands.** They use Wrangler 4.138.0, already installed for GOTO in
`hosting/goto-scheduler/`, so nothing new is downloaded. Run them from that
directory after `wrangler login` (by you, or by me once you're logged in):

```bash
npm exec -- wrangler pages project create dogear-staging --production-branch main
```

```bash
npm exec -- wrangler pages deploy "../../dogear/site-staging" --project-name dogear-staging --branch main
```

**Undo:** `npm exec -- wrangler pages project delete dogear-staging`. Nothing
else exists to clean up.

---

## 11. X4 HTTPS validation against staging (S1 acceptance)

1. **From the Mac:**
   - `curl` `current.json` and the issue; the issue's SHA-256 matches the
     manifest and the fixture hash above;
   - headers as specified;
   - the served chain recorded and verified with only the device's roots.
2. **X4 Pro** (USB, staging build, serial logged): expect `verified TLS to
   dogear-staging.pages.dev` and `downloaded issue dogear-2027-02-01 (hash
   verified); cached`.
3. **X4** (microSD, staging build, saved Wi-Fi; no password re-entry):
   1. **Online:** open DOGEAR. Pass: the "Staging test" issue, 1–7 February
      2027, with no `CACHED`/`OFFLINE` marker.
   2. **Persistence:** press Back on the Wi-Fi screen, sleep and wake the
      device, and reopen DOGEAR offline. Pass: the same staging issue, now
      marked `CACHED`. That confirms the download was saved; the online step
      alone doesn't, because the downloader shows an issue even when saving
      it fails.
   3. Note roughly how long the first online page takes.
4. **Record** both firmware SHA-256s, the fixture and issue SHA-256s, the
   served chain and the results in the hardware test log.

**Result (7 October 2026): PASS on the X4 Pro and the X4.** Evidence is in
the hardware test log (§4c; kept privately). The X4 took about 10–12 s from the Wi-Fi screen to
the first page on its first online open (§11a).

**What a pass establishes (and nothing more):**
- The X4 completes verified TLS to a real Cloudflare Pages host on the
  GTS WE1 → R4 chain (P-256 leaf, P-384 intermediate), within the C3's
  memory.
- It checks the issue hash and keeps the issue on the card.

**What it does not establish:**
- **That NTP ran:** the X4 keeps time across sleep once it has been set,
  e.g. by GOTO, so a plausible clock may already have been there.
- Production TLS (G9).
- The publication transaction (S2).

**Fail:** the previous issue with `CACHED`. Then HOLD. Diagnosis would come
from an X4 build that writes the failure reason to the card, a small
dev-only addition proposed only if needed.

Afterwards, restore both devices to the LAN dev builds, or keep the staging
builds for S2.

### 11a. First-open latency on the X4

10–12 s by hand count on the X4, against about 4 s on the X4 Pro (Wi-Fi
connected to issue on screen, from the serial log). The likely components,
none measured on the X4 itself because it has no serial port:
- Wi-Fi association;
- possibly an NTP clock step (a firmware update restarts the X4, and it has
  no RTC);
- **two** full TLS handshakes, one for the manifest and one for the issue,
  each with X25519 plus P-256 and P-384 signature checks in software on a
  160 MHz single-core C3.

Only a week's first open downloads the issue. Later opens fetch just the
manifest (one handshake) and reuse the card copy. Options for the firmware
review, none adopted here:
- reuse one TLS connection or session for the manifest and the issue;
- show a short "Checking for this week's issue" status while it waits;
- measure the phases on the X4 with a dev build that writes timings to the
  card.

---

## 12. Failure and recovery

| Failure | Behaviour | What devices and readers see | Recovery |
|---|---|---|---|
| Dataset invalid, or any stage check fails | Stage run red; nothing committed; editor alerted | unchanged | Fix the record or approval and re-run stage; Monday's promote stages inline if needed |
| Stage never ran (scheduler drop) | — | unchanged | Backup trigger and GitHub crons; failing that, promote stages the current week inline |
| Approval withdrawn after staging | Promote re-stages; "changed proof" notice | the re-staged issue | Automatic |
| An upload fails, partially or completely | Run red before step 4; orphaned objects stay unregistered and unreachable | unchanged | Retry; uploads are idempotent (content-addressed) |
| Pending record can't be pushed | Abort before switching | unchanged | Retry |
| Pointer write times out or 5xx | Read back and resolve (§6, step 7) | old or new, never torn | Resolved by reading back, never by a blind re-PUT |
| 412 / concurrent change | Abort and alert | whatever the newer write published | Human |
| Switch succeeded, history push failed | `pending.json` left | new issue (correct) | Next run reconciles and finalizes exactly once |
| Registry unreadable or corrupt | Publisher aborts; Worker answers 503 on `/current.json` | devices keep their verified card copy | Human: restore the registry from publication-data history |
| Registry points at a missing or corrupt issue | Device rejects it (hash or parse) and keeps its card copy | card copy | Alert; correct or roll back |
| Monday missed entirely | Previous issue stays current | last week's issue | Daily catch-up for the current week only |
| Run delayed past the week boundary | Aborts at the recheck | unchanged | Next run targets the new week |
| Sparse week | **Pending editorial decision (§14)** | — | — |
| Wrong issue published | — | — | `rollback` (sets `hold` when going back a week), then `resume` |
| Device offline or server down | Card copy (`CACHED`), else the built-in sample (`OFFLINE`) | — | Already proven on hardware |

Last week's issue staying current after a failed promotion is accepted
degraded behaviour, the same rule as GOTO. Its dates show its age, and it is
never replaced by synthesized content.

---

## 13. Approval gates before public publication

| Gate | What | Who |
|---|---|---|
| G1 | Codex reviews this proposal (§16): **done, conditional go**; findings 1–7 addressed in this revision | Codex → you |
| G2 | S1 staging created (§10), synthetic content | **done** (approved 7 October) |
| G3 | **X4 HTTPS fetch from staging passes** (blocker 1) | **PASS 7 October** (X4 Pro and X4; §11) |
| G4 | Editorial decisions: slot-5 42/40, sparse weeks, and the new ones in §14 (blockers 2–3) | **you** |
| G5 | Runway: at least 8 consecutive publishable weeks of approved records; records held for sourcing stay held until resolved (blocker 4) | **you approve records**; never automatic |
| G6 | Implementation plus pipeline staging S2: a full cycle against the staging bucket (stage → promote → correct → rollback → catch-up after a simulated drop) | me, then Codex |
| G7 | Production resources created (repo, bucket, Pages project, scheduler, PAT, R2 keys, `dogear` CNAME at GoDaddy) | **you approve; DNS and PAT are yours to do** |
| G8 | Release firmware decision: a DOGEAR release env without dev-only apps, with SHA-384/P-384 in that env, and findings 7–8 reviewed (blocker 5) | you; Codex final release review |
| G9 | Soft launch: the first approved issue promoted to production, an X4 and an X4 Pro fetch it from the production host (GTS chain), and the web page checked, all before any announcement | you |
| G10 | Scheduled publication enabled (`PUBLICATION_ENABLED=true`, scheduler deployed) | **you** |

Hosted publication can go live (G9–G10) before release firmware ships (G8);
then only dev-build devices read it. Whether to launch web-first or wait for
firmware is listed in §14.

---

## 14. Editorial decisions remaining

Carried over (launch blockers):
1. **Slot-5 bar: 42 or 40.**
2. **Sparse weeks:** does a genuine 1–2-item week publish, and what does a
   0-item week do? The options, which I'm not choosing between:
   - publish the short issue;
   - keep last week's issue current;
   - publish a "quiet week" issue with no items.
3. **Sourcing holds:** a record whose date rests only on weak sources stays held until a stronger
   institutional or scholarly source is found (decisions tracked privately).

New, raised by this design:

4. **Proof content on staging:** S1 uses a synthetic test issue, following
   Codex's recommendation. For S2, synthetic or proof content is decided
   then.
5. **Indexing:** should published issue pages be indexable? GOTO's companion
   pages are `noindex`; a literary archive is arguably meant to be found.
6. **Corrections:** a visible note on the web page (e.g. "Corrected
   14 October"), or silent with the record kept internally?
7. **`/archive/` index page:** in v1 or later?
8. **Link-check strictness:** should a dead primary reading link block
   publication? Proposed: yes (§3).
9. **Launch shape:** web edition first, or wait for release firmware (§13)?
10. **Launch date and runway target:** 8 or 12 weeks.

---

## 15. Repository and code organisation (recommendation)

**Recommended: a new private repo `archievalmariano/dogear`**, split from this
workspace's `dogear/` with its history kept (`git subtree split`), holding:

```
dogear/            package (selector, issue, web, cli; + publish/, runway, coverage)
data/              dataset + affinity (monthly files later)
tests/             unit tests (+ publish pipeline, manifest, window authority)
mock/              render/fitcheck, with the device's Noto TTFs vendored (OFL)
hosting/dogear-site/        Pages advanced-mode Worker + tests
hosting/dogear-scheduler/   cron → workflow_dispatch Worker + tests
hosting/staging/            pinned Wrangler for staging (S1 reuses GOTO's installed 4.138.0)
fixtures/                   synthetic staging fixture
.github/workflows/          dogear-stage.yml, dogear-promote.yml, dogear-correct.yml
```

**Why:**
- GitHub runs scheduled and dispatched workflows from the default branch.
  Keeping DOGEAR in `project-goto` would mean merging it into GOTO's live
  `main`, sharing its Actions, secrets and CI with a production pipeline.
- A separate repo gives DOGEAR its own PAT scope, secrets, Environments and
  failure alerts, so neither project can break the other.

**What doesn't move:**
- Firmware stays in `crosspoint-reader` (`feature/dogear-prototype`).
- `tools/embed_issue.py` keeps reading the issue JSON.
- The cross-repo contract is documented in one place: manifest v1, issue
  schema v2 and the `DogearLimits.h` caps, with a test on each side.

**Alternative:** keep everything in `project-goto` with the DOGEAR workflows
merged to `main`. That's less setup but couples the two projects.

**Your decision either way.** Creating the repo is part of G7, not S1.

---

## 16. Codex review

**Review 1 (7 October 2026): conditional go for S1 with synthetic content.**
Publishing code may proceed locally once findings 1–5 are resolved in the
design and its acceptance tests. Production publication is not yet approved.

| # | Finding | Resolution in this revision |
|---|---|---|
| 1 | Uploaded web files became public before promotion; corrections overwrote the page before the device pointer; rollback couldn't restore the HTML | Web revisions are immutable and content-addressed (`web/<monday>/<sha16>.html`). The registry `publication.json` is the single boundary for web and device. The Worker serves only registered revisions, so an object's existence publishes nothing (§5–§6). |
| 2 | Ambiguous pointer writes; blind re-PUT could undo a newer correction; no recovery if the history push fails | Pending transaction recorded before the switch; conditional write against the expected ETag; ambiguous outcomes resolved by reading back; finalize exactly once; reconciliation at the start of every run; missing vs corrupt registry distinguished (§6). |
| 3 | Catch-up could undo corrections and rollbacks; week boundary; stage-only-W+1 exceptions | Separate transitions with preconditions: promote no-ops on any published revision; correct needs `--expect-rev`; rollback sets `hold`, cleared only by `resume`; week rechecked before the write; two explicit current-week exceptions (§4). |
| 4 | The independent approval check needed an artifact-to-record contract | Private `provenance.json` for every rendered record, bound to the artifact hashes; an independent check of the full `publishable` predicate plus fingerprint equality; content bound by regenerating from frozen inputs; proof and production artifact sets kept apart; `occurs` joins the fingerprint (§3). |
| 5 | The Friday final-QR check conflicted with unpublished-page routing | DOGEAR's own URL is validated locally and checked publicly only after the commit; external links are checked on Friday (§3). |
| 6 | S1 acceptance evidence was too strong | Synthetic, previously uncached issue; offline reopen after sleep proves persistence; exact hashes recorded; the claims narrowed, and NTP explicitly not claimed (§10–§11). |
| 7 | Certificate compatibility needs ongoing checks | G9 on the exact production host and firmware; a weekly chain check with only the device's roots; an ISRG Root YE decision before release (§8). |

Also adopted: the restage exception is stated plainly (§3); Function
responses set their own headers, with no caching of unpublished 404s or the
registry (§5); and noindex is described as labelling, not access control
(§8).

**Review 2 (7 October 2026): proceed with S1.** Two state-machine details,
now corrected in §4 and §6:
- **Resume after a rollback:** `restore` puts an explicit published revision
  back; `resume` only clears the hold.
- **Ambiguous-write retries:** every attempt re-runs all preconditions,
  including the Manila-week check.

Codex's characterisation, adopted: findings 1–7 are **substantially
addressed in the design, awaiting implementation verification**, not
closed.

**S1 execution note:** Wrangler 4.138's first `pages project create`, run
from `hosting/goto-scheduler/`, was silently redirected to a Workers deploy
because it detected an AI agent. It uploaded a copy of `goto-scheduler`'s
code as a Worker named `dogear-staging`:
- no secrets, and its cron update was rejected, so it could trigger nothing;
- found from the deploy log, deleted with approval, deletion confirmed;
- GOTO's resources weren't touched.

S1 was then created with `--force` (Wrangler's documented opt-out) from an
empty directory. **Rule for every later Cloudflare step:** run Wrangler from
a directory with no config of its own, pass `--force` to `pages project
create`, and check the API calls in the deploy log.

**Next Codex review:** these design changes, with the acceptance tests, at
the start of implementation. S1 itself needs no further review: it's static
synthetic content plus two dev firmware builds.

## 17. For review again before public launch

1. The implementation diff:
   - publish pipeline and CLI;
   - Worker and scheduler;
   - workflows, Environments and secret scopes;
   - the link checker.
2. S2 evidence: a full staged cycle including the failure drills in §12.
3. Security:
   - no open publish trigger;
   - PAT scoped to the DOGEAR repo with Actions access only;
   - R2 keys scoped to one bucket each;
   - no secrets in logs or URLs.
4. Release firmware: the DOGEAR release env without dev-only apps, SHA-384/P-384 in release,
   and a regression check of GOTO/ON POINT/OTA on both devices.
5. **Findings 7–8** (final release review, as agreed).
6. A record-approval audit: every approved record's fingerprint matches, and
   no held record is approved.
7. The production TLS chain against `DogearTrustRoots.h`, the weekly chain
   check, the ISRG Root YE decision, and the G9 soft-launch evidence.
8. Transaction drills in S2:
   - an ambiguous write in both directions;
   - a 412;
   - a history-push failure followed by reconciliation;
   - a correction then catch-up (no undo);
   - a rollback with `hold` then catch-up (no undo);
   - a run delayed across a week boundary.

---

## 18. Implementation status: the local publisher (for review before S2)

Built under `dogear/publish/` (Python stdlib; no network in tests) and exposed
as `python3 -m dogear.cli publish …`. **Nothing deployed.** No R2, Worker,
workflow, scheduler, pushes, record approvals or firmware changes.

| Module | Role |
|---|---|
| `policy.py` | Editorial settings: slot-5 bar, sparse week, empty week. `data/publication-policy.json` holds all three as **null (undecided)**, and production refuses until each is set. Tests use an explicit `TEST_POLICY`. |
| `store.py` | Storage interface with conditional writes. `MemoryStore` injects faults: fail, lost write, applied-then-timeout, concurrent writer. `DirStore` is a local stand-in for the bucket. |
| `registry.py` | Strict schema for `publication.json`; the device manifest derived from it; pure transitions: launch, promote, correct, rollback, restore, resume. |
| `pubdata.py` | Staged weeks, `pending.json`, exactly-once `history.jsonl`, `unverified.json` and append-only `verified.jsonl`, mirrors; `git_committer` for production. |
| `eligibility.py` | The independent check: full `publishable` predicate, approval fingerprint, provenance fingerprint, digest/issue correspondence, no proofs. |
| `staging.py` | Frozen-input generation, provenance, and every §3 check. Content binding regenerates and compares bytes. |
| `publisher.py` | Transactions, reconciliation, the seven operations, operator `abandon`. |
| `routes.py` | Reference router the S2 Worker must match: registry-only publication, 404 `no-store`, 301/302 rules. |
| `checks.py` | Production link checker (urllib) and fit checker (the device-font mock, on rendered records only). Not exercised by tests. |
| `cli.py` | Exit codes 0 / 2 aborted / 3 needs human / 4 not ready (also a test-only flag in production) / 5 published-unrecorded / 6 refused / 7 stage refused / 8 published but not yet served. |

**Selector change:** `select_issue(..., slot_bars=)` makes the slot-5 bar a
policy input. The default equals the old constant, so all four samples
regenerate byte-identically.

**Tests:** `tests/test_publish.py`, 71 tests, on Python 3.14 and 3.9 (196 in
total with `test_dogear.py`). Scenario map:

| Required by review | Test |
|---|---|
| Incomplete upload | `test_failed_upload_publishes_nothing_and_the_next_run_succeeds` |
| Ambiguous write, applied | `test_write_that_applied_but_timed_out_is_confirmed_by_reading_back` |
| Ambiguous write, lost (retry after rechecks) | `test_lost_write_is_retried_after_rechecking` |
| Conditional-write conflict | `test_conflicting_writer_is_never_overwritten`; in doubt: `test_concurrent_change_while_in_doubt_needs_a_human` |
| Failed history save after the switch | `test_failed_monday_then_catch_up_then_unsaved_history_then_recorded_once` |
| Failed pending save before the switch | `test_failed_pending_save_switches_nothing` |
| Correction, then catch-up | `test_correction_then_catch_up_never_reverts_it` |
| Rollback, then restore / resume | `test_rollback_holds_catch_up_until_restore_or_resume`; `test_resume_only_lifts_the_hold_and_waits_for_the_next_week`; `test_revision_rollback_within_a_week` |
| Corrupt state | `test_corrupt_registries_are_rejected`; `test_corrupt_state_stops_the_publisher`; `test_missing_or_corrupt_registry` (router) |
| Retry crossing a Manila week boundary | `test_retry_across_the_week_boundary_publishes_nothing` |
| **Whole recovery sequence across runs** | `test_failed_monday_then_catch_up_then_unsaved_history_then_recorded_once`. Every Monday write is lost, then the backup can't read storage, then catch-up reconciles and publishes but the history save fails, then the next day records it exactly once, and the following week's history includes it. |
| Undecided policy refused in production | `test_production_refuses_while_policy_or_checks_are_unset`; `test_the_committed_policy_file_is_undecided`; `test_unset_policy_refuses_staging_in_any_mode`; CLI exit 4 |
| Eligibility and proofs | `test_only_publishable_records_reach_production`; `test_independent_check_rejects_tampered_provenance`; `test_stale_approval_is_ineligible` |
| Content binding | `test_non_reproducible_output_is_refused` |
| Own URL not fetched | `test_own_url_is_validated_locally_not_fetched` |

**Mutation check.** Thirteen safety mechanisms were disabled one at a time,
and each made at least one test fail:
- week recheck, conditional write, reconcile-first;
- published-week no-op, hold, staged re-check;
- exactly-once history, provenance fingerprint, policy refusal at stage and
  in production;
- content binding, and the router's two registry gates.

The approval-fingerprint and `publishable` checks are deliberately
redundant: either alone is covered by the other, and disabling both fails.

**Choices made in implementation:**
- **A 412 is definitive.** The publisher closes its own pending record at once
  (the write certainly didn't apply) unless a read-back shows our registry
  is live.
- **Unknown leaves things pending.** When the registry is unreadable, or
  changed by someone else while our write was in doubt, the pending record
  stays for reconciliation or a human. After inspection, the operator closes
  it with `publish abandon <txn>`.
- **Promote checks cheaply first.** "Already published" and "on hold" are
  checked before any staging work, so routine catch-ups touch nothing.
- **A correction is a fresh generation.** Its `generatedAt` is the time of the
  correction, so the bytes always change and devices re-download.
- **Fit check scope:** the production fit checker runs only on the records
  rendered that week, so an unrelated record can't block a week.
- **Launch timing:** launch refuses between Monday 00:00 and 06:00 Manila
  (before rollover).
- **Indexing:** the router sends `X-Robots-Tag: noindex` until that editorial
  decision is made.

**Known gaps (S2 or later):**
- an R2 store adapter (S3 API with `If-Match` / `If-None-Match`);
- the JavaScript Worker and shared test vectors with `routes.py`;
- served-copy verification over real HTTP (the default verifies through the
  router against storage);
- `git_committer` against a real remote;
- the production link and fit checkers against the network;
- workflows and the scheduler;
- the weekly certificate-chain check.

**For Codex:** the diff of `dogear/publish/`, `tests/test_publish.py`, the
`select.py` parameter, and `data/publication-policy.json`.

### 18a. Codex implementation review 1 (of `599d720`): six findings, fixed

Each reproduced sequence is now a regression test in
`ReviewTwoRegressionTests`. All eight failed on `599d720` before the fixes.

| # | Finding | Fix | Regression tests |
|---|---|---|---|
| 1 (P1) | Promotion trusted the staged provenance; an ALSO THIS WEEK item could be dropped from it, and altered HTML with a matching hash was accepted | Staging keeps the **frozen inputs** (`dataset.json`, `affinity.json`) beside the artifacts. Promotion (`verify_staged`) regenerates the issue, the web page **and the complete provenance** from them and requires byte equality, then checks current eligibility for the regenerated (complete) rendered set. Any mismatch → re-stage with a CHANGED PROOF notice. | `test_1a_…also_item…`, `test_1b_altered_staged_html…` |
| 2 (P1) | Eligibility was checked before uploads, not at the write | An eligibility guard runs immediately before **every** registry-write attempt, retries included, for promote, launch and correct; a change aborts (nothing published; the next run re-stages). Rollback and restore check every revision they make visible (provenance on record, issue object intact, records still publishable). | `test_2_…withdrawn_during_publication…`, `test_2b_…during_a_correction…`, `test_rollback_to_a_week_with_a_withdrawn_record_is_refused` |
| 3 (P1) | A 412 after an unconfirmed attempt erased evidence of that attempt | `uncertain` is tracked across attempts. A 412 or a foreign registry after an unconfirmed attempt goes to `_unresolved`: if the registry carries our transaction's revision, record it as published (then stop for a human); otherwise keep the pending record and stop. Reconciliation uses the same trace rule. "Never published" is recorded only when the registry still has the ETag we planned from. | `test_3_retry_412_after_uncertain_write_keeps_the_evidence` |
| 4 (P2) | A stage that skipped checks could be promoted in production | In production, promotion **re-runs the link and fit checks** on the exact staged inputs, and refuses a stage whose validation was not production (→ re-stage under production validation). | `test_4_…`, `test_4b_checks_are_rerun…`, `test_4c_a_test_mode_stage_is_restaged…` |
| 5 (P2) | `--now` accepted in production | Refused outside `--mode test` (exit 4), like `--no-network-checks`. | `test_5_clock_override_is_refused_in_production` |
| 6 (P2) | Failed served verification still reported success and dropped the work | **Committed and verified are now separate.** The finalize records history and writes `unverified.json` in the same save. Verification then runs, and failure raises `PublishedUnverified` (exit 8). Every later run re-verifies first (read-only, never rewriting the registry) and stays non-success until the host serves the registry. Rollback, restore and resume still run while serving is broken, with a notice. | `test_6_unverified_serving_is_not_success_and_is_retried` |

**A stricter rule that follows from #2.** Rolling back to an earlier
revision is refused when that revision shows a record version that is no
longer the approved one. In practice that's after a correction that edited
and re-approved a record. The editor restores and re-approves the earlier
version first (`test_revision_rollback_within_a_week`). Emergency recourse
otherwise is a fresh correction.

**Mutation check, extended.** Eleven new safeguards were disabled one at a
time, and each made at least one test fail:
- regeneration at promotion;
- current eligibility at promotion;
- the promote and correct write guards;
- 412-after-uncertainty;
- the production-stage requirement;
- the check re-run at promotion;
- the `--now` refusal;
- unverified-as-failure;
- the unverified retry;
- the rollback target check.

Finding 4's two safeguards are covered separately by 4b and 4c.

### 18b. Codex re-review (of `613c1ce`): findings 1–5 fixed; finding 6 completed

Codex confirmed findings 1–5 fixed and found finding 6 incomplete in three
P2 cases. Each is now a regression test in `ReviewThreeRegressionTests`; all
four tests failed on `613c1ce`.

| Case | Fix | Test |
|---|---|---|
| Verification checked only `/current.json` and the current week's page, so a device issue served 404, or a corrected *past* week still serving its old page, passed | Each transaction records its own **verification targets** with expected SHA-256 (`verification_targets`). The targets are the device manifest, plus the page **and** device issue of every week whose visible revision the change altered (including a past week's correction, or a rollback's newly current week). `unverified.json` holds one entry per transaction. An entry clears only when each target is served with the expected bytes, or is **superseded**: a later change replaced that manifest, or that week's active page. Device issue objects are never superseded. | `test_device_issue_not_served_is_unverified`, `test_correcting_a_past_week_verifies_that_weeks_page` |
| An outstanding verification failure blocked the fresh correction that is the recovery for a broken revision | `correct` settles pending transactions, then proceeds even while earlier served-output checks fail, like rollback, restore and resume. All approval guards and the conditional write are unchanged. Its own commit is still verified, and the superseded revision's targets clear. | `test_fresh_correction_proceeds_while_an_earlier_revision_fails_verification` |
| A malformed `unverified.json` (e.g. `null`) read as "nothing outstanding" | `read_unverified` validates the exact structure (entries, txn, since, typed targets with SHA-256). Only an **absent** file means nothing is outstanding; anything else is `CorruptState` and stops the publisher. | `test_malformed_verification_state_stops_the_publisher` (nine malformed variants) |

**Mutation check:** six more safeguards disabled one at a time, and each made
at least one test fail:
- issue targets;
- targets for changed weeks;
- page supersession, both too eager and never applied;
- correction during an unverified state;
- malformed-as-absent.

Kept as Codex agreed: the strict rollback rule, with no override.

### 18c. Codex re-review (of `b66e214`): finding 6, the last gap

Codex confirmed the per-target checks and the correction path. The remaining
gap: `unverified.json` was trusted as its own record. A well-formed entry
with a target removed (the issue) or a page target's week changed passed
validation. The next run then cleared it as served or superseded while the
issue still returned 404 or the page was still broken.

**Fix: an independent record of what each transaction must verify.**
- **History keeps the targets.** Each committed `history.jsonl` line now
  carries `verify`, the transaction's target list, written exactly once in
  the same save as its `unverified.json` entry. (Finalizing originally
  checked only the targets present against the registry; §18d replaces that
  with an exact recomputation.)
- **Settled is recorded too.** `verified.jsonl` is append-only, one line per
  transaction whose serving was settled (served or superseded). It is
  written before the entry leaves `unverified.json`. If a settle is
  interrupted between the two writes, the settled record wins.
- **Every run accounts for every committed transaction** before checking or
  clearing anything (`_outstanding`). Each one must be either outstanding,
  with targets **exactly equal** to its history copy, or settled, never
  missing. Any of these is `CorruptState` (exit 3), never read as "nothing
  to verify":
  - an entry naming an unknown transaction;
  - altered targets;
  - a deleted entry or file;
  - a settled line for a transaction history doesn't have;
  - a history line without valid targets.
- **Fixed target shape** (`targets_problem`), checked on both copies:
  - the manifest first;
  - then a page and its issue for each week, weeks distinct, ascending and
    each a Monday;
  - the page path is that week;
  - the issue path names that week and its own hash prefix.

| Case | Test (`ReviewFourRegressionTests`) |
|---|---|
| Issue target omitted (Codex's reproduction) | `test_omitted_issue_target_is_rejected` |
| A whole week omitted (well-formed: manifest only) | `test_omitted_week_is_rejected` |
| Page target's week changed (Codex's reproduction) | `test_page_target_with_the_wrong_week_is_rejected` |
| Page and issue moved consistently to another real week | `test_consistent_but_wrong_week_is_rejected` |
| Entry or file deleted | `test_missing_verification_record_is_rejected` |
| History copy altered or removed | `test_altered_history_copy_is_rejected` |
| Settled record removed or invented; the genuine path still settles | `test_settled_transactions_are_recorded_and_the_genuine_record_still_settles` |
| Entry for an unknown transaction | `test_entry_for_an_unknown_transaction_is_rejected` |
| Pending targets that disagree with the registry | `test_pending_targets_that_disagree_with_its_registry_are_not_recorded` |
| Target shape (ten malformed lists) | `test_target_shape_is_fixed` |

The first seven tests fail on `b66e214`. Five fail on their assertion (the
old code accepted the tampered record and cleared it). Two error, because
`b66e214` has no history copy and no settled record.

**Mutation check:** nine more safeguards were disabled one at a time, and
each made at least one test fail:
- the history comparison;
- the missing-record check;
- the history-line shape check;
- the settled-but-uncommitted check;
- the unknown-transaction check;
- finalize agreement (superseded by §18d);
- the history keeping the targets;
- settle appending to `verified.jsonl`;
- the page week/path shape rule.

**Limits.** This defends against a damaged or incomplete record, not an
operator who rewrites both history and `unverified.json` consistently. The
private publication-data repository's git log is the audit trail for that.

### 18d. Codex re-review (of `42f72fd`): finding 6, the recovery path

Codex confirmed both §18c cases now stop the publisher, and found one more
path to the same failure. Interrupt a publish after the registry switch, so
its record isn't saved, then cut the saved `pending.json` target list down
to the manifest. Recovery then copied that incomplete list into history and
the outstanding entry, cleared it, and returned noop while the issue URL
still returned 404. The §18c agreement check only looked at the targets that
were present, so it couldn't see that a page and issue were missing.

**Fix: finalizing recomputes the complete list instead of trusting it**
(`_expected_targets_problem`).
- The pending record now also keeps the **registry it replaces**
  (`expectedRegistry`, the exact stored bytes), next to the hash it already
  recorded (`expectedRegistrySha`).
- Before recording anything, finalization:
  - checks the previous registry against its hash, and the published
    registry against `intendedRegistrySha`;
  - parses both strictly;
  - recomputes `verification_targets(previous, published)`;
  - requires `pending["verify"]` to **equal** that list.
- A missing, extra or altered target is a reason to stop, not a smaller job.
  So is a previous registry that's missing while an ETag was recorded, or
  one that doesn't match its hash.
- Any of these is "needs a human" (exit 3). The pending record is kept, and
  nothing is written to history, `unverified.json` or `verified.jsonl`. That
  holds whichever path reaches finalization: `promote`, `reconcile` or
  `publish abandon`.

| Case | Test (`ReviewFiveRegressionTests`) |
|---|---|
| Page and issue removed from pending after the switch (Codex's reproduction); the genuine record still recovers once and stays unverified until the issue is served | `test_pending_without_its_page_and_issue_stops_recovery` |
| An extra, already-served week added; the issue's hash altered | `test_pending_with_an_extra_or_altered_target_stops_recovery` |
| Previous registry edited, or dropped | `test_pending_with_an_altered_previous_registry_stops_recovery` |
| Coordinated edits that keep `verify` consistent with an altered previous or published registry (only the recorded hashes reveal them) | `test_coordinated_edits_are_caught_by_the_recorded_hashes` |
| A rollback's recovery recomputes the newly current week; a cut-down list stops it, the genuine one records | `test_rollback_recovery_recomputes_the_newly_current_week` |

The first four tests fail on `42f72fd`. Codex's reproduction and the rollback
case fail their assertion. The extra-target case is accepted and only fails
later, at served verification. The previous-registry case errors, because
`42f72fd` doesn't keep that registry.

**Mutation check:** six more safeguards were disabled one at a time, and each
made at least one test fail:
- the exact comparison;
- the previous registry's hash;
- requiring the previous registry when an ETag was recorded;
- the published registry's hash;
- pending keeping the previous registry;
- calling the check at finalization.

The earlier §18c mutations still fail tests.

**Limits (unchanged in kind).** Someone who rewrites a pending record and
recomputes its own hashes defeats any check made from that record alone. On
the normal path, the published registry is also bound to live storage:
finalization from `reconcile` requires that storage hold exactly
`intendedRegistrySha`. The publication-data repository's git log remains the
audit trail.


## 19. S2 implementation (local; nothing created or deployed)

Codex closed finding 6 at `a164084` and gave a go for S2 implementation
development, **not** for creating infrastructure or deploying. Everything
below is code and tests in this workspace.

| Piece | Where | Evidence |
|---|---|---|
| Site Worker (Pages advanced mode) | `hosting/dogear-site/` | 224 shared vectors written from the Python reference router (`tools/route_vectors.py`) and run against the Worker: status, every header, body. Includes 26 corrupt registries (the Worker also rejects non-integer number literals, which JavaScript cannot otherwise tell from integers), unregistered objects, a registered object missing from storage, redirects, fonts, methods. 229 node tests. |
| Reference router additions | `routes.respond`, font `Content-Type` | The whole response, body included, is now what the vectors pin. |
| R2 store | `dogear/publish/r2.py` | Stdlib SigV4, checked against AWS's two published examples. `If-Match` (quoted ETag) and `If-None-Match: *`, which R2 supports on PutObject. 412 is `PreconditionFailed`; every other failure, including a dropped connection after the write applied, is unknown and resolved by reading back. Credentials only in the signature, never in errors or `repr`. A fake R2 checks every signature; the unchanged publisher launches and promotes through it. |
| Served verification over HTTPS | `checks.http_served` | Used whenever the store is R2. A cache-buster on each read, no redirects followed, read-only. Tested against a local stand-in Worker. |
| Fixed targets | `publish --target staging\|production` | Sets store, host, mode, dataset and policy together; refuses those flags beside it. Staging = `dogear-issues-staging`, `dogear-staging.pages.dev`, test mode, the S2 fixture and a staging-only policy. Production = `dogear-issues`, the custom host, production mode, the canonical dataset and the still-undecided policy (so it refuses). |
| S2 fixture | `fixtures/staging-s2.json` (generated by `tools/staging_s2_fixture.py`) | 15 synthetic records, three per week from 1 March 2027. Approvals are **fixture-only** (`synthetic-fixture-not-editorial`), because the publisher's eligibility check requires approval in every mode; no canonical record is touched. Links go to `https://example.org/` (its sub-paths answer 404, so S1's fixture links would fail the real link checker). All stage checks pass, the real link and fit checkers included. |
| Drills | `publish --drill conflict\|lost\|applied-then-drop\|unsaved-record` (test mode only) | One injected event around the real registry write: a real concurrent write so storage itself answers 412; a write lost before sending; a write that applies and loses its answer; a failed save after the switch. Tested end to end (CLI → fake R2 → stand-in Worker → HTTPS-style verification, publication data in git with a fresh clone per run) alongside a full stage → promote → correct → rollback (hold) → held catch-up → restore cycle. |
| Git publication data | `git_committer` | Against a real (local bare) remote: one pushed commit per save, a rejected push is `SaveFailed` with the remote unchanged, and the publisher run from fresh clones. |
| Workflows | `.github/workflows/` (inert in this repository) | One reusable job (`environment` and `concurrency` per target, never cancelled in flight) and its callers. Inputs reach the publisher only as environment variables through `tools/run_publish.py`, which validates each and passes it as one argument. A test fails on any `${{ }}` inside a shell command. Scheduled runs act only when `PUBLICATION_ENABLED == 'true'`. |
| Scheduler | `hosting/dogear-scheduler/` | Fri 10:02/10:17, Mon 06:02/06:17 and daily 06:32 PHT, with the Sunday-UTC trap tested. Deployed defaults are inert and staging; dispatch carries an explicit `target`; the HTTP endpoint never dispatches. 7 node tests. |
| Chain check | `tools/chain_check.py`, weekly workflow | Verifies each host with **only** the device's roots (`data/dogear-trust-roots.pem`, a tested copy of `DogearTrustRoots.h` whose seven DER hashes match the header's comments). Alerts on failure or a leaf within 21 days of expiry. Tested with throwaway local certificates. |

**Observed while building (7 October 2026):** `dogear-staging.pages.dev` now
presents a **Let's Encrypt chain anchored at ISRG Root X2** (four
certificates); at S1 it was GTS WE1 → GTS Root R4. It still verifies with the
device roots. This is the rotation §8 warned about, and S2's device fetches
will exercise the X2 path. `goto.archievalmariano.com` is still on GTS R4.

**Not covered by S2 drills:** a run delayed across the Monday boundary needs
the clock to move mid-run. It stays covered by the unit test
(`test_retry_across_the_week_boundary_publishes_nothing`).

### 19a. Proposed S2 staging setup (for approval; nothing done)

Minimum resources, staging only. No GitHub repository, workflow, scheduler,
production resource, DNS change or record approval.

1. **R2 bucket `dogear-issues-staging`**, created with Wrangler 4.138.0 from
   an empty directory, with the log checked.
2. **An R2 API token scoped to that one bucket** (Object Read & Write). The
   user creates it in the dashboard and saves the three values to
   a private env file on the operator's machine (mode 600). Runs source that file and
   never print it. Alternatively the user runs the drill commands.
3. **Redeploy `dogear-staging` in advanced mode** with the `ISSUES` binding,
   from `hosting/dogear-site/staging/`, then read the deploy log. This
   replaces S1's static files: until the launch drill, `/current.json` answers
   503 and devices show their card copy (`CACHED`).
4. **Publication data:** a local bare git repository outside the workspace
   (a local bare repository), cloned fresh
   for each run, so the unsaved-record drill is faithful.

**Drills** (from the Mac, `--mode test`, the S2 fixture, `--now` setting the
Manila clock), in order:
- launch (week of 1 March 2027);
- stage-next;
- promote with each drill: conflict (412, then catch-up publishes), lost,
  applied-then-drop, and unsaved-record (then reconcile records it once);
- correct;
- rollback to the previous week (hold), then held catch-up (no-op), restore,
  and resume;
- a skipped Monday caught up by the daily run (current week only);
- a week missed entirely stays 404.

After each step, the served `/current.json` and page are checked over HTTPS,
and the chain check is run.

**Devices:** once the cycle ends, the X4 Pro and the X4 fetch the final
revision from staging over the new ISRG X2 chain, then reopen offline
(`CACHED`). No reflash is needed: both already run the staging builds.

**Undo:** empty and delete the bucket and revoke the token, then redeploy S1's
static `site-staging/` if wanted.

### 19b. Codex review of the S2 implementation (`a164084..eda9d9b`): follow-ups fixed

Codex reran every suite, confirmed the R2 assumptions against Cloudflare's
documentation (conditional PutObject, strongly consistent reads, Pages R2
bindings, bucket-scoped tokens) and the live staging chain through ISRG Root
X2, and raised four follow-ups. All four are fixed:

| When needed | Finding | Fix | Tests |
|---|---|---|---|
| **Before the first live S2 write** | `--target` still honoured `DOGEAR_R2_ENDPOINT`, so a staging bucket could be paired with any HTTPS host and unpublished uploads sent there | Any endpoint override must be a local test server (127.0.0.1, localhost, ::1). With `--target` an override is refused outright (exit 4, before any request); only an explicit `--store r2:` local test may use one | `test_endpoint_override_only_for_a_local_server_and_only_when_allowed`, `test_target_refuses_an_endpoint_override_before_any_request` |
| Before real-record production | The issue route served every registered revision, contradicting §7: a correction that withdraws disputed content left its old issue URL live | Python router and Worker route **only each week's active revision**. Verification now settles an issue target whose revision is no longer active (superseded), as for pages. Vectors regenerated (revision 0 of a corrected week is 404). Copies already cached by readers or devices cannot be recalled | `ActiveIssueOnlyTests` (withdrawn by a correction, back after a rollback; an unserved issue settles once superseded); vectors; disabling either half fails tests |
| Before real-record production | The link check accepted a HEAD 200 without a GET, and followed redirects from HTTPS to HTTP | GET only (read at most 64 KB); a redirect leaving HTTPS blocks the stage | `tests/test_checks.py`: HEAD 200 / GET 404 blocks, 410 blocks, 503 and 403 warn, a redirect to HTTP blocks |
| Before the repository split | `PUBLICATION_DATA_REPO` must be `owner/repo` for `actions/checkout`; the comment gave bare names | Comment corrected. A first step checks the value (`owner/dogear-publication-data[-staging]`, matching the target) before any checkout | The workflow's own check script, run in a test against good and bad values |


### 19c. S2 staging trial (7 October 2026): PASS (server and both devices); G6 accepted by Codex

Approved by the user. Resources created:
- R2 bucket `dogear-issues-staging` (APAC). Wrangler's log shows one write,
  `POST r2/buckets`.
- The user's Account API token **`dogear-staging-publisher`**: Object Read &
  Write on that bucket only, kept in a private env file on the operator's machine
  (mode 600, never printed).
- `dogear-staging` redeployed in advanced mode from `f831bc7`, binding
  `ISSUES` → `dogear-issues-staging`. Pages API calls only, none to
  `workers/scripts`; deployment `6a47a8ed`.
- Publication data in a local bare repository
  (a local bare repository), cloned fresh for every run.

Every run used `publish --target staging --git --now <Manila time>`. Each
publishing run verified the live host over HTTPS before reporting success.

| Step | Manila time (2027) | Result |
|---|---|---|
| launch | Wed 3 Mar 10:00 | week of 1 Mar published, verified |
| stage-next | Fri 5 Mar 10:02 | 8 Mar staged (real link and fit checks) |
| promote, **drill `conflict`** | Mon 8 Mar 06:02 | **R2 answered 412**; aborted, nothing published; served manifest unchanged (1 Mar) |
| catch-up promote | Mon 8 Mar 06:32 | 8 Mar published from the staged week |
| stage-next | Fri 12 Mar 10:02 | 15 Mar staged |
| promote, **drill `lost`** | Mon 15 Mar 06:02 | retried after re-running the guards; published once |
| correct, **drill `unsaved-record`** | Mon 15 Mar 09:00 | live as revision 1, exit 5, `pending.json` kept in the remote |
| reconcile | Mon 15 Mar 09:30 | recorded once ("had been published"), verified. Revision 0's issue URL now 404, revision 1's 200 (active-only routing on the live Worker) |
| rollback to 8 Mar | Mon 15 Mar 10:00 | current 8 Mar, hold set |
| held catch-up | Tue 16 Mar 06:32 | no-op (15 Mar already published, checked before the hold) |
| restore 15 Mar rev 1 | Tue 16 Mar 07:00 | current 15 Mar rev 1, hold cleared |
| rollback, then **resume** | Tue 16 Mar 08:00, 08:30 | hold cleared; current stays 8 Mar |
| catch-up | Wed 17 Mar 06:32 | no-op: 15 Mar is in the registry, so resume does not republish it |
| week of 22 Mar | no runs | **missed week**: `/2027-03-22/` and days in it are 404 |
| Mon 29 Mar 06:02 | dropped (not run) | — |
| catch-up promote, **drill `applied-then-drop`** | Tue 30 Mar 06:32 | 29 Mar staged inline and published; the dropped answer resolved by reading back |

**End state**, checked from the git remote and the live host:
- **History:** nine committed transactions plus the one aborted 412, each
  recorded once. `verified.jsonl` has nine lines; nothing is pending or
  unverified.
- **Served:** `/current.json` gives 29 Mar rev 0.
  - The issue's served bytes hash to the manifest's `issueSha256`
    (`faecd828…`).
  - Headers: `immutable`, `nosniff`, `noindex`, Content-Length.
  - `/` returns 302 to `/2027-03-29/`; a mid-week day returns 301.
  - `/current.json` has `no-store` (HEAD too).
- **Chain check:** OK, four certificates anchored at ISRG Root X2.

**Not exercised on real storage:**
- the hold refusing an *unpublished* week (unit-tested:
  `test_rollback_holds_catch_up_until_restore_or_resume`);
- a run crossing the Monday boundary (unit-tested).

**Devices (user-tested, 7 October 2026): PASS on both.** The X4 Pro and the
X4, on their existing staging builds (no reflash), each:
- fetched the 29 March 2027 issue from the R2-backed Worker. That was the
  first device fetch over the **ISRG Root X2** chain; S1 had used GTS R4;
- reopened offline (Back on the Wi-Fi screen) to the same issue marked
  `CACHED`.

No serial log was captured for this pass.

**G6 status:** the implementation and the S2 pipeline cycle are done. The
gate passes once Codex has reviewed this evidence. Resources stay up until
then, in case Codex wants a repeat drill. Afterwards: revoke
`dogear-staging-publisher`, then empty and delete the bucket, unless it's
kept for later staging work.

**Codex, G6 review (at `2a2ba9e`): accepted.**
- Codex checked every committed transaction against the local history and
  the live host, and found each verified exactly once.
- It reran the hold and week-boundary unit tests and the drill cycle.
- Those two cases remain unit-test evidence, not real-storage evidence.
- The device acceptance rests on the user's observations, which is enough
  for staging but doesn't replace G9.

**Observation: Cloudflare returns 403 to Python's default User-Agent.**
`Python-urllib/3.x` is refused on `dogear-staging.pages.dev` and also on
GOTO's live `goto.archievalmariano.com`, so it's an account-level rule.
These get 200:
- no User-Agent, curl, or a Safari User-Agent;
- the publisher's own User-Agent;
- the device's `CrossPoint-ESP32-…`.

It didn't affect the publisher or the devices. The production smoke test
covers ordinary clients (§20, step 10).

## 20. G7: repositories and production resources (local preparation done; external steps gated)

G7 creates the production resources and moves DOGEAR to its own repository.
It needs the user's separate authorization; several steps are the user's own.
It **does not publish anything**, enable schedules, deploy the scheduler or
touch firmware:
- the first production publication is G9;
- schedules and the scheduler are G10;
- release firmware is G8.

Production keeps refusing anyway while `data/publication-policy.json` is
undecided.

### 20a. Local preparation (no resources; can be done now)

1. **Vendor the device fonts** into `mock/fonts/`: NotoSans and NotoSerif,
   four TTFs each plus `OFL.txt`, 4.4 MB. Then the fit check doesn't need the
   firmware checkout.
2. **Make the split repository self-contained.** Three things reach outside
   `dogear/` today:
   - the fit fonts (item 1);
   - the `DogearLimits.h` contract test, which already skips when the header
     is absent;
   - the trust-root sync test, which skips likewise.

   Copy the firmware contract values into a small vendored file, so the
   limits test still runs after the split.
3. **Credentials for the publication data: deploy keys**, not a PAT.
   - Each data repository gets one SSH deploy key with write access to that
     repository only.
   - The workflow checks out with `ssh-key:` instead of `token:`, and the
     publisher's push uses the same key.
   - A fine-grained PAT would also work but expires, and spans more than one
     repository unless scoped per environment.
4. **Add a test workflow** (`dogear-ci.yml`) running the Python, site and
   scheduler tests on every push. It has no secrets and no environment.
5. **Split locally and prove it.**
   - Run `git subtree split --prefix=dogear` from `feature/dogear-e0`, which
     keeps the 27 commits that touch DOGEAR.
   - Clone the result into a scratch directory and run every suite there.
   - The history scan (keys, tokens, private keys, the Cloudflare account
     ID) is already clean.

### 20b. Creating the resources (each step needs the owner's authorization)

Four repositories (§20d): **public** `dogear`; **private** `dogear-editorial`,
`dogear-publication-data` and `dogear-publication-data-staging`. Three
Environments on `dogear`: `staging`, `editorial-gate` and `production`.

| # | Step | Who |
|---|---|---|
| 1 | Create the four repositories, empty: `archievalmariano/dogear` **public**; `dogear-editorial`, `dogear-publication-data` and `dogear-publication-data-staging` **private** | Claude with `gh`, or the owner |
| 2 | Protect `main` on all four (rulesets: no force pushes, no deletion). On `dogear`, require approval for workflows from fork pull requests by outside collaborators | Claude with `gh` |
| 3 | Create the Environments **before** any workflow names them (a missing one is created unprotected). `staging`: deploys from `main`. `editorial-gate`: deploys from `main`, no reviewer. `production`: deploys from `main`, the owner as required reviewer, with "prevent self-review" **off** (the owner is the only reviewer and the one who dispatches). Repository variables `PUBLICATION_ENABLED=false`, `PUBLICATION_TARGET=staging`; `PUBLICATION_DATA_REPO` per Environment (full `owner/repo`) | Claude with `gh` |
| 4 | Push exactly: `dogear-split/dogear-public.git` (the one-commit public import) → `dogear`; `dogear-split/work/dogear-editorial` → `dogear-editorial`; `dogear-split/dogear-publication-data.git` → `dogear-publication-data`; the existing staging data repository (S2 history) → `dogear-publication-data-staging`. **Never push `dogear-split/dogear.git`**: it holds the unfiltered history with the dataset. The Mac stops writing staging data from then on | Claude, after the owner approves the exact commits |
| 5 | Deploy keys, each generated on the owner's machine (§20d): `dogear-editorial` **read-only**, its private half as `EDITORIAL_DEPLOY_KEY` in `editorial-gate` and `production`; each publication-data repository **write**, its private half as that Environment's `PUBLICATION_DATA_DEPLOY_KEY` | **the owner** (Claude gives the commands, never handles a private key) |
| 6 | Create R2 bucket `dogear-issues` with Wrangler from an empty directory, checking the log | Claude |
| 7 | R2 tokens: `dogear-production-publisher` (Object Read & Write, `dogear-issues` only) and `dogear-staging-ci` (the same on `dogear-issues-staging`), set as Environment secrets | **the owner** |
| 8 | Create the Pages project `dogear-site` with `--force` from an empty directory; deploy the Worker with `wrangler.jsonc` (`ISSUES` → `dogear-issues`), checking the API calls in the log | Claude |
| 9 | Custom domain `dogear.archievalmariano.com` on `dogear-site`; GoDaddy CNAME `dogear` → `dogear-site.pages.dev` | **the owner** |
| 10 | Check the production host (503 "nothing published"; everything else 404 `no-store`) and run the chain check on it | Claude |
| 11 | **Ordinary-client smoke test:** `/` and a 404 path with no User-Agent, curl, desktop and mobile browsers, the device's User-Agent, and `Python-urllib` (expected 403: recorded, not depended on). Add the production host to the weekly chain check | Claude |
| 12 | First live runs, **staging only**: `DOGEAR tests`, `DOGEAR operate` → `status`, then `promote`; `dogear-editorial`'s own CI | Claude dispatches; the owner watches |
| 13 | `DOGEAR operate` → `status` on **production**: the editorial gate runs first, then the owner approves | the owner approves |
| 14 | Once CI's staging credentials work, revoke the Mac's `dogear-staging-publisher` token | **the owner** |

### 20c. G7 local preparation: done (authorized 7 October 2026; nothing external)

> **Superseded by §20d.** This section records the first, single-repository
> preparation. Its `dogear-split/dogear.git` holds the unfiltered history,
> with the dataset, and **must never be pushed**. The repository map and push
> list are in §20b and §20d.

**Resulting repositories** (local):

| Local repository | Becomes | Contents |
|---|---|---|
| `dogear-split/dogear.git` | **never pushed** (superseded; the public repository comes from `dogear-public.git`) | `git subtree split --prefix=dogear` of `feature/dogear-e0`: the 31 commits that touch `dogear/` (of 76 on the branch), authors and messages kept. Its tree is identical to `dogear/` at the split point. Root: `.github/ README.md data/ docs/ dogear/ fixtures/ hosting/ mock/ samples/ tests/ tools/ web-assets/` |
| `dogear-split/dogear-publication-data.git` | `archievalmariano/dogear-publication-data` | one README commit; production has never published |
| `dogear-staging-data.git` (existing, unchanged) | `archievalmariano/dogear-publication-data-staging` | the S2 history (34 commits) |

**The staging data repository is the existing one, not a new one.** The live
staging bucket's registry names nine transactions. A fresh, empty history
beside it would make the publisher stop for a human ("history has no
committed record of …"). That's correct behaviour, but it would break the
rehearsal target. The history scan of the split (keys, tokens, private keys,
the Cloudflare account ID) is clean, and it has one author.

**Dependencies that were copied** (none was replaced; outputs are unchanged):

| Dependency on the firmware checkout | Now in the repository | Kept in step by |
|---|---|---|
| Noto TTFs for the fit check | `mock/fonts/{NotoSans,NotoSerif}/` with `OFL.txt` (4.4 MB) | byte-identity test |
| **Glyph ranges** from two generated font headers (`notoserif_14_regular.h`, `notoserif_18_bold.h`). The fit check's "glyph the device lacks" test needed these; the plan had missed them | `mock/fonts/device-glyphs.json` (1.3 KB) | equality test against the headers |
| `DogearLimits.h` | `data/firmware-contract.json` (all 11 constants, firmware `3d9d9e56`) | the Python limits are checked against it **always**; it's checked against the header when the firmware is present |
| `DogearTrustRoots.h` | `data/dogear-trust-roots.pem` (since S2) | as before |

Running the fit check on the canonical dataset and the S2 fixture gives
byte-identical output with the vendored files and with the firmware's. The
parity tests run only where the firmware checkout sits beside the
repository, i.e. on this Mac. In GitHub CI they skip, so a firmware change is
caught when `tools/firmware_contract.py --sync` and
`tools/chain_check.py --sync-roots` are run here.

**Fresh-clone results**, cloned into a scratch directory with no firmware
beside it:
- 247 Python tests on 3.14 and on 3.9: OK, 5 skipped. Four are firmware
  parity; one is "inert in project-goto", which in the standalone repository
  reports that its workflows are live.
- 229 site tests and 7 scheduler tests pass.
- Both datasets validate, the fit check passes from vendored files only, and
  the generated files are current.
- **Publication data, checked offline from fresh clones:** production holds
  no history and nothing pending. Staging holds 9 committed transactions and
  1 aborted, 9 settled, 0 outstanding, its mirror at 29 Mar rev 0, and the
  publisher's accounting check passes.

The first fresh-clone run found one test asserting the workflows are inert.
That's true inside `project-goto`, false by design in the standalone
repository. It was fixed and the split redone.

**CI** (`.github/workflows/dogear-ci.yml`) runs on every push and pull
request, with read-only contents and no secrets or Environment. Every
checkout has `persist-credentials: false`. Three jobs:
- **python**: the unit tests on 3.9 and 3.13 (loopback servers and fakes
  only);
- **dataset**: validate the canonical dataset and the S2 fixture, run the fit
  check with the vendored fonts (pinned `pillow==12.3.0`, `segno==1.6.6`),
  and confirm the route vectors and S2 fixture are current;
- **node**: the Worker and scheduler suites, with no `npm install`.

Python 3.9 on `ubuntu-latest` is unverified until the first run.

**Publication-data access: deploy keys, least privilege.**

| Repository | Credential | Access | Why |
|---|---|---|---|
| `dogear` (source) | **none**: the workflow's built-in token, `permissions: contents: read`, and `persist-credentials: false` | read | Nothing ever writes to the source repository from CI |
| `dogear-publication-data-staging` | one deploy key, used only by the `staging` Environment | **write** | see below |
| `dogear-publication-data` | one deploy key, used only by the `production` Environment (required reviewer, `main` only) | **write** | see below |

No repository needs a **read-only** deploy key. The only read-only consumers
are the CI and chain-check workflows, which read the source through the
built-in token and need no data at all. A read-only key would add a
credential with no user.

**Why write access is necessary, not convenient.** The publication
transaction (§6) is only safe because its record is durable before and after
the registry switch:
- **Before switching the registry,** the publisher must **push** `pending.json`.
  If that push fails, it aborts with nothing published.
- **After the switch,** it pushes the history line, the verification state
  and the registry mirror.
- **Every later run** is a fresh checkout. It finds a crashed or unsaved
  transaction, records it exactly once, and re-verifies serving only from
  what was pushed. Weekly selection reads the history of published weeks
  from there too.
- **Stage runs** push the frozen `staged/<week>/` inputs that promotion
  later re-verifies.

With read-only access, every publishing run would stop at the
pending-record push, by design before switching anything. Publication would
be impossible, not merely less convenient.

**Limits on that write access:**
- each key reaches one repository;
- the private half lives only in that Environment's secrets;
- production jobs wait for the user's approval;
- rulesets on `main` block force pushes and deletion, since deploy keys
  can't be limited by branch;
- the user generates the key pairs on their own machine, and Claude never
  sees a private key.

**Changed from the G7 plan:**
1. The glyph ranges also had to be vendored (above).
2. The firmware contract now covers all 11 limits, including the manifest
   and path sizes. The old skip-if-absent limits test was replaced by an
   always-run test plus parity.
3. The staging data repository comes from the existing S2 repository, not a
   new empty one.
4. The workflow secret is now `PUBLICATION_DATA_DEPLOY_KEY`
   (`PUBLICATION_DATA_TOKEN` is gone), and source checkouts keep no
   credentials.
5. The inertness test is adapted for the standalone repository.
6. Some docs inside the repository still give `project-goto` paths, such as
   the hardware-test commands. They stay accurate for this Mac and get
   updated after the push.
7. What happens to `project-goto`'s `dogear/` once the new repository is
   canonical stays the user's decision.

**Stopped here:** nothing has been created, pushed, provisioned or run
against staging or production.

### 20d. The public/private boundary (supersedes §20c's single repository)

GitHub offers required reviewers, environment secrets and deployment-branch
rules for private repositories only on Enterprise (GitHub Pro adds the latter
two, not reviewers). DOGEAR therefore splits by sensitivity, so production's
secrets can be released only after the owner approves each run:

| Repository | Visibility | Holds |
|---|---|---|
| `dogear` | **public**, one clean import commit | all code, hosting, tools, workflows, tests that use synthetic data, synthetic fixtures, vendored fonts (OFL) and firmware contract, these docs |
| `dogear-editorial` | private | the canonical dataset, the editor's affinity list, the reviewed samples, the editorial tests, the editorial handoff, the hardware test log, and the full development history |
| `dogear-publication-data`, `dogear-publication-data-staging` | private | unchanged (§20c) |

**The `dataset/` boundary.** The public code reads the canonical dataset and
affinity only from `dataset/data/`, a checkout of `dogear-editorial`
(gitignored here):
- `--target production` refuses to run without both files;
- staging publishes `fixtures/staging-s2.json` with
  `fixtures/staging-affinity.json`, both synthetic;
- `dogear-editorial`'s own CI checks out this public code to run the
  editorial tests, the canonical validation and fit check, and the sample
  checks.

**Environments and keys:**

| Environment (on `dogear`) | Protection | Secrets |
|---|---|---|
| `staging` | deploys from `main` | `DOGEAR_R2_*` (`dogear-issues-staging` only), `PUBLICATION_DATA_DEPLOY_KEY` (write, `dogear-publication-data-staging`) |
| `editorial-gate` | deploys from `main`; no reviewer | `EDITORIAL_DEPLOY_KEY` (read-only, `dogear-editorial`) |
| `production` | deploys from `main`; the owner as required reviewer | `DOGEAR_R2_*` (`dogear-issues` only), `PUBLICATION_DATA_DEPLOY_KEY` (write, `dogear-publication-data`), `EDITORIAL_DEPLOY_KEY` (read-only) |

| Deploy key | Repository | Access | Why |
|---|---|---|---|
| editorial | `dogear-editorial` | **read-only** | the gate and production read the canonical dataset; the editor writes approvals from their own machine |
| publication data (production) | `dogear-publication-data` | write | the pending record must be pushed before the registry switch, and the history after (§20c) |
| publication data (staging) | `dogear-publication-data-staging` | write | the same, for staging |

`dogear` itself needs no key: it's public, and workflows keep no
credentials. `dogear-editorial`'s CI reads the public code anonymously.

**The editorial gate: production runs only on a checked pair of commits.**
- A production run first runs the `editorial-gate` job: the editorial
  tests, canonical validation, the fit check and the sample regeneration
  (`tools/editorial_gate.py`). They run against **the public commit of this
  run** and **the editorial commit it checks out**, and the job records both.
- Only if it passes does the `production` job start, and only then is the
  owner's approval requested.
- That job checks out the editorial repository **at the gated commit**, and
  stops unless both its public and editorial commits equal the recorded
  pair. A test runs that check against real repositories.

**Public logs.** Actions logs of a public repository are public:
- The publisher always runs with `--public-log`, printing only the result,
  operation, week and transaction ID.
- Reasons, record IDs, link URLs, hold reasons and notices are never
  printed. For refusals that publish nothing, their detail is saved to the
  private publication data (`runs/`).
- The gate prints only pass/fail and test and file counts, not the
  dataset's size.
- Tests plant sentinel text in a reason, a hold, a blocked link, a notice,
  an unexpected error and the gate's checks, and assert it never appears.
- Public setup and tests run before any private repository is checked out.

### 20e. GitHub state (8 October 2026)

- **Repositories:** public `dogear` (the audited one-commit import, then
  this change); private `dogear-editorial`, `dogear-publication-data` and
  `dogear-publication-data-staging`. Each has only `main`.
- **Environments on `dogear`:** `staging`, `editorial-gate` and `production`,
  each deploying from `main` only. `production` requires the owner's review,
  with self-review allowed. No secrets or deploy keys yet.
- **Branch rules:** `dogear`'s `main` blocks force pushes and deletion. GitHub
  Free offers no rulesets or branch protection for private repositories, so
  the three private repositories have none. The publication-data deploy keys
  will be write keys on unprotected branches until that changes.
- **Actions:** enabled on `dogear` only, with approval required for workflows
  from all outside contributors. Only `DOGEAR tests` is active.
  `DOGEAR stage`, `DOGEAR promote`, `DOGEAR operate`, the reusable publish job
  and `DOGEAR certificate chain` are **disabled** in the repository's
  settings until deployment approval (G10). Their schedules are kept in the
  files, marked as disabled, and can't fire. Actions stays disabled on the
  private repositories.
- **Staging publication data:** `archievalmariano/dogear-publication-data-staging`
  on GitHub is now **authoritative**. The Mac's local copy and S2 runner are
  retired and refuse writes. Future staging writes come only through the CI
  path, once that's intentionally enabled.


### 20f. Deploy keys, and the accepted risk of unprotected private repositories

**Decision (owner, 8 October 2026):** stay on GitHub Free. The three private
repositories cannot have branch protection or rulesets on this plan, and this
is accepted as an operational risk. Revisit GitHub Pro if DOGEAR becomes
collaborative, or if the private state repositories become more critical.

**Safeguards that stand in for branch protection:**

- **Each write is a compare-and-swap on the exact remote tip.** The publisher
  is the only code that pushes (`git_committer` in
  `dogear/publish/pubdata.py`). A pre-write check alone is not enough: the
  remote could be rewound or deleted after the check, and a normal push would
  then "fast-forward" from the older or recreated tip. So:
  1. **Check.** Before each save, the publisher fetches `main` and refuses
     (`RemoteMoved`), committing nothing, unless the remote is still the
     commit this run last saw there, or one of this run's own commits whose
     push was never acknowledged. It also refuses if that tip isn't an
     ancestor of what it is about to push, or the checkout isn't on `main`.
  2. **Push.** The push carries an exact lease on that tip:
     `--force-with-lease=refs/heads/main:<tip>`. Git sends the update only if
     the remote `main` is still exactly that commit, and the server applies
     it only if it still is. The push is rejected (`RemoteMoved`) if, after
     the check, the remote:
     - advanced;
     - rewound;
     - was deleted;
     - was deleted and recreated at a different tip.

     A branch deleted and recreated at the *same* commit can't be told apart
     by an OID lease. The push then goes ahead, but the branch ends with
     exactly the content it would have had without the deletion, so nothing
     is lost.
- **No forced divergence, no deletion, no tags.** The lease only ever names
  the tip just verified as an ancestor of `HEAD`, so a write that passes is
  always a fast-forward; nothing forces past divergence. The push names one
  explicit target, `HEAD:refs/heads/main`, never with `+`, a plain `--force`
  or `--delete`. With `--no-follow-tags`, no setting such as
  `push.followTags` can send tags. The workflow also:
  - sets `push.default nothing`;
  - drops any configured push refspec;
  - checks out the publication data at `ref: main`.
- **Tests.** `tests/test_pubdata_git.py` runs against a remote that, like
  these repositories, would accept a forced update. It covers:
  - the remote advancing, rewinding, being deleted, or being deleted and
    recreated at another tip, between the check and the push;
  - a normal expected-tip push succeeding;
  - forcing configuration (`+` refspecs, `push.default matching`) failing to
    get past the lease;
  - `push.followTags` sending no tags.

  `tests/test_key_check.py` also checks that:
  - no workflow or shell tool forces, deletes or mirrors;
  - shell pushes are dry runs;
  - the code's only push is exactly the leased one.
- **Separate keys.** Production and staging have separate keys, each a deploy
  key on exactly one repository. The editorial key is read-only, so
  `dogear-editorial` stays read-only to automation.
- **Recovery.** Every save is a new commit and nothing rewrites history, so
  the repository's own history is the record to recover from. A bad write is
  undone with a new commit (`git revert`), never by resetting `main`. The
  publisher's records (`history.jsonl`, `pending.json`, `publication.json`,
  `published/`) are designed to rebuild from that history (§18). The owner
  may also keep a local mirror (`git clone --mirror`) as an extra copy.

**Deploy keys and Environment secrets:**

| Key | Repository | Access | Environment secret(s) |
|---|---|---|---|
| `dogear-ci editorial (read-only)` | `dogear-editorial` | read-only | `EDITORIAL_DEPLOY_KEY` in `editorial-gate` and `production` |
| `dogear-ci production publisher` | `dogear-publication-data` | write | `PUBLICATION_DATA_DEPLOY_KEY` in `production` |
| `dogear-ci staging publisher` | `dogear-publication-data-staging` | write | `PUBLICATION_DATA_DEPLOY_KEY` in `staging` |

Each repository gets its own ed25519 keypair. Staging holds no editorial key,
and `editorial-gate` holds no data key.

**Installing the keys.** The owner runs `tools/install_deploy_keys.sh` in
their own terminal, and it refuses to run unattended. For each repository it:

1. generates the keypair in `$TMPDIR/dogear-deploy-keys.XXXXXX` (mode 700);
2. opens GitHub's "Add deploy key" page with the public key on the
   clipboard. The web page is used rather than `gh repo deploy-key add`,
   because keys added by `gh` vanish if its token is revoked;
3. waits until GitHub lists that key with the right access;
4. stores the private key as the Environment secret(s) through
   `gh secret set`, reading it from the file;
5. deletes the private key.

No private key is printed, copied or kept.

**Cleanup when the installer stops early.** Every way out runs one cleanup,
exactly once, which deletes the temporary folder:

- **Normal exits:** success, a failure, or typing `q`.
- **Ctrl-C, `SIGTERM`, or the terminal closing (`SIGHUP`):** the script exits
  with status 130, 143 or 129.

Some endings can't be trapped: `SIGKILL` (`kill -9`), a crash, or a power
loss. Then the private key may be left in that folder. The next run finds the
leftover, prints its path, and refuses to continue. To clean up by hand:

1. **Find it.** List `ls -d "$TMPDIR"/dogear-deploy-keys.*`.
2. **Delete it.** Run `rm -rf` on each folder found.
3. **Remove the half-installed key.** If that run had already added the key's
   deploy key on GitHub, delete that deploy key under the repository's
   **Settings → Deploy keys**, because its private key was exposed on disk.
   If the run had stored that key's Environment secret too, delete the secret
   as well.
4. **Rerun.** Start the installer again. It refuses while any of these deploy
   keys or secrets still exist.

To rotate a key, delete its deploy key and secret(s) on GitHub, then run the
script again.

**Checking the isolation.** `DOGEAR deploy-key check`
(`.github/workflows/dogear-key-check.yml` and `tools/key_check.sh`) is manual
only. Each run tests one Environment, from `main`. It holds no R2 credentials
and checks out only this source. It tries each key against all three private
repositories:

- **Read:** a shallow clone without checkout, deleted afterwards.
- **Write:** `git push --dry-run`. GitHub authorizes the write before the dry
  run stops, and nothing is sent.

The access found must match the table exactly. Only GitHub's own refusals
count as "no access"; a network or host-key failure is an error. The
`production` run waits for its required reviewer.

### 20g. Staging on the real calendar (Option A, owner decision 8 October 2026)

S2 ran on a simulated 2027 clock (`--now`), so the staging registry's current
week is 29 March 2027. With the real clock, CI could publish nothing on
staging: promotion refuses any week not after the current one. Staging is
the permanent rehearsal target, so it moves to the real Monday-Sunday
calendar that production uses. The S2 state is archived, not deleted, and
only from a quiescent, reconciled state.

**The year-round synthetic fixture.**
- **What it is.** `fixtures/staging-year.json`, written by
  `tools/staging_fixture.py`, replaces the March-2027-only
  `staging-s2.json`. It has one placeholder record for every month and day,
  29 February included (it appears only in leap years). So every real week
  offers about seven candidates, and selection still has to choose.
- **Not an editorial rule.** That density is fixture shape only.
- **Coverage.** Every week from 29 December 2025 to 30 December 2030 builds
  an issue of 4-5 items, published in sequence so the 365-day history
  applies. The fit check passes for all 366 records.

**The declared synthetic markers.** These five, exactly as `MARKERS` in
`dogear/synthetic.py` defines them:

| Marker | Present when |
|---|---|
| `id` | the id starts with `staging-` or `s2-` |
| `tag` | the tags include `staging-test` |
| `approver` | the approval is by `synthetic-fixture-not-editorial` |
| `names` | the person starts with `Staging Writer`, or the work with `Staging Work` |
| `links` | a source or link URL is on `example.org` |

Every fixture record has the full *staging shape*:
- all five markers;
- both names;
- at least one URL, with every URL on `example.org`.

The affinity file is empty, so it holds no real preferences.

**Staging and production separation, at every write.**
- **Production shows nothing synthetic.** Production refuses a record with
  *any* marker, and a revision whose ids, dataset records or issue entries
  carry one. This is checked when staging, when re-verifying a staged week,
  in the editorial gate ("no synthetic records", which prints no count),
  and in the guard re-run just before every registry write. That guard
  covers rollback and restore, which make an *existing* revision visible.
  The check is made on that stored revision itself, not only by rebuilding
  it.
- **Staging shows only synthetic data.** The staging target refuses any
  record without the full staging shape, and any revision that isn't wholly
  synthetic, at the same points. A real or canonical revision can't become
  current on staging through rollback or restore.
- **Target settings:** production mode always has production separation.
  `--target staging` has staging separation. The staging target stays
  `dogear-issues-staging`, in test mode, using the fixture, with no editorial
  checkout. Production never points at `fixtures/`.

**Real clock.**
- Nothing sets a date on the workflow path. `tools/run_publish.py` never
  passes `--now`, `--drill`, `--no-network-checks`, `--mode`, `--dataset`,
  `--store` or `--policy`, and no workflow mentions them. Tests check both.
- The week is resolved as in production: Monday-Sunday in PHT, rolling over
  on Monday at 06:00.
- `--now` remains for local test-mode rehearsals only.

**Workflows and schedules, precisely.**
- `PUBLICATION_ENABLED` gates only the *scheduled* runs of `DOGEAR stage`
  and `DOGEAR promote`. A manual `workflow_dispatch` of either still runs
  whenever that workflow is enabled. Safety before approval therefore also
  depends on both workflows staying **disabled** in the repository's
  Actions settings until the owner explicitly approves.
- `DOGEAR operate` is manual-only. It can target production, but not with
  one click:
  - its first job, which holds no Environment and no secrets, refuses
    production unless `confirm_production` is typed exactly as
    `production`;
  - the run then still waits for the `production` Environment's required
    reviewer.

**`launch`, exactly as implemented.**
- `launch` first reconciles any pending transaction. It then refuses if
  history.jsonl has any **committed** line ("launch happens once") or if
  the store already has a registry.
- **Aborted lines do not block it.** After an attempt that aborted (nothing
  published), launch can be retried.
- **A retry after an uncertain write settles that write first.** If the
  earlier attempt left a pending transaction whose registry write *did*
  land, the retry's reconcile records it as committed, and launch then
  refuses. That is the correct forward state: the launch happened. The next
  operation is a promotion, not a second launch.
- **What the reset must leave.** No committed *live* history, no pending
  transaction, and no registry in the store. The archived S2 files under
  `archive/` are not read by the publisher.

#### The reset: preflight, transition, launch

Each step runs only after Codex has reviewed this change and the owner has
approved the transition.

1. **Quiescence gate.** Any failure here means **STOP**: reconcile first, and
   never archive unresolved state. All of these must hold:
   - **No writer is running:**
     - `DOGEAR stage`, `DOGEAR promote` and `DOGEAR operate` and the
       reusable job are all disabled, with no run queued or in progress:
       `gh api repos/archievalmariano/dogear/actions/runs?status=in_progress`
       and `?status=queued` both report 0.
     - No local publisher is running. The Mac's S2 runner and local
       staging-data repository are retired and refuse writes.
   - **The read-only preflight passes.** It runs locally against a fresh
     clone of `dogear-publication-data-staging`:
     `python3 -m dogear.cli publish --target staging --pubdata <fresh clone> reset-preflight --expect-current 2027-03-29`.
     It never reconciles, verifies or saves anything, and it isn't
     reachable from any workflow. It reports PASS only if all of these hold:
     - the clone is exactly the remote `main`, with no local changes. Every
       git command must succeed: `git status`, `git rev-parse HEAD` and
       `git ls-remote origin main`. A failed command is a HOLD reason, never
       read as clean;
     - no `pending.json`;
     - `unverified.json` is empty and every committed transaction is in
       `verified.jsonl`;
     - the live R2 registry exists and is byte-identical to the
       publication-data mirror;
     - its current week is 2027-03-29, with no hold;
     - its transaction is the last committed one, so no write is unresolved;
     - every visible revision has its committed record;
     - the host serves exactly what that registry shows. Each item is
       fetched again, because verification history is not enough: an object
       can disappear after it was verified. The checks are:
       - the manifest, and every week's *active* page and device issue,
         byte-compared with the hashes the registry records;
       - every font those pages load, byte-compared with the publisher's
         own copy, since the registry records no font hash.

       A missing object, failed fetch, malformed response or hash mismatch
       is a HOLD.

     Anything else is **HOLD**, with every reason listed.
2. **Snapshot.** Record `dogear-publication-data-staging` `main` (`128f44c`
   when written) and the SHA-256 of the live registry.
3. **Publication data.** Make one ordinary fast-forward commit that moves
   `publication.json`, `history.jsonl`, `verified.jsonl`, `staged/` and
   `published/` under `archive/s2-2027/`.
   - The preflight has already proven there is no `pending.json` and that
     `unverified.json` is empty. Recovery state is never moved aside: if
     either holds anything, step 1 failed.
   - `README.md` stays. Nothing is rewritten.
   - From here on, GitHub stays authoritative and staging writes come only
     through CI.
4. **R2.** This uses Wrangler with the owner's login, not a publisher token.
   - Read `dogear-issues-staging/publication.json` and check it matches the
     snapshot's SHA-256.
   - Write it to `archive/s2-2027/publication.json`, read that back, and
     compare the SHA-256.
   - Only then delete the live `publication.json`.
   - Issue, page and font objects stay. The Worker serves only what a
     registry names, so `archive/` is never served.
   - Afterwards `/current.json` returns 503 ("nothing published") and
     devices keep their cached issue.
5. **Workflows.** Enable only `dogear publish (reusable)` and
   `DOGEAR operate`. Keep `DOGEAR stage`, `DOGEAR promote`,
   `DOGEAR certificate chain` and `DOGEAR deploy-key check` disabled. Leave
   `PUBLICATION_ENABLED` unset. Production stays reachable only through the
   confirmed operate path and its reviewer, and nobody dispatches it in this
   step. The `dogear-scheduler` Worker has never been deployed.
6. **Launch.** Run `DOGEAR operate` with target `staging`, op `launch`, and
   no confirmation. It publishes the current real Manila week from the
   fixture, with no editorial checkout.
7. **Verify.**
   - The issue and page are served and hash-match.
   - `/current.json` names the new immutable issue.
   - `dogear-publication-data-staging` records the transaction once and
     verifies it.
   - The log shows only the result, op, week and transaction.
   - `dogear-issues` and the production Environment are untouched.

#### If something fails

**A. Before any launch attempt.** Recognise this case by: `operate launch`
was never dispatched, and the publication data has no new `pending.json`,
`history.jsonl` or `staged/`. Undo only what was done:

| Point reached | Undo |
|---|---|
| Archive commit pushed, R2 not yet changed | `git revert` the archive commit (fast-forward) |
| R2 archive copy written, live registry not yet deleted | Nothing live changed; leave the archive copy (never served) and `git revert` the archive commit |
| Live registry deleted | Put `archive/s2-2027/publication.json` back as `publication.json`, checking the SHA-256 against the snapshot, then `git revert` the archive commit |

Afterwards, rerun the preflight: it must PASS against 2027-03-29 again.

**B. After a launch attempt started.** A launch writes in this order:
1. `staged/`, then `pending.json`;
2. the registry;
3. the committed history line and verification.

So once it has started, do **not** restore S2 blindly. First find out what
happened:

1. **Inspect, read-only.** Run `DOGEAR operate` `staging` / `status`.
   Locally, read a fresh clone of the publication data and the store's
   registry.
2. **Decide which case this is:**
   - **No registry in the store, and no committed line.** The attempt
     aborted or failed before the switch: nothing was published. If
     `pending.json` remains, `DOGEAR operate` `reconcile` closes it as
     aborted. Then retry `launch`. Its aborted line doesn't block it.
   - **A registry in the store, and `pending.json` present.** The outcome
     is uncertain. Run `reconcile`: it records the launch as committed if
     the store holds the intended registry. If it reports *needs a human*,
     inspect the pending transaction against the store, then either let
     `reconcile` / `resume` complete it, or `abandon` it after inspection.
     Never delete `pending.json` by hand.
   - **A committed launch, possibly unverified (exit 8).** The launch
     happened. Recover forward: later runs re-check serving until it's
     verified. Staging continues on the real calendar, and S2 stays in
     `archive/`.
3. **Manual restore of S2: last resort, owner-approved, only from a known
   verified state.** Only if forward recovery is rejected:
   - archive the attempted launch's state the same way (a fast-forward
     commit, and R2 copies verified by SHA-256);
   - put S2's archived registry back as the live `publication.json`;
   - restore S2's files from `archive/s2-2027/` with a new commit;
   - rerun the preflight against 2027-03-29.

   The publisher has no operation for this, so it's done by hand.

**Devices.** Both devices have the 29 March 2027 S2 issue cached, and the
server will offer the real current week, which is earlier. The hardware test
records whether a device:
- accepts the server issue;
- keeps the newer cached issue;
- treats the server issue as stale;
- or behaves another way.

The firmware is not changed to force a result. If the behaviour blocks
realistic staging validation, that is reported before any firmware change.

## 21. The quiet-week issue (owner decisions, 8 October 2026; Codex PLAN CLEAR)

**Policy:**
- `slot5Bar: 42` and `sparseWeek: publish` are decided and set in
  `data/publication-policy.json`.
- `emptyWeek` gains the mode `quiet-week`. It stays **unset in production**
  until DOGEAR firmware presents quiet issues (see "Production" below), so
  production still refuses to publish.
- Staging uses `quiet-week`.

**What a quiet issue is.**
- **Trigger:** only when the regular selection picks nothing (a sparse week of
  1-2 items publishes as it is, never padded).
- **Contents:** 3 to 5 items drawn from earlier issues' **"Also this week"**
  mentions. A mention is not a publication.
- **Selection:** the regular scorer, composition nudges and slot bars (30 for
  slots 1-4, 42 for slot 5), with a ceiling of 5.
- **Fewer than 3:** the previous issue stays current.
- **Never in the pool:**
  - a record ever published as a full item;
  - a record whose own anniversary falls in [W, W + 35 days);
  - a record about a person or work featured in full within 365 days;
  - a record lacking stable identities.
- **Shape:**
  - the issue belongs to its own Monday-Sunday week;
  - every item keeps its own original date;
  - a quiet issue has no "Also this week".
- **The copy, exactly:** `SOME QUIET THIS WEEK` /
  `Not much landed on our calendar this week. Still, we pinned a few things worth a dogear for you.`
- **Code:**
  - `dogear/quiet.py`: constants, the copy, the frozen source and the
    anniversary window;
  - `select_quiet_issue` in `dogear/select.py`.

**Stable identities.** Canonical records carry `personId` and `workId`:
- `wikidata:Q<digits>`, or `dogear:<slug>` minted once, never computed from a
  display name;
- required on approved records;
- the one-per-subject rule uses them;
- the name fallback, for proofs only, is Unicode-aware and never empty.

Every new digest's identities are written to its provenance (`subjects`,
hash-pinned by the registry) and to its history line, and the two must agree.

**History, no new state.** `dogear/publish/quiet_history.py` derives the
quiet inputs from committed launch, promote and correct lines. Before a line
counts, it is validated against:
- its registry revision (txn, op, issue and web hashes);
- the hash-pinned provenance (digest, also, edition, subjects);
- the stored issue and web bytes;
- the date/edition invariant.

Any mismatch is `CorruptState`, and nothing is published.

**The sets for week W:**
- every regular digest of any week, W included, plus quiet picks of other
  weeks, is excluded for good;
- W's own earlier quiet picks may be kept by a quiet → quiet correction;
- a *different* record about one of those subjects may not;
- the pool is "Also this week" from the previous 26 weeks;
- subject identities come from the previous 365 days, any later week, and W's
  own earlier regular revisions.

Corrected-away and rolled-back revisions count. The source is frozen into a
quiet issue's provenance (`inputs.quietSource`), so it regenerates byte for
byte. If history changes after Friday's stage, Monday's promotion re-stages.

**Legacy lines.** A line written before identities existed has no
`subjects`. Inside the 365-day window it makes the source incomplete, and the
quiet week **holds**: identity is never read back from current records. The
only backfill is `dogear publish attest-subjects --txn T --from MAPPING.json
[--source DATASET]`:
- local and explicit, never in a workflow;
- it refuses unless the line validates and lacks subjects, nothing attests it
  yet, the source is the publication's original frozen dataset (it hashes to
  the revision's `datasetSha256`), the mapping covers the digest exactly at the
  pinned fingerprints, and id presence matches each original record;
- the source is kept immutably under `evidence/<txn>/`;
- the attestation is appended to `subject-attestations.jsonl`;
- a duplicate, or altered evidence, proves nothing.

**The date and edition invariant** (`dogear/publish/edition.py`):

| Edition | Rule |
|---|---|
| Regular | schema 2, no `quiet` key, every entry dated inside its own week |
| Quiet | schema 3, the exact copy, 3-5 entries each at its recorded original date, a `quietSource`, and no "Also this week" |

The edition must agree across issue, provenance and history line. It is
checked:
- at stage;
- at the staged-week re-check;
- in the guard before every registry write and retry (launch, promote,
  correct);
- on every revision rollback, restore or **resume** makes visible (resume
  re-checks the current revision);
- when history is validated;
- by the read-only reset preflight.

The registry format is unchanged: the binding runs through each revision's
existing issue and provenance hashes.

**Devices: schema 3 is the boundary.**
- Pre-quiet firmware accepts only schema 2, so it rejects a quiet issue and
  keeps its verified cache (`CACHED`); the next regular week displays
  normally.
- Presentation firmware (accept 2 and 3; draw the heading and note on the
  cover) is a later, owner-approved step. The cover layout is decided after a
  mock render.

**Production.**
- `Publisher._ready()` refuses `emptyWeek: quiet-week` unless the firmware
  contract records `kIssueSchemaMax >= 3`.
- The switch itself stays the owner's, after the firmware is released and
  physically verified on both devices.

**Staging rehearsal.**
- `fixtures/staging-year.json` leaves out **10-22 November**, so every year
  has one empty week (16-22 November in 2026), and the weeks the gap cuts short
  are sparse.
- `DOGEAR staging ops` (`.github/workflows/dogear-staging-ops.yml`):
  - manual `stage-next`, `promote` or `status`;
  - the target is written literally as `staging`;
  - a first job, with no Environment and no secrets, refuses any ref but
    `main` and any other operation;
  - no schedule.
- **Observed in the multi-year test (2026-2030, weekly publication):**
  - 2026's gap week is quiet (4 items);
  - from 2027 the gap week **holds** ("2 qualifying items").

  The fixture's records recur every year, and anything once published in full
  is excluded for good, so the quiet pool thins year on year. That is the
  owner's rule working as written. In the editorial dataset, the same rule
  means quiet material comes only from records that have never been featured.
- **Staging's one legacy line** (launch 2026-10-05) must be attested before
  November's quiet week can publish. Its original frozen dataset is
  `staged/2026-10-05/dataset.json` in `dogear-publication-data-staging`,
  written once by `f4a4c27`. It hashes to that revision's `datasetSha256`
  (`92c74279…`), and its 4 digest records match their pinned fingerprints.
  The attestation itself is not written yet.
