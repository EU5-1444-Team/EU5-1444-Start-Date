#!/usr/bin/env python3
"""
sort_countries.py

Rewrites 10_countries.txt sorted by continent > superregion > region,
with comment headers at each tier and separators between country blocks.

Reads paths from tools/settings.json (next to this script):
    {
        "base_game": "/path/to/Europa Universalis V",
        "mod_folder": "/path/to/EU5-1444-Start-Date"
    }

Usage:
    python sort_countries.py
"""

import re
import os
import json
from collections import defaultdict


# ---------------------------------------------------------------------------
# 0. Load settings.json
# ---------------------------------------------------------------------------

def load_settings() -> dict:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    settings_path = os.path.join(script_dir, "settings.json")
    if not os.path.exists(settings_path):
        raise FileNotFoundError(f"Could not find settings.json at {settings_path}")
    with open(settings_path, encoding="utf-8") as f:
        raw = f.read()
    raw = re.sub(r'//[^\n]*', '', raw)
    raw = re.sub(r'/\*.*?\*/', '', raw, flags=re.DOTALL)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# 1. Hierarchy definitions
# ---------------------------------------------------------------------------

# continent -> [superregion, ...]  (order matters)
CONTINENTS: dict[str, list[str]] = {
    "europe":   ["western_europe", "eastern_europe"],
    "africa":   ["north_africa", "sub_saharan_africa"],
    "asia":     ["middle_east", "central_north_asia", "south_asia", "south_east_asia", "east_asia"],
    "america":  ["america"],
    "oceania":  ["oceania"],
}

SUPERREGION_MERGE: dict[str, str] = {
    "north_africa":    "north_africa",
    "central_africa":  "sub_saharan_africa",
    "east_africa":     "sub_saharan_africa",
    "southern_africa": "sub_saharan_africa",
    "west_africa":     "sub_saharan_africa",
    "central_asia":    "central_north_asia",
    "north_asia":      "central_north_asia",
    "east_asia":       "east_asia",
    "middle_east":     "middle_east",
    "south_asia":      "south_asia",
    "south_east_asia": "south_east_asia",
    "north_america":   "america",
    "south_america":   "america",
    "western_europe":  "western_europe",
    "eastern_europe":  "eastern_europe",
    "australasia":     "oceania",
    "pacific_islands": "oceania",
}

CONTINENT_NAMES: dict[str, str] = {
    "europe":  "europe",
    "africa":  "africa",
    "asia":    "asia",
    "america": "america",
    "oceania": "oceania",
}


# ---------------------------------------------------------------------------
# 2. Parse definitions.txt → location -> (continent, superregion, region)
# ---------------------------------------------------------------------------

def parse_definitions(path: str) -> dict[str, tuple[str, str, str]]:
    location_to: dict[str, tuple[str, str, str]] = {}

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
                    continent = name_at_depth.get(1, "")
                    sr        = name_at_depth.get(2, "")
                    region    = name_at_depth.get(3, "")
                    sr_folder = SUPERREGION_MERGE.get(sr)
                    clean_continent = CONTINENT_NAMES.get(continent, "")
                    if sr_folder and clean_continent:
                        effective_region = "japan_region" if region == "japan_region" else region
                        location_to[tok] = (clean_continent, sr_folder, effective_region)
                    i += 1

    return location_to


# ---------------------------------------------------------------------------
# 3. Parse 10_countries.txt
# ---------------------------------------------------------------------------

def parse_countries(path: str) -> tuple[str, list[dict], str]:
    with open(path, encoding="utf-8-sig") as f:
        content = f.read()

    outer = re.search(r'^countries\s*=\s*\{', content, re.MULTILINE)
    if not outer:
        raise ValueError("Could not find outer 'countries = {' block")
    inner_start = content.index('{', outer.start()) + 1

    depth = 1
    pos = inner_start
    while pos < len(content) and depth > 0:
        if content[pos] == '{': depth += 1
        elif content[pos] == '}': depth -= 1
        pos += 1
    outer_end = pos

    preamble = content[:inner_start]
    inner_content = content[inner_start:outer_end - 1]
    suffix = content[outer_end - 1:]

    countries = []
    tag_re = re.compile(r'^([ \t]*)([A-Z0-9_]{2,8})\s*=\s*\{', re.MULTILINE)
    i = 0

    while i < len(inner_content):
        m = tag_re.search(inner_content, i)
        if not m:
            break

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
                next_line_start = j + 1
                if re.match(r'[ \t]*#', inner_content[next_line_start:]):
                    eol = inner_content.find('\n', next_line_start)
                    j = eol if eol != -1 else len(inner_content)
                    continue
            if inner_content[j] == '{': depth += 1
            elif inner_content[j] == '}':
                depth -= 1
                if depth == 0: break
            j += 1

        block_text = inner_content[m.start():j + 1]
        locations = extract_locations(block_text)

        countries.append({
            "tag":       tag,
            "block":     block_text.strip(),
            "locations": locations,
        })
        i = j + 1

    return preamble, countries, suffix


def extract_locations(block_text: str) -> set[str]:
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
            if block_text[j] == '{': depth += 1
            elif block_text[j] == '}':
                depth -= 1
                if depth == 0: break
            j += 1
        section = block_text[brace_start + 1:j]
        for line in section.splitlines():
            line = line.split('#')[0]
            for tok in re.findall(r'\b[a-z][a-z0-9_]*\b', line):
                locations.add(tok)
    return locations


# ---------------------------------------------------------------------------
# 4. Assign each country to (continent, superregion, region)
# ---------------------------------------------------------------------------

def is_pops_block(country: dict) -> bool:
    return bool(re.search(r'\btype\s*=\s*pop\b', country["block"]))

def is_building_block(country: dict) -> bool:
    return bool(re.search(r'\btype\s*=\s*building\b', country["block"]))

def assign_country(country: dict, loc_to: dict[str, tuple[str, str, str]]) -> tuple[str, str, str]:
    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    for loc in country["locations"]:
        key = loc_to.get(loc)
        if key:
            counts[key] += 1
    if counts:
        top_score = max(counts.values())
        top_keys = sorted(k for k, c in counts.items() if c == top_score)
        return top_keys[0]
    return ("unassigned", "unassigned", "unassigned")


# ---------------------------------------------------------------------------
# 5. Format output
# ---------------------------------------------------------------------------

LINE = "#" * 79

def continent_header(name: str) -> str:
    return f"{LINE}\n# {name.upper()}\n{LINE}"

def superregion_header(name: str) -> str:
    return f"\n## {name}\n## {'=' * len(name)}"

def region_header(name: str) -> str:
    return f"\n# {name}\n# {'-' * len(name)}"

def tag_separator(tag: str) -> str:
    return f"\n# --- {tag} ---"


def build_output(grouped: dict, special: dict) -> str:
    lines = ["current_age = age_2_renaissance", "", "countries = {", "    countries = {"]

    def emit_group(label: str, countries: list[dict]):
        for i, country in enumerate(countries):
            lines.append("")
            lines.append(country["block"])
            lines.append(tag_separator(country["tag"]))

    # Regular countries sorted by continent > superregion > region
    for continent, superregions in CONTINENTS.items():
        continent_written = False
        for superregion in superregions:
            superregion_written = False
            # Collect all regions under this superregion
            regions_in_sr: dict[str, list[dict]] = defaultdict(list)
            for (c, sr, region), countries in grouped.items():
                if c == continent and sr == superregion:
                    regions_in_sr[region].extend(countries)

            for region in sorted(regions_in_sr):
                regions_in_sr[region].sort(key=lambda c: len(c["locations"]), reverse=True)
                countries = regions_in_sr[region]
                if not countries:
                    continue
                if not continent_written:
                    lines.append(f"\n\n{continent_header(continent)}")
                    continent_written = True
                if not superregion_written:
                    lines.append(superregion_header(superregion))
                    superregion_written = True
                lines.append(region_header(region))
                emit_group(region, countries)

    # Special blocks at the end
    for label, countries in special.items():
        if countries:
            lines.append(f"\n\n{continent_header(label)}")
            for country in countries:
                lines.append("")
                lines.append(country["block"])
                lines.append(tag_separator(country["tag"]))

    lines.append("")
    lines.append("    }")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 6. Main
# ---------------------------------------------------------------------------

def main():
    settings   = load_settings()
    base_game  = settings["base_game"]
    mod_folder = settings["mod_folder"]

    countries_path   = os.path.join(mod_folder, "main_menu", "setup", "start", "10_countries.txt")
    definitions_path = os.path.join(base_game,  "game", "in_game", "map_data", "definitions.txt")
    output_path      = os.path.join(mod_folder, "main_menu", "setup", "start", "10_countries_sorted.txt")

    print(f"Countries:   {countries_path}")
    print(f"Definitions: {definitions_path}")
    print(f"Output:      {output_path}")
    print()

    print("Parsing definitions.txt...")
    loc_to = parse_definitions(definitions_path)
    print(f"  → {len(loc_to)} locations parsed")
    print()

    print("Parsing 10_countries.txt...")
    _, countries, _ = parse_countries(countries_path)
    print(f"  → {len(countries)} countries found")
    print()

    # Classify
    pops_countries     = []
    building_countries = []
    unassigned_countries = []
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

    # Assign
    grouped: dict[tuple[str, str, str], list] = defaultdict(list)
    for country in regular_countries:
        key = assign_country(country, loc_to)
        if key[0] == "unassigned":
            unassigned_countries.append(country)
        else:
            grouped[key].append(country)
        c, sr, region = key
        print(f"  {country['tag']:8s} → {c:10s} / {sr:25s} / {region}")

    print()

    special = {
        "pop-based":      pops_countries,
        "building-based": building_countries,
        "unassigned":     unassigned_countries,
    }

    output = build_output(grouped, special)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output)

    print(f"Done! Written to '{output_path}'")


if __name__ == "__main__":
    main()