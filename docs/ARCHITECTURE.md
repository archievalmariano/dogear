# DOGEAR — architecture and editorial model (E0.3, for review)

*E0 (2026-10-06) set the
architecture; E0.1 fixed pagination, copy fit, the closing page and approval;
E0.2 added the editorial model and a seed dataset; E0.3 (2026-10-07) applies
the E0.2 review: earned issue length, literature on ties, a Global South lift,
observed dates, and a source-confidence hierarchy; it also replaces READ FREE
with READ ONLINE and ties every reading link to a named work.*

**DOGEAR** · *a limited weekly reading digest*. A global reading digest with a
Philippine / Southeast Asian point of view and a recognizable human editor.
The device carries the **digest**; a static web page carries the **expanded
edition**.

## 1. Approved decisions (do not reopen)

* Dataset first; deterministic, model-free weekly selection; sourced records;
  territory-aware rights (a US-only public-domain copy is not READ FREE for
  Philippine readers); approval withdrawn by material change; issues need no
  approval of their own.
* One item per 480×800 screen, 14pt preferred, 12pt controlled fallback, a
  second screen only when the record allows it, no `CONTINUES`.
* Final page `EVERYTHING IS DOGEARED HERE` + QR to the issue's web edition +
  `NEXT ISSUE` / `Monday <date with year>`. Reading-link QRs are secondary
  views outside the page count; at most ~2 per issue.
* Readable public issue URL `dogear.archievalmariano.com/<monday>/`; DESK page
  later at `archievalmariano.com/desk/dogear/`. **Not deployed.**
* Do not refactor GOTO publishing into a shared module. DOGEAR stays in this
  workspace for the E0 cycle; its own repo before production.
* **Editorial model approved (E0.2 review)** with these changes, now built:
  * **Issues earn their length.** Normal 4–6 items; 3 is fine for a weak week;
    7–8 only for unusually strong weeks. Nothing fills slots because valid
    records exist. A sparse week stops early rather than filling up with anchors.
  * **Literature wins ties** on intrinsic strength over adjacent domains.
  * **Global South lift**, modest, with the real regional tags kept: Philippines
    strongest, Southeast Asia next, then wider Asia, Africa, Latin America and
    the Middle East. No quotas.
  * **Observed dates**: a date the subject kept (a chosen birthday) may be
    published, with the uncertainty stated in the copy.
  * **Source-confidence hierarchy**, not a blanket institutional requirement.
* **Reading actions** (E0.3): `READ ONLINE ›` (legitimate full text), `FIND THE
  BOOK ›` (copyrighted work), `READ MORE ›` (context), `VIEW SOURCE ›`
  (archive or primary document). The item page never says FREE; the reading
  screen names the work, the author and the provider ("Free on Project
  Gutenberg"). Reading links follow the work hierarchy in §6.
* **Contents say what happened, in words** (first hardware pass): cover rows
  read `24 NOV · PUBLISHED` / `BORN` / `DIED` / `NOBEL PRIZE` over the title,
  never single letters, icons or a dagger. A national observance shows its
  locality: `OBSERVANCE · PH` (records carry `locality`, observances only);
  ordinary author and book events carry no country tag.
* **Web edition in the archievalmariano.com / DESK family** (first hardware
  pass): paper and ink, heavy rules, bold Inter structure, Newsreader for
  reading, tracked uppercase labels; no ornament, no cards, no third-party
  requests (fonts self-hosted under `/fonts/`). One accent, the site's red:
  `--accent-ink` for labels, kickers and reading actions, `--accent` for
  underlines, hover, focus and the lead's short bar. Never on body copy,
  headlines, rules or backgrounds. The e-ink issue stays monochrome, stripped
  down and typographic.
* **Access before commerce, with a Philippine lens** (third pass). Records
  list candidate `links`; the publication shows the best one for a reader in
  the Philippines: free legal full text (READ ONLINE) → borrow in the
  Philippines → borrow elsewhere (BORROW) → a Philippine bookseller → a
  bookseller elsewhere (FIND THE BOOK). `borrow` and `find-book` links carry
  `market: ph | intl`. No CTA beats a weak commercial link; the CTA names the
  relationship, the reading screen names the provider ("Borrow via …").
* **Selective sources on the page, exhaustive in the data** (third pass). The
  web edition does not repeat a source that is the entry's own reading
  destination, hides Wikidata whenever a human-readable source remains, and
  lists primary/institutional sources first. The dataset keeps all of them.
* **Philippine news sources** (house preference, not exclusivity): official or
  institutional first (NCCA, National Library, a university or publisher, an
  estate), then GMA News Online where it has substantively equivalent
  coverage, then other reputable Philippine outlets, then international or
  reference sources.
* **House style for names** (second pass): the editor's preferred form of a
  name is used (a preference, not a correction); other forms live in the
  record's notes and as aliases in the affinity list. An observance keeps its
  official name, and its copy opens with a short English gloss.
* **Item dates carry no weekday** (pre-firmware review): kickers and contents
  read `3 JUN · PUBLISHED 1850`, not `MON 3 JUN · …`. DOGEAR is weekly, and a
  weekday would belong to the issue year, not the event's. Only scheduling keeps
  one: `NEXT ISSUE · Monday 4 January 2027`.

## 2. Editorial stance

* **Global in scope, with a Philippine / Southeast Asian lens**, legible in
  Manila, Singapore, Dublin or New York. Regional relevance is a lift, not a fence.
* **Books, writers and reading**: novelists, poets, playwrights, essayists,
  historians, philosophers and major nonfiction all qualify (`domain`), with
  literature first when strength is equal.
* **Authored**: a small, explicit affinity layer (`data/affinity.json`).
* **Breadth over time**: grown deliberately, watched with the balance report;
  single weeks are allowed to be what their dates make them.

## 3. Record fields (schema v3)

| Field | Question | Values |
|---|---|---|
| `significance` | How much does it matter to books, writers and reading? | 1–5 |
| `recognition` | Will a general reader know it? | `anchor` / `core` / `discovery` |
| `discoveryValue` | How rewarding is it to introduce? | 0–2 (used for discovery tier) |
| `phRelevance`, `seaRelevance` | Philippine / Southeast Asian relevance | 0–2 |
| `area` | Where the writer or work belongs (kept as the real region) | philippines, southeast-asia, east-asia, south-asia, middle-east, africa, europe, north-america, latin-america, oceania, global |
| `domain` | What kind of reading | literature, history, philosophy, science, nonfiction, reading-culture |
| `type` | Event | birth, death, publication, literary-event, observance |
| `dateBasis` | Is the date recorded, or the one the subject kept? | `recorded` / `observed` |
| `claims` | Is the claim straightforward, or disputed / converted / observed / conflicting? | `straightforward` / `complex` |
| `sources[].kind` | Source confidence | `primary`, `institutional` > `secondary` > `reference` |
| `label` | Kicker word | `NOBEL PRIZE`, `CHOSEN BIRTHDAY` |
| `link` | Where the reader can go next | `{type, url, match, title, author, edition?, mentionedAs?, provider, rights}`; see §6 |
| affinity | The editor's taste | `preferred` / `adjacent`, from `data/affinity.json` by name |

Editorial metadata (significance, recognition, relevance, discovery value,
domain, balance fields) can change without withdrawing approval. Facts, copy,
`label`, `dateBasis`, `claims`, sources and links (with their rights) are
material.

### Source confidence

* **Primary / institutional** (preferred): archives, laws, national libraries,
  NobelPrize.org, Project Gutenberg, museums, publishers, contemporaneous press.
* **Secondary** (acceptable): reputable reference publishers and journalism, such
  as Britannica, newspapers, biographies and literary organizations.
* **Reference**: Wikipedia, Wikidata, open wikis. Fine for **straightforward**
  facts (a well-attested birth date, a book's year).
* A **complex** claim needs at least one secondary-or-better source. Complex
  means a disputed date, a calendar conversion, an observed date, or sources
  that conflict. Until then it is not publishable, and selection says so. Most
  records do not need upgrading; `dogear review` shows each record's strongest
  source and flags the complex ones that are too weak.

### Observed dates

`dateBasis: observed` publishes a date the subject kept when the record itself
is uncertain. It must be `complex`, carry notes and a secondary-or-better
source, and the copy must say what the date is: the item opens by saying the
subject kept that date, then explains the records. The web edition adds a note
under any observed date.

## 4. Selection model (deterministic, model-free)

The aim: **familiar enough to pull the reader in, regional enough to feel
distinctive, unfamiliar enough to teach them something.**

**Eligible:** day precision; anniversary in the week; `verified`; approved at
current content (or `--preview` for proofs); sourcing fit for its claims; at
least one year old; significance ≥ 2.

**Proofs.** A `--preview` run is always a proof (`preview: true`, a proof
banner and `noindex` on the web), and so is any issue that renders a record
awaiting approval anywhere, ALSO THIS WEEK included. Without `--preview`,
unapproved records are excluded before composition, so they cannot appear even
as runners-up. A publisher must still check approvals itself rather than trust
the flag.

**Validation** guarantees what selection and rendering consume: typed scalars
(booleans are not integers), typed lists and objects, https URLs with a real
hostname, and years as CE integers 1–2100 as the sources print them (no BCE,
no calendar conversion). The file must be UTF-8 and nest at most 16 levels,
with no string that cannot be written back as UTF-8 (a lone surrogate escape
such as `\ud800`). Anything else is a `DatasetError`.

**1. Intrinsic strength.** `significance × 10` + recognition (anchor +5, core
+2) + round anniversary (+8 / +6 / +4).

**2. Editorial lifts (modest; the strongest regional one only).**

| Lift | Value |
|---|---|
| Philippine (connection) | +8 (+4) |
| Southeast Asian (connection) | +4 (+2) |
| Wider Asia (East, South) | +2 |
| Global South: African, Latin American, Middle Eastern | +2, reason names the real region |
| Discovery tier | +2 per `discoveryValue` |
| Affinity (only at significance ≥ 3) | preferred +3, adjacent +2 |
| Worldwide-public-domain reading link | +2 |

**3. History (soft).** −6 if in an issue within 365 days, measured issue to
issue (this week's Monday against the earlier issue's Monday), so every item in a
week is judged alike; −2 if its area made up more than half of the last four
issues.

**4. Composition (while picking).** First anchor +4, third −4, fourth −8, …;
first Philippine/SEA/Asian item +4; first discovery +4, third −4, …; each extra
item of an event type already present twice −3, −6, …

**5. Length: each slot is earned.** An item's effective score (score + nudge)
must clear the bar for the slot it would fill:

| Slots | Bar | Meaning |
|---|---|---|
| 1–4 | 30 | the normal core of an issue |
| 5–6 | 42 | a fuller week |
| 7–8 | 52 | unusually strong weeks only |

The composer stops at the first item that misses its bar. Growing anchor
penalties mean an anchor-only week stops at three or four instead of padding
to six. Too few items for a good issue is a signal to grow the dataset, never
to lower the bar.

**Ties** go to the intrinsically stronger item, then **literature** over history,
philosophy, science and other domains, then date. The **lead** is the
intrinsically strongest item, with the same literature tie-break. A second lead
needs significance 5 on a round anniversary.

**Shape (hard):** ≤ 8 entries, one per person/work, ≤ 2 per day, ≤ 5
births/deaths. Reading QRs ≤ 2.

## 5. Copy rules

* **Discovery items** open with who this is and why it matters (`Katherine
  Mansfield, one of the key modernist short-story writers, was born …`).
* **Anchor items** skip the introduction and use the screen for the date.
* **Observed dates** say so in the first sentence.
* **Fit:** about 45–55 words at 14pt. `--fitcheck` is the arbiter, including
  glyph coverage against the firmware's compiled font tables.

## 6. Device, reading links and web

Cover/contents → one item per screen → full-edition QR page; reading QRs as
secondary views; short dateline fallback for long weeks. Item kickers and
contents rows show the day and month only (`14 MAR · DIED 1901`).

**Which work a reading link opens**, in order:

1. `event_work`: the work the event is about (*The Salt Orchard is published* →
   *The Salt Orchard*). Its title must equal the record's `work`, and its author the
   record's `person`.
2. `mentioned_work`: a work the copy foregrounds (a poet's death → the last
   poem the copy discusses, not their best-known novel). Its title, or `mentionedAs` for a
   short form like "Tom Fields", must appear in the digest copy.
3. `representative_work`: an editor-chosen work for an author-focused entry
   (birth or death only), by that author. The reading screen says
   "A representative work".
4. None: no reading action. Not every entry needs a QR.

These rules are checked on every load, so a link cannot quietly drift from its
work; `dogear links` lists all of them for review. Rights live on the link (the
edition's status). `READ ONLINE` needs a public-domain edition, and the device
QR still needs worldwide public domain.

**Reading screen:** `READ ONLINE` / work title / author / *edition or "A
representative work"* when useful / QR / "Free on Project Gutenberg". `FIND THE
BOOK` says "Find it via <provider>".

**Web edition:** longer copy, sources, every eligible link with its work,
author and provider, notes on withheld free reading and observed dates, and the
week's near misses.

## 7. Balance audit

`dogear balance --issues <dir>` reports the full dataset, publishable records
and the last 8 and 12 issues, by tradition, tier, affinity, women writers,
translation, event type, domain and century. It never feeds selection.

## 8. Dataset and samples (private)

The canonical dataset, the editor's affinity list, the reviewed sample issues
and their editorial notes live in the private `dogear-editorial` repository,
together with the tests that pin them. This repository works from synthetic
fixtures (`fixtures/`); production reads the editorial checkout at `dataset/`
(read-only).

## 9. Open editorial questions

Listed in [PUBLISHING.md §14](PUBLISHING.md): the slot-5 bar (42 or 40), thin
and empty weeks, and the editor's sign-off on records (`dogear approve`).

## 10. Device prototype (dev builds, not released)

Firmware branch `feature/dogear-prototype` off `device/crosspoint-main`
(`src/activities/dogear/`), compiled only into the dev envs (`default`,
`x4pro`) behind `DOGEAR_ENABLED`. It reuses GOTO's patterns as copies, not a
shared module: theme-independent layout with built-in Noto fonts; load once per
session, offline-first (`current.json` → SHA-256-checked issue → SD cache under
`/dogear` → compiled-in sample), with `CACHED` / `OFFLINE` markers; CrossPoint's
Wi-Fi picker when offline; capacity-aware QR (`QrUtils`, ECC low) with a
4-module quiet zone; GOTO's button grammar and the X4 Pro's touch page turns.
The layout constants are the mock's, so the mock sheets are the device's proof.

**Transport (after review 1).** DOGEAR has its own GET client
(`DogearHttps`): HTTPS verified against seven public roots
(`DogearTrustRoots.h`: ISRG X1/X2, GTS R1/R4, GlobalSign Root CA, SSL.com RSA/ECC
2022), certificate required, hostname checked, fail closed; the clock is set by
NTP first when unset (the X4 has no RTC); redirects never go HTTPS → HTTP; a
3-redirect limit and byte caps (4 KB manifest, 32 KB issue). Plain HTTP only to
a private LAN address in builds made with `-DDOGEAR_ALLOW_LAN_HTTP`. The DOGEAR
envs build wolfSSL with SHA-384 and P-384 (public CA chains need them); every
other app keeps its transport.

**Cache (after review 1).** Two verified generations: content-named issue
files and `active.txt` / `previous.txt` manifests, committed by temp file,
read-back and rename, so an interrupted update leaves the last good issue
loadable. A verified manifest left only under its temp name (an update cut off
before its final rename) is promoted to active before the next update reuses
that name, so repeated interruptions never lose it. A cached copy is size-capped, re-hashed and checked against its
manifest's issue id before use; a bad one falls back to the previous
generation.

The device reads issue schema v2 exactly as `dogear issue` writes it. For
testing, `dogear stage` (and `tools/dev-server.sh`) serves it from a local machine:
`current.json {issueId, issuePath, issueSha256}`, `issues/<id>.json`, and the
web edition at `/<monday>/`. The hardware checklist and its results are kept
privately with the editorial records.
