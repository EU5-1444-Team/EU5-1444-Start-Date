#!/usr/bin/env python3
"""
split_countries_by_region.py

Splits 10_countries.txt into multiple 10_<region>_countries.txt files.
Each country is assigned to the region where it owns the most locations.
If the capital location is found in a region, that region takes priority.

Reads paths from tools/settings.json (next to this script):
    {
        "base_game": "/path/to/Europa Universalis V",
        "mod_folder": "/path/to/EU5-1444-Start-Date"
    }

Usage:
    python split_countries_by_region.py
"""

import re
import os
import json
from collections import defaultdict


# ---------------------------------------------------------------------------
# 0. Load settings.json
# ---------------------------------------------------------------------------

def load_settings() -> dict:
    """Load settings.json from the tools/ folder next to this script."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    settings_path = os.path.join(script_dir, "settings.json")
    if not os.path.exists(settings_path):
        raise FileNotFoundError(f"Could not find settings.json at {settings_path}")

    with open(settings_path, encoding="utf-8") as f:
        raw = f.read()
    # Strip // comments (jsonc support)
    raw = re.sub(r'//[^\n]*', '', raw)
    raw = re.sub(r'/\*.*?\*/', '', raw, flags=re.DOTALL)

    return json.loads(raw)


# ---------------------------------------------------------------------------
# 1. Parse definitions.txt → location → superregion mapping
# ---------------------------------------------------------------------------

def parse_definitions(path: str) -> dict[str, tuple[str, str]]:
    """
    Returns a dict: location_name -> (superregion_name, region_name)
    Hierarchy: continent=1, superregion=2, region=3, area=4, province=5, location=bare token
    """
    location_to_sr: dict[str, tuple[str, str]] = {}

    depth = 0
    name_at_depth: dict[int, str] = {}

    token_re = re.compile(r'[{}]|[^\s{}#]+')

    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.split("#")[0]
            tokens = token_re.findall(line)
            i = 0
            while i < len(tokens):
                tok = tokens[i]
                if tok == "{":
                    depth += 1
                    i += 1
                elif tok == "}":
                    name_at_depth.pop(depth, None)
                    depth -= 1
                    i += 1
                elif i + 1 < len(tokens) and tokens[i + 1] == "=":
                    name_at_depth[depth + 1] = tokens[i]
                    i += 2
                else:
                    superregion = name_at_depth.get(2)
                    region      = name_at_depth.get(3)
                    if superregion:
                        location_to_sr[tok] = (superregion, region or "")
                    i += 1

    return location_to_sr


# ---------------------------------------------------------------------------
# 2. Merge superregions into broader groupings
# ---------------------------------------------------------------------------

SUPERREGION_MERGE: dict[str, str] = {
    # Africa
    "north_africa":    "north_africa",
    "central_africa":  "sub_saharan_africa",
    "east_africa":     "sub_saharan_africa",
    "southern_africa": "sub_saharan_africa",
    "west_africa":     "sub_saharan_africa",
    # Asia
    "central_asia":    "central_north_asia",
    "north_asia":      "central_north_asia",
    "east_asia":       "east_asia",
    "middle_east":     "middle_east",
    "south_asia":      "south_asia",
    "south_east_asia": "south_east_asia",
    # America
    "north_america":   "america",
    "south_america":   "america",
    # Europe
    "western_europe":  "western_europe",
    "eastern_europe":  "eastern_europe",
    # Oceania
    "australasia":     "oceania",
    "pacific_islands": "oceania",
    # Ocean superregions are omitted → locations dropped
}


def merge_superregions(loc_to_sr: dict[str, tuple[str, str]]) -> dict[str, str]:
    """Remap to merged group names. japan_region gets its own group. Ocean superregions dropped."""
    result = {}
    for loc, (sr, region) in loc_to_sr.items():
        if region == "japan_region":
            result[loc] = "japan"
            continue
        merged = SUPERREGION_MERGE.get(sr)
        if merged:
            result[loc] = merged
    return result


# ---------------------------------------------------------------------------
# 3. Parse 10_countries.txt
# ---------------------------------------------------------------------------

def parse_countries(path: str):
    """
    Returns (preamble_lines, countries, suffix).
    Each country is a dict with: tag, block, locations, capital.
    """
    with open(path, encoding="utf-8-sig") as f:
        content = f.read()

    outer = re.search(r'^countries\s*=\s*\{', content, re.MULTILINE)
    if not outer:
        raise ValueError("Could not find outer 'countries = {' block")
    inner_start = content.index('{', outer.start()) + 1

    depth = 1
    pos = inner_start
    while pos < len(content) and depth > 0:
        if content[pos] == '{':
            depth += 1
        elif content[pos] == '}':
            depth -= 1
        pos += 1
    outer_end = pos

    preamble_lines = content[:inner_start].splitlines()
    inner_content = content[inner_start:outer_end - 1]
    suffix = content[outer_end - 1:]

    countries = []
    tag_re = re.compile(r'^([ \t]*)([A-Z0-9_]{2,8})\s*=\s*\{', re.MULTILINE)
    i = 0

    while i < len(inner_content):
        m = tag_re.search(inner_content, i)
        if not m:
            break

        # Skip if the line is commented out (first non-whitespace char is #)
        line_start = inner_content.rfind('\n', 0, m.start()) + 1
        line_prefix = inner_content[line_start:m.start() + len(m.group(1))]
        if '#' in line_prefix:
            i = m.end()
            continue

        tag = m.group(2)
        brace_start = m.end() - 1

        depth = 0
        j = brace_start
        while j < len(inner_content):
            if inner_content[j] == '\n':
                # Peek at the next line — if it's a comment line, skip to end of line
                next_line_start = j + 1
                rest = inner_content[next_line_start:]
                comment_match = re.match(r'[ \t]*#', rest)
                if comment_match:
                    eol = inner_content.find('\n', next_line_start)
                    j = eol if eol != -1 else len(inner_content)
                    continue
            if inner_content[j] == '{':
                depth += 1
            elif inner_content[j] == '}':
                depth -= 1
                if depth == 0:
                    break
            j += 1

        block_text = inner_content[m.start():j + 1]
        locations = extract_locations(block_text)
        capital_match = re.search(r'\bcapital\s*=\s*(\w+)', block_text)
        capital = capital_match.group(1) if capital_match else None

        countries.append({
            "tag": tag,
            "block": block_text,
            "locations": locations,
            "capital": capital,
        })
        i = j + 1

    return preamble_lines, countries, suffix


def extract_locations(block_text: str) -> set[str]:
    """Extract all bare location tokens from own_* and our_cores_* sections."""
    section_re = re.compile(
        r'\b(own_control_core|own_control_integrated|own_control_conquered|own_control_colony'
        r'|own_core|own_conquered|own_integrated|own_colony'
        r'|control_core|control|our_cores_conquered_by_others)\s*=\s*\{',
        re.IGNORECASE
    )
    locations = set()
    for m in section_re.finditer(block_text):
        brace_start = m.end() - 1
        depth = 0
        j = brace_start
        while j < len(block_text):
            if block_text[j] == '{':
                depth += 1
            elif block_text[j] == '}':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        section = block_text[brace_start + 1:j]
        for line in section.splitlines():
            line = line.split('#')[0]
            for tok in re.findall(r'\b[a-z][a-z0-9_]*\b', line):
                locations.add(tok)
    return locations


# ---------------------------------------------------------------------------
# 4. Assign each country to a group
# ---------------------------------------------------------------------------

def assign_country(country: dict, loc_to_group: dict[str, str]) -> str:
    """
    Assign to the group with the most owned locations.
    Ties broken alphabetically for determinism.
    Returns 'unassigned' if no locations match any group.
    """
    counts: dict[str, int] = defaultdict(int)
    for loc in country["locations"]:
        group = loc_to_group.get(loc)
        if group:
            counts[group] += 1

    if counts:
        top_score = max(counts.values())
        top_groups = sorted(g for g, c in counts.items() if c == top_score)
        return top_groups[0]

    return "unassigned"


# ---------------------------------------------------------------------------
# 5. Write output files
# ---------------------------------------------------------------------------

def build_file_content(country_blocks: list[str]) -> str:
    body = "\n\n".join(country_blocks)
    return "countries = {\n    countries = {\n\n" + body + "\n\n    }\n}\n"


def is_pops_block(country: dict) -> bool:
    return bool(re.search(r'\btype\s*=\s*pop\b', country["block"]))


def is_building_block(country: dict) -> bool:
    return bool(re.search(r'\btype\s*=\s*building\b', country["block"]))


def main():
    settings = load_settings()
    base_game  = settings["base_game"]
    mod_folder = settings["mod_folder"]

    countries_path   = os.path.join(mod_folder, "main_menu", "setup", "start", "10_countries.txt")
    definitions_path = os.path.join(base_game,  "game", "in_game", "map_data", "definitions.txt")
    output_dir       = os.path.join(mod_folder, "main_menu", "setup", "start")

    print(f"Base game:   {base_game}")
    print(f"Mod folder:  {mod_folder}")
    print(f"Countries:   {countries_path}")
    print(f"Definitions: {definitions_path}")
    print(f"Output dir:  {output_dir}")
    print()

    print("Parsing definitions.txt...")
    loc_to_group = parse_definitions(definitions_path)
    loc_to_group = merge_superregions(loc_to_group)
    groups = sorted(set(loc_to_group.values()))
    print(f"  → {len(loc_to_group)} locations mapped to {len(groups)} groups: {groups}")
    print()

    print("Parsing 10_countries.txt...")
    _, countries, _ = parse_countries(countries_path)
    print(f"  → {len(countries)} countries found")
    print()

    # Separate special blocks from regular country blocks
    pops_countries     = []
    building_countries = []
    regular_countries  = []
    for country in countries:
        if is_pops_block(country):
            pops_countries.append(country)
        elif is_building_block(country):
            building_countries.append(country)
        else:
            regular_countries.append(country)
    print(f"  → {len(pops_countries)} pop-based, {len(building_countries)} building-based, {len(regular_countries)} regular")
    print()

    # Assign regular countries to region groups
    group_to_countries: dict[str, list] = defaultdict(list)
    for country in regular_countries:
        group = assign_country(country, loc_to_group)
        group_to_countries[group].append(country)
        print(f"  {country['tag']:8s} → {group:25s}  (capital={country['capital']}, locs={len(country['locations'])})")

    print()
    os.makedirs(output_dir, exist_ok=True)

    # Write region files
    for group, group_countries in sorted(group_to_countries.items()):
        filename = f"10_{group}_countries.txt"
        out_path = os.path.join(output_dir, filename)
        file_content = build_file_content([c["block"] for c in group_countries])
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(file_content)
        print(f"  Wrote {len(group_countries):4d} countries → {filename}")

    # Write pops file
    if pops_countries:
        pops_path = os.path.join(output_dir, "10_pop_based_countries.txt")
        with open(pops_path, "w", encoding="utf-8") as f:
            f.write(build_file_content([c["block"] for c in pops_countries]))
        print(f"  Wrote {len(pops_countries):4d} entries  → 10_pop_based_countries.txt")

    # Write buildings file
    if building_countries:
        buildings_path = os.path.join(output_dir, "10_building_based_countries.txt")
        with open(buildings_path, "w", encoding="utf-8") as f:
            f.write(build_file_content([c["block"] for c in building_countries]))
        print(f"  Wrote {len(building_countries):4d} entries  → 10_building_based_countries.txt")

    total = len(group_to_countries) + (1 if pops_countries else 0) + (1 if building_countries else 0)
    print(f"\nDone! {total} files written to '{output_dir}'")


if __name__ == "__main__":
    main()