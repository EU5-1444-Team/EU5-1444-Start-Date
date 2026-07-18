#!/usr/bin/env python3
"""Parse EU5 error logs and write each error category to its own file."""

import re
import os
from collections import defaultdict

ERROR_LOGS = [
    "/home/rick/Paradox/EU5 Logs/error.log",
    "/home/rick/Paradox/EU5 Logs/error.1.log",
]
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "bugs")

def categorize(source, message):
    """Return (category_name, detail_string) for an error entry."""
    full = f"[{source}]: {message}"

    # ── government.cpp ──────────────────────────────────────────
    if "Removing invalid law" in message:
        m = re.search(r"Removing invalid law '(\w+)' for '([^']+)'", message)
        return "invalid_laws", f"{m.group(1)} | {m.group(2)}" if m else message
    if "Removing invalid policy" in message:
        m = re.search(r"Removing invalid policy '(\w+)' for '([^']+)'", message)
        return "invalid_policies", f"{m.group(1)} | {m.group(2)}" if m else message
    if "Removing invalid reform" in message:
        m = re.search(r"Removing invalid reform '(\w+)' for '([^']+)'", message)
        return "invalid_reforms", f"{m.group(1)} | {m.group(2)}" if m else message
    if "Removing invalid estate privilege" in message:
        m = re.search(r"Removing invalid estate privilege '(\w+)' for '([^']+)'", message)
        return "invalid_estate_privileges", f"{m.group(1)} | {m.group(2)}" if m else message
    if "Subject type" in message and "is invalid" in message:
        m = re.search(r"Subject type '(\w+)' is invalid for '([^']+)'", message)
        return "invalid_subject_types", f"{m.group(1)} | {m.group(2)}" if m else message

    # ── ruler_term overlaps ─────────────────────────────────────
    if "overlaps with" in message:
        return "ruler_term_overlaps", message.strip()

    # ── jomini_script_system ────────────────────────────────────
    if "jomini_script_system" in source or "Script system error" in message:
        # Try to extract the specific error
        m = re.search(r"Error:\s*(.+?)(?:\s*Script location:|$)", message, re.DOTALL)
        detail = m.group(1).strip() if m else message.strip()
        detail = detail.split('\n')[0][:150]
        if "government_type" in detail:
            return "script_government_type", detail
        if "'culture'" in detail and "invalid object" in detail:
            return "script_culture_invalid", detail
        if "'religion'" in detail and "invalid object" in detail:
            return "script_religion_invalid", detail
        if "'capital'" in detail and "invalid object" in detail:
            return "script_capital_invalid", detail
        if "'continent'" in detail and "invalid object" in detail:
            return "script_continent_invalid", detail
        if "set_court_language" in detail:
            return "script_court_language", detail
        if "Null object" in detail or "unset scope" in detail or "invalid object" in detail:
            return "script_scope_errors", detail
        return "script_other_errors", detail

    # ── pdx_persistent_reader (parse errors) ────────────────────
    if "pdx_persistent_reader" in source:
        if "Unknown trigger type" in message:
            m = re.search(r"Unknown trigger type: (\S+)", message)
            return "unknown_triggers", f"{m.group(1)} | {message.strip()[:120]}" if m else message
        if "Unexpected token" in message:
            m = re.search(r"Unexpected token: (\S+), near line: (\d+)", message)
            m2 = re.search(r'in file: "([^"]+)"', message)
            return "parse_errors", f"{m.group(1)} @ L{m.group(2)} in {m2.group(1)}" if m else message
        if "Invalid date string" in message:
            return "parse_date_errors", message.strip()[:150]
        if "Error:" in message:
            return "pdx_reader_other", message.strip()[:150]
        return "pdx_reader_other", message.strip()[:150]

    # ── event_database ──────────────────────────────────────────
    if "event_database" in source:
        if "missing an outcome" in message:
            m = re.search(r"in (.+)\.", message)
            return "event_missing_outcome", m.group(1) if m else message
        if "missing a title" in message:
            m = re.search(r"in (.+)\.", message)
            return "event_missing_title", m.group(1) if m else message
        return "event_other", message.strip()[:150]

    # ── jomini_effect / jomini_trigger ──────────────────────────
    if "jomini_effect" in source and "Unknown effect" in message:
        return "unknown_effects", message.strip()[:150]
    if "jomini_trigger" in source and "Inconsistent" in message:
        return "inconsistent_triggers", message.strip()[:150]
    if "jomini_eventtarget" in source and "Failed to find" in message:
        return "missing_event_targets", message.strip()[:150]
    if "jomini_onaction" in source:
        if "more than one" in message:
            return "duplicate_onaction_effects", message.strip()[:150]
        if "Couldn't find predefined" in message:
            return "missing_onactions", message.strip()[:150]

    # ── gamedatabase.h (duplicated keys) ────────────────────────
    if "Duplicated key" in message:
        m = re.search(r"Duplicated key (\S+)", message)
        m2 = re.search(r"from file: (.+)", message)
        return "duplicated_keys", f"{m.group(1)} in {m2.group(1)}" if m else message

    # ── localization_util ───────────────────────────────────────
    if "localization_util" in source:
        if "MODIFIER_TYPE_" in message:
            return "modifier_localization", message.strip()[:150]
        if "TODO" in message:
            return "localization_todos", message.strip()[:150]
        # Skip redundant name defs (e.g. TUO: "TUO")
        if re.match(r'^\w+:', message.strip()) and "TODO" not in message and '\n' not in message:
            return None, None  # Skip noise
        return "localization_other", message.strip()[:150]

    # ── modifier_type ───────────────────────────────────────────
    if "modifier_type" in source:
        if "Missing Icon" in message:
            m = re.search(r'Modifier : (\S+)', message)
            return "missing_modifier_icons", m.group(1) if m else message
        if "must exist" in message:
            return "missing_modifier_defs", message.strip()[:150]

    # ── initialize_from_bookmark ────────────────────────────────
    if "initialize_from_bookmark" in source:
        if "is 119 years old" in message or "years old" in message:
            return None, None  # Age warnings — skip
        if "ruler_term scripted, but no current ruler" in message:
            return "missing_rulers", message.strip()[:150]
        if "has no birth scripted" in message:
            return "missing_births", message.strip()[:150]
        if "heir-selection" in message:
            return "heir_selection_mismatch", message.strip()[:150]
        if "no heir-selection" in message:
            return "no_heir_selection", message.strip()[:150]
        if "no ruler in setup" in message:
            return "no_ruler_in_setup", message.strip()[:150]
        if "has no pops of its primary culture" in message:
            return "no_primary_culture_pops", message.strip()[:150]
        if "religion" in message and "dominant religion" in message:
            return "religion_mismatch", message.strip()[:150]
        if "has no Court Country" in message:
            return "no_court_country", message.strip()[:150]
        if "no culture set" in message:
            return "missing_character_culture", message.strip()[:150]
        if "both male" in message or "both female" in message:
            return "invalid_spouse_gender", message.strip()[:150]
        if "invalid building" in message:
            return "invalid_buildings", message.strip()[:150]
        return "bookmark_other", message.strip()[:150]

    # ── country.cpp ─────────────────────────────────────────────
    if "country.cpp" in source:
        if "already owned by" in message:
            return "duplicate_location_owner", message.strip()[:150]
        if "already controlled by" in message:
            return "duplicate_location_controller", message.strip()[:150]
        if "assigned location" in message and "more than once" in message:
            return "duplicate_location_assignment", message.strip()[:150]
        if "broken dynasty" in message:
            return "broken_dynasties", message.strip()[:150]
        if "same name" in message:
            return "duplicate_country_names", message.strip()[:150]
        return "country_other", message.strip()[:150]

    # ── government.cpp (non-reform/law) ─────────────────────────
    if "government.cpp" in source:
        if "no ruler in setup" in message:
            return "no_ruler_in_setup", message.strip()[:150]
        if "Skipping adding a reform" in message:
            return "duplicate_reform_assignment", message.strip()[:150]
        return "government_other", message.strip()[:150]

    # ── country_manager ─────────────────────────────────────────
    if "country_manager" in source:
        return "unknown_country_refs", message.strip()[:150]

    # ── cabinet_effects ─────────────────────────────────────────
    if "cabinet_effects" in source:
        return "cabinet_blocked_characters", message.strip()[:150]

    # ── relation.cpp ────────────────────────────────────────────
    if "relation.cpp" in source:
        return "invalid_relations", message.strip()[:150]

    # ── coat_of_arms ────────────────────────────────────────────
    if "coat_of_arms" in source:
        return "coat_of_arms_errors", message.strip()[:150]

    # ── location_manager ────────────────────────────────────────
    if "location_manager" in source:
        return "invalid_locations", message.strip()[:150]

    # ── language_names ──────────────────────────────────────────
    if "language_names" in source:
        return "language_name_errors", message.strip()[:150]

    # ── lexer (encoding) ────────────────────────────────────────
    if "lexer" in source and "utf8-bom" in message:
        return "encoding_errors", message.strip()[:150]

    # ── deferred_database_lookup ────────────────────────────────
    if "deferred_database_lookup" in source:
        return "missing_db_fields", message.strip()[:150]

    # ── dlcreloadable (version mismatch) ────────────────────────
    if "dlcreloadable" in source or "version" in message.lower() and "does not match" in message:
        return "version_mismatches", message.strip()[:150]

    # ── pdxassert ───────────────────────────────────────────────
    if "pdx_assert" in source or "pdxassert" in source:
        return "assertion_failures", message.strip()[:150]

    # ── icondatabase / texture ──────────────────────────────────
    if "icondatabase" in source or "gfx_texture_loader" in source:
        return "texture_errors", message.strip()[:150]

    # ── building_type ───────────────────────────────────────────
    if "building_type" in source:
        return "building_errors", message.strip()[:150]

    # ── country_database ────────────────────────────────────────
    if "country_database" in source:
        return "country_database_errors", message.strip()[:150]

    # ── pdxinput_context ────────────────────────────────────────
    if "pdxinput_context" in source:
        return "input_context_errors", message.strip()[:150]

    # ── gamestate ───────────────────────────────────────────────
    if "gamestate" in source:
        return "gamestate_errors", message.strip()[:150]

    # ── utility.h ───────────────────────────────────────────────
    if "utility.h" in source:
        return "utility_errors", message.strip()[:150]

    # ── Fallback ────────────────────────────────────────────────
    # Use source file as category
    src_name = re.sub(r'\.cpp.*', '', source.split('/')[-1])
    return f"other_{src_name}", message.strip()[:150]


def parse_log(filepath):
    """Parse a log file into (source, message) entries."""
    entries = []
    current = None
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.rstrip('\n')
            m = re.match(r'^\[(\d+:\d+:\d+)\]\[([^\]]+)\]:\s*(.*)', line)
            if m:
                if current:
                    entries.append(current)
                current = {
                    'timestamp': m.group(1),
                    'source': m.group(2),
                    'message': m.group(3),
                    'raw': line,
                    'file': os.path.basename(filepath),
                }
            elif current:
                current['message'] += '\n' + line
                current['raw'] += '\n' + line
    if current:
        entries.append(current)
    return entries


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # category -> list of (filename, entry)
    categories = defaultdict(list)

    for logpath in ERROR_LOGS:
        basename = os.path.basename(logpath)
        entries = parse_log(logpath)
        print(f"Parsed {basename}: {len(entries)} entries")

        for entry in entries:
            cat, detail = categorize(entry['source'], entry['message'])
            if cat is None:
                continue  # Skip noise
            categories[cat].append((basename, entry, detail))

    # Sort by count descending
    sorted_cats = sorted(categories.items(), key=lambda x: -len(x[1]))

    # Write index file
    index_path = os.path.join(OUTPUT_DIR, "00_INDEX.txt")
    with open(index_path, 'w') as f:
        f.write("EU5 ERROR LOG CATEGORIES\n")
        f.write("=" * 60 + "\n\n")
        total = sum(len(v) for _, v in sorted_cats)
        f.write(f"Total error entries (excluding noise): {total}\n")
        f.write(f"Total categories: {len(sorted_cats)}\n\n")
        f.write(f"{'Count':>6s}  {'Category':<40s}  File\n")
        f.write("-" * 80 + "\n")
        for cat, items in sorted_cats:
            filename = f"{cat}.txt"
            f.write(f"{len(items):6d}  {cat:<40s}  {filename}\n")

    print(f"\nWrote {len(sorted_cats)} category files to {OUTPUT_DIR}/\n")

    # Write each category file
    for cat, items in sorted_cats:
        filepath = os.path.join(OUTPUT_DIR, f"{cat}.txt")
        with open(filepath, 'w') as f:
            f.write(f"Category: {cat}\n")
            f.write(f"Count: {len(items)}\n")
            f.write("=" * 80 + "\n\n")

            # Group by detail to show unique instances
            by_detail = defaultdict(list)
            for basename, entry, detail in items:
                by_detail[detail].append((basename, entry))

            for detail, group in sorted(by_detail.items(), key=lambda x: -len(x[1])):
                count = len(group)
                f.write(f"--- [{count}x] {detail}\n")
                # Show first 3 instances with timestamps
                for basename, entry in group[:3]:
                    f.write(f"    [{basename}] {entry['timestamp']} {entry['source']}\n")
                if count > 3:
                    f.write(f"    ... and {count - 3} more\n")
                f.write("\n")

    # Print summary
    print(f"{'Count':>6s}  {'Category':<40s}")
    print("-" * 50)
    for cat, items in sorted_cats:
        print(f"{len(items):6d}  {cat}")


if __name__ == '__main__':
    main()
