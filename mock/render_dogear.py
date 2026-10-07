#!/usr/bin/env python3
"""DOGEAR desktop mock renderer and copy-fit checker (review aid, not firmware).

Renders a DOGEAR issue JSON into 480x800 1-bit PNG screens that approximate the
X4 / X4 Pro portrait display, and checks dataset copy against the same layout.

Fidelity: the same NotoSans / NotoSerif TTFs the firmware rasterizes, at the
built-in sizes only (12/14/16/18), converted the way fontconvert.py does it
(size pt at 150 DPI, so ppem = size * 150 / 72). Layout is in device pixels.
QR screens use segno with ECC L, like the firmware's ECC_LOW + smallest-fit
version; module scaling is an integer, as on device.

Page model (the main issue; the pager counts these):
    1        cover / contents
    2..N-1   one literary item per screen (a rare second screen only when the
             record sets copy.allowSecondPage; it shares its item's page number)
    N        EVERYTHING IS DOGEARED HERE: QR to the full web edition
Secondary screens (outside the count): an item's READ ONLINE / FIND THE BOOK QR,
opened from that item and closed with Back.

    mock/.venv/bin/python mock/render_dogear.py out/dogear-2026-11-23.json --out out/pages
    mock/.venv/bin/python mock/render_dogear.py --fitcheck dataset/data/literary-dates.json
"""

from __future__ import annotations

import os
import argparse
import io
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import segno
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    sys.exit("Needs Pillow and segno:  python3 -m venv mock/.venv && mock/.venv/bin/pip install Pillow segno")

W, H = 480, 800
BLACK, WHITE = 0, 255

# The firmware's own TTFs. A standalone DOGEAR repository vendors them under mock/fonts (OFL);
# DOGEAR_FONT_DIR overrides both.
_VENDORED = Path(__file__).resolve().parent / "fonts"
FONT_DIR = Path(os.environ.get("DOGEAR_FONT_DIR") or (
    _VENDORED if _VENDORED.is_dir()
    else Path(__file__).resolve().parents[2] / "crosspoint-reader" / "lib" / "EpdFont" / "builtinFonts" / "source"))

# Layout (device px). Theme-independent, like GOTO: fixed margins and safe zones.
MARGIN = 24
CONTENT_W = W - 2 * MARGIN
TOP_SAFE = 16
BOTTOM_SAFE = 28
HEAD_RULE_GAP = 8
RULE_CONTENT_GAP = 18
FEATURED_RULE = 3
FEATURED_RULE_GAP = 14
KICKER_HEADLINE_GAP = 10
HEADLINE_BODY_GAP = 14
FEATURED_HEADLINE_BODY_GAP = 20  # featured items get air, not more words
BODY_FOOTER_GAP = 12
RULE_PAGER_GAP = 10
HEADLINE_MAX_LINES = 3
LEADING = 1.0  # body line advance as a multiple of advanceY; 1.0 = GOTO / device default

# The firmware's line advance (EpdFontData.advanceY) for the built-in Noto sizes,
# identical for sans and serif and every weight. Read from builtinFonts/*.h.
ADVANCE_Y = {12: 34, 14: 40, 16: 45, 18: 51}

# Cover contents rows: a 12pt regular sans label close over a serif title. The
# label's step is its cap height plus a few pixels, not a full line, so label
# and title read as one unit and rows separate by the title's own leading.
CONTENTS_LABEL_STEP = 22
# (lead title pt, row title pt, gap between rows), largest first: the lead
# steps down before the rows do, and 12pt rows are the last resort.
COVER_DATELINE_GAP = 18
COVER_LEAD_GAP = 14
COVER_RULE_GAP = 12
COVER_HEAD_GAP = 8
COVER_LADDER = ((18, 14, 6), (16, 14, 4), (16, 14, 2), (16, 12, 6))

BODY_SIZES = (14, 12)  # 14 preferred; 12 is the controlled fallback
HEADLINE_SIZES = {"featured": (18, 16), "standard": (16, 14)}


def _px(pt: int) -> int:
    return round(pt * 150 / 72)


_cache: dict = {}


def font(family: str, pt: int, bold: bool = False, italic: bool = False) -> ImageFont.FreeTypeFont:
    key = (family, pt, bold, italic)
    if key not in _cache:
        style = ("Bold" if bold else "") + ("Italic" if italic else "") or "Regular"
        f = ImageFont.truetype(str(FONT_DIR / family / f"{family}-{style}.ttf"), _px(pt))
        f.dogear_pt = pt
        _cache[key] = f
    return _cache[key]


def sans(pt, bold=False):
    return font("NotoSans", pt, bold)


def serif(pt, bold=False, italic=False):
    return font("NotoSerif", pt, bold, italic)


def line_h(f: ImageFont.FreeTypeFont) -> int:
    """Line advance exactly as the device's GfxRenderer::getLineHeight gives it."""
    return ADVANCE_Y[f.dogear_pt]


def advance(f) -> int:
    return round(line_h(f) * LEADING)


def wrap(text: str, f: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if f.getlength(trial) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def ellipsize(lines: list[str], f, width: int, max_lines: int) -> list[str]:
    if len(lines) <= max_lines:
        return lines
    kept = lines[:max_lines]
    last = kept[-1]
    while last and f.getlength(last + "…") > width:
        last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
    kept[-1] = last + "…"
    return kept


class Canvas:
    def __init__(self):
        self.img = Image.new("L", (W, H), WHITE)
        self.d = ImageDraw.Draw(self.img)

    def text(self, x, y, s, f, tracking=0):
        if not tracking:
            self.d.text((x, y), s, font=f, fill=BLACK)
            return
        for ch in s:
            self.d.text((x, y), ch, font=f, fill=BLACK)
            x += f.getlength(ch) + tracking

    def centered(self, y, s, f, tracking=0):
        width = f.getlength(s) + tracking * max(0, len(s) - 1)
        self.text((W - width) / 2, y, s, f, tracking)

    def rule(self, y, weight=1, x0=MARGIN, x1=W - MARGIN):
        self.d.rectangle([x0, y, x1, y + weight - 1], fill=BLACK)

    def paste_qr(self, url: str, top: int, max_size: int) -> int:
        """Centered QR, integer module scale, 4-module quiet zone. Returns height."""
        qr = segno.make(url, error="l", micro=False, boost_error=False)
        modules = qr.symbol_size(border=4)[0]
        scale = max(1, max_size // modules)
        buf = io.BytesIO()
        qr.save(buf, kind="png", scale=scale, border=4)
        buf.seek(0)
        img = Image.open(buf).convert("L")
        self.img.paste(img, ((W - img.width) // 2, top))
        return img.height

    def finish(self) -> Image.Image:
        return self.img.point(lambda v: 0 if v < 128 else 255, mode="1")


# ---- shared chrome --------------------------------------------------------


def head_bottom() -> int:
    return TOP_SAFE + line_h(sans(12, True)) + HEAD_RULE_GAP + 1 + RULE_CONTENT_GAP


def running_head(c: Canvas, issue: dict) -> int:
    f, fr = sans(12, True), sans(12)
    c.text(MARGIN, TOP_SAFE, issue["label"], f, tracking=2)
    room = CONTENT_W - f.getlength(issue["label"]) - 2 * len(issue["label"]) - 16
    line = issue["dateline"] if fr.getlength(issue["dateline"]) <= room else issue["datelineShort"]
    c.text(W - MARGIN - fr.getlength(line), TOP_SAFE, line, fr)
    c.rule(TOP_SAFE + line_h(f) + HEAD_RULE_GAP)
    return head_bottom()


def footer_top() -> int:
    return H - BOTTOM_SAFE - line_h(sans(12)) - RULE_PAGER_GAP


def footer(c: Canvas, left: str, right: str | None = None) -> None:
    f = sans(12)
    pager_y = H - BOTTOM_SAFE - line_h(f)
    c.rule(pager_y - RULE_PAGER_GAP)
    c.text(MARGIN, pager_y, left, sans(12, left.startswith("«")))
    if right:
        fb = sans(12, True)
        c.text(W - MARGIN - fb.getlength(right), pager_y, right, fb)


BODY_BOTTOM = footer_top() - BODY_FOOTER_GAP


# ---- item layout ----------------------------------------------------------


@dataclass
class ItemLayout:
    """How one item lands on the device: the fit the editor is judged by."""

    kicker: list[str]
    headline_pt: int
    headline: list[str]
    body_pt: int
    pages: list[list[str]]  # body lines per screen
    fit: str  # "14" | "12" | "second-page" | "overflow"


def kicker_lines(date_label: str, kicker: str) -> list[str]:
    """``18 OCT · PUBLISHED 1851 · 175 YEARS`` on one line; wraps only if it must."""
    f = sans(12, True)
    full = f"{date_label.upper()} · {kicker}"
    return [full] if f.getlength(full) <= CONTENT_W else wrap(full, f, CONTENT_W)


def _headline(text: str, role: str) -> tuple[int, list[str]]:
    """GOTO's headline ladder: the largest size whose complete headline fits."""
    sizes = HEADLINE_SIZES[role]
    for pt in sizes:
        lines = wrap(text, serif(pt, True), CONTENT_W)
        if len(lines) <= HEADLINE_MAX_LINES:
            return pt, lines
    f = serif(sizes[-1], True)
    return sizes[-1], ellipsize(wrap(text, f, CONTENT_W), f, CONTENT_W, HEADLINE_MAX_LINES)


def _body_top(role: str, n_kicker: int, headline_pt: int, n_headline: int) -> int:
    y = head_bottom()
    if role == "featured":
        y += FEATURED_RULE + FEATURED_RULE_GAP
    y += n_kicker * line_h(sans(12, True)) + KICKER_HEADLINE_GAP
    y += n_headline * line_h(serif(headline_pt, True))
    return y + (FEATURED_HEADLINE_BODY_GAP if role == "featured" else HEADLINE_BODY_GAP)


def _continuation_top() -> int:
    return head_bottom() + line_h(serif(12, italic=True)) + HEADLINE_BODY_GAP


def layout_item(date_label: str, kick: str, headline: str, body: str, role: str,
                allow_second_page: bool) -> ItemLayout:
    klines = kicker_lines(date_label, kick)
    hpt, hlines = _headline(headline, role)
    top = _body_top(role, len(klines), hpt, len(hlines))
    for pt in BODY_SIZES:
        lines = wrap(body, serif(pt), CONTENT_W)
        if len(lines) * advance(serif(pt)) <= BODY_BOTTOM - top:
            return ItemLayout(klines, hpt, hlines, pt, [lines], str(pt))
    if allow_second_page:
        for pt in BODY_SIZES:
            lines = wrap(body, serif(pt), CONTENT_W)
            first = (BODY_BOTTOM - top) // advance(serif(pt))
            second = (BODY_BOTTOM - _continuation_top()) // advance(serif(pt))
            if len(lines) <= first + second:
                return ItemLayout(klines, hpt, hlines, pt, [lines[:first], lines[first:]], "second-page")
    # Last resort, never expected in a published issue (fitcheck blocks it):
    # truncate at 12pt, as GOTO does.
    f = serif(BODY_SIZES[-1])
    room = (BODY_BOTTOM - top) // advance(f)
    lines = ellipsize(wrap(body, f, CONTENT_W), f, CONTENT_W, room)
    return ItemLayout(klines, hpt, hlines, BODY_SIZES[-1], [lines], "overflow")


def draw_item(c: Canvas, issue: dict, e: dict, lay: ItemLayout, part: int) -> None:
    y = running_head(c, issue)
    if part > 0:
        # Quiet continuation: the item's title in a reduced header, nothing else.
        c.text(MARGIN, head_bottom(), e["title"], serif(12, italic=True))
        y = _continuation_top()
    else:
        if e["role"] == "featured":
            c.rule(y, FEATURED_RULE)
            y += FEATURED_RULE + FEATURED_RULE_GAP
        kf = sans(12, True)
        for ln in lay.kicker:
            c.text(MARGIN, y, ln, kf)
            y += line_h(kf)
        y += KICKER_HEADLINE_GAP
        hf = serif(lay.headline_pt, True)
        for ln in lay.headline:
            c.text(MARGIN, y, ln, hf)
            y += line_h(hf)
        y += FEATURED_HEADLINE_BODY_GAP if e["role"] == "featured" else HEADLINE_BODY_GAP
    bf = serif(lay.body_pt)
    for ln in lay.pages[part]:
        c.text(MARGIN, y, ln, bf)
        y += advance(bf)


# ---- cover / contents -----------------------------------------------------


def draw_cover(c: Canvas, issue: dict) -> None:
    y = TOP_SAFE
    wf = sans(18, True)
    c.text(MARGIN, y, issue["label"], wf, tracking=6)
    y += line_h(wf) + 2
    sf = serif(12, italic=True)
    c.text(MARGIN, y, issue["strapline"], sf)
    y += line_h(sf) + 12
    c.rule(y, FEATURED_RULE)
    y += FEATURED_RULE + 14
    for text, df in ((issue["dateline"], sans(16, True)), (issue["dateline"], sans(14, True)),
                     (issue["datelineShort"], sans(16, True)), (issue["datelineShort"], sans(14, True))):
        if df.getlength(text) <= CONTENT_W:
            break
    c.text(MARGIN, y, text, df)
    y += line_h(df) + COVER_DATELINE_GAP

    featured = [e for e in issue["entries"] if e["role"] == "featured"]
    others = [e for e in issue["entries"] if e["role"] != "featured"]
    kf = sans(12, True)

    def plan(feat_pt, row_pt, row_gap):
        """Heights for a candidate size combination (the cover's own ladder)."""
        feat = []
        for e in featured:
            f = serif(feat_pt, True)
            feat.append((e, f, ellipsize(wrap(e["title"], f, CONTENT_W), f, CONTENT_W, 2)))
        rows = []
        for e in others:
            f = serif(row_pt)
            rows.append((e, f, ellipsize(wrap(e["title"], f, CONTENT_W), f, CONTENT_W, 2)))
        h = sum(len(kicker_lines(e["dateLabel"], e["kicker"])) * line_h(kf) + 4 + len(ls) * line_h(f) + COVER_LEAD_GAP
                for e, f, ls in feat)
        if rows:
            h += COVER_RULE_GAP + line_h(kf) + COVER_HEAD_GAP + sum(CONTENTS_LABEL_STEP + len(ls) * line_h(f) + row_gap for _, f, ls in rows)
        return feat, rows, h

    for feat_pt, row_pt, row_gap in COVER_LADDER:
        feat, rows, h = plan(feat_pt, row_pt, row_gap)
        if y + h <= BODY_BOTTOM:
            break
    else:
        print(f"  WARNING: {issue['issueId']} cover contents overflow by {y + h - BODY_BOTTOM}px")
    print(f"  cover: lead {feat_pt}pt, contents {row_pt}pt, row gap {row_gap}px, "
          f"{BODY_BOTTOM - y - h}px spare")

    # Featured: kicker over a large title. The hierarchy does the work.
    for e, f, lines in feat:
        for ln in kicker_lines(e["dateLabel"], e["kicker"]):
            c.text(MARGIN, y, ln, kf)
            y += line_h(kf)
        y += 4
        for ln in lines:
            c.text(MARGIN, y, ln, f)
            y += line_h(f)
        y += COVER_LEAD_GAP
    if not rows:
        return
    c.rule(y)
    y += COVER_RULE_GAP
    c.text(MARGIN, y, "ALSO THIS WEEK", kf, tracking=1)
    y += line_h(kf) + COVER_HEAD_GAP
    # The rest: what kind of entry it is, in words, over its title.
    for e, f, lines in rows:
        c.text(MARGIN, y, contents_label(e), sans(12))
        y += CONTENTS_LABEL_STEP
        for ln in lines:
            c.text(MARGIN, y, ln, f)
            y += line_h(f)
        y += row_gap


def contents_label(e: dict) -> str:
    """``24 NOV · PUBLISHED``, ``27 NOV · OBSERVANCE · PH``."""
    return f"{e['dateLabel'].upper()} · {e.get('contentsLabel') or e['kicker']}"


def _day(e: dict) -> str:
    return e["dateLabel"].upper()  # "26 NOV"


# ---- final page and secondary QR views -----------------------------------


def _balanced(text: str, f) -> list[str]:
    """One line if it fits, else two lines of similar length (no lone last word)."""
    if f.getlength(text) <= CONTENT_W:
        return [text]
    words = text.split()
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if f.getlength(a) <= CONTENT_W and f.getlength(b) <= CONTENT_W:
            score = abs(f.getlength(a) - f.getlength(b))
            if best is None or score < best[0]:
                best = (score, [a, b])
    return best[1] if best else wrap(text, f, CONTENT_W)


def centered_fit(c: Canvas, y: int, text: str, fonts: list, tracking: int = 0) -> int:
    """Draw centered at the first size that fits one line; else balance the
    smallest over two lines. Returns the y below the text."""
    for f in fonts:
        if f.getlength(text) + tracking * (len(text) - 1) <= CONTENT_W:
            c.centered(y, text, f, tracking)
            return y + line_h(f)
    f = fonts[-1]
    for ln in _balanced(text, f):
        c.centered(y, ln, f, tracking)
        y += line_h(f)
    return y


FINAL_QR_MAX = 330


def draw_final(c: Canvas, issue: dict) -> None:
    """The issue's last counted page: the way into the full web edition."""
    y = running_head(c, issue) + 10
    y = centered_fit(c, y, "EVERYTHING IS DOGEARED HERE", [sans(16, True), sans(14, True), sans(12, True)])
    y += 12
    y += c.paste_qr(issue["editionUrl"], y, FINAL_QR_MAX) + 10
    y = centered_fit(c, y, "SCAN FOR THIS WEEK\u2019S FULL EDITION", [sans(12, True)])
    y = centered_fit(c, y + 2, "Stories, sources and reading links online.", [serif(12, italic=True)])
    y += 22
    c.rule(y, x0=W // 2 - 40, x1=W // 2 + 40)
    y += 20
    y = centered_fit(c, y, "NEXT ISSUE", [sans(12, True)], tracking=1)
    centered_fit(c, y + 2, issue["nextIssue"], [serif(16, True), serif(14, True)])


def draw_reading_qr(c: Canvas, issue: dict, e: dict) -> None:
    """Secondary reading screen: says exactly which work opens, by whom, and where.

    READ ONLINE / title / author / [edition] / [A representative work] / QR /
    "Free on Project Gutenberg". The item page's action never says FREE; this
    screen does, because it names the provider.
    """
    cta = e["cta"]
    y = running_head(c, issue)
    kf = sans(12, True)
    c.text(MARGIN, y, cta["label"], kf, tracking=1)
    y += line_h(kf) + KICKER_HEADLINE_GAP
    hf = serif(16, True)
    for ln in ellipsize(wrap(cta["title"], hf, CONTENT_W), hf, CONTENT_W, 2):
        c.text(MARGIN, y, ln, hf)
        y += line_h(hf)
    if cta.get("author"):
        af = serif(14)
        c.text(MARGIN, y, cta["author"], af)
        y += line_h(af)
    notes = []
    if cta.get("edition"):
        notes.append(cta["edition"][0].upper() + cta["edition"][1:])
    if cta.get("representative"):
        notes.append("A representative work")
    nf = serif(12, italic=True)
    for note in notes:
        for ln in ellipsize(wrap(note, nf, CONTENT_W), nf, CONTENT_W, 2):
            c.text(MARGIN, y, ln, nf)
            y += line_h(nf)
    cf = sans(12)
    y += 20
    room = BODY_BOTTOM - 20 - line_h(cf) - 12 - y
    y += c.paste_qr(cta["url"], y, min(CONTENT_W, room)) + 12
    c.centered(y, cta["note"], cf)


# ---- driver ---------------------------------------------------------------


@dataclass
class Screen:
    kind: str  # cover | item | final | qr
    number: int | None  # main pager number; None for secondary screens
    entry: dict | None = None
    layout: ItemLayout | None = None
    part: int = 0
    notes: list[str] = field(default_factory=list)


def build_screens(issue: dict) -> tuple[list[Screen], list[Screen]]:
    main = [Screen("cover", 1)]
    secondary = []
    for i, e in enumerate(issue["entries"], start=2):
        lay = layout_item(e["dateLabel"], e["kicker"], e["headline"], e["body"], e["role"], e.get("secondPage", False))
        for part in range(len(lay.pages)):
            main.append(Screen("item", i, e, lay, part))
        if e["cta"]:
            secondary.append(Screen("qr", None, e))
    main.append(Screen("final", len(issue["entries"]) + 2))
    return main, secondary


def render(issue: dict, out: Path) -> tuple[list[Path], list[Path]]:
    out.mkdir(parents=True, exist_ok=True)
    main, secondary = build_screens(issue)
    total = main[-1].number
    main_paths, sec_paths = [], []
    for idx, s in enumerate(main, start=1):
        c = Canvas()
        right = None
        if s.kind == "cover":
            draw_cover(c, issue)
            right = "NEXT ›"
        elif s.kind == "item":
            draw_item(c, issue, s.entry, s.layout, s.part)
            last_part = s.part == len(s.layout.pages) - 1
            if last_part and s.entry["cta"]:
                right = s.entry["cta"]["label"] + " ›"
            part = f" (screen {s.part + 1} of {len(s.layout.pages)})" if len(s.layout.pages) > 1 else ""
            print(f"  {s.number:>2}: {s.entry['id']:<40} headline {s.layout.headline_pt}pt, "
                  f"body {s.layout.body_pt}pt, fit {s.layout.fit}{part}")
        else:
            draw_final(c, issue)
        footer(c, f"{s.number} / {total}", right)
        p = out / f"{issue['issueId']}-{idx:02d}.png"
        c.finish().save(p)
        main_paths.append(p)
    for s in secondary:
        c = Canvas()
        draw_reading_qr(c, issue, s.entry)
        footer(c, "« BACK")
        p = out / f"{issue['issueId']}-x-{s.entry['id']}.png"
        c.finish().save(p)
        sec_paths.append(p)
    return main_paths, sec_paths


def contact_sheet(issue: dict, main: list[Path], secondary: list[Path], dest: Path, per_row: int = 5) -> None:
    """Main issue in order, then the secondary QR views, each under a label."""
    gap, label_h = 16, 44
    lf = sans(12, True)

    def rows(n):
        return (n + per_row - 1) // per_row

    height = label_h + rows(len(main)) * (H + gap) + gap
    if secondary:
        height += label_h + rows(len(secondary)) * (H + gap) + gap
    sheet = Image.new("L", (per_row * (W + gap) + gap, height), 205)
    d = ImageDraw.Draw(sheet)
    y = 0
    title = f"{issue['issueId']} · main issue, {len(main)} screens"
    if issue.get("preview"):
        title += " · PROOF (records awaiting approval)"
    sections = ((title, main), ("secondary views · opened from an item, closed with Back · not counted", secondary))
    for label, paths in sections:
        if not paths:
            continue
        d.text((gap, y + 14), label, font=lf, fill=40)
        y += label_h
        for i, p in enumerate(paths):
            r, col = divmod(i, per_row)
            sheet.paste(Image.open(p).convert("L"), (gap + col * (W + gap), y + r * (H + gap)))
        y += rows(len(paths)) * (H + gap) + gap
    sheet.save(dest)


# ---- fit check ------------------------------------------------------------

# The firmware's compiled font tables. A standalone DOGEAR repository vendors just
# their Unicode ranges as mock/fonts/device-glyphs.json (tools/firmware_contract.py).
BUILTIN = Path(__file__).resolve().parents[2] / "crosspoint-reader" / "lib" / "EpdFont" / "builtinFonts"
DEVICE_GLYPHS = _VENDORED / "device-glyphs.json"


def device_codepoints(font_name: str) -> list[tuple[int, int]]:
    """Unicode ranges a built-in font can draw: the vendored table, else its generated header."""
    import json as _json
    import re as _re

    if DEVICE_GLYPHS.is_file():
        return [tuple(r) for r in _json.loads(DEVICE_GLYPHS.read_text(encoding="utf-8"))["fonts"][font_name]]
    text = (BUILTIN / f"{font_name}.h").read_text(encoding="utf-8")
    block = text.split(f"{font_name}Intervals[] = {{", 1)[1].split("};", 1)[0]
    return [(int(a, 16), int(b, 16)) for a, b in _re.findall(r"\{\s*(0x[0-9A-Fa-f]+),\s*(0x[0-9A-Fa-f]+),", block)]


def missing_glyphs(text: str, ranges: list[tuple[int, int]]) -> set[str]:
    return {ch for ch in text if ch not in "\n" and not any(a <= ord(ch) <= b for a, b in ranges)}


def fitcheck(dataset_path: Path) -> int:
    """Lay out every record's digest copy as a standard and as a featured item.

    14 = fits at the preferred size; 12 = needs the fallback (tighten the copy
    if you can); second-page = runs to a second screen, allowed by the editor;
    overflow = does not fit and is not allowed a second screen (blocks publishing).
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dogear.dataset import load_dataset  # noqa: E402
    from dogear.issue import typeset  # noqa: E402

    ds = load_dataset(dataset_path)
    problems = 0
    body_ranges = device_codepoints("notoserif_14_regular")
    head_ranges = device_codepoints("notoserif_18_bold")
    print(f"{'record':<40} {'words':>5}  standard      featured")
    for r in ds.records:
        res = []
        for role in ("standard", "featured"):
            # Worst-case kicker: the widest form the issue builder can produce.
            kick = "OBSERVANCE" if r.type == "observance" else f"PUBLISHED {r.year}"
            lay = layout_item("Wed 30 Sep", kick, typeset(r.headline), typeset(r.digest), role, r.allow_second_page)
            res.append(lay.fit)
        flag = ""
        glyphs = missing_glyphs(typeset(r.digest), body_ranges) | missing_glyphs(typeset(r.headline), head_ranges)
        if glyphs:
            flag, problems = f"  << NO GLYPH for {''.join(sorted(glyphs))}", problems + 1
        elif "overflow" in res:
            flag, problems = "  << OVERFLOW", problems + 1
        elif "second-page" in res:
            flag = "  (second page allowed)"
        elif "12" in res:
            flag = "  (12pt fallback)"
        print(f"{r.id:<40} {len(r.digest.split()):>5}  {res[0]:<13} {res[1]:<13}{flag}")
    print(f"\n{problems} record(s) overflow one screen without permission for a second, or use a glyph the device lacks.")
    return 1 if problems else 0


def specimen(issue: dict, entry_id: str, dataset_path: Path, dest: Path) -> None:
    """Template proof of the rare two-screen item: the entry's web-length
    (expanded) copy, laid out as if the editor had set copy.allowSecondPage."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dogear.dataset import load_dataset  # noqa: E402
    from dogear.issue import typeset  # noqa: E402

    rec = load_dataset(dataset_path).by_id(entry_id)
    e = next(x for x in issue["entries"] if x["id"] == entry_id)
    number = issue["entries"].index(e) + 2
    total = len(issue["entries"]) + 2
    e = {**e, "body": typeset(rec.expanded or rec.digest), "secondPage": True}
    lay = layout_item(e["dateLabel"], e["kicker"], e["headline"], e["body"], e["role"], True)
    screens = []
    for part in range(len(lay.pages)):
        c = Canvas()
        draw_item(c, issue, e, lay, part)
        last = part == len(lay.pages) - 1
        footer(c, f"{number} / {total}", e["cta"]["label"] + " ›" if last and e["cta"] else None)
        screens.append(c.finish().convert("L"))
    gap, label_h = 16, 44
    sheet = Image.new("L", (gap + len(screens) * (W + gap), H + label_h + gap), 205)
    ImageDraw.Draw(sheet).text((gap, 14), f"specimen · two-screen item · both are page {number}",
                               font=sans(12, True), fill=40)
    for i, im in enumerate(screens):
        sheet.paste(im, (gap + i * (W + gap), label_h))
    sheet.save(dest)
    print(f"  specimen {entry_id}: fit {lay.fit}, body {lay.body_pt}pt, lines {[len(p) for p in lay.pages]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("issue", type=Path, nargs="?")
    ap.add_argument("--out", type=Path, default=Path("out/pages"))
    ap.add_argument("--fitcheck", type=Path, metavar="DATASET")
    ap.add_argument("--specimen", metavar="ENTRY_ID", help="also render a two-screen template proof of this entry")
    ap.add_argument("--dataset", type=Path,
                    default=Path(__file__).resolve().parents[1] / "dataset" / "data" / "literary-dates.json")
    args = ap.parse_args()
    if args.fitcheck:
        return fitcheck(args.fitcheck)
    if not args.issue:
        ap.error("give an issue JSON or --fitcheck DATASET")
    issue = json.loads(args.issue.read_text(encoding="utf-8"))
    print(f"{issue['issueId']}:")
    main_paths, sec_paths = render(issue, args.out)
    sheet = args.out / f"{issue['issueId']}-sheet.png"
    contact_sheet(issue, main_paths, sec_paths, sheet)
    if args.specimen:
        specimen(issue, args.specimen, args.dataset, args.out / f"{issue['issueId']}-specimen-two-screen.png")
    print(f"  {len(main_paths)} main screens + {len(sec_paths)} secondary -> {args.out}  (sheet: {sheet.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
