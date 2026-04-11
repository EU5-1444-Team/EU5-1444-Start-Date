#!/usr/bin/env python3
"""GUI editor for 1444 pop data with geography filters from basegame definitions."""

from __future__ import annotations

import json
import re
import tkinter as tk
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


REPO_ROOT        = Path(__file__).resolve().parents[1]
SETTINGS_PATH    = REPO_ROOT / "tools/settings.json"
BASE_POPS_REL    = Path("game/main_menu/setup/start/06_pops.txt")
MAX_VISIBLE_ROWS = 3000
STD_ATTR_ORDER   = ["type", "size", "culture", "religion"]
FILTER_FIELDS    = ["continent", "superregion", "region", "area", "province", "location"]
TABLE_COLUMNS    = (
    ["location"] + STD_ATTR_ORDER
    + ["continent", "superregion", "region", "area", "province", "extra", "differs", "basegame"]
)
EDITABLE_COLUMNS = {"location", "type", "size", "culture", "religion", "extra"}

# For each filter field, the ordered list of ancestor fields (closest first).
ANCESTOR_MAP: dict[str, list[str]] = {
    "continent":   [],
    "superregion": ["continent"],
    "region":      ["superregion", "continent"],
    "area":        ["region", "superregion", "continent"],
    "province":    ["area", "region", "superregion", "continent"],
    "location":    ["province", "area", "region", "superregion", "continent"],
}

COLUMN_WIDTHS = {
    "continent": 120, "superregion": 150, "region": 150, "area": 150,
    "province": 170,  "location": 170,    "type": 100,   "size": 90,
    "culture": 120,   "religion": 120,    "extra": 300,  "differs": 70,
    "basegame": 360,
}


# ── data classes ──────────────────────────────────────────────────────────────

@dataclass
class GeoInfo:
    continent:   str = ""
    superregion: str = ""
    region:      str = ""
    area:        str = ""
    province:    str = ""


@dataclass
class PopRow:
    row_id:   int
    location: str
    attrs:    OrderedDict[str, str]

    def get_value(self, column: str, geo: GeoInfo) -> str:
        if column == "location":
            return self.location
        if column in FILTER_FIELDS:
            return getattr(geo, column)
        if column == "extra":
            return "; ".join(f"{k}={v}" for k, v in self.attrs.items() if k not in STD_ATTR_ORDER)
        return self.attrs.get(column, "")


# ── helpers ───────────────────────────────────────────────────────────────────

def _safe_float(s: str) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def normalize_size(value: str) -> str:
    """Return value formatted to three decimal places, or unchanged if non-numeric."""
    try:
        return f"{float(value):.3f}"
    except Exception:
        return value


def pop_attrs_equal(left: OrderedDict, right: OrderedDict) -> bool:
    return list(left.items()) == list(right.items())


def format_attrs_summary(attrs: OrderedDict) -> str:
    return ", ".join(f"{k}={v}" for k, v in attrs.items())


# ── settings ──────────────────────────────────────────────────────────────────

def load_settings() -> dict:
    if SETTINGS_PATH.is_file():
        try:
            s = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if "base_game" in s and "mod_folder" in s:
                return s
        except json.JSONDecodeError:
            pass
    return {}


def prompt_for_paths() -> dict:
    print("Settings file is missing or malformed.")
    settings = {
        "base_game":  input("Enter the base game path: ").strip(),
        "mod_folder": input("Enter the mod path: ").strip(),
    }
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return settings


# ── parsers ───────────────────────────────────────────────────────────────────

def tokenize_definitions(text: str) -> list[str]:
    cleaned = re.sub(r"#.*$", "", text, flags=re.MULTILINE)
    return re.findall(r"[{}=]|[^\s{}=]+", cleaned)


def _parse_geo_block(
    tokens: list[str],
    idx: int,
    path: tuple[str, ...],
    location_geo: dict[str, GeoInfo],
) -> int:
    """Consume tokens for one geo block, recording leaf locations. Returns new idx."""
    while idx < len(tokens):
        token = tokens[idx]
        if token == "}":
            return idx + 1
        key = token
        idx += 1
        if idx < len(tokens) and tokens[idx] == "=":
            idx += 1
            if idx >= len(tokens):
                raise ValueError(f"Unexpected end of tokens after '=' for {key}")
            if tokens[idx] != "{":
                raise ValueError(f"Expected '{{' after {key}")
            idx = _parse_geo_block(tokens, idx + 1, path + (key,), location_geo)
        else:
            padded = list(path) + [""] * 5
            location_geo[key] = GeoInfo(*padded[:5])
    raise ValueError("Unclosed geography block")


def parse_definitions(path: Path) -> dict[str, GeoInfo]:
    """Parse definitions.txt and return a mapping of location name -> GeoInfo."""
    tokens = tokenize_definitions(path.read_text(encoding="utf-8-sig"))
    location_geo: dict[str, GeoInfo] = {}
    idx = 0
    while idx < len(tokens):
        name = tokens[idx]
        idx += 1
        if idx >= len(tokens) or tokens[idx] != "=":
            raise ValueError(f"Expected '=' after {name}")
        idx += 1
        if idx >= len(tokens) or tokens[idx] != "{":
            raise ValueError(f"Expected '{{' after {name} =")
        idx = _parse_geo_block(tokens, idx + 1, (name,), location_geo)
    return location_geo


def find_matching_brace(text: str, start_idx: int) -> int:
    depth = 0
    for i in range(start_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("Unmatched brace")


def parse_pop_attributes(block: str) -> OrderedDict[str, str]:
    attrs = OrderedDict()
    for key, value in re.findall(r"([A-Za-z0-9_]+)\s*=\s*([^\s}]+)", block):
        attrs[key] = value
    return attrs


def parse_pops(path: Path) -> tuple[list[str], dict[str, list[OrderedDict[str, str]]]]:
    text = path.read_text(encoding="utf-8-sig")
    root_match = re.search(r"locations\s*=\s*\{", text)
    if not root_match:
        raise ValueError("Could not find locations={...} in pop file")
    root_brace = text.find("{", root_match.start())
    root_end   = find_matching_brace(text, root_brace)
    body = text[root_brace + 1 : root_end]

    location_order: list[str] = []
    location_pops:  dict[str, list[OrderedDict]] = OrderedDict()
    idx = 0
    while idx < len(body):
        while idx < len(body) and body[idx].isspace():
            idx += 1
        if idx >= len(body):
            break
        name_match = re.match(r"([A-Za-z0-9_]+)\s*=", body[idx:])
        if not name_match:
            idx += 1
            continue
        location  = name_match.group(1)
        brace_idx = body.find("{", idx + name_match.end())
        if brace_idx == -1:
            raise ValueError(f"Missing block for location {location}")
        block_end  = find_matching_brace(body, brace_idx)
        block_text = body[brace_idx + 1 : block_end]
        pops = [
            attrs for pop_body in re.findall(r"define_pop\s*=\s*\{([^{}]*)\}", block_text)
            if (attrs := parse_pop_attributes(pop_body))
        ]
        location_order.append(location)
        location_pops[location] = pops
        idx = block_end + 1
    return location_order, location_pops


def format_pop_line(attrs: OrderedDict[str, str]) -> str:
    ordered = OrderedDict()
    for key in STD_ATTR_ORDER:
        if key in attrs:
            ordered[key] = normalize_size(attrs[key]) if key == "size" else attrs[key]
    for key, value in attrs.items():
        if key not in ordered:
            ordered[key] = value
    return "\tdefine_pop = { " + "\t".join(f"{k} = {v}" for k, v in ordered.items()) + " }"


def _normalize_pops(raw: dict[str, list[OrderedDict]]) -> dict[str, list[OrderedDict]]:
    """Return a copy of a location->pops mapping with all sizes normalized."""
    return {
        loc: [
            OrderedDict((k, normalize_size(v) if k == "size" else v) for k, v in attrs.items())
            for attrs in pop_list
        ]
        for loc, pop_list in raw.items()
    }


# ── GUI application ───────────────────────────────────────────────────────────

class PopEditorApp:
    def __init__(self, root: tk.Tk, base_game_path: Path, mod_folder_path: Path):
        self.root = root
        self.root.title("EU5 1444 Pop Geography Editor")

        self.location_geo:       dict[str, GeoInfo] = {}
        self.location_order:     list[str] = []
        self.rows:               list[PopRow] = []
        self.base_location_pops: dict[str, list[OrderedDict]] = {}
        self.row_diff_cache:     dict[int, tuple[bool, str]] = {}
        self.next_row_id = 1
        self.item_to_row:  dict[str, PopRow] = {}
        self.sort_column   = "location"
        self.sort_reverse  = False

        self.filter_vars    = {f: tk.StringVar() for f in FILTER_FIELDS}
        self.include_values = {f: set()          for f in FILTER_FIELDS}
        self.exclude_values = {f: set()          for f in FILTER_FIELDS}
        self.batch_add_var  = tk.StringVar(value="0")
        self.batch_mult_var = tk.StringVar(value="1")
        self.status_var     = tk.StringVar(value="Load a pop file to begin.")
        self.path_vars      = {
            "base_game":  tk.StringVar(value=str(base_game_path)),
            "mod_folder": tk.StringVar(value=str(mod_folder_path)),
        }

        self._build_ui()
        self.load_data()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.root.geometry("1500x850")
        self.root.rowconfigure(3, weight=1)
        self.root.columnconfigure(0, weight=1)

        # path bar
        file_frame = ttk.Frame(self.root, padding=8)
        file_frame.grid(row=0, column=0, sticky="ew")
        file_frame.columnconfigure(1, weight=1)
        file_frame.columnconfigure(4, weight=1)
        for col, (label, key) in enumerate([("Base Game", "base_game"), ("Mod Folder", "mod_folder")]):
            ttk.Label(file_frame, text=label).grid(row=0, column=col * 3, sticky="w")
            ttk.Entry(file_frame, textvariable=self.path_vars[key]).grid(
                row=0, column=col * 3 + 1, sticky="ew", padx=(6, 6))
            ttk.Button(file_frame, text="Browse",
                       command=lambda k=key: self._browse_dir(k)).grid(
                row=0, column=col * 3 + 2, padx=(0, 10))
        ttk.Button(file_frame, text="Reload", command=self.load_data).grid(row=0, column=6)
        ttk.Button(file_frame, text="Save",   command=self.save_data).grid(row=0, column=7, padx=(6, 0))

        # geography filters
        filter_frame = ttk.LabelFrame(self.root, text="Geography Selection", padding=8)
        filter_frame.grid(row=1, column=0, sticky="ew", padx=8)
        filter_frame.columnconfigure(4, weight=1)
        for idx, field in enumerate(FILTER_FIELDS):
            ttk.Label(filter_frame, text=field.title()).grid(row=idx, column=0, sticky="w")
            combo = ttk.Combobox(filter_frame, textvariable=self.filter_vars[field],
                                 state="readonly", width=24)
            combo.grid(row=idx, column=1, sticky="ew", padx=(4, 8))
            combo.bind("<<ComboboxSelected>>", self.on_filter_change)
            setattr(self, f"{field}_combo", combo)
            ttk.Button(filter_frame, text="Include",
                       command=lambda f=field: self.add_selection(f, "include")).grid(
                row=idx, column=2, padx=(0, 4))
            ttk.Button(filter_frame, text="Exclude",
                       command=lambda f=field: self.add_selection(f, "exclude")).grid(
                row=idx, column=3, padx=(0, 8))
            summary = ttk.Label(filter_frame, text="", justify="left")
            summary.grid(row=idx, column=4, sticky="ew")
            setattr(self, f"{field}_summary", summary)
            ttk.Button(filter_frame, text="Clear",
                       command=lambda f=field: self.clear_selection(f)).grid(row=idx, column=5)
        ttk.Button(filter_frame, text="Clear All",
                   command=self.clear_filters).grid(row=len(FILTER_FIELDS), column=0, pady=(8, 0), sticky="w")
        ttk.Button(filter_frame, text="Copy Locations",
                   command=self.copy_matching_locations).grid(row=len(FILTER_FIELDS), column=1, pady=(8, 0), sticky="w")

        # batch edit
        batch_frame = ttk.LabelFrame(self.root, text="Batch Size Edit", padding=8)
        batch_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 4))
        batch_frame.columnconfigure(5, weight=1)
        for col, (label, var) in enumerate([("Multiplier", self.batch_mult_var), ("Addition", self.batch_add_var)]):
            ttk.Label(batch_frame, text=label).grid(row=0, column=col * 2, sticky="w")
            ttk.Entry(batch_frame, textvariable=var, width=12).grid(
                row=0, column=col * 2 + 1, sticky="w", padx=(6, 12))
        ttk.Button(batch_frame, text="Apply to Filtered Rows",
                   command=self.apply_batch_size_edit).grid(row=0, column=4, sticky="w")
        ttk.Label(batch_frame,
                  text="new_size = old_size * multiplier + addition  (clamped to 0 if negative)").grid(
            row=0, column=5, sticky="w", padx=(12, 0))

        # table
        table_frame = ttk.Frame(self.root, padding=(8, 4, 8, 8))
        table_frame.grid(row=3, column=0, sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(table_frame, columns=TABLE_COLUMNS, show="headings", selectmode="browse")
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical",   command=self.tree.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        for column in TABLE_COLUMNS:
            self.tree.heading(column, text=column.title(), command=lambda c=column: self.sort_by(c))
            self.tree.column(column, width=COLUMN_WIDTHS.get(column, 120), anchor="w", stretch=True)
        self.tree.bind("<Double-1>", self.begin_edit_cell)
        self.tree.tag_configure("differs", background="#fff1d6")

        ttk.Label(self.root, textvariable=self.status_var, padding=(8, 2)).grid(row=4, column=0, sticky="ew")

    # ── data loading / saving ─────────────────────────────────────────────────

    def _browse_dir(self, key: str) -> None:
        selected = filedialog.askdirectory(title=f"Select {key.replace('_', ' ').title()} Directory",
                                           initialdir=self.path_vars[key].get())
        if selected:
            self.path_vars[key].set(selected)

    def load_data(self) -> None:
        try:
            base_game  = Path(self.path_vars["base_game"].get()).expanduser()
            mod_folder = Path(self.path_vars["mod_folder"].get()).expanduser()
            pops_path      = mod_folder / "main_menu/setup/start/06_pops.txt"
            defs_path      = base_game  / "game/in_game/map_data/definitions.txt"
            base_pops_path = base_game  / BASE_POPS_REL

            self.location_geo = parse_definitions(defs_path)
            self.location_order, location_pops = parse_pops(pops_path)
            _, base_raw = parse_pops(base_pops_path)
            self.base_location_pops = _normalize_pops(base_raw)

            self.rows = []
            self.next_row_id = 1
            for location in self.location_order:
                for attrs in location_pops.get(location, []):
                    if "size" in attrs:
                        attrs["size"] = normalize_size(attrs["size"])
                    self.rows.append(PopRow(self.next_row_id, location, attrs))
                    self.next_row_id += 1

            self.rebuild_row_metadata()
            self.update_filter_options()
            self.refresh_table()
            self.status_var.set(
                f"Loaded {len(self.rows)} pop rows from {pops_path.name}; "
                f"basegame compare from {base_pops_path.name}."
            )
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))
            self.status_var.set("Load failed.")

    def save_data(self) -> None:
        try:
            mod_folder  = Path(self.path_vars["mod_folder"].get()).expanduser()
            output_path = mod_folder / "main_menu/setup/start/06_pops.txt"
            backup_path = output_path.with_suffix(output_path.suffix + ".bak")
            if output_path.exists():
                backup_path.write_text(output_path.read_text(encoding="utf-8-sig"), encoding="utf-8")

            grouped: dict[str, list[PopRow]] = defaultdict(list)
            for row in self.rows:
                grouped[row.location].append(row)

            ordered_locations = list(dict.fromkeys(
                self.location_order + [r.location for r in self.rows]
            ))
            lines = ["locations={", ""]
            for location in ordered_locations:
                rows_here = grouped.get(location, [])
                if not rows_here:
                    continue
                lines.append(f"{location} = {{")
                for row in rows_here:
                    lines.append(format_pop_line(row.attrs))
                lines.append("}")
            lines.append("}")
            output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.rebuild_row_metadata()
            self.status_var.set(
                f"Saved {len(self.rows)} pop rows to {output_path} (backup: {backup_path.name})."
            )
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            self.status_var.set("Save failed.")

    # ── diff metadata ─────────────────────────────────────────────────────────

    def rebuild_row_metadata(self) -> None:
        self.row_diff_cache = {}
        positions: dict[str, int] = defaultdict(int)
        for row in self.rows:
            idx = positions[row.location]
            base_list = self.base_location_pops.get(row.location, [])
            if idx < len(base_list):
                differs = not pop_attrs_equal(row.attrs, base_list[idx])
                summary = format_attrs_summary(base_list[idx]) if differs else ""
            else:
                differs, summary = True, "<missing in basegame>"
            self.row_diff_cache[row.row_id] = (differs, summary)
            positions[row.location] += 1

    # ── filtering ─────────────────────────────────────────────────────────────

    def _geo_value(self, row: PopRow, field: str) -> str:
        if field == "location":
            return row.location
        return getattr(self.location_geo.get(row.location, GeoInfo()), field)

    def update_filter_options(self) -> None:
        for field in FILTER_FIELDS:
            seen: set[str] = set()
            for row in self.rows:
                value = self._geo_value(row, field)
                if not value:
                    continue
                if any(
                    (fv := self.filter_vars[anc].get()) and self._geo_value(row, anc) != fv
                    or (inc := self.include_values[anc]) and self._geo_value(row, anc) not in inc
                    or (exc := self.exclude_values[anc]) and self._geo_value(row, anc) in exc
                    for anc in ANCESTOR_MAP[field]
                ):
                    continue
                seen.add(value)
            combo   = getattr(self, f"{field}_combo")
            current = self.filter_vars[field].get()
            combo["values"] = [""] + sorted(seen)
            if current not in combo["values"]:
                self.filter_vars[field].set("")
            self._update_summary(field)

    def _update_summary(self, field: str) -> None:
        inc = ", ".join(sorted(self.include_values[field])) or "-"
        exc = ", ".join(sorted(self.exclude_values[field])) or "-"
        getattr(self, f"{field}_summary").configure(text=f"Include: {inc} | Exclude: {exc}")

    def on_filter_change(self, _event=None) -> None:
        self.update_filter_options()
        self.refresh_table()

    def add_selection(self, field: str, mode: str) -> None:
        value = self.filter_vars[field].get()
        if not value:
            return
        (self.include_values if mode == "include" else self.exclude_values)[field].add(value)
        (self.exclude_values if mode == "include" else self.include_values)[field].discard(value)
        self._update_summary(field)
        self.update_filter_options()
        self.refresh_table()

    def clear_selection(self, field: str) -> None:
        self.include_values[field].clear()
        self.exclude_values[field].clear()
        self._update_summary(field)
        self.update_filter_options()
        self.refresh_table()

    def clear_filters(self) -> None:
        for field in FILTER_FIELDS:
            self.filter_vars[field].set("")
            self.include_values[field].clear()
            self.exclude_values[field].clear()
            self._update_summary(field)
        self.update_filter_options()
        self.refresh_table()

    def _row_matches(self, row: PopRow, selections: dict[str, set[str]]) -> bool:
        return any(self._geo_value(row, f) in vals for f, vals in selections.items() if vals)

    def iter_filtered_rows(self) -> list[PopRow]:
        active = {f: self.filter_vars[f].get() for f in FILTER_FIELDS if self.filter_vars[f].get()}
        has_includes = any(self.include_values.values())
        result = []
        for row in self.rows:
            if active and any(self._geo_value(row, f) != v for f, v in active.items()):
                continue
            if has_includes and not self._row_matches(row, self.include_values):
                continue
            if self._row_matches(row, self.exclude_values):
                continue
            result.append(row)
        return result

    def copy_matching_locations(self) -> None:
        locations = list(dict.fromkeys(row.location for row in self.iter_filtered_rows()))
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(locations))
        self.status_var.set(f"Copied {len(locations)} matching locations to the clipboard.")

    # ── table display ─────────────────────────────────────────────────────────

    def _table_value(self, row: PopRow, column: str) -> str:
        if column == "differs":
            return "yes" if self.row_diff_cache.get(row.row_id, (False,))[0] else ""
        if column == "basegame":
            return self.row_diff_cache.get(row.row_id, ("", ""))[1]
        return row.get_value(column, self.location_geo.get(row.location, GeoInfo()))

    def refresh_table(self) -> None:
        filtered = self.iter_filtered_rows()
        filtered.sort(key=self._sort_key, reverse=self.sort_reverse)
        visible = filtered[:MAX_VISIBLE_ROWS]

        for child in self.tree.get_children():
            self.tree.delete(child)
        self.item_to_row.clear()

        for row in visible:
            values  = [self._table_value(row, col) for col in TABLE_COLUMNS]
            differs = self.row_diff_cache.get(row.row_id, (False,))[0]
            item    = self.tree.insert("", "end", values=values, tags=("differs",) if differs else ())
            self.item_to_row[item] = row

        suffix    = (f" Showing first {MAX_VISIBLE_ROWS}; narrow filters to edit the rest."
                     if len(filtered) > MAX_VISIBLE_ROWS else "")
        total_pop = sum(_safe_float(r.attrs.get("size", "0")) for r in filtered)
        self.status_var.set(
            f"{len(filtered)} matching pop rows across "
            f"{len({r.location for r in filtered})} locations, "
            f"total pop: {total_pop:.3f}.{suffix}"
        )

    def _sort_key(self, row: PopRow):
        value = self._table_value(row, self.sort_column)
        if self.sort_column == "size":
            return _safe_float(value)
        if self.sort_column == "differs":
            return 1 if value else 0
        return value.lower()

    def sort_by(self, column: str) -> None:
        self.sort_reverse = (column == self.sort_column) and not self.sort_reverse
        self.sort_column  = column
        self.refresh_table()

    # ── cell editing ──────────────────────────────────────────────────────────

    def begin_edit_cell(self, event) -> None:
        item      = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        if not item or not column_id:
            return
        column = TABLE_COLUMNS[int(column_id.replace("#", "")) - 1]
        if column not in EDITABLE_COLUMNS:
            return
        bbox = self.tree.bbox(item, column_id)
        if not bbox:
            return
        row = self.item_to_row.get(item)
        if row is None:
            return

        old_value = self._table_value(row, column)
        editor = ttk.Entry(self.tree)
        editor.insert(0, old_value)
        editor.select_range(0, tk.END)
        editor.focus()
        editor.place(x=bbox[0], y=bbox[1], width=bbox[2], height=bbox[3])

        finished = [False]

        def finish(_event=None, save=True):
            if finished[0]:
                return
            finished[0] = True
            new_value = editor.get().strip()
            editor.destroy()
            if save and new_value != old_value:
                try:
                    self.apply_edit(row, column, new_value)
                    self.rebuild_row_metadata()
                    self.refresh_table()
                except Exception as exc:
                    messagebox.showerror("Invalid edit", str(exc))

        editor.bind("<Return>",   finish)
        editor.bind("<Escape>",   lambda _e: finish(save=False))
        editor.bind("<FocusOut>", finish)

    def apply_edit(self, row: PopRow, column: str, value: str) -> None:
        if column == "location":
            if value not in self.location_geo:
                raise ValueError(f"Unknown location '{value}' in definitions file.")
            old = row.location
            row.location = value
            if value not in self.location_order:
                try:
                    self.location_order.insert(self.location_order.index(old) + 1, value)
                except ValueError:
                    self.location_order.append(value)
            return

        if column == "extra":
            extras = OrderedDict()
            for part in (p.strip() for p in value.split(";") if p.strip()):
                if "=" not in part:
                    raise ValueError("Extra attributes must use 'key=value; key=value'.")
                key, attr_value = (s.strip() for s in part.split("=", 1))
                if not key:
                    raise ValueError("Extra attribute key cannot be empty.")
                if key in STD_ATTR_ORDER:
                    raise ValueError(f"'{key}' is a standard attribute; edit it in its own column.")
                extras[key] = attr_value
            row.attrs = OrderedDict(
                [(k, row.attrs[k]) for k in STD_ATTR_ORDER if k in row.attrs] + list(extras.items())
            )
            return

        if column == "size":
            try:
                float(value)
            except ValueError as exc:
                raise ValueError("Size must be numeric.") from exc
            value = normalize_size(value)

        if column not in row.attrs and column not in STD_ATTR_ORDER:
            raise ValueError(f"Unsupported column '{column}'.")

        if value:
            row.attrs[column] = value
            # re-impose STD_ATTR_ORDER on the whole dict
            row.attrs = OrderedDict(
                [(k, row.attrs[k]) for k in STD_ATTR_ORDER if k in row.attrs]
                + [(k, v) for k, v in row.attrs.items() if k not in STD_ATTR_ORDER]
            )
        elif column in row.attrs:
            del row.attrs[column]

    # ── batch edit ────────────────────────────────────────────────────────────

    def apply_batch_size_edit(self) -> None:
        try:
            multiplier = float(self.batch_mult_var.get().strip())
            addition   = float(self.batch_add_var.get().strip())
        except ValueError:
            messagebox.showerror("Invalid batch edit", "Multiplier and addition must be numeric.")
            return

        filtered = self.iter_filtered_rows()
        if not filtered:
            self.status_var.set("No filtered rows to edit.")
            return

        changed = clamped = 0
        for row in filtered:
            try:
                new_size = float(row.attrs.get("size", "")) * multiplier + addition
            except ValueError:
                continue
            if new_size < 0:
                new_size = 0.0
                clamped += 1
            row.attrs["size"] = f"{new_size:.3f}"
            changed += 1

        self.rebuild_row_metadata()
        self.refresh_table()
        clamp_note = f" {clamped} row(s) clamped to 0." if clamped else ""
        self.status_var.set(
            f"Updated size on {changed} row(s) (x{multiplier} +{addition}).{clamp_note}"
        )


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    settings = load_settings() or prompt_for_paths()
    root = tk.Tk()
    PopEditorApp(
        root,
        base_game_path=Path(settings.get("base_game",  "")).expanduser(),
        mod_folder_path=Path(settings.get("mod_folder", REPO_ROOT)).expanduser(),
    )
    root.mainloop()


if __name__ == "__main__":
    main()