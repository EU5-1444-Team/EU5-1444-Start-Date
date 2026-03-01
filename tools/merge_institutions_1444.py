#!/usr/bin/env python3
"""Merge institution setup for the 1444 mod.

Rules:
- For locations in the 1444 institutions file that have `renaissance = yes`,
  take the full location block from the 1444 file.
- Everywhere else, take the full location block from the basegame file.
- Emit locations in the same order as the basegame file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

INSTITUTIONS_REL = Path("main_menu/setup/start/08_institutions.txt")
DEFINITIONS_REL = Path("in_game/map_data/definitions.txt")
SETTINGS_PATH = Path("tools/settings.json")
LOCATIONS_RE = re.compile(r"\blocations\b\s*=\s*\{", re.MULTILINE)
LOCATION_BLOCK_RE = re.compile(r"(?m)^[ \t]*([A-Za-z0-9_]+)\s*=\s*\{")
RENAISSANCE_YES_RE = re.compile(r"(?m)^\s*renaissance\s*=\s*yes\b")
TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|\{|\}|=", re.ASCII)

BANKING_REGIONS = [
    "iberia_region",
    "france_region",
    "italy_region",
    "great_britain_region",
]

BANKING_AREAS = [
    "flanders_area",
    "wallonia_area",
    "brabant_area",
    "holland_area",
    "friesland_area",
    "rhineland_area",
    "hesse_area",
    "upper_rhine_area",
    "western_switzerland_area",
    "central_switzerland_area",
    "eastern_switzerland_area",
    "swabia_area",
    "bavaria_area",
    "tirol_area",
    "carinthia_area",
    "salzburg_area",
    "upper_austria_area",
    "styria_area",
    "austria_area",
    "bohemia_area",
    "moravia_area",
    "lesser_poland_area",
    "slovenia_area",
    "transdanubia_area",
    "north_alfold_area",
    "south_alfold_area",
    "croatia_area",
    "slavonia_area",
    "bosnia_area",
    "serbia_area",
    "albania_area",
    "northern_greece_area",
    "morea_area",
    "macedonia_area",
    "thrace_area",
    "aegean_archipelago_area",
    "marmara_area",
    "west_anatolia_area",
]

PROF_REGIONS = [
    "iberia_region",
    "france_region",
    "italy_region",
    "great_britain_region",
]

PROF_AREAS = [
    "flanders_area",
    "wallonia_area",
    "brabant_area",
    "holland_area",
    "western_switzerland_area",
    "central_switzerland_area",
    "eastern_switzerland_area",
    "thrace_area",
    "macedonia_area",
    "austria_area",
    "styria_area",
]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def find_matching_brace(text: str, open_idx: int) -> int:
    depth = 1
    for i in range(open_idx + 1, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("Unbalanced braces while parsing locations block")


def extract_locations_block(text: str) -> Tuple[int, int]:
    m = LOCATIONS_RE.search(text)
    if not m:
        raise ValueError("Could not find `locations = { ... }` block")
    open_idx = text.find("{", m.start(), m.end())
    if open_idx == -1:
        raise ValueError("Could not find opening brace for locations block")
    close_idx = find_matching_brace(text, open_idx)
    return open_idx, close_idx


def parse_location_blocks(text: str) -> List[Tuple[str, str]]:
    open_idx, close_idx = extract_locations_block(text)
    body = text[open_idx + 1 : close_idx]
    blocks: List[Tuple[str, str]] = []

    i = 0
    n = len(body)
    depth = 0
    while i < n:
        ch = body[i]

        if ch in " \t\r\n":
            i += 1
            continue

        if ch == "#":
            nl = body.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue

        if ch == "/" and i + 1 < n and body[i + 1] == "/":
            nl = body.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue

        if ch == "{":
            depth += 1
            i += 1
            continue

        if ch == "}":
            depth = max(0, depth - 1)
            i += 1
            continue

        if depth == 0 and (ch.isalpha() or ch == "_"):
            name_start = i
            i += 1
            while i < n and (body[i].isalnum() or body[i] == "_"):
                i += 1
            name = body[name_start:i]

            while i < n and body[i] in " \t\r\n":
                i += 1

            if i >= n or body[i] != "=":
                continue
            i += 1

            while i < n and body[i] in " \t\r\n":
                i += 1

            if i >= n or body[i] != "{":
                continue

            block_open = i
            block_close = find_matching_brace(body, block_open)
            raw_block = body[name_start : block_close + 1].strip("\n")
            blocks.append((name, raw_block))
            i = block_close + 1
            continue

        i += 1

    return blocks


def resolve_base_file(explicit_base_file: str | None, explicit_base_game: str | None, repo_root: Path) -> Path:
    if explicit_base_file:
        p = Path(explicit_base_file).expanduser().resolve()
        if p.is_file():
            return p
        raise FileNotFoundError(f"Basegame file not found: {p}")

    candidates: List[Path] = []

    if explicit_base_game:
        candidates.append(Path(explicit_base_game).expanduser())

    env_base = os.environ.get("EU5_BASE_GAME")
    if env_base:
        candidates.append(Path(env_base).expanduser())

    settings_file = repo_root / SETTINGS_PATH
    if settings_file.is_file():
        try:
            data = json.loads(read_text(settings_file))
            for key in ("base_game", "base_game_dir"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    candidates.append(Path(val).expanduser())
        except Exception:
            pass

    checked: List[Path] = []
    for root in candidates:
        root = root.resolve()
        for variant in (root, root / "game"):
            candidate = variant / INSTITUTIONS_REL
            checked.append(candidate)
            if candidate.is_file():
                return candidate

    checked_str = "\n  ".join(str(p) for p in checked) if checked else "(no candidates)"
    raise FileNotFoundError(
        "Could not resolve basegame institutions file. Checked:\n"
        f"  {checked_str}\n"
        "Use --base-file or --base-game to provide it explicitly."
    )


def resolve_definitions_file(explicit_definitions_file: str | None, base_file: Path, repo_root: Path) -> Path:
    if explicit_definitions_file:
        p = Path(explicit_definitions_file).expanduser().resolve()
        if p.is_file():
            return p
        raise FileNotFoundError(f"Definitions file not found: {p}")

    candidates: List[Path] = []
    candidates.append(repo_root / DEFINITIONS_REL)
    try:
        game_root = base_file.parents[3]
        candidates.append(game_root / DEFINITIONS_REL)
    except Exception:
        pass

    for c in candidates:
        c = c.resolve()
        if c.is_file():
            return c

    checked_str = "\n  ".join(str(c) for c in candidates)
    raise FileNotFoundError(
        "Could not resolve definitions file. Checked:\n"
        f"  {checked_str}\n"
        "Use --definitions-file to provide it explicitly."
    )


def normalize_location_block(block: str, name: str) -> str:
    lines = block.splitlines()
    if not lines:
        return f"    {name} = {{\n    }}"

    lines[0] = re.sub(
        r"^\s*" + re.escape(name) + r"\s*=\s*\{",
        f"{name} = {{",
        lines[0],
        count=1,
    )
    return "\n".join(f"    {line}" if line else line for line in lines)


def build_merged_text(base_text: str, mod_text: str) -> Tuple[str, int, int, int]:
    base_blocks = parse_location_blocks(base_text)
    mod_map: Dict[str, str] = {name: block for name, block in parse_location_blocks(mod_text)}

    merged_blocks: List[str] = []
    from_mod = 0
    from_base = 0
    renaissance_kept = 0

    for name, base_block in base_blocks:
        mod_block = mod_map.get(name)
        if mod_block is not None and RENAISSANCE_YES_RE.search(mod_block):
            renaissance_kept += 1
            merged_blocks.append(normalize_location_block(mod_block, name))
            from_mod += 1
        else:
            merged_blocks.append(normalize_location_block(base_block, name))
            from_base += 1

    open_idx, close_idx = extract_locations_block(base_text)
    inner = "\n".join(merged_blocks)
    merged_text = base_text[: open_idx + 1] + "\n" + inner + "\n" + base_text[close_idx:]
    merged_text = re.sub(r"\blocations\s*=\s*\{", "locations = {", merged_text, count=1)

    return merged_text, from_mod, from_base, renaissance_kept


def strip_comments(text: str) -> str:
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def tokenize(text: str) -> List[str]:
    return TOKEN_RE.findall(text)


def parse_block(tokens: List[str], i: int) -> Tuple[Dict[str, Any], int]:
    if tokens[i] != "{":
        raise ValueError("Expected '{'")
    i += 1
    node: Dict[str, Any] = {"values": [], "children": {}}

    while i < len(tokens):
        tok = tokens[i]
        if tok == "}":
            return node, i + 1

        if tok in ("{", "="):
            raise ValueError(f"Unexpected token {tok}")

        name = tok
        if i + 1 < len(tokens) and tokens[i + 1] == "=":
            i += 2
            if i >= len(tokens) or tokens[i] != "{":
                raise ValueError("Expected '{' after assignment")
            child, i = parse_block(tokens, i)
            node["children"][name] = child
        else:
            node["values"].append(name)
            i += 1

    raise ValueError("Unclosed block")


def parse_definitions(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    text = strip_comments(text)
    tokens = tokenize(text)

    root: Dict[str, Any] = {"values": [], "children": {}}
    i = 0
    while i < len(tokens):
        if i + 2 < len(tokens) and tokens[i + 1] == "=" and tokens[i + 2] == "{":
            name = tokens[i]
            child, i = parse_block(tokens, i + 2)
            root["children"][name] = child
        else:
            i += 1
    return root


def flatten_values(node: Dict[str, Any]) -> List[str]:
    out = list(node.get("values", []))
    for child in node.get("children", {}).values():
        out.extend(flatten_values(child))
    return out


def find_node(root: Dict[str, Any], name: str) -> Dict[str, Any] | None:
    stack = [root]
    while stack:
        n = stack.pop()
        children = n.get("children", {})
        if name in children:
            return children[name]
        stack.extend(children.values())
    return None


def parse_location_spans(text: str) -> Tuple[Tuple[int, int], List[Tuple[str, int, int, str]]]:
    m = re.search(r"\blocations\b\s*=\s*\{", text)
    if not m:
        raise ValueError("No locations block found")
    open_idx = text.find("{", m.start(), m.end())

    def match_brace(s: str, start: int) -> int:
        depth = 1
        for j in range(start + 1, len(s)):
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    return j
        raise ValueError("Unbalanced braces")

    close_idx = match_brace(text, open_idx)
    body = text[open_idx + 1 : close_idx]

    blocks: List[Tuple[str, int, int, str]] = []
    i = 0
    n = len(body)
    while i < n:
        while i < n and body[i].isspace():
            i += 1
        if i >= n:
            break

        m2 = re.match(r"([A-Za-z0-9_]+)\s*=\s*\{", body[i:])
        if not m2:
            i += 1
            continue

        name = m2.group(1)
        rel_start = i
        brace_rel = body.find("{", i, i + m2.end())
        rel_end = match_brace(body, brace_rel)
        raw = body[rel_start : rel_end + 1]
        blocks.append((name, rel_start, rel_end + 1, raw))
        i = rel_end + 1

    return (open_idx + 1, close_idx), blocks


def add_institution(block: str, institution: str) -> Tuple[str, bool]:
    if re.search(rf"(?m)^\s*{re.escape(institution)}\s*=\s*yes\b", block):
        return block, False

    lines = block.splitlines()
    if not lines:
        return block, False

    indent = "\t"
    for ln in lines[1:]:
        s = ln.strip()
        if s and s != "}":
            indent = ln[: len(ln) - len(ln.lstrip())]
            break

    close_idx = len(lines) - 1
    while close_idx >= 0 and lines[close_idx].strip() != "}":
        close_idx -= 1

    insert_line = f"{indent}{institution} = yes"
    if close_idx == -1:
        lines.append(insert_line)
    else:
        lines.insert(close_idx, insert_line)

    return "\n".join(lines), True


def locations_for_groups(def_root: Dict[str, Any], groups: List[str]) -> Tuple[set[str], List[str]]:
    locs: set[str] = set()
    missing: List[str] = []
    for g in groups:
        node = find_node(def_root, g)
        if not node:
            missing.append(g)
            continue
        locs.update(flatten_values(node))
    return locs, missing


def apply_area_region_institutions(text: str, definitions_path: Path) -> Tuple[str, int, int, int, int, List[str], List[str]]:
    def_root = parse_definitions(definitions_path)
    banking_locs, missing_b = locations_for_groups(def_root, BANKING_REGIONS + BANKING_AREAS)
    prof_locs, missing_p = locations_for_groups(def_root, PROF_REGIONS + PROF_AREAS)

    (loc_open, loc_close), blocks = parse_location_spans(text)
    by_name = {name: (start, end, raw) for name, start, end, raw in blocks}
    all_locs = set(by_name.keys())

    target_banking = all_locs.intersection(banking_locs)
    target_prof = all_locs.intersection(prof_locs)

    updated_blocks: Dict[str, str] = {}
    add_b = 0
    add_p = 0
    for name in target_banking.union(target_prof):
        raw = by_name[name][2]
        new_raw = raw
        if name in target_banking:
            new_raw, added = add_institution(new_raw, "banking")
            if added:
                add_b += 1
        if name in target_prof:
            new_raw, added = add_institution(new_raw, "professional_armies")
            if added:
                add_p += 1
        updated_blocks[name] = new_raw

    body = text[loc_open:loc_close]
    out_parts: List[str] = []
    last = 0
    for name, start, end, raw in blocks:
        out_parts.append(body[last:start])
        out_parts.append(updated_blocks.get(name, raw))
        last = end
    out_parts.append(body[last:])
    new_body = "".join(out_parts)
    new_text = text[:loc_open] + new_body + text[loc_close:]

    return (
        new_text,
        len(target_banking),
        len(target_prof),
        add_b,
        add_p,
        missing_b,
        missing_p,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge 08_institutions.txt (1444 renaissance + basegame else) and apply area/region institution flags."
    )
    parser.add_argument("--base-file", help="Path to basegame main_menu/setup/start/08_institutions.txt")
    parser.add_argument("--base-game", help="Path to basegame root (or .../game root)")
    parser.add_argument("--definitions-file", help="Path to basegame in_game/map_data/definitions.txt")
    parser.add_argument("--mod-file", help="Path to mod 08_institutions.txt (default: repo main_menu/setup/start/08_institutions.txt)")
    parser.add_argument("--output", help="Output file path (default: overwrite --mod-file)")
    parser.add_argument("--no-backup", action="store_true", help="Do not create .bak when overwriting output")
    parser.add_argument("--dry-run", action="store_true", help="Only print counts; do not write file")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    mod_file = Path(args.mod_file).expanduser().resolve() if args.mod_file else (repo_root / INSTITUTIONS_REL)
    base_file = resolve_base_file(args.base_file, args.base_game, repo_root)
    definitions_file = resolve_definitions_file(args.definitions_file, base_file, repo_root)
    output_file = Path(args.output).expanduser().resolve() if args.output else mod_file

    if not mod_file.is_file():
        print(f"Mod institutions file not found: {mod_file}", file=sys.stderr)
        return 1

    base_text = read_text(base_file)
    mod_text = read_text(mod_file)

    merged_text, from_mod, from_base, renaissance_kept = build_merged_text(base_text, mod_text)
    final_text, banking_targets, prof_targets, banking_added, prof_added, missing_b, missing_p = apply_area_region_institutions(
        merged_text, definitions_file
    )

    print(f"Base file: {base_file}")
    print(f"Definitions file: {definitions_file}")
    print(f"Mod file:  {mod_file}")
    print(f"Output:    {output_file}")
    print(f"Selected from 1444 (renaissance):    {from_mod}")
    print(f"Selected from basegame:              {from_base}")
    print(f"Renaissance blocks kept from 1444:   {renaissance_kept}")
    print(f"banking targets in output:           {banking_targets}")
    print(f"professional_armies targets:         {prof_targets}")
    print(f"banking added:                       {banking_added}")
    print(f"professional_armies added:           {prof_added}")
    print(f"missing groups (banking):            {missing_b}")
    print(f"missing groups (professional):       {missing_p}")

    if args.dry_run:
        return 0

    if output_file.exists() and not args.no_backup:
        backup = output_file.with_suffix(output_file.suffix + ".bak")
        shutil.copy2(output_file, backup)
        print(f"Backup written: {backup}")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(final_text, encoding="utf-8")
    print("Merged institutions file written.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
