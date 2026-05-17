#!/usr/bin/env python3
"""
Sync ALL base game characters into the 1444 mod file:
- Characters WITHOUT death_date in base game → add with death_date = 1399.12.31
- Characters WITH death_date in base game not in mod → add as-is
- Characters already in mod with matching base game key and no base game death_date
  → replace with base game version + death_date = 1399.12.31
- Characters already in mod with matching base game key AND base game death_date → skip
- Mod-only characters → skip
"""

import re
import sys
import shutil
from collections import OrderedDict

BASEGAME = "/home/rick/Paradox/Games/Europa Universalis V/game/main_menu/setup/start/05_characters.txt"
MOD = "/home/rick/Paradox/EU5-1444-Start-Date/main_menu/setup/start/05_characters.txt"
DEFAULT_DEATH = "1399.12.31"


def strip_comment(line):
    idx = line.find('#')
    return line[:idx] if idx >= 0 else line


def find_char_db_start(lines):
    for i, line in enumerate(lines):
        stripped = strip_comment(line)
        if 'character_db' in stripped and '{' in stripped:
            return i
    return None


def parse_character_blocks(lines, db_start):
    chars = OrderedDict()
    db_depth = 0
    current_key = None
    current_start = None

    for i in range(db_start, len(lines)):
        line = lines[i]
        stripped = strip_comment(line)
        open_b = stripped.count('{')
        close_b = stripped.count('}')

        if i == db_start:
            db_depth = open_b - close_b
            continue

        depth_before = db_depth
        db_depth += open_b - close_b

        if current_key is None and depth_before == 1 and open_b > 0:
            m = re.match(r'\s*([\w.]+)\s*=\s*\{', stripped)
            if m:
                current_key = m.group(1)
                current_start = i

        if current_key is not None and db_depth <= 1:
            text = ''.join(lines[current_start:i + 1])
            if not text.endswith('\n'):
                text += '\n'
            chars[current_key] = (current_start, i, text)
            current_key = None
            current_start = None
            if db_depth <= 0:
                break

    return chars


def has_death_date(text):
    for line in text.split('\n'):
        stripped = strip_comment(line).strip()
        if stripped.startswith('death_date'):
            return True
    return False


def add_death_date(text, death_date=None):
    if death_date is None:
        death_date = DEFAULT_DEATH
    lines = text.split('\n')

    indent = None
    birth_idx = None

    for i, line in enumerate(lines):
        stripped = strip_comment(line)
        if 'birth_date' in stripped:
            indent = re.match(r'^(\s*)', line).group(1)
            birth_idx = i
            break

    if indent is None:
        for line in lines:
            s = line.strip()
            if s and s != '{' and s != '}' and not s.startswith('#'):
                indent = re.match(r'^(\s*)', line).group(1)
                break

    if indent is None:
        indent = '\t\t'

    death_line = f"{indent}death_date = {death_date}\n"

    new_lines = list(lines)
    if birth_idx is not None:
        new_lines.insert(birth_idx + 1, death_line)
    else:
        for i in range(len(new_lines) - 1, -1, -1):
            if new_lines[i].strip() == '}':
                new_lines.insert(i, death_line)
                break

    return '\n'.join(new_lines)


def find_char_db_close(lines, db_start):
    db_depth = 0
    for i in range(db_start, len(lines)):
        line = lines[i]
        stripped = strip_comment(line)
        open_b = stripped.count('{')
        close_b = stripped.count('}')

        if i == db_start:
            db_depth = open_b - close_b
            continue

        db_depth += open_b - close_b
        if db_depth <= 0:
            return i
    return None


def main():
    print("=" * 60)
    print("  EU5 Character Sync Script")
    print("  Sync ALL base game chars to mod")
    print(f"  Alive chars get death_date = {DEFAULT_DEATH}")
    print("=" * 60)
    print()

    # Read files
    print("[1/5] Reading base game file...")
    with open(BASEGAME, 'r', encoding='utf-8') as f:
        base_lines = f.readlines()
    print(f"       {len(base_lines)} lines")

    print("[2/5] Reading mod file...")
    with open(MOD, 'r', encoding='utf-8') as f:
        mod_lines = f.readlines()
    print(f"       {len(mod_lines)} lines")

    # Parse base game
    print("[3/5] Parsing base game characters...")
    base_db_start = find_char_db_start(base_lines)
    if base_db_start is None:
        print("  ERROR: Cannot find character_db in base game file!")
        sys.exit(1)
    print(f"       character_db starts at line {base_db_start + 1}")

    base_chars = parse_character_blocks(base_lines, base_db_start)
    base_keys = set(base_chars.keys())
    print(f"       Total characters: {len(base_chars)}")

    # Separate alive vs dead
    alive_base = OrderedDict()
    dead_base = OrderedDict()
    for key, (start, end, text) in base_chars.items():
        if has_death_date(text):
            dead_base[key] = text
        else:
            alive_base[key] = text
    print(f"       Alive (no death_date): {len(alive_base)}")
    print(f"       Dead (has death_date):  {len(dead_base)}")

    # Parse mod file
    print("[4/5] Parsing mod file characters...")
    mod_db_start = find_char_db_start(mod_lines)
    if mod_db_start is None:
        print("  ERROR: Cannot find character_db in mod file!")
        sys.exit(1)
    print(f"       character_db starts at line {mod_db_start + 1}")

    mod_chars = parse_character_blocks(mod_lines, mod_db_start)
    mod_keys = set(mod_chars.keys())
    print(f"       Total characters in mod: {len(mod_chars)}")

    # Determine what to do
    alive_keys = set(alive_base.keys())
    dead_keys = set(dead_base.keys())

    # Alive chars: replace in mod if exist, add if missing
    alive_to_replace = alive_keys & mod_keys
    alive_to_add = alive_keys - mod_keys

    # Dead chars: only add if missing from mod, don't replace existing
    dead_to_add = dead_keys - mod_keys

    print()
    print(f"  Alive chars to REPLACE in mod: {len(alive_to_replace)}")
    print(f"  Alive chars to ADD to mod:     {len(alive_to_add)}")
    print(f"  Dead chars to ADD to mod:      {len(dead_to_add)}")

    total_changes = len(alive_to_replace) + len(alive_to_add) + len(dead_to_add)
    if total_changes == 0:
        print("  Nothing to do!")
        return

    # Backup
    backup_path = MOD + ".restore_backup"
    shutil.copy2(MOD, backup_path)
    print(f"  Backup saved to: {backup_path}")

    # Build new mod content
    print("[5/5] Writing modified mod file...")
    new_mod = list(mod_lines)

    # Replace alive chars in mod (reverse line order to preserve indices)
    replaced = 0
    for key in sorted(alive_to_replace, key=lambda k: mod_chars[k][0], reverse=True):
        orig_start, orig_end, _ = mod_chars[key]
        new_text = add_death_date(alive_base[key])
        replacement = new_text.splitlines(keepends=True)
        if replacement and not replacement[-1].endswith('\n'):
            replacement[-1] += '\n'
        new_mod[orig_start:orig_end + 1] = replacement
        replaced += 1

    # Add new entries before character_db closing
    added_alive = 0
    added_dead = 0
    all_to_add = {}

    for key in alive_to_add:
        all_to_add[key] = add_death_date(alive_base[key])
    for key in dead_to_add:
        all_to_add[key] = dead_base[key]  # Keep original death_date

    if all_to_add:
        db_close_idx = find_char_db_close(new_mod, mod_db_start)
        if db_close_idx is not None:
            additions = []
            for key in sorted(all_to_add):
                lines = all_to_add[key].splitlines(keepends=True)
                additions.extend(lines)
                additions.append('\n')

            if additions and additions[-1] == '\n':
                additions.pop()

            new_mod[db_close_idx:db_close_idx] = additions
            added_alive = len(alive_to_add)
            added_dead = len(dead_to_add)
        else:
            print("  ERROR: Could not find character_db closing brace!")

    # Write
    out_text = ''.join(new_mod)
    with open(MOD, 'w', encoding='utf-8') as f:
        f.write(out_text)

    print(f"  Replaced (alive): {replaced}")
    print(f"  Added (alive):    {added_alive}")
    print(f"  Added (dead):     {added_dead}")
    print(f"  Total changes:    {replaced + added_alive + added_dead}")
    print()

    # Quick verification
    print("Verification: counting characters and death_date entries...")
    with open(MOD, 'r', encoding='utf-8') as f:
        verify_lines = f.readlines()

    death_count = sum(
        1 for line in verify_lines
        if strip_comment(line).strip().startswith('death_date')
    )

    # Count total characters in new mod
    verify_db_start = find_char_db_start(verify_lines)
    if verify_db_start:
        verify_chars = parse_character_blocks(verify_lines, verify_db_start)
        print(f"  Characters in mod: {len(verify_chars)}")
    print(f"  death_date entries: {death_count}")

    opens = out_text.count('{')
    closes = out_text.count('}')
    print(f"  Brace balance: {opens} == {closes} -> {'OK' if opens == closes else 'MISMATCH!'}")
    print()
    print("Done!")


if __name__ == '__main__':
    main()
