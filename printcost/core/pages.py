"""The "Pages (copies)" field: which pages to print and how many copies of each.

    1-6, 8-9, 11          pages, one copy each (the old format still works)
    1-2 [2], 3 (4), 4     2 copies of pages 1-2, then 4 copies of page 3, then 1 copy of page 4
    all (2), 3 (1)        every page twice, except page 3 which prints once
    (2)                   same as all (2)

The number of copies goes in (), [] or {}. "all" has the lowest priority: any page you list yourself uses
its own number. For other overlaps, the entry written last wins.
"""
import re
from collections import Counter

MAX_COPIES = 999
HINT = "Use page numbers like 1-6, 8-9 (2), 11 [3], or all (2). Copies go in (), [] or {}"

_ENTRY = re.compile(
    r"(?:(?P<pages>all\b|\d+(?:\s*[-\u2013\u2014]\s*\d+)?)\s*)?"
    r"(?:\(\s*(?P<a>\d+)\s*\)|\[\s*(?P<b>\d+)\s*\]|\{\s*(?P<c>\d+)\s*\})?"
    r"(?=[\s,;]|$)", re.I)
_SEP = re.compile(r"[\s,;]*")


def _entries(text, page_count):
    """[(start, end, copies or None, is_all)] in the order written. Raises ValueError with a readable message."""
    text = (text or "").strip()
    hint = f"{HINT} (this file has {page_count} page(s))."
    out, pos = [], 0
    while True:
        pos = _SEP.match(text, pos).end()
        if pos >= len(text):
            return out
        m = _ENTRY.match(text, pos)
        if not m or m.end() == pos:
            bad = re.split(r"[\s,;]", text[pos:], maxsplit=1)[0]
            raise ValueError(f'Can\'t read "{bad}". {hint}')
        pos = m.end()
        raw = m.group(0).strip()
        copies = next((int(g) for g in (m.group("a"), m.group("b"), m.group("c")) if g is not None), None)
        if copies is not None and not 1 <= copies <= MAX_COPIES:
            raise ValueError(f'"{raw}": copies must be between 1 and {MAX_COPIES}.')
        what = m.group("pages")
        if what is None or what.lower() == "all":  # a lone (2) means all (2)
            out.append((1, page_count, copies, True))
            continue
        nums = [int(n) for n in re.split(r"\s*[-\u2013\u2014]\s*", what)]
        start, end = nums[0], nums[-1]
        if start > end:
            raise ValueError(f'"{what}" goes backwards. {hint}')
        if start < 1 or end > page_count:
            raise ValueError(f'"{what}" is outside the document. {hint}')
        out.append((start, end, copies, False))


def parse_page_ranges(text, page_count):
    """Which pages are used, ignoring copies: '1-6, 8 (2)' -> [1, 2, 3, 4, 5, 6, 8]. Blank or 'all' -> None."""
    entries = _entries(text, page_count)
    if not entries or any(e[3] for e in entries):
        return None
    pages = set()
    for start, end, _, _ in entries:
        pages.update(range(start, end + 1))
    return sorted(pages) or None


def _passes(pages, copies):
    """Collated: the whole set once, then again, ... pages with fewer copies drop out of the later rounds."""
    rounds = max((copies[p] for p in pages), default=0)
    return [p for k in range(1, rounds + 1) for p in pages if copies[p] >= k]


def parse_page_plan(text, page_count):
    """Every page to print, in print order, repeated once per copy. None means all pages, one copy each.
    Without any copies this is the same sorted list parse_page_ranges gives."""
    entries = _entries(text, page_count)
    if not entries:
        return None
    all_copies = next((e[2] or 1 for e in reversed(entries) if e[3]), None)
    if all_copies is not None:  # "all" is the base; anything listed beside it only changes its own pages
        copies = {p: all_copies for p in range(1, page_count + 1)}
        for start, end, n, is_all in entries:
            if not is_all:
                for p in range(start, end + 1):
                    copies[p] = n or 1
        if all(c == 1 for c in copies.values()):
            return None
        return _passes(list(range(1, page_count + 1)), copies)

    copies, owner = {}, {}
    for i, (start, end, n, _) in enumerate(entries):
        for p in range(start, end + 1):
            copies[p], owner[p] = n or 1, i  # the entry written last wins
    if all(c == 1 for c in copies.values()):
        return sorted(copies)
    plan = []
    for i, (start, end, _, _) in enumerate(entries):
        plan += _passes([p for p in range(start, end + 1) if owner[p] == i], copies)
    return plan


def format_page_ranges(pages):
    """[1, 2, 3, 5, 7, 8] -> '1-3, 5, 7-8'. A page that repeats counts once."""
    out, start, prev = [], None, None
    for n in sorted(set(pages)):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}-{prev}" if prev > start else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}-{prev}" if prev > start else str(start))
    return ", ".join(out)


def has_copies(pages):
    return bool(pages) and len(set(pages)) != len(pages)


def format_page_plan(pages):
    """[1, 2, 1, 2, 3, 3, 3, 3, 4] -> '1-2 x2, 3 x4, 4'. Without repeats it is the same as format_page_ranges."""
    count = Counter(pages)
    out, group = [], []

    def flush():
        if group:
            span = f"{group[0]}-{group[-1]}" if len(group) > 1 else str(group[0])
            n = count[group[0]]
            out.append(f"{span} x{n}" if n > 1 else span)
            group.clear()

    for p in sorted(count):
        if group and (p != group[-1] + 1 or count[p] != count[group[0]]):
            flush()
        group.append(p)
    flush()
    return ", ".join(out)


def group_copies(per_page):
    """Merges the repeated rows of the same page into one: adds "copies" and adds up the cost."""
    groups = {}
    for row in per_page:
        g = groups.get(row["page"])
        if g is None:
            groups[row["page"]] = dict(row, copies=1)
        else:
            g["copies"] += 1
            for key in ("ink_cost", "page_cost", "total"):
                g[key] += row[key]
    return list(groups.values())