#!/usr/bin/env python3
"""Generate country color picker startup dispatcher + per-tag events from entry data."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Entry:
    name: str
    tag: str
    choices: dict[int, str]
    choice_labels: dict[int, str]
    keep_default_label: str | None


def _strip_inline_comment(line: str) -> str:
    if "#" not in line:
        return line
    return line.split("#", 1)[0]


def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
        return v[1:-1]
    return v


def _yaml_escape(value: str) -> str:
    return value.replace('"', '\\"')


_NAMED_COLOR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COLOR_EXPR_RE = re.compile(r"^(rgb|hsv|hsv360)\s*\{.+\}$", re.DOTALL)
_TAG_KEY_RE = re.compile(r"^[A-Z0-9]{3}$")


def _normalize_ws(value: str) -> str:
    return " ".join(value.split())


def _is_named_color_token(value: str) -> bool:
    return bool(_NAMED_COLOR_RE.match(value.strip()))


def _is_color_expression(value: str) -> bool:
    return bool(_COLOR_EXPR_RE.match(_normalize_ws(value.strip())))


def _choice_generated_color_key(entry: Entry, choice_idx: int) -> str:
    return f"map_1444_cpp_{entry.tag.lower()}_{choice_idx}"


def _choice_effect_color_value(entry: Entry, choice_idx: int) -> str:
    value = entry.choices[choice_idx].strip()
    if _is_named_color_token(value):
        return value
    return _choice_generated_color_key(entry, choice_idx)


def parse_entries(text: str) -> list[Entry]:
    # Strip comments before structural parsing so braces in comments
    # can't corrupt block depth tracking.
    text = "\n".join(_strip_inline_comment(line) for line in text.splitlines())

    entries: list[Entry] = []
    i = 0
    n = len(text)

    while i < n:
        match = re.search(r"([A-Za-z0-9_]+)\s*=", text[i:])
        if not match:
            break

        name = match.group(1)
        eq_pos = i + match.end(0)

        j = eq_pos
        while j < n and text[j].isspace():
            j += 1
        if j >= n or text[j] != "{":
            i = eq_pos
            continue

        depth = 0
        k = j
        while k < n:
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1

        if k >= n:
            # Ignore malformed/incomplete blocks so the generator can still
            # produce a no-op output for debugging.
            i = eq_pos
            continue

        body = text[j + 1 : k]

        parsed = _parse_entry_body(name, body)
        if parsed is not None:
            entries.append(parsed)

        i = k + 1

    _validate_entries(entries)
    return entries


def _parse_entry_body(name: str, body: str) -> Entry | None:
    if not _TAG_KEY_RE.match(name):
        return None

    tag = name
    choices: dict[int, str] = {}
    choice_labels: dict[int, str] = {}
    keep_default_label: str | None = None

    for raw_line in body.splitlines():
        line = _strip_inline_comment(raw_line).strip()
        if not line:
            continue

        m = re.match(r"([A-Za-z0-9_]+)\s*=\s*(.+)$", line)
        if not m:
            continue

        key = m.group(1)
        value = m.group(2).strip()

        if key == "tag":
            raise ValueError(
                f"Entry '{name}' uses 'tag = ...'. Use the block key as the tag instead, "
                "e.g. TUR = { ... }."
            )

        if key == "keep_default_label":
            keep_default_label = _unquote(value)
            continue

        choice_match = re.match(r"choice([1-9][0-9]*)$", key)
        if choice_match:
            idx = int(choice_match.group(1))
            choices[idx] = value
            continue

        label_match = re.match(r"choice([1-9][0-9]*)_(label|text|name)$", key)
        if label_match:
            idx = int(label_match.group(1))
            choice_labels[idx] = _unquote(value)
            continue

    if not choices:
        # Treat a tag block with no choices as disabled.
        return None

    return Entry(
        name=name,
        tag=tag,
        choices=choices,
        choice_labels=choice_labels,
        keep_default_label=keep_default_label,
    )


def _validate_entries(entries: list[Entry]) -> None:
    seen_tags: set[str] = set()
    for entry in entries:
        if entry.tag in seen_tags:
            raise ValueError(f"Duplicate tag '{entry.tag}' across entries.")
        seen_tags.add(entry.tag)

        if 1 not in entry.choices:
            raise ValueError(f"Entry '{entry.name}' ({entry.tag}) must define choice1.")

        max_choice = max(entry.choices.keys())
        if max_choice > 4:
            raise ValueError(
                f"Entry '{entry.name}' ({entry.tag}) defines choice{max_choice}; max supported is choice4."
            )

        for idx in range(1, max_choice + 1):
            if idx not in entry.choices:
                raise ValueError(
                    f"Entry '{entry.name}' ({entry.tag}) has a gap: missing choice{idx}."
                )

            choice_value = entry.choices[idx].strip()
            if not (_is_named_color_token(choice_value) or _is_color_expression(choice_value)):
                raise ValueError(
                    f"Entry '{entry.name}' ({entry.tag}) choice{idx} has invalid color value "
                    f"'{choice_value}'. Use a named color token (e.g. map_TUR) or "
                    "a color expression (e.g. rgb { 180 25 25 })."
                )


def build_dispatcher_effects(entries: list[Entry]) -> str:
    out: list[str] = []

    out.append("# AUTO-GENERATED FILE - DO NOT EDIT DIRECTLY")
    out.append("# Generated by: tools/generate_country_color_picker_effects.py")
    out.append("# Source: tools/country_color_picker_entries/1444_country_color_picker_entries.txt")
    out.append("")
    out.append("1444_queue_country_color_picker_events = {")
    for idx, entry in enumerate(entries, start=1):
        out.append(f"\tc:{entry.tag} = {{")
        out.append("\t\ttrigger_event_non_silently = {")
        out.append(f"\t\t\tid = 1444_country_color_picker_generated.{idx}")
        out.append("\t\t\tdays = 1")
        out.append("\t\t}")
        out.append("\t}")

    out.append("}")
    out.append("")

    return "\n".join(out)


def build_generated_named_colors(entries: list[Entry]) -> str:
    out: list[str] = []
    out.append("# AUTO-GENERATED FILE - DO NOT EDIT DIRECTLY")
    out.append("# Generated by: tools/generate_country_color_picker_effects.py")
    out.append("# Source: tools/country_color_picker_entries/1444_country_color_picker_entries.txt")
    out.append("")
    out.append("colors = {")

    for entry in entries:
        max_choice = max(entry.choices.keys())
        for choice_idx in range(1, max_choice + 1):
            value = entry.choices[choice_idx].strip()
            if _is_named_color_token(value):
                continue

            key = _choice_generated_color_key(entry, choice_idx)
            out.append(f"\t{key} = {_normalize_ws(value)}")

    out.append("}")
    out.append("")
    return "\n".join(out)


def build_generated_events(entries: list[Entry]) -> str:
    out: list[str] = []

    out.append("namespace = 1444_country_color_picker_generated")
    out.append("")

    for idx, entry in enumerate(entries, start=1):
        max_choice = max(entry.choices.keys())

        out.append(f"1444_country_color_picker_generated.{idx} = {{")
        out.append("\ttype = country_event")
        out.append("\ttitle = 1444_country_color_picker.1.title")
        out.append("\tdesc = 1444_country_color_picker.1.desc")
        out.append("")
        out.append("\tfire_only_once = yes")
        out.append("\ttrigger = {")
        out.append("\t\tis_human = yes")
        out.append(f"\t\ttag = {entry.tag}")
        out.append("\t}")
        out.append("")

        for choice_idx in range(1, max_choice + 1):
            opt_key = chr(ord("a") + (choice_idx - 1))
            value = _choice_effect_color_value(entry, choice_idx)
            out.append("\toption = {")
            out.append(f"\t\tname = 1444_country_color_picker_generated.{idx}.{opt_key}")
            out.append(f"\t\tchange_country_color = {value}")
            out.append("\t}")
            out.append("")

        keep_key = chr(ord("a") + max_choice)
        out.append("\toption = {")
        out.append(f"\t\tname = 1444_country_color_picker_generated.{idx}.{keep_key}")
        out.append("\t\t# Keep default/current color.")
        out.append("\t}")
        out.append("}")
        out.append("")

    return "\n".join(out)


def build_generated_localization(entries: list[Entry]) -> str:
    out: list[str] = []
    out.append("l_english:")

    for idx, entry in enumerate(entries, start=1):
        max_choice = max(entry.choices.keys())
        for choice_idx in range(1, max_choice + 1):
            opt_key = chr(ord("a") + (choice_idx - 1))
            label = entry.choice_labels.get(choice_idx, f"Color Option {choice_idx}")
            out.append(
                f"  1444_country_color_picker_generated.{idx}.{opt_key}: \"{_yaml_escape(label)}\""
            )

        keep_key = chr(ord("a") + max_choice)
        keep_label = entry.keep_default_label or "Keep Default Color"
        out.append(
            f"  1444_country_color_picker_generated.{idx}.{keep_key}: \"{_yaml_escape(keep_label)}\""
        )

    out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--entries",
        default="tools/country_color_picker_entries/1444_country_color_picker_entries.txt",
        help="Path to data entries file.",
    )
    parser.add_argument(
        "--effects-output",
        default="in_game/common/scripted_effects/1444_country_color_picker_effects.txt",
        help="Path to generated scripted effects dispatcher.",
    )
    parser.add_argument(
        "--events-output",
        default="in_game/events/Meta/1444_country_color_picker_generated.txt",
        help="Path to generated events file.",
    )
    parser.add_argument(
        "--colors-output",
        default="main_menu/common/named_colors/zz_1444_country_color_picker_generated.txt",
        help="Path to generated named colors file for inline rgb/hsv choices.",
    )
    parser.add_argument(
        "--loc-output",
        default="main_menu/localization/english/1444_country_color_picker_generated_l_english.yml",
        help="Path to generated english localization for generated option labels.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    entries_path = (repo_root / args.entries).resolve()
    effects_path = (repo_root / args.effects_output).resolve()
    events_path = (repo_root / args.events_output).resolve()
    colors_path = (repo_root / args.colors_output).resolve()
    loc_path = (repo_root / args.loc_output).resolve()

    text = entries_path.read_text(encoding="utf-8-sig")
    entries = parse_entries(text)

    effects_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    colors_path.parent.mkdir(parents=True, exist_ok=True)
    loc_path.parent.mkdir(parents=True, exist_ok=True)

    effects_path.write_text(build_dispatcher_effects(entries), encoding="utf-8-sig")
    events_path.write_text(build_generated_events(entries), encoding="utf-8-sig")
    colors_path.write_text(build_generated_named_colors(entries), encoding="utf-8-sig")
    loc_path.write_text(build_generated_localization(entries), encoding="utf-8-sig")

    print(f"Generated dispatcher: {effects_path}")
    print(f"Generated events: {events_path}")
    print(f"Generated named colors: {colors_path}")
    print(f"Generated localization: {loc_path}")
    print(f"Entries processed: {len(entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
