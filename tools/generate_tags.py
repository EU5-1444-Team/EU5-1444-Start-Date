#!/usr/bin/env python3
"""Generate `in_game/setup/countries/1444_tags.info`.

Run without arguments from the repository root:
    python3 tools/generate_tags.py

It scans `in_game/setup/countries/*.txt` for 3-letter tags and attempts to determine each country's
display name from (in order): inline name, name= inside the block, a comment near the block,
localization files, or falling back to the tag itself.
"""

import io
import os
import re
from glob import glob

# Defaults (no args required)
COUNTRIES_DIR = os.path.join('in_game', 'setup', 'countries')
OUTPUT_PATH = os.path.join(COUNTRIES_DIR, '1444_tags.info')
LOCALE_ROOTS = [os.path.join('main_menu', 'localization'), '.']

TAG_LINE_RE = re.compile(r'^\s*([A-Z][A-Z0-9]{2})\s*=', re.MULTILINE)
INLINE_NAME_RE = re.compile(r'^\s*([A-Z][A-Z0-9]{2})\s*=\s*["\'](.+?)["\']\s*$', re.MULTILINE)
NAME_IN_BLOCK_RE = re.compile(r'name\s*=\s*["\'](.+?)["\']', re.IGNORECASE | re.DOTALL)

LOCALE_LINE_RE = re.compile(r'^\s*([A-Z][A-Z0-9]{2})\s*(?:\: ?\d+|\:)?\s*["\'](.+?)["\']\s*$', re.MULTILINE)
LOCALE_ALT_RE = re.compile(r'^\s*([A-Z][A-Z0-9]{2})\s+"(.+?)"\s*$', re.MULTILINE)


def read_text(path):
    try:
        with io.open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        with io.open(path, 'r', encoding='latin-1') as f:
            return f.read()


def strip_inline_comments(text):
    # Preserve full comment lines (they may contain names); remove inline '#' comments when safe
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith('#') or s.startswith('//'):
            out.append(line)
            continue
        if '#' in line:
            parts = line.split('#', 1)
            if parts[0].count('"') % 2 == 0 and parts[0].count("'") % 2 == 0:
                line = parts[0]
        out.append(line)
    return "\n".join(out)


def find_tags_in_country_text(text):
    """Return a list of (tag, name_or_None) occurrences found in the file.
    Using a list preserves duplicates defined multiple times in the same file.
    """
    entries = []
    # capture inline assignments as individual occurrences
    for m in INLINE_NAME_RE.finditer(text):
        entries.append((m.group(1), m.group(2).strip()))

    # capture block assignments, including repeated tags
    for m in TAG_LINE_RE.finditer(text):
        tag = m.group(1)
        idx = m.end()
        rest = text[idx:]
        s = rest.lstrip()
        if not s:
            continue
        first = s[0]
        name_candidate = None
        if first == '{':
            open_pos = idx + rest.find('{')
            line_start = text.rfind('\n', 0, open_pos) + 1
            line_until_brace = text[line_start:open_pos]
            cm = re.search(r'[#/]{1,2}\s*(.+)$', line_until_brace)
            if cm:
                name_candidate = cm.group(1).strip()
            prev_line_end = line_start - 1
            if prev_line_end > 0:
                prev_line_start = text.rfind('\n', 0, prev_line_end - 1) + 1
                prev_line = text[prev_line_start:prev_line_end].strip()
                if prev_line.startswith('#') or prev_line.startswith('//'):
                    cname = prev_line.lstrip('#').lstrip('/').strip()
                    if cname:
                        name_candidate = name_candidate or cname
            # brace match
            depth = 0
            i = open_pos
            end_pos = None
            while i < len(text):
                ch = text[i]
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        end_pos = i
                        break
                i += 1
            if end_pos:
                block = text[open_pos + 1:end_pos]
                nm = NAME_IN_BLOCK_RE.search(block)
                if nm:
                    entries.append((tag, nm.group(1).strip().replace('\n', ' ')))
                    continue
                # first non-empty line inside block may be a comment with the name
                block_lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
                if block_lines:
                    first_ln = block_lines[0]
                    if first_ln.startswith('#') or first_ln.startswith('//'):
                        cname = first_ln.lstrip('#').lstrip('/').strip()
                        if cname:
                            entries.append((tag, cname))
                            continue
                if name_candidate:
                    entries.append((tag, name_candidate))
                    continue
                entries.append((tag, None))
        else:
            entries.append((tag, None))
    return entries


def index_localization_files(roots):
    locales = {}
    candidates = []
    for root in roots:
        root_dir = root or '.'
        for path in glob(os.path.join(root_dir, '**', '*.*'), recursive=True):
            lower = path.lower()
            if '/localization/' in lower or '\\localization\\' in lower or 'localisation' in lower:
                if lower.endswith(('.yml', '.yaml', '.txt', '.csv')):
                    candidates.append(path)
    candidates = sorted(set(candidates))
    for path in candidates:
        try:
            text = read_text(path)
        except Exception:
            continue
        for m in LOCALE_LINE_RE.finditer(text):
            tag = m.group(1)
            name = m.group(2).strip()
            locales.setdefault(tag, name)
        for m in LOCALE_ALT_RE.finditer(text):
            tag = m.group(1)
            name = m.group(2).strip()
            locales.setdefault(tag, name)
    return locales


def write_tags_file(path, tag_map):
    header = 'All modded tags added to setup in alphabetical order:'
    lines = [header, '']
    for tag in sorted(tag_map.keys()):
        name = tag_map[tag] or tag
        lines.append(f"{tag} - {name}")
    content = "\n".join(lines) + "\n"
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)
    try:
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        os.rename(tmp, path)


def main():
    # collect occurrences per tag so we can report duplicates
    occurrences = {}
    files = sorted(glob(os.path.join(COUNTRIES_DIR, '*.txt')))
    for fpath in files:
        if os.path.basename(fpath) == os.path.basename(OUTPUT_PATH):
            continue
        txt = read_text(fpath)
        txt = strip_inline_comments(txt)
        found = find_tags_in_country_text(txt)
        for tag, name in found:
            occurrences.setdefault(tag, []).append((fpath, name))

    # choose a display name per tag (prefer an explicit name from occurrences)
    tag_map = {}
    for tag, entries in occurrences.items():
        chosen = None
        for fpath, name in entries:
            if name:
                chosen = name
                break
        tag_map[tag] = chosen

    # localization fallback
    locales = index_localization_files(LOCALE_ROOTS)
    for tag, name in list(tag_map.items()):
        if not name:
            lname = locales.get(tag)
            if lname:
                tag_map[tag] = lname

    # final fallback to tag itself
    for tag in list(tag_map.keys()):
        if not tag_map[tag]:
            tag_map[tag] = tag

    # write tags file
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    write_tags_file(OUTPUT_PATH, tag_map)

    # report duplicates
    duplicates = {tag: entries for tag, entries in occurrences.items() if len(entries) > 1}
    # Print duplicates to console (no file output)
    if duplicates:
        print('Duplicate tags found (tag: files)\n')
        for tag in sorted(duplicates.keys()):
            print(f"{tag}:")
            # collapse multiple occurrences in the same file; only show unique file paths
            unique_files = sorted({os.path.relpath(fp) for fp, _ in duplicates[tag]})
            for uf in unique_files:
                print(f"  {uf}")
            print()
    # else: do nothing (no duplicates)


if __name__ == '__main__':
    main()
