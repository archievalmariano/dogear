"""Render the expanded web edition as one static HTML page.

This is the page the device's final QR opens (``editionUrl``). It belongs to
the archievalmariano.com / DESK design family: warm paper and ink, heavy
rules, tight bold sans for structure, a serif for reading, small tracked
uppercase labels. No scripts, no tracking, no third-party requests; the two
typefaces (Inter, Newsreader; SIL OFL) are self-hosted from ``/fonts/``, which
``dogear stage`` copies beside the page. Hosting is a later step; nothing here
deploys.
"""

from __future__ import annotations

from html import escape

_FONTS = [
    # family, file stem, style, unicode-range (fontsource subsets)
    ("Inter", "inter-latin-wght-normal", "normal", "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD"),
    ("Inter", "inter-latin-ext-wght-normal", "normal", "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF"),
    ("Inter", "inter-vietnamese-wght-normal", "normal", "U+0102-0103,U+0110-0111,U+0128-0129,U+0168-0169,U+01A0-01A1,U+01AF-01B0,U+0300-0301,U+0303-0304,U+0308-0309,U+0323,U+0329,U+1EA0-1EF9,U+20AB"),
]
for _style in ("normal", "italic"):
    _FONTS += [
        ("Newsreader", f"newsreader-latin-wght-{_style}", _style, _FONTS[0][3]),
        ("Newsreader", f"newsreader-latin-ext-wght-{_style}", _style, _FONTS[1][3]),
        ("Newsreader", f"newsreader-vietnamese-wght-{_style}", _style, _FONTS[2][3]),
    ]

_FONT_FACES = "\n".join(
    f"@font-face {{ font-family: '{fam}'; font-style: {style}; font-display: swap; font-weight: 100 900;"
    f" src: url('../fonts/{stem}.woff2') format('woff2'); unicode-range: {rng}; }}"
    for fam, stem, style, rng in _FONTS
)

_CSS = """
:root {
  --paper: #f4f0e5; --ink: #172126; --ink-soft: #3f4a4e; --ink-faint: #636d71;
  --rule: #b9b4a9; --heavy-rule: 2px solid var(--ink);
  /* The one accent, as the portfolio uses it: --accent-ink for small text
     (labels, kickers, actions), --accent for underlines, hover and focus. */
  --accent: #d84f38; --accent-ink: #a93829;
  --serif: 'Newsreader', Georgia, 'Times New Roman', serif;
  --sans: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif;
  --step--1: clamp(0.83rem, 0.8rem + 0.15vw, 0.9rem);
  --step-0: clamp(1rem, 0.96rem + 0.2vw, 1.08rem);
  --step-1: clamp(1.2rem, 1.12rem + 0.4vw, 1.4rem);
  --step-2: clamp(1.5rem, 1.35rem + 0.7vw, 1.9rem);
  --step-3: clamp(1.95rem, 1.7rem + 1.2vw, 2.8rem);
  --gutter: clamp(1rem, 5vw, 3.5rem);
  color-scheme: light;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--paper); color: var(--ink); font: var(--step-0)/1.6 var(--sans);
  -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; overflow-x: clip; }
a { color: inherit; text-decoration-color: var(--rule); text-underline-offset: 0.2em; }
a:hover { color: var(--accent); text-decoration-color: var(--accent); }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
main { max-width: 64rem; margin: 0 auto; padding: 0 var(--gutter) 6rem; }

/* Masthead */
.mast { padding: clamp(2.5rem, 7vw, 5.5rem) 0 1.25rem; border-bottom: var(--heavy-rule); }
.label { margin: 0; font: 800 0.72rem/1.35 var(--sans); letter-spacing: 0.1em; text-transform: uppercase; }
.mast .label { color: var(--accent-ink); }
.mast h1 { margin: 0.6rem 0 0; font: 760 clamp(3.3rem, 11vw, 7.2rem)/0.9 var(--sans); letter-spacing: -0.045em; }
.mast .strap { margin: 0.9rem 0 0; font: italic 400 var(--step-1)/1.35 var(--serif); color: var(--ink-soft); }
.mast .dateline { margin: 1.6rem 0 0; font: 760 var(--step-1)/1.2 var(--sans); letter-spacing: -0.01em; }
.proof { margin: 1.25rem 0 0; padding: 0.55rem 0.7rem; border: 1px solid var(--ink);
  font: 600 var(--step--1)/1.45 var(--sans); max-width: 40rem; }

/* Contents */
.contents { padding: 1.75rem 0 0.5rem; border-bottom: var(--heavy-rule); }
.contents ol { list-style: none; margin: 0.9rem 0 0; padding: 0; border-top: 1px solid var(--rule); }
.contents li { border-bottom: 1px solid var(--rule); }
.contents li:last-child { border-bottom: 0; }
.contents a { display: grid; grid-template-columns: minmax(10rem, 15rem) minmax(0, 1fr); gap: 0.2rem 1.5rem;
  align-items: baseline; padding: 0.7rem 0; text-decoration: none; }
.contents > .label, .also > .label { color: var(--accent-ink); }
.contents li .label { color: var(--ink-faint); font-weight: 700; }
.contents .title { font: 500 var(--step-1)/1.25 var(--serif); }
.contents a:hover .title { color: var(--accent); }

/* Entries */
.entry { display: grid; grid-template-columns: minmax(10rem, 15rem) minmax(0, 1fr); gap: 0.75rem 1.5rem;
  padding: 2.25rem 0 2.5rem; border-bottom: 1px solid var(--rule); }
.entry .meta { padding-top: 0.45rem; }
.entry .meta .label { color: var(--accent-ink); }
.entry.lead .meta::before { content: ""; display: block; width: 2.5rem; height: 4px; margin-bottom: 0.8rem;
  background: var(--accent); }
.entry.lead .meta .label + .label { margin-top: 0.3rem; }
.entry h2 { margin: 0 0 1rem; font: 560 var(--step-2)/1.12 var(--serif); letter-spacing: -0.012em; text-wrap: balance; }
.entry.lead { border-bottom: var(--heavy-rule); }
.entry.lead h2 { font: 760 var(--step-3)/1.02 var(--sans); letter-spacing: -0.035em; }
.copy { max-width: 40rem; }
.copy p { margin: 0 0 1.05em; font: 400 1.18rem/1.62 var(--serif); }
.actions { margin: 1.4rem 0 0; }
.action { margin: 0 0 0.7rem; }
.cta { display: inline-block; font: 750 0.75rem/1.3 var(--sans); letter-spacing: 0.075em; text-transform: uppercase;
  color: var(--accent-ink); text-decoration: none; border-bottom: 2px solid var(--accent); padding-bottom: 0.2rem; }
.cta:hover { color: var(--ink); border-color: var(--ink); }
.cta-detail { display: block; margin-top: 0.35rem; font: 400 var(--step--1)/1.45 var(--sans); color: var(--ink-faint); }
.note { margin: 1.1rem 0 0; font: 400 var(--step--1)/1.5 var(--sans); color: var(--ink-faint); max-width: 40rem; }
.sources { list-style: none; margin: 1.4rem 0 0; padding: 0; font: 400 var(--step--1)/1.45 var(--sans);
  color: var(--ink-faint); max-width: 40rem; }
.sources li { padding: 0.3rem 0; border-top: 1px solid var(--rule); }
.sources li:last-child { border-bottom: 1px solid var(--rule); }

/* Also this week, and the colophon */
.also { padding: 2.25rem 0 0; }
.also ul { list-style: none; margin: 0.9rem 0 0; padding: 0; border-top: var(--heavy-rule); }
.also li { display: grid; grid-template-columns: minmax(10rem, 15rem) minmax(0, 1fr); gap: 0.2rem 1.5rem;
  align-items: baseline; padding: 0.75rem 0; border-bottom: 1px solid var(--rule); }
.also li .label { color: var(--ink-faint); font-weight: 700; }
.also .title { font: 400 var(--step-0)/1.4 var(--serif); }
footer { margin-top: 3rem; padding-top: 1.25rem; border-top: var(--heavy-rule); font: 400 var(--step--1)/1.55 var(--sans);
  color: var(--ink-faint); }
footer .label { margin: 0 0 0.3rem; color: var(--accent-ink); }
footer .next { margin: 0 0 0.9rem; font: 760 var(--step-1)/1.3 var(--sans); color: var(--ink); letter-spacing: -0.01em; }
footer p { margin: 0 0 0.4rem; }

@media (max-width: 40rem) {
  .contents a, .entry, .also li { grid-template-columns: minmax(0, 1fr); }
  .entry .meta { padding-top: 0; }
}
"""


def _label(e: dict, word: str) -> str:
    return f"{e['dateLabel'].upper()} · {word}"


def _sources(items) -> str:
    if not items:
        return ""
    lis = "".join(f'<li><a href="{escape(s["url"])}">{escape(s["title"])}</a></li>' for s in items)
    return f'<ul class="sources" aria-label="Sources">{lis}</ul>'


def _link(l: dict) -> str:
    bits = [l["title"] + (f", {l['author']}" if l.get("author") else "")]
    if l.get("edition"):
        bits.append(l["edition"])
    if l.get("representative"):
        bits.append("a representative work")
    bits.append(l["note"])
    return (f'<p class="action"><a class="cta" href="{escape(l["url"])}">{escape(l["label"])} ›</a>'
            f'<span class="cta-detail">{escape(" · ".join(bits))}</span></p>')


def _entry(e: dict) -> str:
    paras = "".join(f"<p>{escape(p)}</p>" for p in e["body"].split("\n\n"))
    notes = ""
    if e.get("observedDate"):
        notes += ('<p class="note">The date is the one the writer kept; the records leave the exact '
                  "date uncertain.</p>")
    if e["freeReadingWithheld"]:
        notes += ('<p class="note">A free copy exists online, but it is not yet clearly in the public domain '
                  "in every country, so DOGEAR does not link it.</p>")
    actions = f'<div class="actions">{"".join(_link(l) for l in e["links"])}</div>' if e["links"] else ""
    lead = e["role"] == "featured"
    kicker = e["kicker"]
    meta = f'<p class="label">{escape(_label(e, kicker))}</p>'
    if lead:
        meta = '<p class="label">This week</p>' + meta
    return (
        f'<article class="entry{" lead" if lead else ""}" id="{escape(e["id"])}">'
        f'<div class="meta">{meta}</div>'
        f'<div><h2>{escape(e["headline"])}</h2><div class="copy">{paras}</div>{actions}{notes}'
        f'{_sources(e["sources"])}</div></article>'
    )


def render_web(web: dict) -> str:
    proof = (
        '<p class="proof">Proof. This issue contains records still awaiting editorial approval. '
        "It is not a published edition.</p>"
        if web.get("preview")
        else ""
    )
    contents = "".join(
        f'<li><a href="#{escape(e["id"])}"><span class="label">'
        f'{escape(_label(e, e.get("contentsLabel") or e["kicker"]))}</span>'
        f'<span class="title">{escape(e["title"])}</span></a></li>'
        for e in web["entries"]
    )
    also = ""
    if web["alsoThisWeek"]:
        items = "".join(
            f'<li><span class="label">{escape(_label(a, a.get("contentsLabel") or a["kicker"]))}</span>'
            f'<span class="title">{escape(a["headline"])}</span></li>'
            for a in web["alsoThisWeek"]
        )
        also = (f'<section class="also" aria-labelledby="also"><p class="label" id="also">Also this week</p>'
                f"<ul>{items}</ul></section>")
    entries = "".join(_entry(e) for e in web["entries"])
    title = f'{web["label"]} · {web["dateline"].title()}'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{'<meta name="robots" content="noindex">' if web.get("preview") else ""}
<title>{escape(title)}</title>
<meta name="description" content="{escape(web["strapline"])}: {escape(web["dateline"].title())}">
<style>
{_FONT_FACES}
{_CSS}</style>
</head>
<body>
<main>
<header class="mast">
<p class="label">Desk · Archieval Mariano</p>
<h1>{escape(web["label"])}</h1>
<p class="strap">{escape(web["strapline"])}</p>
<p class="dateline">{escape(web["dateline"])}</p>
{proof}
</header>
<nav class="contents" aria-labelledby="contents"><p class="label" id="contents">In this issue</p><ol>{contents}</ol></nav>
{entries}
{also}
<footer>
<p class="label">Next issue</p>
<p class="next">{escape(web["nextIssue"])}</p>
<p>DOGEAR is drawn from a curated dataset of literary dates. Every entry keeps its sources.</p>
</footer>
</main>
</body>
</html>
"""
