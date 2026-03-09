#!/usr/bin/env python3
"""GUI editor for 1444 pop data with geography filters from basegame definitions."""

from __future__ import annotations

import copy
import json
import re
import tkinter as tk
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


REPO_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = REPO_ROOT / "tools/settings.json"
BASE_POPS_REL = Path("game/main_menu/setup/start/06_pops.txt")
MAX_VISIBLE_ROWS = 3000
STD_ATTR_ORDER = ["type", "size", "culture", "religion"]
FILTER_FIELDS = ["continent", "superregion", "region", "area", "province", "location"]
TABLE_COLUMNS = ["location"] + STD_ATTR_ORDER + ["continent", "superregion", "region", "area", "province", "extra", "differs", "basegame"]
EDITABLE_COLUMNS = {"location", "type", "size", "culture", "religion", "extra"}


@dataclass
class GeoInfo:
    continent: str = ""
    superregion: str = ""
    region: str = ""
    area: str = ""
    province: str = ""


@dataclass
class PopRow:
    row_id: int
    location: str
    attrs: OrderedDict[str, str]

    def get_value(self, column: str, geo: GeoInfo) -> str:
        if column in FILTER_FIELDS:
            if column == "location":
                return self.location
            return getattr(geo, column)
        if column == "extra":
            extras = [f"{k}={v}" for k, v in self.attrs.items() if k not in STD_ATTR_ORDER]
            return "; ".join(extras)
        return self.attrs.get(column, "")


def load_settings() -> dict:
    if SETTINGS_PATH.is_file():
        try:
            settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if "base_game" in settings and "mod_folder" in settings:
                return settings
        except json.JSONDecodeError:
            pass
    return {}


def prompt_for_paths() -> dict:
    print("Settings file is missing or malformed.")
    base_game = input("Enter the base game path (which has the corresponding files in root/game): ").strip()
    mod_folder = input("Enter the mod path (which has the corresponding files in root): ").strip()
    settings = {"base_game": base_game, "mod_folder": mod_folder}
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return settings


def resolve_default_paths() -> tuple[Path, Path, Path]:
    settings = load_settings()
    if not settings:
        settings = prompt_for_paths()
    mod_folder = Path(settings.get("mod_folder", REPO_ROOT)).expanduser()
    base_game = Path(settings.get("base_game", "")).expanduser()
    pops_path = mod_folder / "main_menu/setup/start/06_pops.txt"
    definitions_path = base_game / "game/in_game/map_data/definitions.txt"
    base_pops_path = base_game / BASE_POPS_REL
    return pops_path, definitions_path, base_pops_path


def pop_attrs_equal(left: OrderedDict[str, str], right: OrderedDict[str, str]) -> bool:
    return list(left.items()) == list(right.items())


def format_attrs_summary(attrs: OrderedDict[str, str]) -> str:
    return ", ".join(f"{key}={value}" for key, value in attrs.items())


def normalize_size(value: str) -> str:
    """Return a size string with three decimal places if numeric.

    Non-numeric values are returned unchanged so the rest of the parser can
    handle unusual entries without raising exceptions.
    """
    try:
        return f"{float(value):.3f}"
    except Exception:
        return value


class GeoNode:
    def __init__(self, name: str, path: tuple[str, ...]):
        self.name = name
        self.path = path
        self.children: list[GeoNode] = []
        self.locations: list[str] = []
        self.descendant_locations: set[str] = set()


def tokenize_definitions(text: str) -> list[str]:
    cleaned = re.sub(r"#.*$", "", text, flags=re.MULTILINE)
    return re.findall(r"[{}=]|[^\s{}=]+", cleaned)


def classify_geo(path: tuple[str, ...]) -> GeoInfo:
    values = list(path)
    while len(values) < 5:
        values.append("")
    return GeoInfo(
        continent=values[0],
        superregion=values[1],
        region=values[2],
        area=values[3],
        province=values[4],
    )


def parse_geo_node(
    tokens: list[str],
    idx: int,
    name: str,
    path: tuple[str, ...],
    nodes_by_name: dict[str, list[GeoNode]],
    location_geo: dict[str, GeoInfo],
) -> tuple[GeoNode, int]:
    node = GeoNode(name, path)
    nodes_by_name[name].append(node)
    while idx < len(tokens):
        token = tokens[idx]
        if token == "}":
            idx += 1
            node.descendant_locations.update(node.locations)
            for child in node.children:
                node.descendant_locations.update(child.descendant_locations)
            return node, idx
        key = token
        idx += 1
        if idx < len(tokens) and tokens[idx] == "=":
            idx += 1
            if tokens[idx] != "{":
                raise ValueError(f"Expected '{{' after {key}")
            idx += 1
            child, idx = parse_geo_node(tokens, idx, key, path + (key,), nodes_by_name, location_geo)
            node.children.append(child)
        else:
            node.locations.append(key)
            location_geo[key] = classify_geo(path)
    raise ValueError(f"Unclosed geography block for {name}")


def parse_definitions(path: Path) -> tuple[dict[str, GeoInfo], dict[str, set[str]]]:
    tokens = tokenize_definitions(path.read_text(encoding="utf-8-sig"))
    idx = 0
    nodes_by_name: dict[str, list[GeoNode]] = defaultdict(list)
    location_geo: dict[str, GeoInfo] = {}
    while idx < len(tokens):
        name = tokens[idx]
        idx += 1
        if idx >= len(tokens) or tokens[idx] != "=":
            raise ValueError(f"Expected '=' after {name}")
        idx += 1
        if tokens[idx] != "{":
            raise ValueError(f"Expected '{{' after {name} =")
        idx += 1
        _, idx = parse_geo_node(tokens, idx, name, (name,), nodes_by_name, location_geo)
    geography_locations = {
        name: set().union(*(node.descendant_locations for node in nodes))
        for name, nodes in nodes_by_name.items()
    }
    return location_geo, geography_locations


def find_matching_brace(text: str, start_idx: int) -> int:
    depth = 0
    for idx in range(start_idx, len(text)):
        if text[idx] == "{":
            depth += 1
        elif text[idx] == "}":
            depth -= 1
            if depth == 0:
                return idx
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
    root_end = find_matching_brace(text, root_brace)
    body = text[root_brace + 1 : root_end]

    location_order: list[str] = []
    location_pops: dict[str, list[OrderedDict[str, str]]] = OrderedDict()
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
        location = name_match.group(1)
        block_name_start = idx
        brace_idx = body.find("{", idx + name_match.end())
        if brace_idx == -1:
            raise ValueError(f"Missing block for location {location}")
        block_end = find_matching_brace(body, brace_idx)
        block_text = body[brace_idx + 1 : block_end]
        pops = []
        for pop_body in re.findall(r"define_pop\s*=\s*\{([^{}]*)\}", block_text):
            attrs = parse_pop_attributes(pop_body)
            if attrs:
                pops.append(attrs)
        location_order.append(location)
        location_pops[location] = pops
        idx = block_end + 1
        while idx < len(body) and body[idx] in "\r\n":
            idx += 1
    return location_order, location_pops


def format_pop_line(attrs: OrderedDict[str, str]) -> str:
    # make sure size is formatted before writing
    if "size" in attrs:
        attrs = OrderedDict(attrs)
        attrs["size"] = normalize_size(attrs["size"])

    ordered = OrderedDict()
    for key in STD_ATTR_ORDER:
        if key in attrs:
            ordered[key] = attrs[key]
    for key, value in attrs.items():
        if key not in ordered:
            ordered[key] = value
    return "\tdefine_pop = { " + "\t".join(f"{key} = {value}" for key, value in ordered.items()) + " }"


class PopEditorApp:
    def __init__(self, root: tk.Tk, base_game_path: Path, mod_folder_path: Path):
        self.root = root
        self.root.title("EU5 1444 Pop Geography Editor")
        self.base_game_path = base_game_path
        self.mod_folder_path = mod_folder_path

        self.location_geo: dict[str, GeoInfo] = {}
        self.geography_locations: dict[str, set[str]] = {}
        self.location_order: list[str] = []
        self.rows: list[PopRow] = []
        self.original_rows: list[PopRow] = []
        self.original_location_order: list[str] = []
        self.base_location_pops: dict[str, list[OrderedDict[str, str]]] = {}
        self.row_position_by_id: dict[int, int] = {}
        self.row_diff_cache: dict[int, tuple[bool, str]] = {}
        self.next_row_id = 1
        self.filter_vars = {field: tk.StringVar(value="") for field in FILTER_FIELDS}
        self.include_values = {field: set() for field in FILTER_FIELDS}
        self.exclude_values = {field: set() for field in FILTER_FIELDS}
        self.batch_add_var = tk.StringVar(value="0")
        self.batch_mult_var = tk.StringVar(value="1")
        self.status_var = tk.StringVar(value="Load a pop file to begin.")
        self.path_vars = {
            "base_game": tk.StringVar(value=str(self.base_game_path)),
            "mod_folder": tk.StringVar(value=str(self.mod_folder_path)),
        }
        self.item_to_row: dict[str, PopRow] = {}
        self.sort_column = "location"
        self.sort_reverse = False

        self._build_ui()
        self.load_data()

    def _build_ui(self) -> None:
        self.root.geometry("1500x850")
        self.root.rowconfigure(3, weight=1)
        self.root.columnconfigure(0, weight=1)

        file_frame = ttk.Frame(self.root, padding=8)
        file_frame.grid(row=0, column=0, sticky="ew")
        file_frame.columnconfigure(1, weight=1)
        file_frame.columnconfigure(4, weight=1)

        ttk.Label(file_frame, text="Base Game").grid(row=0, column=0, sticky="w")
        ttk.Entry(file_frame, textvariable=self.path_vars["base_game"]).grid(row=0, column=1, sticky="ew", padx=(6, 6))
        ttk.Button(file_frame, text="Browse", command=self.browse_base_game).grid(row=0, column=2, padx=(0, 10))
        ttk.Label(file_frame, text="Mod Folder").grid(row=0, column=3, sticky="w")
        ttk.Entry(file_frame, textvariable=self.path_vars["mod_folder"]).grid(row=0, column=4, sticky="ew", padx=(6, 6))
        ttk.Button(file_frame, text="Browse", command=self.browse_mod_folder).grid(row=0, column=5, padx=(0, 10))
        ttk.Button(file_frame, text="Reload", command=self.load_data).grid(row=0, column=6)
        ttk.Button(file_frame, text="Save", command=self.save_data).grid(row=0, column=7, padx=(6, 0))

        filter_frame = ttk.LabelFrame(self.root, text="Geography Selection", padding=8)
        filter_frame.grid(row=1, column=0, sticky="ew", padx=8)
        filter_frame.columnconfigure(4, weight=1)
        for idx, field in enumerate(FILTER_FIELDS):
            ttk.Label(filter_frame, text=field.title()).grid(row=idx, column=0, sticky="w")
            combo = ttk.Combobox(
                filter_frame,
                textvariable=self.filter_vars[field],
                state="readonly",
                width=24,
            )
            combo.grid(row=idx, column=1, sticky="ew", padx=(4, 8))
            combo.bind("<<ComboboxSelected>>", self.on_filter_change)
            setattr(self, f"{field}_combo", combo)
            ttk.Button(filter_frame, text="Include", command=lambda f=field: self.add_selection(f, "include")).grid(
                row=idx, column=2, padx=(0, 4)
            )
            ttk.Button(filter_frame, text="Exclude", command=lambda f=field: self.add_selection(f, "exclude")).grid(
                row=idx, column=3, padx=(0, 8)
            )
            summary = ttk.Label(filter_frame, text="", justify="left")
            summary.grid(row=idx, column=4, sticky="ew")
            setattr(self, f"{field}_summary", summary)
            ttk.Button(filter_frame, text="Clear", command=lambda f=field: self.clear_selection(f)).grid(
                row=idx, column=5
            )
        ttk.Button(filter_frame, text="Clear All", command=self.clear_filters).grid(
            row=len(FILTER_FIELDS), column=0, pady=(8, 0), sticky="w"
        )
        ttk.Button(filter_frame, text="Copy Locations", command=self.copy_matching_locations).grid(
            row=len(FILTER_FIELDS), column=1, pady=(8, 0), sticky="w"
        )

        batch_frame = ttk.LabelFrame(self.root, text="Batch Size Edit", padding=8)
        batch_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 4))
        batch_frame.columnconfigure(5, weight=1)
        ttk.Label(batch_frame, text="Multiplier").grid(row=0, column=0, sticky="w")
        ttk.Entry(batch_frame, textvariable=self.batch_mult_var, width=12).grid(
            row=0, column=1, sticky="w", padx=(6, 12)
        )
        ttk.Label(batch_frame, text="Addition").grid(row=0, column=2, sticky="w")
        ttk.Entry(batch_frame, textvariable=self.batch_add_var, width=12).grid(
            row=0, column=3, sticky="w", padx=(6, 12)
        )
        ttk.Button(batch_frame, text="Apply to Filtered Rows", command=self.apply_batch_size_edit).grid(
            row=0, column=4, sticky="w"
        )
        ttk.Label(
            batch_frame,
            text="Uses: new_size = old_size * multiplier + addition. Negative results are clamped to 0.",
        ).grid(row=0, column=5, sticky="w", padx=(12, 0))

        table_frame = ttk.Frame(self.root, padding=(8, 4, 8, 8))
        table_frame.grid(row=3, column=0, sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            table_frame,
            columns=TABLE_COLUMNS,
            show="headings",
            selectmode="browse",
        )
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        widths = {
            "continent": 120,
            "superregion": 150,
            "region": 150,
            "area": 150,
            "province": 170,
            "location": 170,
            "type": 100,
            "size": 90,
            "culture": 120,
            "religion": 120,
            "extra": 300,
            "differs": 70,
            "basegame": 360,
        }
        for column in TABLE_COLUMNS:
            self.tree.heading(column, text=column.title(), command=lambda c=column: self.sort_by(c))
            self.tree.column(column, width=widths.get(column, 120), anchor="w", stretch=True)
        self.tree.bind("<Double-1>", self.begin_edit_cell)
        self.tree.tag_configure("differs", background="#fff1d6")

        status = ttk.Label(self.root, textvariable=self.status_var, padding=(8, 2))
        status.grid(row=4, column=0, sticky="ew")

    def browse_base_game(self) -> None:
        selected = filedialog.askdirectory(
            title="Select Base Game Directory",
            initialdir=self.path_vars["base_game"].get(),
        )
        if selected:
            self.path_vars["base_game"].set(selected)

    def browse_mod_folder(self) -> None:
        selected = filedialog.askdirectory(
            title="Select Mod Folder Directory",
            initialdir=self.path_vars["mod_folder"].get(),
        )
        if selected:
            self.path_vars["mod_folder"].set(selected)

    def load_data(self) -> None:
        try:
            base_game_path = Path(self.path_vars["base_game"].get()).expanduser()
            mod_folder_path = Path(self.path_vars["mod_folder"].get()).expanduser()
            self.pops_path = mod_folder_path / "main_menu/setup/start/06_pops.txt"
            self.definitions_path = base_game_path / "game/in_game/map_data/definitions.txt"
            self.base_pops_path = base_game_path / BASE_POPS_REL
            self.location_geo, self.geography_locations = parse_definitions(self.definitions_path)
            self.location_order, location_pops = parse_pops(self.pops_path)
            _, self.base_location_pops = parse_pops(self.base_pops_path)
            self.rows = []
            self.next_row_id = 1
            for location in self.location_order:
                for attrs in location_pops.get(location, []):
                    # normalize size values on load so the table always shows
                    # three decimal places
                    if "size" in attrs:
                        attrs = OrderedDict(attrs)
                        attrs["size"] = normalize_size(attrs["size"])
                    self.rows.append(PopRow(self.next_row_id, location, OrderedDict(attrs)))
                    self.next_row_id += 1
            self.original_rows = copy.deepcopy(self.rows)
            self.original_location_order = list(self.location_order)
            self.rebuild_row_metadata()
            self.update_filter_options()
            self.refresh_table()
            self.status_var.set(
                f"Loaded {len(self.rows)} pop rows from {self.pops_path.name}; basegame compare from {self.base_pops_path.name}."
            )
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))
            self.status_var.set("Load failed.")

    def rebuild_row_metadata(self) -> None:
        self.row_position_by_id = {}
        self.row_diff_cache = {}
        current_positions: dict[str, int] = defaultdict(int)
        for row in self.rows:
            idx = current_positions[row.location]
            self.row_position_by_id[row.row_id] = idx
            base_attrs = self.base_location_pops.get(row.location, [])
            if idx < len(base_attrs):
                base_row = base_attrs[idx]
                differs = not pop_attrs_equal(row.attrs, base_row)
                base_summary = "" if not differs else format_attrs_summary(base_row)
            else:
                differs = True
                base_summary = "<missing in basegame>"
            self.row_diff_cache[row.row_id] = (differs, base_summary)
            current_positions[row.location] += 1

    def get_diff_info(self, row: PopRow) -> tuple[bool, str]:
        return self.row_diff_cache.get(row.row_id, (False, ""))

    def get_table_value(self, row: PopRow, column: str, geo: GeoInfo) -> str:
        if column == "differs":
            return "yes" if self.get_diff_info(row)[0] else ""
        if column == "basegame":
            return self.get_diff_info(row)[1]
        return row.get_value(column, geo)

    def update_filter_options(self) -> None:
        """Rebuild the combobox choices for each filter field.

        The available values for a field are drawn from the pop rows but are
        restricted by any selections made on ancestor fields.  Both include and
        exclude selections are taken into account so that children only offer
        options that actually exist under the current parent constraints.
        """
        parent_map = {
            "superregion": "continent",
            "region": "superregion",
            "area": "region",
            "province": "area",
            "location": "province",
        }

        # build ancestor lists for each field for fast lookup
        ancestor_map: dict[str, list[str]] = {}
        for field in FILTER_FIELDS:
            ancestors: list[str] = []
            p = parent_map.get(field)
            while p:
                ancestors.append(p)
                p = parent_map.get(p)
            ancestor_map[field] = ancestors

        for field in FILTER_FIELDS:
            values: set[str] = set()
            for row in self.rows:
                geo = self.location_geo.get(row.location, GeoInfo())
                if field == "location":
                    value = row.location
                else:
                    value = getattr(geo, field)
                if not value:
                    continue

                # Ancestors can restrict the candidate rows in three ways:
                # * a value is selected in the combobox itself (filter_vars)
                # * a value has been added to the include set
                # * a value has been added to the exclude set
                # The combobox selection acts like a provisional include; it
                # affects the options of sibling/descendant fields immediately
                # without the user having to press an "Include" button.
                skip = False
                for anc in ancestor_map[field]:
                    # determine the row's value for this ancestor
                    anc_val = row.location if anc == "location" else getattr(geo, anc)
                    if anc_val == "":
                        continue
                    # filter_vars selection (temporary include)
                    fv = self.filter_vars.get(anc).get()
                    if fv and anc_val != fv:
                        skip = True
                        break
                    inc_vals = self.include_values.get(anc, set())
                    if inc_vals and anc_val not in inc_vals:
                        skip = True
                        break
                    exc_vals = self.exclude_values.get(anc, set())
                    if exc_vals and anc_val in exc_vals:
                        skip = True
                        break
                if skip:
                    continue

                values.add(value)

            values = sorted(values - {""})
            combo = getattr(self, f"{field}_combo")
            current = self.filter_vars[field].get()
            combo["values"] = [""] + values
            if current not in combo["values"]:
                self.filter_vars[field].set("")
            self.update_selection_summary(field)

    def clear_filters(self) -> None:
        for var in self.filter_vars.values():
            var.set("")
        for field in FILTER_FIELDS:
            self.include_values[field].clear()
            self.exclude_values[field].clear()
            self.update_selection_summary(field)
        self.update_filter_options()
        self.refresh_table()

    def on_filter_change(self, _event=None) -> None:
        # when the user selects a value in a combobox we need to rebuild the
        # other comboboxes so that children are restricted by any newly chosen
        # parent value.  Afterwards refresh the visible rows.
        self.update_filter_options()
        self.refresh_table()

    def add_selection(self, field: str, mode: str) -> None:
        value = self.filter_vars[field].get()
        if not value:
            return
        target = self.include_values if mode == "include" else self.exclude_values
        other = self.exclude_values if mode == "include" else self.include_values
        target[field].add(value)
        other[field].discard(value)
        self.update_selection_summary(field)
        self.update_filter_options()
        self.refresh_table()

    def clear_selection(self, field: str) -> None:
        self.include_values[field].clear()
        self.exclude_values[field].clear()
        self.update_selection_summary(field)
        self.update_filter_options()
        self.refresh_table()

    def update_selection_summary(self, field: str) -> None:
        include_text = ", ".join(sorted(self.include_values[field])) or "-"
        exclude_text = ", ".join(sorted(self.exclude_values[field])) or "-"
        label = getattr(self, f"{field}_summary")
        label.configure(text=f"Include: {include_text} | Exclude: {exclude_text}")

    def row_matches_selection(self, row: PopRow, selections: dict[str, set[str]]) -> bool:
        geo = self.get_geo(row.location)
        for field, values in selections.items():
            if not values:
                continue
            candidate = row.location if field == "location" else getattr(geo, field)
            if candidate in values:
                return True
        return False

    def get_geo(self, location: str) -> GeoInfo:
        return self.location_geo.get(location, GeoInfo())

    def iter_filtered_rows(self) -> list[PopRow]:
        filtered = []
        has_includes = any(values for values in self.include_values.values())
        for row in self.rows:
            include_match = self.row_matches_selection(row, self.include_values) if has_includes else True
            exclude_match = self.row_matches_selection(row, self.exclude_values)
            if include_match and not exclude_match:
                filtered.append(row)
        return filtered

    def copy_matching_locations(self) -> None:
        locations = []
        seen = set()
        for row in self.iter_filtered_rows():
            if row.location not in seen:
                seen.add(row.location)
                locations.append(row.location)
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(locations))
        self.status_var.set(f"Copied {len(locations)} matching locations to the clipboard.")

    def apply_batch_size_edit(self) -> None:
        try:
            multiplier = float(self.batch_mult_var.get().strip())
            addition = float(self.batch_add_var.get().strip())
        except ValueError:
            messagebox.showerror("Invalid batch edit", "Multiplier and addition must be numeric.")
            return

        filtered = self.iter_filtered_rows()
        if not filtered:
            self.status_var.set("No filtered rows to edit.")
            return

        changed = 0
        clamped = 0
        for row in filtered:
            old_raw = row.attrs.get("size", "")
            try:
                old_size = float(old_raw)
            except ValueError:
                continue
            new_size = old_size * multiplier + addition
            if new_size < 0:
                new_size = 0.0
                clamped += 1
            row.attrs["size"] = f"{new_size:.3f}"
            changed += 1

        self.rebuild_row_metadata()
        self.refresh_table()
        clamp_suffix = f" {clamped} row(s) were clamped to 0." if clamped else ""
        self.status_var.set(
            f"Updated size on {changed} filtered row(s) with multiplier={multiplier} and addition={addition}.{clamp_suffix}"
        )

    def sort_by(self, column: str) -> None:
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        self.refresh_table()

    def refresh_table(self) -> None:
        filtered = self.iter_filtered_rows()
        filtered.sort(
            key=lambda row: self.sort_key(row, self.sort_column),
            reverse=self.sort_reverse,
        )
        visible = filtered[:MAX_VISIBLE_ROWS]
        self.tree.delete(*self.tree.get_children())
        self.item_to_row.clear()
        for row in visible:
            geo = self.get_geo(row.location)
            values = [self.get_table_value(row, column, geo) for column in TABLE_COLUMNS]
            differs, _ = self.get_diff_info(row)
            item = self.tree.insert("", "end", values=values, tags=("differs",) if differs else ())
            self.item_to_row[item] = row
        suffix = ""
        if len(filtered) > MAX_VISIBLE_ROWS:
            suffix = f" Showing first {MAX_VISIBLE_ROWS}; narrow filters to edit the rest."
        unique_locations = len({row.location for row in filtered})
        total_pop = sum(float(row.attrs.get("size", "0")) for row in filtered)
        # display three decimals for the total population
        self.status_var.set(
            f"{len(filtered)} matching pop rows across {unique_locations} locations, total pop: {total_pop:.3f}.{suffix}"
        )

    def sort_key(self, row: PopRow, column: str):
        geo = self.get_geo(row.location)
        value = self.get_table_value(row, column, geo)
        if column == "size":
            try:
                return float(value)
            except ValueError:
                return 0.0
        if column == "differs":
            return 1 if value else 0
        return value.lower()

    def begin_edit_cell(self, event) -> None:
        item = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        if not item or not column_id:
            return
        column = TABLE_COLUMNS[int(column_id.replace("#", "")) - 1]
        if column not in EDITABLE_COLUMNS:
            return
        bbox = self.tree.bbox(item, column_id)
        if not bbox:
            return
        row = self.item_to_row[item]
        old_value = self.get_table_value(row, column, self.get_geo(row.location))
        editor = ttk.Entry(self.tree)
        editor.insert(0, old_value)
        editor.select_range(0, tk.END)
        editor.focus()
        editor.place(x=bbox[0], y=bbox[1], width=bbox[2], height=bbox[3])

        def finish(_event=None, save=True):
            new_value = editor.get().strip()
            editor.destroy()
            if save:
                try:
                    self.apply_edit(row, column, new_value)
                    self.rebuild_row_metadata()
                    self.refresh_table()
                except Exception as exc:
                    messagebox.showerror("Invalid edit", str(exc))

        editor.bind("<Return>", finish)
        editor.bind("<Escape>", lambda _e: finish(save=False))
        editor.bind("<FocusOut>", finish)

    def apply_edit(self, row: PopRow, column: str, value: str) -> None:
        if column == "location":
            if value not in self.location_geo:
                raise ValueError(f"Unknown location '{value}' in definitions file.")
            row.location = value
            if value not in self.location_order:
                self.location_order.append(value)
            return
        if column == "extra":
            extras = OrderedDict()
            if value:
                for part in value.split(";"):
                    part = part.strip()
                    if not part:
                        continue
                    if "=" not in part:
                        raise ValueError("Extra attributes must use 'key=value; key=value'.")
                    key, attr_value = [piece.strip() for piece in part.split("=", 1)]
                    if not key:
                        raise ValueError("Extra attribute key cannot be empty.")
                    extras[key] = attr_value
            updated = OrderedDict()
            for key in STD_ATTR_ORDER:
                if key in row.attrs:
                    updated[key] = row.attrs[key]
            updated.update(extras)
            row.attrs = updated
            return
        if column == "size":
            # ensure numeric and normalize to three decimals
            try:
                value = normalize_size(value)
            except ValueError as exc:
                raise ValueError("Size must be numeric.") from exc
        if column not in row.attrs and column not in STD_ATTR_ORDER:
            raise ValueError(f"Unsupported column '{column}'.")
        if value:
            if column in row.attrs:
                row.attrs[column] = value
            else:
                updated = OrderedDict()
                inserted = False
                for key in STD_ATTR_ORDER:
                    if key == column:
                        updated[key] = value
                        inserted = True
                    if key in row.attrs:
                        updated[key] = row.attrs[key]
                for key, attr_value in row.attrs.items():
                    if key not in updated:
                        updated[key] = attr_value
                if not inserted:
                    updated[column] = value
                row.attrs = updated
        elif column in row.attrs:
            del row.attrs[column]

    def save_data(self) -> None:
        try:
            mod_folder_path = Path(self.path_vars["mod_folder"].get()).expanduser()
            output_path = mod_folder_path / "main_menu/setup/start/06_pops.txt"
            backup_path = output_path.with_suffix(output_path.suffix + ".bak")
            if output_path.exists():
                backup_path.write_text(output_path.read_text(encoding="utf-8-sig"), encoding="utf-8")

            grouped: dict[str, list[PopRow]] = defaultdict(list)
            for row in self.rows:
                grouped[row.location].append(row)

            ordered_locations = list(self.location_order)
            for row in self.rows:
                if row.location not in ordered_locations:
                    ordered_locations.append(row.location)

            lines = ["locations={", ""]
            for location in ordered_locations:
                lines.append(f"{location} = {{")
                for row in grouped.get(location, []):
                    attrs = dict(row.attrs)
                    if "size" in attrs:
                        attrs["size"] = normalize_size(attrs["size"])
                    lines.append(format_pop_line(attrs))
                lines.append("}")
            lines.append("}")
            output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.rebuild_row_metadata()
            self.status_var.set(f"Saved {len(self.rows)} pop rows to {output_path} (backup: {backup_path.name}).")
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            self.status_var.set("Save failed.")


def main() -> None:
    settings = load_settings()
    if not settings:
        settings = prompt_for_paths()
    base_game_path = Path(settings.get("base_game", "")).expanduser()
    mod_folder_path = Path(settings.get("mod_folder", REPO_ROOT)).expanduser()

    root = tk.Tk()
    app = PopEditorApp(root, base_game_path, mod_folder_path)
    root.mainloop()


if __name__ == "__main__":
    main()
