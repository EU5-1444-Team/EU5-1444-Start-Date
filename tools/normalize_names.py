#!/usr/bin/env python3
"""
EU5 Mod Name/Loc Normalizer
============================

Problem this solves:
  - Character/country name IDs (name=, nickname=, last_name=, regnal_numbers{})
    have been entered inconsistently: some prefixed (name_timur), some bare
    (timur), some with wrong casing (Timur / TIMUR), some as raw multi-word
    underscored strings that should be split into connector + segments
    (de_trasmistria -> connector_de.lastname_trasmistria).
  - The loc .yml file has to have a matching key for every one of these,
    with the *display value* (which may contain diacritics) preserved.
  - Character/country BLOCK IDs (e.g. slz_friedrich_iv_truchess_von_emmerberg,
    country tags like SLZ) must NEVER be touched - only the name/nickname/
    last_name FIELD VALUES inside those blocks.

Source of truth:
  characters.txt (and any other character files) define, per field type,
  which pool a bare key belongs to (name_ / nickname_ / lastname_). We use
  that to generate canonical IDs, then propagate the rename to:
    - the loc yml (rewriting keys, keeping quoted values intact)
    - countries.txt / countries' regnal_numbers block (same field = "name")

Nothing here renames a character/country BLOCK id. Only field values that
look like name/nickname/last_name/regnal-number references are ever changed.

USAGE:
    python3 normalize_names.py --root /path/to/mod --apply
    (omit --apply to do a dry run and just print the report)

OUTPUT:
    report.txt        - human-readable summary, conflicts, ambiguous cases
    rename_map.json    - full old_id -> new_id mapping actually used
    (files are modified in place if --apply is passed; .bak backups made)
"""

import argparse
import json
import re
import sys
from pathlib import Path
from collections import defaultdict

try:
    from unidecode import unidecode
except ImportError:
    print("ERROR: this script requires the 'unidecode' package.\n"
          "Install it with: pip install unidecode --break-system-packages",
          file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONNECTORS = {
    "de", "de la", "del", "della", "der", "des", "di", "du", "of", "the",
    "van", "van_de", "van_der", "von", "la", "le", "les", "el", "al",
    "ibn", "bin", "bint", "zu", "ze", "af", "av", "y", "e",
}

POOL_PREFIXES = {
    "name": "name_",
    "nickname": "nickname_",
    "lastname": "lastname_",
}
# Alternate/shorthand prefixes seen in the wild that mean the same pool as
# one of the canonical POOL_PREFIXES above. Recognized on input (so we
# don't treat "nick" as a literal name segment), but always normalized to
# the canonical prefix on output.
POOL_PREFIX_ALIASES = {
    "nick_": "nickname",
    "fname_": "name",
    "first_name_": "name",
    "lname_": "lastname",
    "last_name_": "lastname",
}
CONNECTOR_PREFIX = "connector_"

# Matches: first_name = { name = XXX }   /  nickname = { name = XXX }
# etc. Handles the value being bare, quoted, or already prefixed.
FIELD_BLOCK_RE = re.compile(
    r'(?P<field>first_name|nickname|last_name)\s*=\s*\{\s*name\s*=\s*'
    r'(?P<quote>"?)(?P<value>[^"\s}][^"}]*?)(?P=quote)\s*\}'
)

# regnal_numbers = { name_x = N  name_y = N ... }
REGNAL_BLOCK_RE = re.compile(r'regnal_numbers\s*=\s*\{([^}]*)\}', re.DOTALL)
REGNAL_ENTRY_RE = re.compile(r'(?P<key>[A-Za-z0-9_]+)\s*=\s*\d+')

# Loc yml line: key:0 "Value with possible diacritics"   (number optional)
LOC_LINE_RE = re.compile(
    r'^(?P<indent>\s*)(?P<key>[^\s:#]+)\s*:\s*(?P<num>\d*)\s*'
    r'"(?P<value>.*)"(?P<trail>.*)$'
)

FIELD_TO_POOL = {
    "first_name": "name",
    "nickname": "nickname",
    "last_name": "lastname",
}


def scan_loc_for_hyphenated_values(loc_files):
    """
    Pre-scan every loc file to find display values that contain a hyphen,
    e.g. anjou_naples:0 "Anjou-Naples". Returns a set of underscore-joined,
    lowercased, ASCII-folded bare forms (e.g. "anjou_naples") whose loc
    value contains a hyphen at a word boundary matching the underscore
    positions - i.e. a strong signal the id is a hyphenated compound word,
    not several independent name segments that should be split and
    prefixed separately.

    We deliberately only flag these - the caller decides whether to skip
    splitting or just report it, but either way this makes the hyphen
    information (which the ID/key format itself cannot carry) available
    from the one place it survives: the loc display string.
    """
    hyphenated_bare_forms = set()
    for path in loc_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = LOC_LINE_RE.match(line.rstrip("\n"))
            if not m:
                continue
            raw_key = m.group("key")
            value = m.group("value")
            if "-" not in value:
                continue
            folded_key = unidecode(raw_key.lower())
            _, bare = strip_pool_prefix(folded_key)
            bare = bare if bare else folded_key
            # value words (hyphen or space separated) vs key words
            # (underscore separated) should line up in count/rough spelling
            # for us to trust this is the same compound, not a coincidence
            value_words = re.split(r"[-\s]+", unidecode(value.lower()))
            key_words = bare.split("_")
            if len(value_words) == len(key_words) and len(key_words) > 1:
                hyphenated_bare_forms.add(bare)
    return hyphenated_bare_forms


def strip_pool_prefix(raw):
    """If raw already starts with a known pool prefix (canonical or a
    recognized alias like nick_/fname_/lname_), return (pool, rest).
    Otherwise return (None, raw). Aliases are stripped just like canonical
    prefixes - canonicalize() re-adds the correct canonical prefix, so
    nick_al_dawla becomes nickname_al_dawla, not nickname_nick.connector_al...
    """
    # check aliases first (longest-prefix-wins isn't needed here since none
    # of the alias/canonical prefixes are prefixes of each other except
    # nick_/nickname_ - and nickname_ is checked in the canonical loop, so
    # order between the two dicts doesn't cause a wrong partial match)
    for alias_prefix, pool in POOL_PREFIX_ALIASES.items():
        if raw.startswith(alias_prefix):
            return pool, raw[len(alias_prefix):]
    for pool, prefix in POOL_PREFIXES.items():
        if raw.startswith(prefix):
            return pool, raw[len(prefix):]
    return None, raw


def split_segments(rest):
    """Split a bare (already lowercased) identifier on underscores into
    segments, checking multi-word connectors greedily (longest match first)
    against the CONNECTORS set. Returns list of (is_connector, text)."""
    parts = rest.split("_")
    segments = []
    i = 0
    n = len(parts)
    # Sort candidate connector phrases by word-length descending so
    # "van_der" is tried before "van".
    multi_word_connectors = sorted(
        (c for c in CONNECTORS if "_" in c or " " in c),
        key=lambda c: -len(c.replace(" ", "_").split("_"))
    )
    while i < n:
        matched = False
        for c in multi_word_connectors:
            c_norm = c.replace(" ", "_")
            c_parts = c_norm.split("_")
            span = len(c_parts)
            if parts[i:i + span] == c_parts:
                segments.append((True, c_norm))
                i += span
                matched = True
                break
        if not matched:
            word = parts[i]
            is_conn = word in CONNECTORS
            segments.append((is_conn, word))
            i += 1
    return segments


def canonicalize(raw_value, pool, hyphenated_forms=None, flag_sink=None):
    """
    Turn a raw field value (as found in characters.txt/countries.txt or a
    loc key) into its canonical dotted id, given which pool (name/nickname/
    lastname) it belongs to.

    Examples:
      canonicalize("Timur", "name")            -> "name_timur"
      canonicalize("name_timur", "name")       -> "name_timur"
      canonicalize("abu_said", "name")         -> "name_abu.name_said"
      canonicalize("Truchseb", "nickname")     -> "nickname_truchseb"
      canonicalize("de_trasmistria", "lastname")
            -> "connector_de.lastname_trasmistria"
      canonicalize("of_the_holy_secuplur", "nickname")
            -> "connector_of.connector_the.nickname_holy.nickname_secuplur"

    If hyphenated_forms is given (a set built by scan_loc_for_hyphenated_
    values) and this value's bare form is in it, we do NOT split - the loc
    file shows this is one hyphenated compound word (e.g. Anjou-Naples),
    not several independent name segments. It's kept as a single prefixed
    id instead, and reported via flag_sink so the user can sanity-check it.

    IDEMPOTENCY: if raw_value is already a fully-canonical dotted id (every
    dot-separated segment already starts with a known pool prefix or the
    connector prefix), it is returned completely unchanged. Without this
    check, re-running the tool on its own previous output would re-split
    an already-correct id like "name_rukh.name_mirza" on the underscore
    inside "name_mirza", corrupting it into "name_rukh.name.name_mirza".
    """
    if _is_already_canonical(raw_value):
        return raw_value.lower(), pool

    if "." in raw_value:
        # partially-malformed dotted value: some segments have a pool
        # prefix, others don't (e.g. name_john.battista) - most likely
        # leftover damage from an earlier run of this same bug, or a
        # hand-edited id. Repair it by re-canonicalizing each segment on
        # its own, joining the results back with dots. A bare "name"
        # segment (no prefix, no content) is junk debris from a prior
        # corruption - not a real name piece - so it's dropped rather than
        # given a bogus name_name prefix.
        repaired_segments = []
        for seg in raw_value.split("."):
            if seg.lower() in ("name", "nickname", "lastname", "connector", ""):
                continue
            seg_canonical, _ = canonicalize(seg, pool, hyphenated_forms, flag_sink)
            repaired_segments.append(seg_canonical)
        if not repaired_segments:
            # everything was junk - fall back to treating the whole
            # original value as one bare word rather than return nothing
            repaired_segments = [canonicalize(raw_value.replace(".", "_"), pool)[0]]
        return ".".join(repaired_segments), pool

    # normalize spaces (from quoted multi-word values like "von Emmerberg")
    # to underscores before anything else, so the rest of the pipeline only
    # ever has to deal with one separator character
    normalized_value = raw_value.strip().replace(" ", "_")
    existing_pool, rest = strip_pool_prefix(normalized_value.lower())
    pool_to_use = existing_pool or pool
    prefix = POOL_PREFIXES[pool_to_use]

    if "_" not in rest:
        return f"{prefix}{rest}", pool_to_use

    if hyphenated_forms is not None and rest in hyphenated_forms:
        if flag_sink is not None:
            flag_sink.append(
                f"HYPHENATED NAME (not split) for '{raw_value}': loc shows this "
                f"as a hyphenated compound (e.g. \"{rest.replace('_', '-').title()}\"), "
                f"kept as single id '{prefix}{rest}' instead of splitting into segments. "
                f"Double check this is right."
            )
        return f"{prefix}{rest}", pool_to_use

    segments = split_segments(rest)
    out = []
    for is_conn, text in segments:
        if is_conn:
            out.append(f"{CONNECTOR_PREFIX}{text}")
        else:
            out.append(f"{prefix}{text}")
    return ".".join(out), pool_to_use


def _is_already_canonical(value):
    """
    True if value is already a fully-formed canonical id: one or more
    dot-separated segments, where EVERY segment starts with a recognized
    pool prefix (name_/nickname_/lastname_) or the connector prefix, and
    no segment is empty or malformed. A single bare prefixed word like
    "name_timur" also counts (a dot isn't required).
    """
    if not value or "." not in value:
        # single-segment case: only treat as canonical if it's prefixed
        # AND doesn't also contain a stray underscore after the prefix,
        # since "name_john_battista" (raw, unsplit) must still be split,
        # while "name_timur" (already atomic) must not be re-processed.
        for prefix in (*POOL_PREFIXES.values(), CONNECTOR_PREFIX):
            if value.startswith(prefix):
                rest = value[len(prefix):]
                return bool(rest) and "_" not in rest and "." not in rest
        return False

    segments = value.split(".")
    known_prefixes = (*POOL_PREFIXES.values(), CONNECTOR_PREFIX)
    multi_word_connector_bodies = {
        c.replace(" ", "_") for c in CONNECTORS if "_" in c or " " in c
    }
    for seg in segments:
        if not seg:
            return False
        matched_prefix = next((p for p in known_prefixes if seg.startswith(p)), None)
        if matched_prefix is None:
            return False
        rest = seg[len(matched_prefix):]
        if not rest or "." in rest:
            return False
        if "_" in rest:
            # an underscore after the prefix is only valid if the whole
            # remainder is a known multi-word connector body (de_la,
            # van_der, etc.) - anything else means this segment is really
            # multiple unprocessed words and needs (re)splitting
            if matched_prefix != CONNECTOR_PREFIX or rest not in multi_word_connector_bodies:
                return False
    return True


# ---------------------------------------------------------------------------
# Pass 1: scan characters.txt (and similar) to build the canonical map
# ---------------------------------------------------------------------------

def scan_loc_bare_keys(loc_files):
    """
    Pre-scan every loc file and collect every key's ASCII-folded, prefix-
    stripped bare form. Used to decide "ownership": if a characters.txt
    name/nickname/last_name reference has no bare-form match anywhere in
    this set, nothing in the mod's loc ever defined a display string for
    it - which almost certainly means it's a base-game/Paradox character
    reference the mod happens to reuse, not something the mod needs to
    rename or fix. We leave those completely untouched.
    """
    bare_keys = set()
    for path in loc_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = LOC_LINE_RE.match(line.rstrip("\n"))
            if not m:
                continue
            raw_key = m.group("key")
            folded = unidecode(raw_key.lower())
            _, rest = strip_pool_prefix(folded)
            bare = rest if rest else folded
            bare_keys.add(bare)
    return bare_keys


class NameRegistry:
    def __init__(self, hyphenated_forms=None, loc_bare_keys=None):
        # canonical_id -> set of raw source strings that mapped to it
        self.canonical_sources = defaultdict(set)
        # raw_lowered_bare -> set of canonical ids it produced (should be 1;
        # if >1, that's a genuine ambiguity to flag)
        self.bare_to_canonical = defaultdict(set)
        # every raw key seen (for cross-file rename lookups), keyed by
        # (pool, raw_lowercase) -> canonical
        self.rename_map = {}
        self.conflicts = []  # list of dicts for the report
        # (pool, folded_bare_form) -> set of canonical ids ; built lazily
        # via build_lookup_index() after all registration is done
        self._lookup_index = None
        # bare underscore-joined forms confirmed hyphenated by the loc scan
        self.hyphenated_forms = hyphenated_forms or set()
        # collects HYPHENATED NAME flag messages from canonicalize() calls
        self.hyphen_flags = []
        # every bare key that exists anywhere in the mod's loc files -
        # used to decide whether a characters.txt reference is "ours"
        # (mod-owned, safe to rename) or presumed vanilla/Paradox (no loc
        # entry exists for it at all, so leave it completely alone)
        self.loc_bare_keys = loc_bare_keys if loc_bare_keys is not None else set()
        # (pool, raw_lowercase) keys we decided NOT to touch because no
        # loc entry backs them - kept so rewrite passes can skip them too
        self.unowned_keys = set()
        self.vanilla_skip_count = 0
        # every raw reference value ever seen in characters.txt/countries.txt,
        # lowercased but NOT ascii-folded, regardless of ownership status.
        # Used as an extra safety check before commenting out a loc entry as
        # "orphaned" - catches cases like nickname = { name = Truchseb } vs
        # loc key Truchseß, where ascii-folding disagrees (unidecode folds
        # ß->ss, but a human manually typed 'b') so the normal owned/pool
        # matching misses the connection entirely on both sides.
        self.all_raw_reference_values = set()

    def is_owned(self, raw_value, pool):
        """True if this reference has at least one piece that is NOT a bare
        vanilla/Paradox loc key - i.e. the mod added or touched it in some
        way. False only when EVERY piece is something Paradox's own loc
        already defines, meaning the mod is just reusing a stock reference
        it never touched.

        Handles three shapes:
          - a single bare/prefixed word:      name_basil
          - an underscore-joined raw value:    name_basil_the_stupid
              (not yet split into segments - still raw from a field value)
          - an already-dotted canonical id:    name_basil.the_stupid
              (Paradox's name_basil joined with a mod-added segment)

        A dotted or underscore-joined id is "owned" (mod-touched) if ANY
        of its pieces is NOT a known Paradox/vanilla loc key - even if one
        piece (basil) is vanilla, a mod-added piece (the_stupid) means the
        mod introduced this specific combination and it should still be
        checked/normalized, not silently skipped as "vanilla".
        """
        normalized_value = raw_value.strip().replace(" ", "_")
        folded = unidecode(normalized_value.lower())

        # Strip the value's own leading pool prefix once (name_/nickname_/
        # lastname_ or an alias like nick_), then split the remainder on
        # both '.' (already-canonicalized dotted ids) and '_' (raw,
        # not-yet-split field values) - either way we end up with the
        # individual bare words to check against loc_bare_keys.
        existing_pool, rest_after_prefix = strip_pool_prefix(folded)
        working = rest_after_prefix if existing_pool else folded
        raw_pieces = re.split(r"[.\_]", working)

        cleaned_pieces = []
        for p in raw_pieces:
            # a dotted id repeats the pool prefix on every segment
            # (name_basil.name_the.name_stupid) - strip it per-piece too
            p_pool, p_rest = strip_pool_prefix(p)
            cleaned_pieces.append(p_rest if p_pool else p)
        cleaned_pieces = [p for p in cleaned_pieces if p]

        if len(cleaned_pieces) <= 1:
            # single bare word: owned only if the loc actually defines it.
            # No loc entry at all = presumed vanilla/Paradox, per the rule
            # "if we can't find a pair in the loc, it's a Paradox name".
            return bool(cleaned_pieces) and cleaned_pieces[0] in self.loc_bare_keys

        # multi-piece (dotted or underscore-joined) id: owned/mod-touched
        # if ANY piece is NOT a word Paradox's own loc already defines -
        # that piece is what the mod added, even if the other piece(s)
        # are vanilla words the mod is just reusing.
        return any(p not in self.loc_bare_keys for p in cleaned_pieces)

    def build_lookup_index(self):
        index = defaultdict(set)
        for (pool, raw), canonical in self.rename_map.items():
            folded = unidecode(raw.lower())
            pool_stripped, rest = strip_pool_prefix(folded)
            bare = rest if pool_stripped else folded
            index[(pool, bare)].add(canonical)
        self._lookup_index = index

    def register(self, raw_value, pool, source_file, source_line):
        if not raw_value:
            return

        # Track every raw reference value seen, regardless of ownership
        # outcome - also strip a pool prefix so "Truchseb" and "nickname_
        # truchseb" both register as the bare word "truchseb".
        _, bare_for_tracking = strip_pool_prefix(raw_value.lower())
        self.all_raw_reference_values.add(bare_for_tracking or raw_value.lower())
        # also track individual pieces of a compound, in case only part of
        # it resembles the loc key we're later checking
        for piece in re.split(r"[.\_]", bare_for_tracking or raw_value.lower()):
            if piece:
                self.all_raw_reference_values.add(piece)

        if not self.is_owned(raw_value, pool):
            # No matching loc entry anywhere - presumed vanilla/Paradox
            # content. Do not generate a canonical id, do not add it to
            # the rename map, and remember it so rewrite passes skip it.
            self.unowned_keys.add((pool, raw_value.lower()))
            self.vanilla_skip_count += 1
            return

        canonical, resolved_pool = canonicalize(
            raw_value, pool, self.hyphenated_forms, self.hyphen_flags
        )
        key = (resolved_pool, raw_value.lower())
        prior = self.rename_map.get(key)
        if prior is not None and prior != canonical:
            self.conflicts.append({
                "raw": raw_value, "pool": resolved_pool,
                "canonical_a": prior, "canonical_b": canonical,
                "file": source_file, "line": source_line,
            })
        else:
            self.rename_map[key] = canonical
        self.canonical_sources[canonical].add(raw_value)


def scan_character_file(path, registry, report_lines):
    text = path.read_text(encoding="utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in FIELD_BLOCK_RE.finditer(line):
            field = m.group("field")
            pool = FIELD_TO_POOL[field]
            value = m.group("value")
            registry.register(value, pool, str(path), lineno)

    for block_m in REGNAL_BLOCK_RE.finditer(text):
        block_text = block_m.group(1)
        # compute line number of block start for reporting
        start_line = text[:block_m.start()].count("\n") + 1
        for entry_m in REGNAL_ENTRY_RE.finditer(block_text):
            key = entry_m.group("key")
            registry.register(key, "name", str(path), start_line)


# ---------------------------------------------------------------------------
# Pass 2: rewrite loc yml files using the registry
# ---------------------------------------------------------------------------

LANGUAGE_DIALECT_SUFFIX_RE = re.compile(r"^(?P<base>.+)\.(?P<variant>[a-z_]+_(?:language|dialect))$")


def split_language_dialect_suffix(key):
    """
    If key matches BASE.something_language or BASE.something_dialect (e.g.
    name_sween.english_language, name_michael.turkish_dialect), return
    (base, variant_suffix). Otherwise return (key, None).

    This pattern looks like a dot-joined compound but ISN'T one - the part
    after the dot is a variant tag, not a second name segment, so it must
    never be run through the generic "a dot means multiple names" splitter.
    """
    m = LANGUAGE_DIALECT_SUFFIX_RE.match(key.lower())
    if m:
        return m.group("base"), m.group("variant")
    return key, None


def resolve_loc_key(raw_key, registry, report_lines):
    """
    Figure out canonical id for a loc key using the registry built from
    characters.txt. Matching is done by comparing the ASCII-folded,
    lowercased, prefix-stripped form of the key against every canonical id
    the registry knows about (also folded the same way) - so a loc key that
    still carries a diacritic (truchseß) will match a registry entry that
    was sanitized to ASCII in characters.txt (truchseb), and a bare key
    (stuart) will match a canonical id that only ever appeared prefixed
    elsewhere (name_stuart).

    Returns (canonical_or_None, truly_orphaned):
      - (canonical, False)  - resolved cleanly, safe to rename
      - (None, False)       - ambiguous, or a near-miss match exists (a
                              real reference is probably there under a
                              different spelling) - leave untouched, don't
                              comment out, just flag for manual review
      - (None, True)        - no match and no near-miss at all - nothing
                              in characters.txt/countries.txt references
                              this key under any spelling we can find -
                              safe to comment out as an orphaned loc entry
    """
    base_key, variant_suffix = split_language_dialect_suffix(raw_key)

    lowered = base_key.lower()
    folded = unidecode(lowered)
    existing_pool, rest = strip_pool_prefix(folded)
    pools_to_check = [existing_pool] if existing_pool else list(POOL_PREFIXES)
    bare_target = rest if existing_pool else folded

    if registry._lookup_index is None:
        registry.build_lookup_index()

    candidates = set()
    for pool in pools_to_check:
        candidates |= registry._lookup_index.get((pool, bare_target), set())

    if variant_suffix and len(candidates) == 1:
        return f"{candidates.pop()}.{variant_suffix}", False
    elif variant_suffix and len(candidates) > 1:
        report_lines.append(
            f"AMBIGUOUS loc key '{raw_key}': base '{base_key}' matches multiple canonical ids -> {candidates}. Left untouched."
        )
        return None, False
    elif variant_suffix:
        # base name not found anywhere - but this is a language/dialect
        # variant entry, not a standalone name, so don't comment it out
        # as orphaned on its own; flag it instead since the underlying
        # base name issue is what actually needs attention.
        report_lines.append(
            f"LANGUAGE/DIALECT VARIANT with unresolved base '{base_key}' for key "
            f"'{raw_key}': base name not found in characters.txt/countries.txt. "
            f"Left untouched rather than commented out - check the base name."
        )
        return None, False

    if len(candidates) == 1:
        return candidates.pop(), False
    elif len(candidates) > 1:
        report_lines.append(
            f"AMBIGUOUS loc key '{raw_key}': matches multiple canonical ids -> {candidates}. Left untouched."
        )
        return None, False

    # No exact fold-match. As a last resort, check for a "near miss": a
    # registry entry whose folded form starts the same way - this catches
    # manual mis-sanitizations like Truchseß -> "Truchseb" (a workmate typed
    # a 'b' where unidecode would produce 'ss'). We do NOT auto-merge these,
    # only flag them - guessing wrong here silently corrupts data worse than
    # leaving it alone.
    if len(bare_target) >= 5:
        near_misses = set()
        for pool in pools_to_check:
            for (idx_pool, idx_bare), canon_set in registry._lookup_index.items():
                if idx_pool != pool or idx_bare == bare_target:
                    continue
                if idx_bare[:5] == bare_target[:5]:
                    near_misses |= canon_set
        if near_misses:
            report_lines.append(
                f"POSSIBLE MATCH (not auto-merged) for loc key '{raw_key}': "
                f"similar to existing id(s) {near_misses} but not an exact fold-match. "
                f"Likely a manual sanitization mismatch (e.g. \u00df typed as 'b' instead of "
                f"unidecode's 'ss'). Check by hand."
            )
            return None, False  # a probable reference exists - don't comment out

    return None, True  # genuinely orphaned - nothing references this key


def looks_orphaned(raw_key, registry):
    """
    Extra safety gate before treating a loc key as truly unreferenced.
    resolve_loc_key's ascii-folded matching can miss a real connection when
    a manual sanitization disagrees with ascii-folding (e.g. a workmate
    typed Truchseb by hand where unidecode would produce Truchsess for
    Truchseß) - in that case BOTH sides look unmatched via folding, but a
    raw, non-folded fuzzy comparison against every reference value actually
    seen in characters.txt/countries.txt will usually still catch it.

    Returns True only if nothing in the mod's characters.txt/countries.txt
    resembles this key even loosely - safe to comment out. Returns False
    (don't touch it) if there's any plausible raw match, erring toward
    leaving a possibly-still-needed line alone rather than commenting out
    something a human would recognize as connected.
    """
    lowered = raw_key.lower()
    if lowered in registry.all_raw_reference_values:
        return False
    if len(lowered) < 5:
        # too short for a meaningful fuzzy check - if the exact folded
        # match already failed, treat as orphaned rather than risk wild
        # false-positive fuzzy matches on short strings
        return True
    for ref in registry.all_raw_reference_values:
        if len(ref) < 5:
            continue
        if ref[:5] == lowered[:5] or ref[-5:] == lowered[-5:]:
            return False  # plausible match exists under a different spelling
    return True


def split_compound_value_for_loc(value, canonical_segments):
    """
    Given a compound canonical id's dot-separated segments (already stripped
    of their pool/connector prefix, e.g. ['pir', 'muhammad']) and the
    original loc display value ("Pir Muhammad"), try to split the value's
    words to line up 1:1 with the segments. Returns a list of (segment_bare,
    display_word) pairs if the word count matches, else None (can't safely
    split - leave the original combined line alone and flag it).
    """
    value_words = value.split(" ")
    if len(value_words) != len(canonical_segments):
        return None
    return list(zip(canonical_segments, value_words))


def rewrite_loc_file(path, registry, report_lines, apply_changes):
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    out_lines = []
    seen_canonical = {}  # canonical_key -> (value, original_raw, lineno)
    changed = False

    # First pass: know which canonical keys already have their OWN loc
    # entry somewhere in this file, so a compound split doesn't create a
    # duplicate for a segment that's already independently defined.
    existing_canonical_keys = set()
    for line in lines:
        m = LOC_LINE_RE.match(line.rstrip("\n"))
        if not m:
            continue
        canonical, orphaned = resolve_loc_key(m.group("key"), registry, [])
        if canonical and "." not in canonical:
            existing_canonical_keys.add(canonical)

    for lineno, line in enumerate(lines, start=1):
        m = LOC_LINE_RE.match(line.rstrip("\n"))
        if not m:
            out_lines.append(line)
            continue

        raw_key = m.group("key")
        value = m.group("value")
        num = m.group("num")  # only reconstruct this if it was actually present

        canonical, orphaned = resolve_loc_key(raw_key, registry, report_lines)

        if orphaned and not looks_orphaned(raw_key, registry):
            # resolve_loc_key found no exact fold-match, but a raw
            # (non-folded) reference value looks similar enough that a real
            # connection probably exists under a different manual spelling
            # (e.g. Truchseß in loc vs Truchseb in characters.txt - ascii
            # folding disagrees with the manual sanitization on both sides).
            # Don't comment this out - flag it for a human to check instead.
            orphaned = False
            report_lines.append(
                f"POSSIBLE MATCH (not commented out) for loc key '{raw_key}' in "
                f"{path.name}:{lineno}: a similarly-spelled reference exists in "
                f"characters.txt/countries.txt, but folding doesn't match exactly. "
                f"Check by hand - likely a manual sanitization mismatch."
            )

        if orphaned:
            # Nothing in characters.txt/countries.txt references this key
            # under any spelling we could find - comment it out rather than
            # delete it, so it's reversible and visible in a diff.
            changed = True
            action = "would comment out" if not apply_changes else "commented out"
            report_lines.append(
                f"  {action} orphaned loc entry: '{raw_key}' in {path.name}:{lineno} "
                f"(no reference found in characters.txt/countries.txt)"
            )
            out_lines.append(f"# ORPHANED (no source id found): {line}" if not line.startswith("#") else line)
            continue

        new_key = canonical if canonical else raw_key.lower()
        # note: even if not found in characters.txt, we still lowercase
        # it for consistency, but we do NOT merge/rename its structure

        if new_key and "." in new_key and not LANGUAGE_DIALECT_SUFFIX_RE.match(new_key):
            # A dot ALWAYS means multiple independent name ids combined -
            # this must never be written back as a single combined loc
            # entry. Split into separate lines, one per segment, using
            # each segment's own existing loc entry if there is one, or a
            # freshly split word from this line's display value otherwise.
            changed = True
            indent = m.group("indent")
            num_part = f":{num}" if num else ":"
            segments = new_key.split(".")

            # strip prefixes to get bare words for the word-splitting match
            bare_segments = []
            for seg in segments:
                for p in (*POOL_PREFIXES.values(), CONNECTOR_PREFIX):
                    if seg.startswith(p):
                        bare_segments.append(seg[len(p):])
                        break
                else:
                    bare_segments.append(seg)

            split_pairs = split_compound_value_for_loc(value, bare_segments)

            if split_pairs is None:
                report_lines.append(
                    f"COMPOUND ID, COULD NOT AUTO-SPLIT loc value for '{raw_key}' "
                    f"in {path.name}:{lineno}: canonical id '{new_key}' has "
                    f"{len(segments)} segment(s) but display value \"{value}\" "
                    f"doesn't split into a matching number of words. Original "
                    f"line left untouched - split and fix the loc entries by hand."
                )
                out_lines.append(line)
                continue

            action = "would split" if not apply_changes else "split"
            report_lines.append(
                f"  {action} compound loc entry '{raw_key}':\"{value}\" in "
                f"{path.name}:{lineno} into separate entries per name segment "
                f"(id '{new_key}' is a combination, never a single loc entry)"
            )
            any_written = False
            for seg_full, (seg_bare, seg_word) in zip(segments, split_pairs):
                if seg_full in existing_canonical_keys:
                    report_lines.append(
                        f"    skipped '{seg_full}' - already has its own loc entry elsewhere"
                    )
                    continue
                out_lines.append(f'{indent}{seg_full}{num_part} "{seg_word}"\n')
                existing_canonical_keys.add(seg_full)
                any_written = True
            if not any_written:
                # every segment already existed elsewhere - the original
                # combined line is now fully redundant, drop it (it's
                # already reported above as "split", lines just weren't
                # re-added because nothing was missing)
                pass
            continue

        if new_key in seen_canonical:
            prev_value, prev_raw, prev_line = seen_canonical[new_key]
            if prev_value != value:
                report_lines.append(
                    f"CONFLICT in {path.name}: '{new_key}' has different "
                    f"values - line {prev_line} ('{prev_raw}': \"{prev_value}\") "
                    f"vs line {lineno} ('{raw_key}': \"{value}\"). "
                    f"BOTH FLAGGED, neither auto-merged."
                )
                out_lines.append(line)  # leave the conflicting line as-is
                continue
            else:
                # true duplicate, safe to drop this line
                changed = True
                action = "would merge" if not apply_changes else "merged"
                report_lines.append(
                    f"  {action} duplicate in {path.name}: '{raw_key}' (line {lineno}) "
                    f"-> '{new_key}' (already defined line {prev_line})"
                )
                continue
        else:
            seen_canonical[new_key] = (value, raw_key, lineno)

        if new_key != raw_key:
            changed = True
            num_part = f":{num}" if num else ":"
            new_line = f'{m.group("indent")}{new_key}{num_part} "{value}"{m.group("trail")}\n'
            out_lines.append(new_line)
            action = "would rename" if not apply_changes else "renamed"
            report_lines.append(f"  {action}: '{raw_key}' -> '{new_key}' in {path.name}:{lineno}")
        else:
            out_lines.append(line)

    if changed and apply_changes:
        backup = path.with_suffix(path.suffix + ".bak")
        if not backup.exists():
            backup.write_text(text, encoding="utf-8")
        path.write_text("".join(out_lines), encoding="utf-8")

    return changed


# ---------------------------------------------------------------------------
# Pass 3: rewrite characters.txt / countries.txt field values in place
# ---------------------------------------------------------------------------

def rewrite_field_file(path, registry, report_lines, apply_changes):
    text = path.read_text(encoding="utf-8", errors="replace")
    original = text
    action = "would change" if not apply_changes else "changed"

    def field_replacer(m):
        field = m.group("field")
        pool = FIELD_TO_POOL[field]
        value = m.group("value")
        if not registry.is_owned(value, pool):
            # No matching loc entry anywhere in the mod - presumed
            # vanilla/Paradox reference. Leave the original text exactly
            # as it was; do not rename.
            return m.group(0)
        canonical, _ = canonicalize(value, pool, registry.hyphenated_forms, registry.hyphen_flags)
        if canonical == value:
            return m.group(0)
        lineno = text[:m.start()].count("\n") + 1
        report_lines.append(
            f"  {action} in {path.name}:{lineno}: {field} = {{ name = {value} }} "
            f"-> {field} = {{ name = {canonical} }}"
        )
        # canonical ids never need quoting - drop quotes even if the
        # original value had them (e.g. "von Emmerberg" -> unquoted id)
        return f'{field} = {{ name = {canonical} }}'

    text = FIELD_BLOCK_RE.sub(field_replacer, text)

    def regnal_block_replacer(block_m):
        block_text = block_m.group(1)
        block_start_line = text[:block_m.start()].count("\n") + 1

        def entry_replacer(entry_m):
            key = entry_m.group("key")
            if not registry.is_owned(key, "name"):
                return entry_m.group(0)
            canonical, _ = canonicalize(key, "name", registry.hyphenated_forms, registry.hyphen_flags)
            num_part = entry_m.group(0).split("=")[-1].strip()
            if canonical == key:
                return entry_m.group(0)
            entry_line = block_start_line + block_text[:entry_m.start()].count("\n")
            report_lines.append(
                f"  {action} in {path.name}:{entry_line} (regnal_numbers): "
                f"{key} = {num_part} -> {canonical} = {num_part}"
            )
            return f"{canonical} = {num_part}"

        new_block_text = REGNAL_ENTRY_RE.sub(entry_replacer, block_text)
        return f"regnal_numbers = {{{new_block_text}}}"

    text = REGNAL_BLOCK_RE.sub(regnal_block_replacer, text)

    changed = text != original
    if changed and apply_changes:
        backup = path.with_suffix(path.suffix + ".bak")
        if not backup.exists():
            backup.write_text(original, encoding="utf-8")
        path.write_text(text, encoding="utf-8")

    return changed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def find_files(root):
    root = Path(root)
    # match files that CONTAIN "characters"/"countries" anywhere in the name
    # (not just files starting with it) - covers real-world prefixed names
    # like 05_characters.txt, 10_countries.txt, as well as plain
    # characters.txt / countries.txt
    char_files = [p for p in root.rglob("*.txt") if "characters" in p.name.lower()]
    country_files = [p for p in root.rglob("*.txt") if "countries" in p.name.lower()]
    # only the one loc file that actually holds character names - never
    # touch other _l_english.yml files (culture/religion adjectives, event
    # text, etc.) even though they share the same file extension/suffix
    loc_files = [
        p for p in root.rglob("*_l_english.yml")
        if "character_names" in p.name.lower()
    ]
    return char_files, country_files, loc_files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="Root folder of the mod to scan")
    ap.add_argument("--apply", action="store_true", help="Actually write changes (default: dry run)")
    args = ap.parse_args()

    report_lines = []
    char_files, country_files, loc_files = find_files(args.root)

    if not char_files:
        print("WARNING: no characters*.txt files found under root.", file=sys.stderr)

    # Pre-scan loc files for hyphenated display values (e.g. "Anjou-Naples")
    # BEFORE we canonicalize anything, so canonicalize() knows not to split
    # a compound word like anjou_naples into separate name segments.
    hyphenated_forms = scan_loc_for_hyphenated_values(loc_files)

    # Pre-scan loc files for every bare key that exists at all. characters.txt
    # contains ALL character ids in the game, including Paradox's own vanilla
    # characters - if a name/nickname/last_name reference has no corresponding
    # loc entry anywhere in the mod, it's presumed vanilla and must never be
    # renamed (nothing else in the game would follow that rename).
    loc_bare_keys = scan_loc_bare_keys(loc_files)

    registry = NameRegistry(hyphenated_forms, loc_bare_keys)
    for f in char_files:
        scan_character_file(f, registry, report_lines)
    # countries.txt can also define regnal_numbers blocks (per-country
    # ruler naming) - scan those into the same registry too
    for f in country_files:
        scan_character_file(f, registry, report_lines)

    if registry.vanilla_skip_count:
        report_lines.append(
            f"\n=== VANILLA/UNOWNED REFERENCES SKIPPED: {registry.vanilla_skip_count} ===\n"
            f"  (name/nickname/last_name references in characters.txt or countries.txt\n"
            f"  with no matching loc entry anywhere in the mod - presumed Paradox's own\n"
            f"  base-game characters, left completely untouched)"
        )

    if registry.hyphen_flags:
        report_lines.append("\n=== HYPHENATED NAMES (kept as single id, not split - verify) ===")
        for flag in sorted(set(registry.hyphen_flags)):
            report_lines.append(f"  {flag}")

    if registry.conflicts:
        report_lines.append("\n=== ID RESOLUTION CONFLICTS (same raw key -> different canonical ids) ===")
        for c in registry.conflicts:
            report_lines.append(
                f"  '{c['raw']}' ({c['pool']}) in {c['file']}:{c['line']} "
                f"-> '{c['canonical_a']}' vs '{c['canonical_b']}'"
            )


    report_lines.append(f"\n=== REGISTRY: {len(registry.rename_map)} unique name/nickname/lastname refs found ===")

    report_lines.append("\n=== LOC FILE CHANGES ===")
    any_loc_changed = False
    write_verb = "Modified" if args.apply else "Would modify"
    for f in loc_files:
        if rewrite_loc_file(f, registry, report_lines, args.apply):
            any_loc_changed = True
            report_lines.append(f"  {write_verb}: {f}")
    if not any_loc_changed:
        report_lines.append("  (no changes)")

    report_lines.append("\n=== characters.txt / countries.txt CHANGES ===")
    any_field_changed = False
    for f in char_files + country_files:
        if rewrite_field_file(f, registry, report_lines, args.apply):
            any_field_changed = True
            report_lines.append(f"  {write_verb}: {f}")
    if not any_field_changed:
        report_lines.append("  (no changes)")

    mode = "APPLIED" if args.apply else "DRY RUN (no files modified, use --apply to write changes)"
    header = f"EU5 Name/Loc Normalizer report - mode: {mode}\n" + "=" * 60 + "\n"
    full_report = header + "\n".join(report_lines)

    report_path = Path(args.root) / "normalize_names_report.txt"
    report_path.write_text(full_report, encoding="utf-8")

    map_path = Path(args.root) / "normalize_names_rename_map.json"
    serializable_map = {f"{pool}:{raw}": canon for (pool, raw), canon in registry.rename_map.items()}
    map_path.write_text(json.dumps(serializable_map, indent=2, ensure_ascii=False), encoding="utf-8")

    print(full_report)
    print(f"\nReport written to: {report_path}")
    print(f"Rename map written to: {map_path}")


if __name__ == "__main__":
    main()
