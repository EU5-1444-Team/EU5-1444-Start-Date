#!/usr/bin/env python3
"""
Reads all "vassals of tusi" (second = XXX where subject_type = tusi) from 12_diplomacy.txt,
collects their country blocks from the source 10_countries.txt,
and replaces the corresponding blocks in the target 10_countries.txt.
"""

import re
import sys

DIPLOMACY_FILE  = "/home/rick/Paradox/Games/Europa Universalis V/game/main_menu/setup/start/12_diplomacy.txt"
SOURCE_COUNTRIES = "/home/rick/Paradox/Games/Europa Universalis V/game/main_menu/setup/start/10_countries.txt"
TARGET_COUNTRIES = "/home/rick/Paradox/EU5-1444-Start-Date/main_menu/setup/start/10_countries.txt"


# ── 1. Collect tusi vassal tags from 12_diplomacy.txt ────────────────────────

EXCLUDE_TAGS = {"CHI", "LNG"}

def get_tusi_tags(path: str) -> list[str]:
    """Return all country tags (both first and second) involved in any tusi dependency,
    excluding CHI and LNG."""
    block_pattern = re.compile(r'dependency\s*=\s*\{([^}]*subject_type\s*=\s*tusi[^}]*)\}')
    tag_pattern = re.compile(r'\b(?:first|second)\s*=\s*(\w+)')
    tags: list[str] = []
    seen: set[str] = set()
    with open(path, encoding="utf-8") as f:
        content = f.read()
    for block_m in block_pattern.finditer(content):
        for tag_m in tag_pattern.finditer(block_m.group(1)):
            tag = tag_m.group(1)
            if tag not in seen and tag not in EXCLUDE_TAGS:
                seen.add(tag)
                tags.append(tag)
    return tags


# ── 2. Extract country blocks from a countries file ───────────────────────────

def extract_blocks(path: str, tags: set[str]) -> dict[str, str]:
    """
    For every tag in *tags*, find the block that starts with:
        TAG = {
            ...
        }
    followed optionally by a trailing comment line:
        # --- TAG ---
    Return a dict mapping tag -> full block text (including trailing comment if present).
    """
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    blocks: dict[str, str] = {}
    i = 0
    while i < len(lines):
        # Match lines like "    TAG = {"  (leading whitespace allowed)
        m = re.match(r'^(\s*)(\w+)\s*=\s*\{', lines[i])
        if m and m.group(2) in tags:
            tag = m.group(2)
            indent = m.group(1)
            start = i
            # Walk forward counting braces to find the closing }
            depth = 0
            j = i
            while j < len(lines):
                depth += lines[j].count('{') - lines[j].count('}')
                j += 1
                if depth == 0:
                    break
            # Optionally consume a trailing "# --- TAG ---" comment line
            if j < len(lines) and re.match(rf'^\s*#\s*---\s*{re.escape(tag)}\s*---', lines[j]):
                j += 1
            # Also consume any blank lines that immediately follow the comment
            # (we keep them to preserve spacing, but stop at the next non-blank line)
            # Actually: include the blank line that typically sits after the comment
            if j < len(lines) and lines[j].strip() == '':
                j += 1
            blocks[tag] = ''.join(lines[start:j])
            i = j
        else:
            i += 1

    return blocks


# ── 3. Replace blocks in target file ─────────────────────────────────────────

def replace_blocks(target_path: str, replacements: dict[str, str]) -> tuple[list[str], list[str]]:
    """
    Read the target file and replace every block for a tag in *replacements*
    with the new block text.  Returns (replaced_tags, missing_tags).
    Writes the modified file back in place.
    """
    with open(target_path, encoding="utf-8") as f:
        lines = f.readlines()

    replaced: list[str] = []
    missing: list[str] = []
    new_lines: list[str] = []
    i = 0

    while i < len(lines):
        m = re.match(r'^(\s*)(\w+)\s*=\s*\{', lines[i])
        if m and m.group(2) in replacements:
            tag = m.group(2)
            # Skip over the old block (same brace-counting logic)
            depth = 0
            j = i
            while j < len(lines):
                depth += lines[j].count('{') - lines[j].count('}')
                j += 1
                if depth == 0:
                    break
            # Skip optional trailing comment + blank line
            if j < len(lines) and re.match(rf'^\s*#\s*---\s*{re.escape(tag)}\s*---', lines[j]):
                j += 1
            if j < len(lines) and lines[j].strip() == '':
                j += 1
            # Insert the replacement
            new_lines.append(replacements[tag])
            replaced.append(tag)
            i = j
        else:
            new_lines.append(lines[i])
            i += 1

    # Write back
    with open(target_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    # Report any tags not found in the target
    for tag in replacements:
        if tag not in replaced:
            missing.append(tag)

    return replaced, missing


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Reading tusi tags from:\n  {DIPLOMACY_FILE}")
    tusi_tags = get_tusi_tags(DIPLOMACY_FILE)
    print(f"  Found {len(tusi_tags)} tusi vassals: {', '.join(tusi_tags)}\n")

    print(f"Extracting blocks from source:\n  {SOURCE_COUNTRIES}")
    blocks = extract_blocks(SOURCE_COUNTRIES, set(tusi_tags))
    print(f"  Extracted {len(blocks)} blocks.")
    not_in_source = [t for t in tusi_tags if t not in blocks]
    if not_in_source:
        print(f"  WARNING – not found in source: {', '.join(not_in_source)}")
    print()

    if not blocks:
        print("Nothing to replace. Exiting.")
        sys.exit(0)

    print(f"Replacing blocks in target:\n  {TARGET_COUNTRIES}")
    replaced, missing = replace_blocks(TARGET_COUNTRIES, blocks)
    print(f"  Replaced {len(replaced)} blocks: {', '.join(replaced)}")
    if missing:
        print(f"  WARNING – tags not found in target (skipped): {', '.join(missing)}")

    print("\nDone.")


if __name__ == "__main__":
    main()