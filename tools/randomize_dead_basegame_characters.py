#!/usr/bin/env python3
"""Replace invalid office-holder character references in 10_countries.

Rules:
- If a `ruler`/`heir`/`consort`/`active_regent`/`regent` value exists in
  basegame `05_characters.txt`, replace it with `random`.
- Add `# TODO: add me` only when that country has land at game start.
  Land is determined by presence of one of the start ownership/control keys
  (`own_control_core`, `own_core`, `control_core`, etc.) in that country block.
- Do not add TODO comment for landless starts (e.g. tags with only
  `our_cores_conquered_by_others` or `add_pops_from_locations`).

Intentionally does NOT touch `ruler_term = { character = ... }` history.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


CHAR_DB_START_RE = re.compile(r"^\s*character_db\s*=\s*\{")
BLOCK_START_RE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=\s*\{")
BLOCK_OPEN_RE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=\s*\{\s*$")
ASSIGN_KEY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
TAG_RE = re.compile(r"^[A-Z0-9_]{2,4}$")

# This matches plain one-line assignments.
ASSIGNMENT_RE = re.compile(
    r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([A-Za-z0-9_:.+\-]+)(\s*(#.*)?)$"
)

ROLE_KEYS = {"ruler", "heir", "consort", "active_regent", "regent"}
TODO_MARKER = "# TODO: add me"
TODO_COMMENT = f" {TODO_MARKER}"
LAND_KEYS = {
    "own_control_core",
    "own_control_integrated",
    "own_control_conquered",
    "own_control_colony",
    "own_core",
    "own_conquered",
    "own_integrated",
    "own_colony",
    "control_core",
    "control",
}


def parse_character_ids(path: Path) -> set[str]:
    character_ids: set[str] = set()
    depth = 0
    in_character_db = False

    with path.open("r", encoding="utf-8-sig") as handle:
        for raw_line in handle:
            # Remove comments for structural parsing.
            line = raw_line.split("#", 1)[0]

            if not in_character_db and CHAR_DB_START_RE.match(line):
                in_character_db = True

            if in_character_db:
                if depth == 1:
                    match = BLOCK_START_RE.match(line)
                    if match:
                        character_ids.add(match.group(1))

            depth += line.count("{")
            depth -= line.count("}")

            if in_character_db and depth <= 0:
                in_character_db = False

    return character_ids


def country_context(lines: list[str]) -> tuple[list[str | None], dict[str, bool]]:
    """Return country tag per line and whether each country starts with land."""
    stack: list[str | None] = []
    country_stack: list[tuple[str, int]] = []
    line_country: list[str | None] = []
    has_land_by_country: dict[str, bool] = defaultdict(bool)

    for raw_line in lines:
        code = raw_line.split("#", 1)[0]
        current_country = country_stack[-1][0] if country_stack else None
        line_country.append(current_country)

        if current_country is not None:
            country_depth = country_stack[-1][1]
            if len(stack) == country_depth:
                key_match = ASSIGN_KEY_RE.match(code)
                if key_match:
                    key = key_match.group(1)
                    if key in LAND_KEYS:
                        has_land_by_country[current_country] = True

        open_count = code.count("{")
        close_count = code.count("}")

        opened_named = False
        open_match = BLOCK_OPEN_RE.match(code)
        if open_match:
            opened_named = True
            key = open_match.group(1)
            parent = stack[-1] if stack else None
            stack.append(key)

            if parent == "countries" and TAG_RE.match(key):
                country_stack.append((key, len(stack)))

        unnamed_opens = open_count - (1 if opened_named else 0)
        for _ in range(max(unnamed_opens, 0)):
            stack.append(None)

        for _ in range(close_count):
            if not stack:
                break
            popped = stack.pop()
            if country_stack and popped == country_stack[-1][0]:
                country_stack.pop()

    return line_country, has_land_by_country


def replace_in_countries(
    countries_path: Path,
    basegame_ids: set[str],
    dry_run: bool,
) -> tuple[int, int, Counter[str], Counter[str]]:
    with countries_path.open("r", encoding="utf-8-sig") as handle:
        original_lines = handle.readlines()

    line_country, has_land_by_country = country_context(original_lines)

    updated_lines: list[str] = []
    replaced_count = 0
    normalized_count = 0
    replaced_by_key: Counter[str] = Counter()
    replaced_by_reason: Counter[str] = Counter()

    for idx, raw_line in enumerate(original_lines):
        line_no_nl = raw_line.rstrip("\n")
        country = line_country[idx]
        has_land = has_land_by_country.get(country or "", False)

        if line_no_nl.lstrip().startswith("#"):
            updated_lines.append(raw_line)
            continue

        match = ASSIGNMENT_RE.match(line_no_nl)
        if not match:
            updated_lines.append(raw_line)
            continue

        indent, key, value = match.group(1), match.group(2), match.group(3)
        trailing = match.group(4) or ""

        if key not in ROLE_KEYS:
            updated_lines.append(raw_line)
            continue

        # Normalize TODO comments from previous runs:
        # keep TODO only on landed countries.
        if value == "random":
            if TODO_MARKER in trailing and not has_land:
                normalized_count += 1
                new_line = f"{indent}{key} = random"
                if raw_line.endswith("\n"):
                    new_line += "\n"
                updated_lines.append(new_line)
            else:
                updated_lines.append(raw_line)
            continue

        if value not in basegame_ids:
            updated_lines.append(raw_line)
            continue

        reason = "basegame_character_reference"
        replaced_count += 1
        replaced_by_key[key] += 1
        replaced_by_reason[reason] += 1

        # Intentionally drop any existing trailing inline comment on replaced lines.
        new_line = f"{indent}{key} = random"
        if has_land:
            new_line += TODO_COMMENT
        if raw_line.endswith("\n"):
            new_line += "\n"
        updated_lines.append(new_line)

    if (replaced_count > 0 or normalized_count > 0) and not dry_run:
        with countries_path.open("w", encoding="utf-8") as handle:
            handle.writelines(updated_lines)

    return replaced_count, normalized_count, replaced_by_key, replaced_by_reason


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Replace basegame character office-holder references in 10_countries.txt "
            "with 'random'."
        )
    )
    parser.add_argument(
        "--countries-file",
        default="main_menu/setup/start/10_countries.txt",
        help="Path to the mod countries setup file.",
    )
    parser.add_argument(
        "--basegame-characters-file",
        default="/home/rick/Paradox/Games/Europa Universalis V/game/main_menu/setup/start/05_characters.txt",
        help="Path to the basegame character database file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show replacement counts without writing file changes.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    countries_path = Path(args.countries_file)
    basegame_characters_path = Path(args.basegame_characters_file)

    for required_path in (countries_path, basegame_characters_path):
        if not required_path.exists():
            print(f"error: missing file: {required_path}", file=sys.stderr)
            return 2

    basegame_ids = parse_character_ids(basegame_characters_path)

    replaced_count, normalized_count, replaced_by_key, replaced_by_reason = replace_in_countries(
        countries_path=countries_path,
        basegame_ids=basegame_ids,
        dry_run=args.dry_run,
    )

    action = "Would replace" if args.dry_run else "Replaced"
    print(f"{action} {replaced_count} assignment(s) in {countries_path}.")
    normalize_action = "Would normalize" if args.dry_run else "Normalized"
    print(f"{normalize_action} {normalized_count} TODO comment assignment(s) in {countries_path}.")
    if replaced_count:
        print("By key:")
        for key, count in replaced_by_key.most_common():
            print(f"  {key}: {count}")
        print("By reason:")
        for reason, count in replaced_by_reason.most_common():
            print(f"  {reason}: {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
