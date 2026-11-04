"""Japanese caption layout: BudouX phrase breaks, at most 2 lines of ~11 full-width characters per caption.

Widths follow the QA rule in vg/qa.py (safe_area): full-width = 1.0 em, ASCII/half-width = 0.55 em,
780 px usable width (x 120-900); 66 px captions -> 11.8 em per line.
"""
from __future__ import annotations

import math
import unicodedata

import budoux

from .schemas import Caption, Line, LineTiming

PARSER = budoux.load_default_japanese_parser()
MAX_EM = 11.5
MAX_LINES = 2
FONT_PX = {"default": 66, "speaker_a": 64, "speaker_b": 64, "title": 84}
NO_LINE_START = set("、。，．・：；？！ー）」』】〉》〕…‥ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ")


def em(text: str, emphasis: list[str] | tuple[str, ...] = ()) -> float:
    """Match Caption.tsx: first word at the earliest position wins; accented glyphs are 1.2x."""
    widths = [1.0 if unicodedata.east_asian_width(c) in "WFA" else 0.55 for c in text]
    offset = 0
    while offset < len(text):
        matches = [(text.find(word, offset), word) for word in emphasis if word]
        matches = [(i, word) for i, word in matches if i >= 0]
        if not matches:
            break
        start, word = min(matches, key=lambda match: match[0])
        for i in range(start, start + len(word)):
            widths[i] *= 1.2
        offset = start + len(word)
    return sum(widths)


# BudouX sometimes cuts inside an inflection (増えに|くい, 読み|やすい): glue a phrase that starts with one
# of these continuations to the previous phrase when that one ends in kana.
CONTINUATIONS = ("くい", "にくい", "やすい", "づらい", "がたい", "ない", "なかっ", "たい", "たく", "ます", "まし",
                 "れる", "られ", "せる", "させ", "そう", "すぎ", "がち", "っぽ", "らし", "ぎみ")


def _is_kana(ch: str) -> bool:
    return "ぁ" <= ch <= "ヿ"


def _phrases(text: str) -> list[str]:
    out: list[str] = []
    for phrase in PARSER.parse(text):
        if (out and _is_kana(out[-1][-1]) and phrase.startswith(CONTINUATIONS)
                and em(out[-1] + phrase) <= MAX_EM):
            out[-1] += phrase
        else:
            out.append(phrase)
    return out


def _split_long(phrase: str, max_em: float, emphasis) -> list[str]:
    """A phrase wider than one line, cut into near-equal parts (no part starts with a no-line-start char)."""
    parts, rest = [], phrase
    while em(rest, emphasis) > max_em:
        target = em(rest, emphasis) / math.ceil(em(rest, emphasis) / max_em)
        candidates = [i for i in range(1, len(rest)) if em(rest[:i], emphasis) <= max_em]
        if not candidates:
            raise ValueError("caption width is smaller than one glyph")
        cut = min(candidates, key=lambda i: abs(em(rest[:i], emphasis) - target)
                  + (9 if rest[i] in NO_LINE_START else 0))
        parts.append(rest[:cut])
        rest = rest[cut:]
    return parts + [rest]


def _pieces(text: str, max_em: float, emphasis) -> list[str]:
    """Phrases (BudouX + fixes); a phrase wider than a line is cut into near-equal parts."""
    out = []
    for phrase in _phrases(text):
        out.extend(_split_long(phrase, max_em, emphasis) if em(phrase, emphasis) > max_em else [phrase])
    return out


PUNCT = set("、。！？!?」』）)…")


def _lines(pieces: list[str], max_em: float, emphasis) -> list[str] | None:
    """Best one- or two-line layout of consecutive phrases (balanced, preferring a break after punctuation)."""
    whole = "".join(pieces)
    if em(whole, emphasis) <= max_em:
        return [whole]
    best = None
    for i in range(1, len(pieces)):
        a, b = "".join(pieces[:i]), "".join(pieces[i:])
        if em(a, emphasis) <= max_em and em(b, emphasis) <= max_em and b[0] not in NO_LINE_START:
            score = abs(em(a, emphasis) - em(b, emphasis)) - (2.0 if a[-1] in PUNCT else 0.0)
            if best is None or score < best[0]:
                best = (score, [a, b])
    return best[1] if best else None


def layout(text: str, max_em: float = MAX_EM, emphasis: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Caption pages (≤ 2 lines each, '\n'-joined). Pages are cut at phrase boundaries near an even share of the
    text, preferring cuts after punctuation, so no page is left with a stray fragment."""
    rest, pages = _pieces(text.replace("\n", ""), max_em, emphasis), []
    while rest:
        whole = _lines(rest, max_em, emphasis)
        if whole:
            pages.append(whole)
            break
        total = em("".join(rest), emphasis)
        target = total / math.ceil(total / (2 * max_em * 0.9))
        best, cum = None, 0.0
        for i in range(1, len(rest)):
            cum = em("".join(rest[:i]), emphasis)
            lines = _lines(rest[:i], max_em, emphasis)
            if lines is None:
                break  # longer prefixes cannot fit either
            score = abs(cum - target) - (3.0 if rest[i - 1][-1] in PUNCT else 0.0)
            if best is None or score < best[0]:
                best = (score, i, lines)
        if best is None:
            best = (0.0, 1, [rest[0]])
        pages.append(best[2])
        rest = rest[best[1]:]
    return ["\n".join(p) for p in pages]


def captions_for_line(line: Line, timing: LineTiming, style: str = "default") -> list[Caption]:
    """Split one script line into caption pages over its audio, timed by character share."""
    pages = layout(line.text, min(MAX_EM, 780 / FONT_PX.get(style, FONT_PX["default"])), line.emphasis)
    weights = [max(1.0, em(p.replace("\n", ""))) for p in pages]
    total, t, out = sum(weights), timing.start, []
    for page, w in zip(pages, weights):
        end = t + (timing.end - timing.start) * w / total
        out.append(Caption(start=round(t, 3), end=round(end, 3), text=page, style=style,
                           emphasis=[e for e in line.emphasis if any(e in row for row in page.split("\n"))],
                           speaker=line.speaker))
        t = end
    return out
