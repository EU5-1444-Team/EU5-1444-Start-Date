import argparse
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime

from SPARQLWrapper import JSON, SPARQLWrapper

wikidata = SPARQLWrapper("https://query.wikidata.org/sparql")


CUTOFF_DATE = date(1444, 11, 11)


@dataclass(frozen=True)
class Person:
    qid: str
    label_en: str | None
    label_ja: str | None
    birth: str | None  # ISO YYYY-MM-DD
    death: str | None  # ISO YYYY-MM-DD
    father_qid: str | None
    mother_qid: str | None
    birth_place_en: str | None


def normalize_qid(qid: str) -> str:
    qid = qid.strip()
    if qid.startswith("http"):
        qid = qid.rsplit("/", 1)[-1]
    if not re.fullmatch(r"Q\d+", qid):
        raise ValueError(f"Invalid QID: {qid}")
    return qid


def iso_to_eu_date(date_str: str | None) -> str | None:
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
    except Exception:
        return None
    if dt.year <= 0:
        return None
    return f"{dt.year}.{dt.month}.{dt.day}"


def parse_iso_date(date_str: str | None) -> date | None:
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
    except Exception:
        return None
    if dt.year <= 0:
        return None
    return dt.date()


def is_alive_on(person: Person, on_date: date, *, require_birth: bool = False) -> bool:
    birth = parse_iso_date(person.birth)
    death = parse_iso_date(person.death)

    if require_birth and not birth:
        return False

    if birth and birth > on_date:
        return False
    if death and death <= on_date:
        return False
    return True


def make_character_id_from_person(person: Person, id_prefix: str) -> str:
    label = (person.label_en or "").strip()
    if label:
        return f"{id_prefix}{slugify_to_token(label)}"
    return f"{id_prefix}{person.qid.lower()}"


def slugify_to_token(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "unknown"


def extract_given_name(label: str) -> str:
    label = label.strip()
    if not label:
        return ""
    parts = re.split(r"\s+", label)
    if not parts:
        return ""
    return parts[-1]


def make_name_token(person: Person, *, name_prefix: str, fallback_prefix: str) -> str:
    base = (person.label_en or "").strip()
    if base:
        given = extract_given_name(base)
        if given:
            return f"{name_prefix}{slugify_to_token(given)}"
        return f"{name_prefix}{slugify_to_token(base)}"
    return f"{fallback_prefix}{person.qid.lower()}"


def sparql_sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def fetch_person(qid: str, sleep_time: float) -> Person:
    qid = normalize_qid(qid)
    wikidata.setQuery(
        f"""
SELECT
  ?labelEn
  ?labelJa
  ?birth
  ?death
  ?father
  ?mother
  ?birthPlaceLabelEn
WHERE {{
  OPTIONAL {{ wd:{qid} rdfs:label ?labelEn . FILTER(LANG(?labelEn) = \"en\") }}
  OPTIONAL {{ wd:{qid} rdfs:label ?labelJa . FILTER(LANG(?labelJa) = \"ja\") }}
  OPTIONAL {{ wd:{qid} wdt:P569 ?birth . }}
  OPTIONAL {{ wd:{qid} wdt:P570 ?death . }}
  OPTIONAL {{ wd:{qid} wdt:P22 ?father . }}
  OPTIONAL {{ wd:{qid} wdt:P25 ?mother . }}
  OPTIONAL {{
    wd:{qid} wdt:P19 ?birthPlace .
    ?birthPlace rdfs:label ?birthPlaceLabelEn .
    FILTER(LANG(?birthPlaceLabelEn) = \"en\")
  }}
}}
LIMIT 1
"""
    )
    wikidata.setReturnFormat(JSON)
    sparql_sleep(sleep_time)

    try:
        results = wikidata.query().convert()
        binding = results["results"]["bindings"][0]
    except Exception:
        return Person(
            qid=qid,
            label_en=None,
            label_ja=None,
            birth=None,
            death=None,
            father_qid=None,
            mother_qid=None,
            birth_place_en=None,
        )

    label_en = binding.get("labelEn", {}).get("value")
    label_ja = binding.get("labelJa", {}).get("value")
    birth = binding.get("birth", {}).get("value")
    death = binding.get("death", {}).get("value")

    father = binding.get("father", {}).get("value")
    mother = binding.get("mother", {}).get("value")
    father_qid = father.rsplit("/", 1)[-1] if father else None
    mother_qid = mother.rsplit("/", 1)[-1] if mother else None

    birth_place_en = binding.get("birthPlaceLabelEn", {}).get("value")

    return Person(
        qid=qid,
        label_en=label_en,
        label_ja=label_ja,
        birth=birth[:10] if birth else None,
        death=death[:10] if death else None,
        father_qid=father_qid,
        mother_qid=mother_qid,
        birth_place_en=birth_place_en,
    )


def fetch_children_qids(qid: str, sleep_time: float) -> list[str]:
    qid = normalize_qid(qid)
    wikidata.setQuery(
        f"""
SELECT ?child
WHERE {{
  wd:{qid} wdt:P40 ?child .
}}
ORDER BY ?child
"""
    )
    wikidata.setReturnFormat(JSON)
    sparql_sleep(sleep_time)

    try:
        results = wikidata.query().convert()
        bindings = results.get("results", {}).get("bindings", [])
    except Exception:
        return []

    children: list[str] = []
    for b in bindings:
        child = b.get("child", {}).get("value")
        if not child:
            continue
        child_qid = child.rsplit("/", 1)[-1]
        if re.fullmatch(r"Q\d+", child_qid):
            children.append(child_qid)
    return children


def render_character_entry(
    person: Person,
    *,
    char_id: str,
    id_for_qid,
    name_prefix: str,
    name_fallback_prefix: str,
    culture: str,
    religion: str,
    dynasty: str | None,
    tag: str | None,
    birth_location: str | None,
    include_parent_refs: bool,
    known_qids: set[str] | None,
) -> str:
    label_en = person.label_en or person.qid
    label_ja = person.label_ja
    header_comment = f" # {label_ja}" if label_ja else ""
    header_comment += f" ({person.qid})"

    lines: list[str] = []
    lines.append(f"    {char_id} = {{{header_comment}")
    lines.append(
        f"        first_name = {{ name = {make_name_token(person, name_prefix=name_prefix, fallback_prefix=name_fallback_prefix)} }}"
    )
    lines.append(f"        culture = {culture}")
    lines.append(f"        religion = {religion}")

    eu_birth = iso_to_eu_date(person.birth)
    eu_death = iso_to_eu_date(person.death)
    death_dt = parse_iso_date(person.death)
    if eu_birth:
        lines.append(f"        birth_date = {eu_birth}")
    if eu_death and death_dt and death_dt <= CUTOFF_DATE:
        lines.append(f"        death_date = {eu_death}")

    if birth_location:
        lines.append(f"        birth = {birth_location}")
    elif person.birth_place_en:
        lines.append(f"        # birth = <TODO>  # Wikidata birthplace: {person.birth_place_en}")

    if dynasty:
        lines.append(f"        dynasty = {dynasty}")

    if include_parent_refs:
        if person.father_qid:
            lines.append(f"        father = {id_for_qid(person.father_qid)}")
        if person.mother_qid and (known_qids is None or person.mother_qid in known_qids):
            lines.append(f"        mother = {id_for_qid(person.mother_qid)}")

    if tag:
        lines.append(f"        tag = {tag}")

    lines.append("    }")
    return "\n".join(lines)


def emit_person_with_ancestors_and_descendants(
    root_qid: str,
    *,
    id_prefix: str,
    name_prefix: str,
    name_fallback_prefix: str,
    culture: str,
    religion: str,
    dynasty: str | None,
    tag: str | None,
    birth_location: str | None,
    sleep_time: float,
    max_ancestor_depth: int,
    max_descendant_depth: int,
    ensure_fathers_depth: int,
    include_root_if_not_alive: bool,
    descendants_require_birth: bool,
) -> None:
    root_qid = normalize_qid(root_qid)

    person_cache: dict[str, Person] = {}

    qid_to_char_id: dict[str, str] = {}
    used_char_ids: dict[str, str] = {}

    def get_person(qid: str) -> Person:
        qid = normalize_qid(qid)
        if qid not in person_cache:
            person_cache[qid] = fetch_person(qid, sleep_time)
        return person_cache[qid]

    def id_for_qid(qid: str) -> str:
        qid = normalize_qid(qid)
        if qid in qid_to_char_id:
            return qid_to_char_id[qid]

        person = get_person(qid)
        candidate = make_character_id_from_person(person, id_prefix)

        existing = used_char_ids.get(candidate)
        if existing and existing != qid:
            candidate = f"{candidate}_{qid.lower()}"

        qid_to_char_id[qid] = candidate
        used_char_ids[candidate] = qid
        return candidate

    root_person = get_person(root_qid)
    root_alive_on_cutoff = is_alive_on(root_person, CUTOFF_DATE, require_birth=False)
    include_root_entry = include_root_if_not_alive or root_alive_on_cutoff

    ancestor_qids: set[str] = set()
    descendant_qids: set[str] = set()

    visited_ancestors: set[str] = set()

    def gather_ancestors(qid: str, depth: int) -> None:
        qid = normalize_qid(qid)
        if qid in visited_ancestors:
            return
        visited_ancestors.add(qid)
        person = get_person(qid)

        # Always traverse, but only *include* entries that are alive on the cutoff date.
        if depth == 0:
            if include_root_entry:
                ancestor_qids.add(qid)
        else:
            if is_alive_on(person, CUTOFF_DATE, require_birth=True):
                ancestor_qids.add(qid)

        if depth >= max_ancestor_depth:
            return
        if person.father_qid:
            gather_ancestors(person.father_qid, depth + 1)
        if person.mother_qid:
            gather_ancestors(person.mother_qid, depth + 1)

    def gather_descendants_alive(qid: str, depth_remaining: int) -> None:
        if depth_remaining <= 0:
            return

        for child_qid in fetch_children_qids(qid, sleep_time):
            child = get_person(child_qid)
            if not is_alive_on(child, CUTOFF_DATE, require_birth=descendants_require_birth):
                continue
            if child.qid in descendant_qids:
                continue
            descendant_qids.add(child.qid)
            gather_descendants_alive(child.qid, depth_remaining - 1)

    gather_ancestors(root_qid, 0)
    # If the root isn't alive in 1444 (and we're not force-including it), only emit ancestors.
    if include_root_entry:
        gather_descendants_alive(root_qid, max_descendant_depth)

    all_qids: set[str] = set(ancestor_qids)
    all_qids.update(descendant_qids)

    if ensure_fathers_depth > 0:
        frontier: set[str] = set(all_qids)
        for _ in range(ensure_fathers_depth):
            next_frontier: set[str] = set()
            for qid in frontier:
                father_qid = get_person(qid).father_qid
                if father_qid and father_qid not in all_qids:
                    # Fathers are included even if not alive on the cutoff, to preserve
                    # at least the patriline linkage in the emitted set.
                    all_qids.add(father_qid)
                    next_frontier.add(father_qid)
            if not next_frontier:
                break
            frontier = next_frontier

    emitted: set[str] = set()

    def emit_parents_first(qid: str) -> None:
        qid = normalize_qid(qid)
        if qid in emitted:
            return
        person = get_person(qid)
        if person.father_qid and person.father_qid in all_qids:
            emit_parents_first(person.father_qid)
        if person.mother_qid and person.mother_qid in all_qids:
            emit_parents_first(person.mother_qid)
        if emitted:
            print()
        print(
            render_character_entry(
                person,
                char_id=id_for_qid(person.qid),
                id_for_qid=id_for_qid,
                name_prefix=name_prefix,
                name_fallback_prefix=name_fallback_prefix,
                culture=culture,
                religion=religion,
                dynasty=dynasty,
                tag=tag,
                birth_location=birth_location,
                include_parent_refs=True,
                known_qids=all_qids,
            )
        )
        emitted.add(qid)

    # Emit the root (and its ancestors) first when included; otherwise emit only alive ancestors.
    if include_root_entry:
        emit_parents_first(root_qid)
        for qid in sorted(descendant_qids):
            emit_parents_first(qid)
    else:
        for qid in sorted(ancestor_qids):
            emit_parents_first(qid)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate EU5-style character entries from Wikidata and print them to stdout. "
            "This script does not modify any game files."
        )
    )
    parser.add_argument("qid", nargs="?", default="Q8081786", help="Root Wikidata QID")
    parser.add_argument(
        "--id-prefix",
        default="wd_",
        help='Prefix for generated character IDs (default: "wd_")',
    )
    parser.add_argument(
        "--name-prefix",
        default="name_",
        help='Prefix for generated localization name tokens (default: "name_")',
    )
    parser.add_argument(
        "--name-fallback-prefix",
        default="name_q",
        help='Prefix for fallback name tokens when label is missing (default: "name_q")',
    )
    parser.add_argument(
        "--culture",
        default="saigoku_culture",
        help="EU5 culture token to emit",
    )
    parser.add_argument(
        "--religion",
        default="shinto",
        help="EU5 religion token to emit",
    )
    parser.add_argument(
        "--dynasty",
        default=None,
        help="EU5 dynasty token to emit (e.g., oouchi_dynasty)",
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="EU5 country tag token to emit (e.g., OUC)",
    )
    parser.add_argument(
        "--birth-location",
        default=None,
        help="EU5 birth location token to emit (e.g., kyoto)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.2,
        help="Delay between Wikidata requests (seconds)",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=6,
        help="How many generations of ancestors to consider (default: 6). Only prints those alive on 1444.11.11.",
    )
    parser.add_argument(
        "--descendant-depth",
        type=int,
        default=6,
        help=(
            "How many generations of descendants to include, filtering to only those alive on "
            "1444.11.11 (default: 6)"
        ),
    )
    parser.add_argument(
        "--ensure-fathers-depth",
        type=int,
        default=1,
        help=(
            "Ensure fathers are included/emitted for printed people by pulling in missing fathers up to this many steps "
            "(default: 1). Set 0 to disable."
        ),
    )
    parser.add_argument(
        "--include-root-if-not-alive",
        action="store_true",
        help=(
            "Include the root person even if not alive on 1444.11.11 (default: skip if not alive)."
        ),
    )
    parser.add_argument(
        "--descendants-require-birth",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Require descendants to have a known birth date to be considered alive on 1444.11.11. "
            "Disable with --no-descendants-require-birth to include children with unknown birth dates."
        ),
    )
    args = parser.parse_args(argv)

    emit_person_with_ancestors_and_descendants(
        args.qid,
        id_prefix=args.id_prefix,
        name_prefix=args.name_prefix,
        name_fallback_prefix=args.name_fallback_prefix,
        culture=args.culture,
        religion=args.religion,
        dynasty=args.dynasty,
        tag=args.tag,
        birth_location=args.birth_location,
        sleep_time=args.sleep,
        max_ancestor_depth=max(0, args.depth),
        max_descendant_depth=max(0, args.descendant_depth),
        ensure_fathers_depth=max(0, args.ensure_fathers_depth),
        include_root_if_not_alive=args.include_root_if_not_alive,
        descendants_require_birth=args.descendants_require_birth,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(130)
