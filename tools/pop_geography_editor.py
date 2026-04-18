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
MAX_VISIBLE_ROWS = 3000
STD_ATTR_ORDER   = ["type", "size", "culture", "religion"]
FILTER_FIELDS    = ["continent", "superregion", "region", "area", "province", "location"]
POP_ATTR_FILTER_FIELDS = ["culture", "religion", "type"]
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


# ── searchable combobox ───────────────────────────────────────────────────────

class SearchableCombobox(tk.Frame):
    """
    A combobox replacement whose dropdown filters as you type.

    Public interface mirrors ttk.Combobox:
      .get()            → current text
      .set(value)       → set text programmatically (clears if value is "")
      ["values"] = list → update the full option list
      bind(event, cb)   → <<ComboboxSelected>> fires when user picks a value;
                          other events forwarded to the inner Entry
    """

    def __init__(self, master, textvariable: tk.StringVar | None = None,
                 width: int = 24, **kwargs):
        super().__init__(master, **kwargs)
        self._var       = textvariable or tk.StringVar()
        self._all_values: list[str] = []
        self._popup: tk.Toplevel | None = None
        self._selected_callbacks: list = []
        self._suppress_trace = False

        self._entry = tk.Entry(self, textvariable=self._var, width=width)
        self._entry.pack(fill="x", expand=True)

        self._var.trace_add("write", self._on_text_change)
        self._entry.bind("<Down>",     self._open_or_focus)
        self._entry.bind("<Return>",   self._on_entry_return)
        self._entry.bind("<Escape>",   lambda _e: self._close_popup())
        self._entry.bind("<FocusOut>", self._on_focus_out)

    # ── public API ────────────────────────────────────────────────────────────

    def get(self) -> str:
        return self._var.get()

    def set(self, value: str) -> None:
        self._suppress_trace = True
        self._var.set(value)
        self._suppress_trace = False
        if self._popup:
            self._close_popup()

    def __setitem__(self, key, value):
        if key == "values":
            self._all_values = list(value)
            if self._popup:
                self._refresh_listbox(self._current_filter())
        else:
            super().__setitem__(key, value)

    def __getitem__(self, key):
        if key == "values":
            return self._all_values
        return super().__getitem__(key)

    def bind(self, event, callback=None, add=None):
        if event == "<<ComboboxSelected>>":
            if callback:
                self._selected_callbacks.append(callback)
        else:
            self._entry.bind(event, callback, add)

    # ── internals ─────────────────────────────────────────────────────────────

    def _current_filter(self) -> str:
        return self._var.get().lower()

    def _matching(self, filt: str) -> list[str]:
        if not filt:
            return self._all_values
        return [v for v in self._all_values if filt in v.lower()]

    def _on_text_change(self, *_):
        if self._suppress_trace:
            return
        filt = self._current_filter()
        if self._popup:
            self._refresh_listbox(filt)
        elif filt:
            self._open_popup(filt)

    def _open_or_focus(self, _event=None):
        if self._popup:
            self._listbox.focus_set()
        else:
            self._open_popup(self._current_filter())

    def _open_popup(self, filt: str = "") -> None:
        if self._popup:
            return
        popup = tk.Toplevel(self._entry)
        popup.wm_overrideredirect(True)
        popup.wm_attributes("-topmost", True)

        x = self._entry.winfo_rootx()
        y = self._entry.winfo_rooty() + self._entry.winfo_height()
        w = max(self._entry.winfo_width(), 200)
        popup.wm_geometry(f"{w}x180+{x}+{y}")

        frame = tk.Frame(popup, bd=1, relief="solid")
        frame.pack(fill="both", expand=True)
        sb = tk.Scrollbar(frame, orient="vertical")
        lb = tk.Listbox(frame, yscrollcommand=sb.set, selectmode="browse",
                        activestyle="dotbox", exportselection=False)
        sb.config(command=lb.yview)
        sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)

        lb.bind("<Return>",   lambda _e: self._commit_selection())
        lb.bind("<Double-1>", lambda _e: self._commit_selection())
        lb.bind("<Escape>",   lambda _e: self._close_popup())
        lb.bind("<FocusOut>", self._on_listbox_focus_out)

        self._popup   = popup
        self._listbox = lb
        self._refresh_listbox(filt)

    def _refresh_listbox(self, filt: str) -> None:
        matches = self._matching(filt)
        self._listbox.delete(0, "end")
        for v in matches:
            self._listbox.insert("end", v)
        if matches:
            self._listbox.selection_set(0)
            self._listbox.see(0)

    def _commit_selection(self) -> None:
        sel = self._listbox.curselection()
        if not sel:
            return
        value = self._listbox.get(sel[0])
        self.set(value)          # uses suppress_trace so popup isn't re-opened
        self._close_popup()
        for cb in self._selected_callbacks:
            try:
                cb(None)
            except Exception:
                pass

    def _close_popup(self) -> None:
        if self._popup:
            self._popup.destroy()
            self._popup = None
        self._entry.focus_set()

    def _on_entry_return(self, _event=None) -> None:
        if self._popup:
            self._commit_selection()

    def _on_focus_out(self, _event=None) -> None:
        self._entry.after(150, self._maybe_close)

    def _on_listbox_focus_out(self, _event=None) -> None:
        self._entry.after(150, self._maybe_close)

    def _maybe_close(self) -> None:
        if not self._popup:
            return
        try:
            focused = self._popup.focus_get()
        except Exception:
            focused = None
        if focused not in (self._entry, self._listbox):
            self._close_popup()


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
        self.original_location_pops: dict[str, list[OrderedDict]] = {}
        self.row_diff_cache:     dict[int, tuple[bool, str]] = {}
        self.next_row_id = 1
        self.item_to_row:  dict[str, PopRow] = {}
        self.sort_column   = "location"
        self.sort_reverse  = False

        self.filter_vars    = {f: tk.StringVar() for f in FILTER_FIELDS}
        self.include_values = {f: set()          for f in FILTER_FIELDS}
        self.exclude_values = {f: set()          for f in FILTER_FIELDS}

        self.pop_attr_filter_vars    = {f: tk.StringVar() for f in POP_ATTR_FILTER_FIELDS}
        self.pop_attr_include_values = {f: set()          for f in POP_ATTR_FILTER_FIELDS}
        self.pop_attr_exclude_values = {f: set()          for f in POP_ATTR_FILTER_FIELDS}

        self.batch_add_var  = tk.StringVar(value="0")
        self.batch_mult_var = tk.StringVar(value="1")

        self.blend_from_culture_var = tk.StringVar()
        self.blend_to_culture_var = tk.StringVar()
        self.blend_from_religion_var = tk.StringVar()
        self.blend_to_religion_var = tk.StringVar()
        self.blend_ratio_var = tk.StringVar(value="100")

        self.redist_location_var = tk.StringVar()
        self.redist_ratio_var = tk.StringVar(value="10")

        self.add_location_var = tk.StringVar()
        self.add_culture_var = tk.StringVar()
        self.add_religion_var = tk.StringVar()
        self.add_type_var = tk.StringVar()
        self.add_size_var = tk.StringVar(value="1")

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
        self.root.rowconfigure(9, weight=1)
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
            combo = SearchableCombobox(filter_frame, textvariable=self.filter_vars[field], width=24)
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
        btn_row = len(FILTER_FIELDS)
        ttk.Button(filter_frame, text="Clear All",
                   command=self.clear_filters).grid(row=btn_row, column=0, pady=(8, 0), sticky="w")
        ttk.Button(filter_frame, text="Copy Locations",
                   command=self.copy_matching_locations).grid(row=btn_row, column=1, pady=(8, 0), sticky="w", padx=(4, 0))

        # pop attribute filters (culture / religion / type)
        pop_attr_frame = ttk.LabelFrame(self.root, text="Pop Attribute Filters (Culture / Religion / Type)", padding=8)
        pop_attr_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 2))
        pop_attr_frame.columnconfigure(4, weight=1)
        for idx, field in enumerate(POP_ATTR_FILTER_FIELDS):
            ttk.Label(pop_attr_frame, text=field.title()).grid(row=idx, column=0, sticky="w")
            combo = SearchableCombobox(pop_attr_frame, textvariable=self.pop_attr_filter_vars[field], width=24)
            combo.grid(row=idx, column=1, sticky="ew", padx=(4, 8))
            combo.bind("<<ComboboxSelected>>", self.on_pop_attr_filter_change)
            setattr(self, f"pop_attr_{field}_combo", combo)
            ttk.Button(pop_attr_frame, text="Include",
                       command=lambda f=field: self.add_pop_attr_selection(f, "include")).grid(
                row=idx, column=2, padx=(0, 4))
            ttk.Button(pop_attr_frame, text="Exclude",
                       command=lambda f=field: self.add_pop_attr_selection(f, "exclude")).grid(
                row=idx, column=3, padx=(0, 8))
            summary = ttk.Label(pop_attr_frame, text="", justify="left")
            summary.grid(row=idx, column=4, sticky="ew")
            setattr(self, f"pop_attr_{field}_summary", summary)
            ttk.Button(pop_attr_frame, text="Clear",
                       command=lambda f=field: self.clear_pop_attr_selection(f)).grid(row=idx, column=5)
        ttk.Button(pop_attr_frame, text="Clear All",
                   command=self.clear_pop_attr_filters).grid(
            row=len(POP_ATTR_FILTER_FIELDS), column=0, pady=(8, 0), sticky="w")

        # batch edit
        batch_frame = ttk.LabelFrame(self.root, text="Batch Size Edit", padding=8)
        batch_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 2))
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

        # blend cultures/religions
        blend_frame = ttk.LabelFrame(self.root, text="Blend (Convert % from Source to Target)", padding=8)
        blend_frame.grid(row=5, column=0, sticky="ew", padx=8, pady=(0, 4))
        blend_frame.columnconfigure(3, weight=1)
        ttk.Label(blend_frame, text="From Culture").grid(row=0, column=0, sticky="w")
        self.blend_from_combo = SearchableCombobox(blend_frame, textvariable=self.blend_from_culture_var, width=18)
        self.blend_from_combo.grid(row=0, column=1, sticky="w", padx=(4, 12))
        ttk.Label(blend_frame, text="To Culture").grid(row=0, column=2, sticky="w")
        self.blend_to_combo = SearchableCombobox(blend_frame, textvariable=self.blend_to_culture_var, width=18)
        self.blend_to_combo.grid(row=0, column=3, sticky="w", padx=(4, 12))
        ttk.Label(blend_frame, text="From Religion").grid(row=1, column=0, sticky="w")
        self.blend_from_religion_combo = SearchableCombobox(blend_frame, textvariable=self.blend_from_religion_var, width=18)
        self.blend_from_religion_combo.grid(row=1, column=1, sticky="w", padx=(4, 12))
        ttk.Label(blend_frame, text="To Religion").grid(row=1, column=2, sticky="w")
        self.blend_to_religion_combo = SearchableCombobox(blend_frame, textvariable=self.blend_to_religion_var, width=18)
        self.blend_to_religion_combo.grid(row=1, column=3, sticky="w", padx=(4, 12))
        ttk.Label(blend_frame, text="Ratio %").grid(row=0, column=4, sticky="w")
        ttk.Entry(blend_frame, textvariable=self.blend_ratio_var, width=8).grid(row=0, column=5, sticky="w", padx=(4, 12))
        ttk.Button(blend_frame, text="Apply to Filtered Rows",
                  command=self.apply_blend_cultures).grid(row=0, column=6, sticky="w")
        ttk.Label(blend_frame,
                  text="Swaps culture and/or religion by ratio; fills both to apply both.").grid(
            row=0, column=7, sticky="w", padx=(12, 0))

        # redistribute
        redistribute_frame = ttk.LabelFrame(self.root, text="Redistribute / Collect Pops", padding=8)
        redistribute_frame.grid(row=7, column=0, sticky="ew", padx=8, pady=(0, 4))
        redistribute_frame.columnconfigure(2, weight=1)
        ttk.Label(redistribute_frame, text="Source Location").grid(row=0, column=0, sticky="w")
        self.redist_location_combo = SearchableCombobox(redistribute_frame, textvariable=self.redist_location_var, width=22)
        self.redist_location_combo.grid(row=0, column=1, sticky="w", padx=(4, 12))
        ttk.Label(redistribute_frame, text="Ratio %").grid(row=0, column=2, sticky="w")
        ttk.Entry(redistribute_frame, textvariable=self.redist_ratio_var, width=8).grid(row=0, column=3, sticky="w", padx=(4, 12))
        ttk.Button(redistribute_frame, text="Redistribute",
                   command=self.apply_redistribute_to_province).grid(row=0, column=4, sticky="w", padx=(12, 4))
        ttk.Button(redistribute_frame, text="Collect",
                   command=self.apply_redistribute_to_location).grid(row=0, column=5, sticky="w")
        ttk.Label(redistribute_frame,
                  text="Redistribute: take ratio% from source, distribute to other province locs. Collect: take ratio% from other locs, add to source.").grid(
            row=0, column=6, sticky="w", padx=(12, 0))

        # add pop
        add_frame = ttk.LabelFrame(self.root, text="Add New Pop", padding=8)
        add_frame.grid(row=8, column=0, sticky="ew", padx=8, pady=(0, 4))
        add_frame.columnconfigure(4, weight=1)
        ttk.Label(add_frame, text="Location").grid(row=0, column=0, sticky="w")
        self.add_location_combo = SearchableCombobox(add_frame, textvariable=self.add_location_var, width=18)
        self.add_location_combo.grid(row=0, column=1, sticky="w", padx=(4, 8))
        ttk.Label(add_frame, text="Type").grid(row=0, column=2, sticky="w")
        self.add_type_combo = ttk.Combobox(add_frame, textvariable=self.add_type_var, width=12)
        self.add_type_combo.grid(row=0, column=3, sticky="w", padx=(4, 8))
        ttk.Label(add_frame, text="Culture").grid(row=1, column=0, sticky="w")
        self.add_culture_combo = SearchableCombobox(add_frame, textvariable=self.add_culture_var, width=18)
        self.add_culture_combo.grid(row=1, column=1, sticky="w", padx=(4, 8))
        ttk.Label(add_frame, text="Religion").grid(row=1, column=2, sticky="w")
        self.add_religion_combo = SearchableCombobox(add_frame, textvariable=self.add_religion_var, width=18)
        self.add_religion_combo.grid(row=1, column=3, sticky="w", padx=(4, 8))
        ttk.Label(add_frame, text="Size").grid(row=1, column=4, sticky="w")
        ttk.Entry(add_frame, textvariable=self.add_size_var, width=10).grid(row=1, column=5, sticky="w", padx=(4, 8))
        ttk.Button(add_frame, text="Add", command=self.apply_add_pop).grid(row=0, column=6, sticky="w", padx=(12, 0))

        # table
        table_frame = ttk.Frame(self.root, padding=(8, 4, 8, 8))
        table_frame.grid(row=9, column=0, sticky="nsew")
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

        ttk.Label(self.root, textvariable=self.status_var, padding=(8, 2)).grid(row=10, column=0, sticky="ew")

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

            self.location_geo = parse_definitions(defs_path)
            self.location_order, location_pops = parse_pops(pops_path)
            self.original_location_pops = _normalize_pops(location_pops)

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
            self.update_pop_attr_filter_options()
            self.update_blend_culture_options()
            self.update_redistribute_options()
            self.update_add_pop_options()
            self.refresh_table()
            self.status_var.set(
                f"Loaded {len(self.rows)} pop rows from {pops_path.name}; "
                f"compare to original (on load)."
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
            base_list = self.original_location_pops.get(row.location, [])
            if idx < len(base_list):
                differs = not pop_attrs_equal(row.attrs, base_list[idx])
                summary = format_attrs_summary(base_list[idx]) if differs else ""
            else:
                differs, summary = True, "<missing in original>"
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
                combo.set("")
            self._update_summary(field)
        self.update_pop_attr_filter_options()

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
        # Clear via combo.set() so the popup closes and suppress_trace is honoured
        getattr(self, f"{field}_combo").set("")
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
            getattr(self, f"{field}_combo").set("")
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
        has_pop_includes = any(self.pop_attr_include_values.values())
        result = []
        for row in self.rows:
            if active and any(self._geo_value(row, f) != v for f, v in active.items()):
                continue
            if has_includes and not self._row_matches(row, self.include_values):
                continue
            if self._row_matches(row, self.exclude_values):
                continue
            # pop attribute filters (culture / religion / type)
            if has_pop_includes:
                if not any(
                    row.attrs.get(f) in vals
                    for f, vals in self.pop_attr_include_values.items() if vals
                ):
                    continue
            if any(
                row.attrs.get(f) in vals
                for f, vals in self.pop_attr_exclude_values.items() if vals
            ):
                continue
            result.append(row)
        return result

    def update_pop_attr_filter_options(self) -> None:
        # Collect unique values from all rows that pass geo filters only
        active = {f: self.filter_vars[f].get() for f in FILTER_FIELDS if self.filter_vars[f].get()}
        has_includes = any(self.include_values.values())
        geo_rows = []
        for row in self.rows:
            if active and any(self._geo_value(row, f) != v for f, v in active.items()):
                continue
            if has_includes and not self._row_matches(row, self.include_values):
                continue
            if self._row_matches(row, self.exclude_values):
                continue
            geo_rows.append(row)
        for field in POP_ATTR_FILTER_FIELDS:
            seen = sorted({
                row.attrs.get(field, "") for row in geo_rows
                if row.attrs.get(field)
                # narrow: row must pass the *other* pop attr filters (not the one we're building)
                and not any(
                    vals and row.attrs.get(f) not in vals
                    for f, vals in self.pop_attr_include_values.items()
                    if f != field and vals
                )
                and not any(
                    row.attrs.get(f) in vals
                    for f, vals in self.pop_attr_exclude_values.items()
                    if f != field and vals
                )
            })
            combo = getattr(self, f"pop_attr_{field}_combo")
            current = self.pop_attr_filter_vars[field].get()
            combo["values"] = [""] + seen
            if current not in combo["values"]:
                combo.set("")
            self._update_pop_attr_summary(field)

    def _update_pop_attr_summary(self, field: str) -> None:
        inc = ", ".join(sorted(self.pop_attr_include_values[field])) or "-"
        exc = ", ".join(sorted(self.pop_attr_exclude_values[field])) or "-"
        getattr(self, f"pop_attr_{field}_summary").configure(text=f"Include: {inc} | Exclude: {exc}")

    def on_pop_attr_filter_change(self, _event=None) -> None:
        self.update_pop_attr_filter_options()
        self.refresh_table()

    def add_pop_attr_selection(self, field: str, mode: str) -> None:
        value = self.pop_attr_filter_vars[field].get()
        if not value:
            return
        (self.pop_attr_include_values if mode == "include" else self.pop_attr_exclude_values)[field].add(value)
        (self.pop_attr_exclude_values if mode == "include" else self.pop_attr_include_values)[field].discard(value)
        # Clear via combo.set() so popup closes and suppress_trace is honoured
        getattr(self, f"pop_attr_{field}_combo").set("")
        self._update_pop_attr_summary(field)
        self.update_pop_attr_filter_options()
        self.refresh_table()

    def clear_pop_attr_selection(self, field: str) -> None:
        self.pop_attr_include_values[field].clear()
        self.pop_attr_exclude_values[field].clear()
        self._update_pop_attr_summary(field)
        self.update_pop_attr_filter_options()
        self.refresh_table()

    def clear_pop_attr_filters(self) -> None:
        for field in POP_ATTR_FILTER_FIELDS:
            getattr(self, f"pop_attr_{field}_combo").set("")
            self.pop_attr_include_values[field].clear()
            self.pop_attr_exclude_values[field].clear()
            self._update_pop_attr_summary(field)
        self.refresh_table()

    def copy_matching_locations(self) -> None:
        locations = list(dict.fromkeys(row.location for row in self.iter_filtered_rows()))
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(locations))
        self.status_var.set(f"Copied {len(locations)} matching locations to the clipboard.")

    def update_blend_culture_options(self) -> None:
        all_cultures = sorted({
            row.attrs.get("culture", "")
            for row in self.rows
            if row.attrs.get("culture")
        })
        self.blend_from_combo["values"] = [""] + all_cultures
        self.blend_to_combo["values"] = [""] + all_cultures

        all_religions = sorted({
            row.attrs.get("religion", "")
            for row in self.rows
            if row.attrs.get("religion")
        })
        self.blend_from_religion_combo["values"] = [""] + all_religions
        self.blend_to_religion_combo["values"] = [""] + all_religions

    def apply_blend_cultures(self) -> None:
        from_culture = self.blend_from_culture_var.get().strip()
        to_culture = self.blend_to_culture_var.get().strip()
        from_religion = self.blend_from_religion_var.get().strip()
        to_religion = self.blend_to_religion_var.get().strip()
        try:
            ratio = float(self.blend_ratio_var.get().strip())
        except ValueError:
            messagebox.showerror("Invalid ratio", "Ratio must be a number.")
            return
        if not from_culture and not from_religion:
            messagebox.showerror("Missing input", "Please specify at least one of culture or religion to swap.")
            return
        if ratio <= 0 or ratio > 100:
            messagebox.showerror("Invalid ratio", "Ratio must be between 0 and 100.")
            return
        if from_culture and to_culture and from_culture == to_culture:
            messagebox.showerror("Same value", "From and to cultures must be different.")
            return
        if from_religion and to_religion and from_religion == to_religion:
            messagebox.showerror("Same value", "From and to religions must be different.")
            return

        active_geo = {f: self.filter_vars[f].get() for f in FILTER_FIELDS if self.filter_vars[f].get()}
        has_geo_includes = any(self.include_values.values())

        matching_rows = []
        for row in self.rows:
            if active_geo and any(self._geo_value(row, f) != v for f, v in active_geo.items()):
                continue
            if has_geo_includes and not self._row_matches(row, self.include_values):
                continue
            if self._row_matches(row, self.exclude_values):
                continue
            matching_rows.append(row)

        if not matching_rows:
            self.status_var.set("No matching rows to edit.")
            return

        created = 0
        converted = 0
        new_rows_to_add = []

        for row in matching_rows:
            matches_culture = from_culture and row.attrs.get("culture") == from_culture
            matches_religion = from_religion and row.attrs.get("religion") == from_religion
            if not matches_culture and not matches_religion:
                continue
            try:
                old_size = _safe_float(row.attrs.get("size", "0"))
            except ValueError:
                continue
            if old_size <= 0:
                continue

            new_target_size = old_size * (ratio / 100)
            new_source_size = old_size * (1 - ratio / 100)

            new_attrs = OrderedDict()
            new_attrs["size"] = normalize_size(str(new_target_size))
            if to_culture:
                new_attrs["culture"] = to_culture
            else:
                new_attrs["culture"] = row.attrs.get("culture", "")
            if to_religion:
                new_attrs["religion"] = to_religion
            else:
                new_attrs["religion"] = row.attrs.get("religion", "")
            for k, v in row.attrs.items():
                if k not in ("size", "culture", "religion"):
                    new_attrs[k] = v
            new_row = PopRow(self.next_row_id, row.location, new_attrs)
            new_rows_to_add.append(new_row)
            self.next_row_id += 1
            created += 1

            row.attrs["size"] = normalize_size(str(new_source_size))
            converted += 1

        self.rows.extend(new_rows_to_add)

        if converted == 0:
            self.status_var.set(f"No rows matching from-culture or from-religion in filtered locations.")
            return

        self.rebuild_row_metadata()
        self.update_filter_options()
        self.update_pop_attr_filter_options()
        self.update_blend_culture_options()
        self.refresh_table()
        self.status_var.set(
            f"Blended {converted} row(s): {created} new target row(s) created, "
            f"{ratio}% converted."
        )

    def update_redistribute_options(self) -> None:
        all_locations = sorted(self.location_geo.keys())
        self.redist_location_combo["values"] = [""] + all_locations
        self.add_location_combo["values"] = [""] + all_locations

    def update_add_pop_options(self) -> None:
        all_cultures = sorted({
            row.attrs.get("culture", "")
            for row in self.rows
            if row.attrs.get("culture")
        })
        self.add_culture_combo["values"] = [""] + all_cultures

        all_religions = sorted({
            row.attrs.get("religion", "")
            for row in self.rows
            if row.attrs.get("religion")
        })
        self.add_religion_combo["values"] = [""] + all_religions

        all_types = sorted({
            row.attrs.get("type", "")
            for row in self.rows
            if row.attrs.get("type")
        })
        self.add_type_combo["values"] = all_types

    def apply_redistribute_to_province(self) -> None:
        source_location = self.redist_location_var.get().strip()
        if not source_location:
            messagebox.showerror("Missing source", "Please select a source location.")
            return
        if source_location not in self.location_geo:
            messagebox.showerror("Invalid location", f"'{source_location}' not found in definitions.")
            return
        try:
            ratio = float(self.redist_ratio_var.get().strip())
        except ValueError:
            messagebox.showerror("Invalid ratio", "Ratio must be a number.")
            return
        if ratio <= 0 or ratio > 100:
            messagebox.showerror("Invalid ratio", "Ratio must be between 0 and 100.")
            return

        source_province = self.location_geo[source_location].province
        if not source_province:
            messagebox.showerror("No province", "Source location has no province defined.")
            return

        other_locations = [
            loc for loc, geo in self.location_geo.items()
            if geo.province == source_province and loc != source_location
        ]
        if not other_locations:
            messagebox.showerror("No other locations", "No other locations in this province.")
            return

        source_rows = [r for r in self.rows if r.location == source_location]
        if not source_rows:
            messagebox.showerror("No pops", "No pops at source location.")
            return

        new_rows = []
        redistributed = 0

        for row in source_rows:
            try:
                old_size = _safe_float(row.attrs.get("size", "0"))
            except ValueError:
                continue
            if old_size <= 0:
                continue

            size_per_loc = (old_size * ratio / 100) / len(other_locations)
            remaining_size = old_size * (1 - ratio / 100)

            row.attrs["size"] = normalize_size(str(remaining_size))
            redistributed += 1

            for loc in other_locations:
                new_attrs = OrderedDict(row.attrs)
                new_attrs["size"] = normalize_size(str(size_per_loc))
                new_rows.append(PopRow(self.next_row_id, loc, new_attrs))
                self.next_row_id += 1

        self.rows.extend(new_rows)

        self.rebuild_row_metadata()
        self.update_filter_options()
        self.update_pop_attr_filter_options()
        self.update_blend_culture_options()
        self.update_redistribute_options()
        self.refresh_table()
        self.status_var.set(
            f"Skimmed {redistributed} row(s) from {source_location} to {len(other_locations)} other province locations ({ratio}% each)."
        )

    def apply_redistribute_to_location(self) -> None:
        source_location = self.redist_location_var.get().strip()
        if not source_location:
            messagebox.showerror("Missing source", "Please select a source location.")
            return
        if source_location not in self.location_geo:
            messagebox.showerror("Invalid location", f"'{source_location}' not found in definitions.")
            return
        try:
            ratio = float(self.redist_ratio_var.get().strip())
        except ValueError:
            messagebox.showerror("Invalid ratio", "Ratio must be a number.")
            return
        if ratio <= 0 or ratio > 100:
            messagebox.showerror("Invalid ratio", "Ratio must be between 0 and 100.")
            return

        source_province = self.location_geo[source_location].province
        if not source_province:
            messagebox.showerror("No province", "Source location has no province defined.")
            return

        other_locations = [
            loc for loc, geo in self.location_geo.items()
            if geo.province == source_province and loc != source_location
        ]
        if not other_locations:
            messagebox.showerror("No other locations", "No other locations in this province.")
            return

        source_rows = [r for r in self.rows if r.location == source_location]
        if not source_rows:
            messagebox.showerror("No pops", "No pops at source location.")
            return

        collected = 0

        for loc in other_locations:
            loc_rows = [r for r in self.rows if r.location == loc]
            for row in loc_rows:
                try:
                    old_size = _safe_float(row.attrs.get("size", "0"))
                except ValueError:
                    continue
                if old_size <= 0:
                    continue

                size_to_move = old_size * ratio / 100
                new_size = old_size - size_to_move
                row.attrs["size"] = normalize_size(str(new_size)) if new_size > 0 else "0.000"

                for src_row in source_rows:
                    src_size = _safe_float(src_row.attrs.get("size", "0"))
                    src_row.attrs["size"] = normalize_size(str(src_size + size_to_move))
                collected += 1
                break

        if collected == 0:
            messagebox.showerror("No pops", "No pops found at other locations.")
            return

        self.rebuild_row_metadata()
        self.update_filter_options()
        self.update_pop_attr_filter_options()
        self.update_blend_culture_options()
        self.refresh_table()
        self.status_var.set(
            f"Collected {ratio}% from {len(other_locations)} other locations in province to {source_location}."
        )

    def apply_add_pop(self) -> None:
        location = self.add_location_var.get().strip()
        culture = self.add_culture_var.get().strip()
        religion = self.add_religion_var.get().strip()
        pop_type = self.add_type_var.get().strip()
        try:
            size = float(self.add_size_var.get().strip())
        except ValueError:
            messagebox.showerror("Invalid size", "Size must be a number.")
            return
        if not location:
            messagebox.showerror("Missing location", "Please select a location.")
            return
        if not pop_type:
            messagebox.showerror("Missing type", "Please select a pop type.")
            return
        if not culture:
            messagebox.showerror("Missing culture", "Please enter a culture.")
            return
        if not religion:
            messagebox.showerror("Missing religion", "Please enter a religion.")
            return
        if size <= 0:
            messagebox.showerror("Invalid size", "Size must be greater than 0.")
            return

        if location not in self.location_geo:
            messagebox.showerror("Invalid location", f"'{location}' not found.")
            return

        attrs = OrderedDict()
        attrs["type"] = pop_type
        attrs["size"] = normalize_size(str(size))
        attrs["culture"] = culture
        attrs["religion"] = religion

        for k, v in STD_ATTR_ORDER:
            if k in attrs:
                del attrs[k]
        for k in STD_ATTR_ORDER:
            attrs[k] = attrs.pop(k)

        new_row = PopRow(self.next_row_id, location, attrs)
        self.rows.append(new_row)
        self.next_row_id += 1

        self.rebuild_row_metadata()
        self.update_filter_options()
        self.update_pop_attr_filter_options()
        self.update_blend_culture_options()
        self.update_redistribute_options()
        self.update_add_pop_options()
        self.refresh_table()
        self.status_var.set(f"Added new pop at {location}: {pop_type} {culture} {religion} {size}.")

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

        changed = clamped = removed = 0
        rows_to_remove = []
        for row in filtered:
            try:
                new_size = float(row.attrs.get("size", "")) * multiplier + addition
            except ValueError:
                continue
            if new_size <= 0:
                rows_to_remove.append(row)
                removed += 1
                continue
            if new_size < 0:
                new_size = 0.0
                clamped += 1
            row.attrs["size"] = f"{new_size:.3f}"
            changed += 1

        for row in rows_to_remove:
            self.rows.remove(row)

        self.rebuild_row_metadata()
        self.refresh_table()
        parts = [f"Updated size on {changed} row(s)"]
        if clamped:
            parts.append(f"{clamped} clamped to 0")
        if removed:
            parts.append(f"{removed} removed (size 0)")
        self.status_var.set(f"{', '.join(parts)}.")


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