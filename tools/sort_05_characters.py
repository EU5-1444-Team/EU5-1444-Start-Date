#!/usr/bin/env python3
"""
Sort characters in 05_characters.txt by:
  1. dynasty
  2. tag
  3. birth_date ascending within each group

Produces:
  - ../main_menu/setup/start/05_characters.txt  (sorted output, overwrites input)
  - ./sort_characters.log                        (inconsistency report)

Run from: ./tools/
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR  = Path(__file__).parent
INPUT_FILE  = SCRIPT_DIR / "../main_menu/setup/start/05_characters.txt"
OUTPUT_FILE = INPUT_FILE
LOG_FILE    = SCRIPT_DIR / "sort_characters.log"

# ── helpers ───────────────────────────────────────────────────────────────────

DATE_RE = re.compile(r'^(\d+)\.(\d*)\.?(\d*)')

def parse_date(s):
    if not s:
        return (9999, 0, 0)
    m = DATE_RE.match(s.strip())
    if not m:
        return (9999, 0, 0)
    y  = int(m.group(1))
    mo = int(m.group(2)) if m.group(2) else 0
    d  = int(m.group(3)) if m.group(3) else 0
    return (y, mo, d)

def date_str(t):
    if t == (9999, 0, 0):
        return "(unknown)"
    y, mo, d = t
    return f"{y}.{mo or '?'}.{d or '?'}"

# ── parse ─────────────────────────────────────────────────────────────────────

with open(INPUT_FILE, encoding="utf-8") as f:
    raw = f.read()

header_match = re.search(r'^(.*?character_db\s*=\s*\{)(.*)', raw, re.DOTALL)
if not header_match:
    sys.exit("Could not find character_db={ in file")

preamble = header_match.group(1)
body     = header_match.group(2)

CHAR_BLOCK_RE = re.compile(
    r'(?m)^(\s*)([a-zA-Z_\u00C0-\u024F][a-zA-Z0-9_\-\u00C0-\u024F]*)\s*=\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}',
)

characters = []

for m in CHAR_BLOCK_RE.finditer(body):
    key       = m.group(2)
    inner     = re.sub(r'#[^\n]*', '', m.group(3))  # strip comments
    raw_block = m.group(0)

    def field(name, text=inner):
        fm = re.search(rf'\b{name}\s*=\s*([^\s{{}}]+)', text)
        return fm.group(1) if fm else ""

    dynasty    = field("dynasty")
    tag        = field("tag")
    birth_date = field("birth_date")
    death_date = field("death_date")
    father     = field("father")
    mother     = field("mother")
    spouse     = field("spouse")
    culture    = field("culture")
    religion   = field("religion")

    characters.append({
        "key":         key,
        "raw":         raw_block,
        "dynasty":     dynasty or "~",
        "tag":         tag     or "~",
        "birth_date":  birth_date,
        "death_date":  death_date,
        "birth_tuple": parse_date(birth_date),
        "death_tuple": parse_date(death_date),
        "father":      father,
        "mother":      mother,
        "spouse":      spouse,
        "culture":     culture,
        "religion":    religion,
    })

print(f"Parsed {len(characters)} character blocks.")

# ── consistency checks ────────────────────────────────────────────────────────

errors   = []
warnings = []
info     = []

key_to_char = {c["key"]: c for c in characters}

# duplicate keys
seen_keys = {}
for c in characters:
    if c["key"] in seen_keys:
        errors.append(f"DUPLICATE KEY: '{c['key']}' defined more than once")
    seen_keys[c["key"]] = c

for c in characters:
    k  = c["key"]
    bt = c["birth_tuple"]
    dt = c["death_tuple"]

    # missing fields
    if not c["birth_date"]:
        warnings.append(f"NO BIRTH_DATE:  {k}")
    if not c["culture"]:
        info.append(f"NO CULTURE:     {k}")
    if not c["religion"]:
        info.append(f"NO RELIGION:    {k}")
    if c["dynasty"] == "~":
        info.append(f"NO DYNASTY:     {k}")
    if c["tag"] == "~":
        warnings.append(f"NO TAG:         {k}")

    # death before birth
    if bt != (9999, 0, 0) and dt != (9999, 0, 0) and dt < bt:
        errors.append(
            f"DEATH BEFORE BIRTH: {k}  (born {date_str(bt)}, died {date_str(dt)})"
        )

    # parent checks
    for rel in ("father", "mother"):
        parent_key = c[rel]
        if not parent_key:
            continue
        if parent_key not in key_to_char and parent_key not in ("random", "\"random\""):
            warnings.append(
                f"MISSING {rel.upper()}: {k} references '{parent_key}' which does not exist"
            )
        else:
            p  = key_to_char[parent_key]
            pt = p["birth_tuple"]
            pd = p["death_tuple"]

            if bt != (9999, 0, 0) and pt != (9999, 0, 0) and bt < pt:
                errors.append(
                    f"CHILD BORN BEFORE PARENT: {k} (born {date_str(bt)}) "
                    f"— {rel} {parent_key} (born {date_str(pt)})"
                )
            if bt != (9999, 0, 0) and pd != (9999, 0, 0) and bt[0] > pd[0] + 1:
                warnings.append(
                    f"CHILD BORN AFTER PARENT DEATH: {k} (born {date_str(bt)}) "
                    f"— {rel} {parent_key} (died {date_str(pd)})"
                )
    # spouse reference
    if c["spouse"] and c["spouse"] not in key_to_char and c["spouse"] not in ("random", "\"random\""):
        warnings.append(
            f"MISSING SPOUSE: {k} references '{c['spouse']}' which does not exist"
        )

# duplicate birth dates among siblings (same father + tag + date)
by_father = defaultdict(list)
for c in characters:
    if c["father"] and c["birth_date"]:
        by_father[(c["father"], c["tag"], c["birth_date"])].append(c["key"])

for (father, tag, bdate), siblings in by_father.items():
    if len(siblings) > 1:
        warnings.append(
            f"SAME BIRTH_DATE FOR SIBLINGS: {', '.join(siblings)}  "
            f"(father={father}, tag={tag}, date={bdate})"
        )

# ── write log ─────────────────────────────────────────────────────────────────

total = len(errors) + len(warnings) + len(info)

with open(LOG_FILE, "w", encoding="utf-8") as log:
    def section(title, items):
        if not items:
            log.write(f"\n{'─'*70}\n{title} — none\n")
            return
        log.write(f"\n{'─'*70}\n{title} ({len(items)})\n{'─'*70}\n")
        for item in items:
            log.write(f"  {item}\n")

    log.write("CHARACTER CONSISTENCY REPORT\n")
    log.write(f"Input:  {INPUT_FILE.resolve().relative_to(SCRIPT_DIR.parent.resolve())}\n")
    log.write(f"Total characters parsed: {len(characters)}\n")
    log.write(f"Total issues: {total}  "
              f"({len(errors)} errors, {len(warnings)} warnings, {len(info)} info)\n")

    section("ERRORS — definite mistakes that need fixing", errors)
    section("WARNINGS — likely mistakes, worth reviewing", warnings)
    section("INFO — minor observations", info)

print(f"Log:  {LOG_FILE.resolve().relative_to(SCRIPT_DIR.parent.resolve())}")
print(f"      {len(errors)} errors, {len(warnings)} warnings, {len(info)} info")

# ── sort ──────────────────────────────────────────────────────────────────────

def date_sort(group):
    return sorted(group, key=lambda c: (c["birth_tuple"], c["key"]))

groups = defaultdict(list)
for c in characters:
    groups[(c["dynasty"], c["tag"])].append(c)

sorted_group_keys = sorted(groups.keys(), key=lambda g: (g[0], g[1]))

sorted_characters = []
for gk in sorted_group_keys:
    sorted_characters.extend(date_sort(groups[gk]))

print(f"Sorted {len(sorted_characters)} characters.")

# ── write output ──────────────────────────────────────────────────────────────

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write(preamble + "\n\n")

    current_group = (None, None)
    for c in sorted_characters:
        gk = (c["dynasty"], c["tag"])
        if gk != current_group:
            dynasty_label = c["dynasty"] if c["dynasty"] != "~" else "(no dynasty)"
            tag_label     = c["tag"]     if c["tag"]     != "~" else "(no tag)"
            f.write(f"\n    ### {dynasty_label} | {tag_label} ###\n\n")
            current_group = gk
        f.write(c["raw"].rstrip() + "\n\n")

    f.write("}\n")

with open(OUTPUT_FILE, encoding="utf-8") as f:
    content = f.read()

content = '\n'.join(line.rstrip() for line in content.splitlines())
content = re.sub(r'\n{3,}', '\n\n', content)

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write(content)

print(f"Done! Output written to {OUTPUT_FILE.resolve().relative_to(SCRIPT_DIR.parent.resolve())}")