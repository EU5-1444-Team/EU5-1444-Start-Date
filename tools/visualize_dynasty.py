#!/usr/bin/env python3
"""Visualize a dynasty family tree from the game's character file using Graphviz.

Usage:
  python3 visualize_dynasty.py <dynasty> [--file PATH] [--engine dot]

The script looks for character blocks in `main_menu/setup/start/05_characters.txt` by default,
extracts `father`/`mother` fields and `dynasty` (or identifier prefixes), and draws a directed
graph (parent -> child). It will open the generated graph using Graphviz's viewer.

Requirements:
  - Python package `graphviz` (pip install graphviz)
  - Graphviz system binaries (dot) available in PATH
"""

import argparse
import os
import re
import sys
from graphviz import Digraph


def parse_characters(file_path):
    """Parse the characters file into a dict of id -> block_text."""
    chars = {}
    if not os.path.isfile(file_path):
        raise FileNotFoundError(file_path)

    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        data = f.read()

    # Find top-level entries like: identifier = { ...balanced braces... }
    pos = 0
    length = len(data)
    start_re = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=\s*\{", re.M)

    while True:
        m = start_re.search(data, pos)
        if not m:
            break
        ident = m.group(1)
        brace_open = m.end() - 1
        i = brace_open
        depth = 0
        # move to the first '{'
        while i < length and data[i] != '{':
            i += 1
        if i >= length:
            break
        depth = 1
        i += 1
        start = i
        while i < length and depth > 0:
            c = data[i]
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
            i += 1
        block = data[m.end():i]
        chars[ident] = block
        pos = i

    return chars


def parse_inner_characters(block_text):
    """Given the text of `character_db = { ... }`, parse inner identifier blocks and return dict."""
    inner = {}
    pos = 0
    length = len(block_text)
    start_re = re.compile(r"\s*([A-Za-z0-9_]+)\s*=\s*\{", re.M)
    while True:
        m = start_re.search(block_text, pos)
        if not m:
            break
        ident = m.group(1)
        i = m.end() - 1
        # advance to '{'
        while i < length and block_text[i] != '{':
            i += 1
        if i >= length:
            break
        depth = 1
        i += 1
        start = i
        while i < length and depth > 0:
            c = block_text[i]
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
            i += 1
        block = block_text[m.end():i]
        inner[ident] = block
        pos = i
    return inner


def extract_fields(block_text):
    """Extract name, dynasty, father, mother from a block of text."""
    # name may be a quoted string or nested block; fall back to identifier
    name = None
    m = re.search(r'name\s*=\s*"([^"]+)"', block_text)
    if m:
        name = m.group(1)
    else:
        # try simple 'first_name' or 'last_name'
        m2 = re.search(r'first_name\s*=\s*"([^"]+)"', block_text)
        if m2:
            name = m2.group(1)

    # try unquoted name identifiers like: name = name_takauji
    if not name:
        m3 = re.search(r'name\s*=\s*([A-Za-z0-9_]+)', block_text)
        if m3:
            name = m3.group(1)

    # try nested first_name blocks: first_name = { name = name_takauji }
    if not name:
        mfn = re.search(r'first_name\s*=\s*\{[^}]*name\s*=\s*([A-Za-z0-9_]+)', block_text)
        if mfn:
            name = mfn.group(1)
        else:
            # or unquoted simple first_name: first_name = name_takauji
            mfn2 = re.search(r'first_name\s*=\s*([A-Za-z0-9_]+)', block_text)
            if mfn2:
                name = mfn2.group(1)

    # dynasty may be numeric or identifier
    dynasty = None
    m = re.search(r'dynasty\s*=\s*"?([A-Za-z0-9_]+)"?', block_text)
    if m:
        dynasty = m.group(1)

    father = None
    mother = None
    m = re.search(r'father\s*=\s*([A-Za-z0-9_]+)', block_text)
    if m:
        father = m.group(1)
    m = re.search(r'mother\s*=\s*([A-Za-z0-9_]+)', block_text)
    if m:
        mother = m.group(1)

    # collect spouse identifiers (may be multiple). Handle simple and nested forms.
    spouses = []
    # simple occurrences: spouse = jap_someone
    for ms in re.findall(r'spouse\s*=\s*"?([A-Za-z0-9_]+)"?', block_text):
        spouses.append(ms)
    # nested spouse blocks: spouse = { id = jap_someone }
    for ms in re.findall(r'spouse\s*=\s*\{[^}]*?id\s*=\s*"?([A-Za-z0-9_]+)"?[^}]*\}', block_text):
        spouses.append(ms)
    # dedupe while preserving order
    seen_sp = set()
    spouses_clean = []
    for s in spouses:
        if s not in seen_sp:
            spouses_clean.append(s)
            seen_sp.add(s)

    return {'name': name, 'dynasty': dynasty, 'father': father, 'mother': mother, 'spouses': spouses_clean}


def find_localisation_files(base_dir):
    """Yield localisation file paths under base_dir searching common folders."""
    paths = []
    for root, dirs, files in os.walk(base_dir):
        # only consider directories that contain 'localiz' to keep it quick
        if 'localization' in root.lower() or 'localisation' in root.lower() or root.lower().endswith('localization'):
            for fn in files:
                if fn.lower().endswith(('.yml', '.yaml', '.txt')):
                    paths.append(os.path.join(root, fn))
    return paths


def load_localisations(mod_dir, base_game_dir):
    """Load localisation mappings, preferring mod files over base game files.

    Returns dict: key -> localized string
    """
    loc = {}
    seen = set()
    eng_loc = {}

    # helper to parse a single file (returns generator of k,v)
    def parse_file(path):
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                data = f.read()
        except Exception:
            return

        # If the file is an english localisation file (filename contains l_english)
        # or contains an 'l_english:' header, prefer parsing only that block.
        in_english = False
        start_pos = 0
        basename = os.path.basename(path).lower()
        if 'l_english' in basename or re.search(r'^\s*l_english\s*:\s*$', data, re.M):
            # find the 'l_english:' header if present
            m = re.search(r'^\s*l_english\s*:\s*$', data, re.M)
            if m:
                in_english = True
                start_pos = m.end()
            else:
                # filename indicates english but no explicit header; parse whole file
                in_english = True
                start_pos = 0

        if in_english:
            # iterate only over lines in the english block
            for line in data[start_pos:].splitlines():
                # stop if we hit another top-level language header like 'l_french:'
                if re.match(r'^\s*l_[a-zA-Z0-9_]+\s*:\s*$', line):
                    break
                # only consider indented/local keys (they normally start with whitespace)
                m = re.match(r'^\s*([A-Za-z0-9_\.]+)\s*:\s*\d*\s*"([^"]+)"', line)
                if not m:
                    m = re.match(r'^\s*([A-Za-z0-9_\.]+)\s*:\s*"([^"]+)"', line)
                if m:
                    k = m.group(1)
                    v = m.group(2)
                    yield k, v
            return

        # Fallback: parse the whole file for key: "Text" patterns (non-english files)
        for line in data.splitlines():
            m = re.match(r'^\s*([A-Za-z0-9_\.]+)\s*:\s*\d*\s*"([^"]+)"', line)
            if not m:
                m = re.match(r'^\s*([A-Za-z0-9_\.]+)\s*:\s*"([^"]+)"', line)
            if m:
                k = m.group(1)
                v = m.group(2)
                yield k, v

    # Parse base game files first, then mod files as a fallback.
    base_paths = []
    mod_paths = []
    if base_game_dir:
        base_paths = find_localisation_files(base_game_dir)
    if mod_dir:
        mod_paths = find_localisation_files(mod_dir)

    def process_paths(paths, primary_loc, primary_eng_loc, allow_override=False):
        for p in paths:
            is_english_file = ('l_english' in os.path.basename(p).lower())
            try:
                with open(p, 'r', encoding='utf-8', errors='ignore') as pf:
                    sample = pf.read(4096)
                    if re.search(r'^\s*l_english\s*:\s*$', sample, re.M):
                        is_english_file = True
            except Exception:
                pass

            if is_english_file:
                for k, v in parse_file(p):
                    if allow_override or k not in primary_eng_loc:
                        primary_eng_loc.setdefault(k, v)
            else:
                for k, v in parse_file(p):
                    if allow_override or k not in primary_loc:
                        primary_loc.setdefault(k, v)

    # First load base-game files into loc / eng_loc
    process_paths(base_paths, loc, eng_loc, allow_override=False)
    # Then load mod files, but only set keys missing from base (fallback)
    process_paths(mod_paths, loc, eng_loc, allow_override=False)

    # Ensure english entries override general ones
    loc.update(eng_loc)
    return loc


def build_graph(char_blocks, selected_ids, fields, localisations=None, dynasty_query=None):
    g = Digraph(comment='Dynasty tree')
    # layout hints to reduce edge crossings
    g.attr(overlap='false')
    g.attr(splines='true')
    g.attr(nodesep='0.35')
    g.attr(ranksep='0.8')
    g.attr('node', shape='box')

    # helper to pick a label using name field or localisation keys
    def label_for(cid, dynasty_query=None):
        info = fields.get(cid, {})


        # Determine a human-readable character name
        char_name = None
        # prefer localisation lookup for extracted name identifier
        n = info.get('name')
        if n and localisations and n in localisations:
            char_name = localisations.get(n)
        elif n:
            # if no localisation available, use the raw extracted name
            char_name = n

        # if we still don't have a pretty name, try localisation entries for common keys
        if not char_name and localisations:
            for key in (cid, cid + '_title', cid + '_name', cid + '_desc'):
                if key in localisations:
                    char_name = localisations[key]
                    break

        # Determine dynasty display name: prefer the character's dynasty field, else use the provided query
        dyn_id = info.get('dynasty') or (dynasty_query or '')
        dyn_display = dyn_id
        if localisations and dyn_id and dyn_id in localisations:
            dyn_display = localisations[dyn_id]

        # Build final label placing dynasty (last name) after the character name
        if char_name and dyn_display:
            return f"{char_name} {dyn_display}\n({cid})"
        if char_name:
            return f"{char_name}\n({cid})"
        if dyn_display:
            return f"{dyn_display} {cid}\n({cid})"
        return cid

    # Add nodes
    for cid in selected_ids:
        g.node(cid, label_for(cid, dynasty_query=dynasty_query))

    # Add parent nodes if referenced
    # Build spouse pairs and marriage nodes to route children through, reducing crossings.
    spouse_edges = set()
    for cid in selected_ids:
        info = fields.get(cid, {})
        spouses = info.get('spouses', []) or []
        for s in spouses:
            if not s:
                continue
            # ensure nodes exist
            if s not in fields:
                g.node(s, label_for(s, dynasty_query=dynasty_query))
            pair = tuple(sorted((cid, s)))
            spouse_edges.add(pair)

    # create marriage node for each spouse pair
    marriage_node_for_pair = {}
    for i, (a, b) in enumerate(sorted(spouse_edges)):
        mn = f"marriage_{i}_{a}_{b}"
        marriage_node_for_pair[(a, b)] = mn
        # small invisible point to act as marriage connector
        g.node(mn, label='', shape='point', width='0.01', height='0.01')
        # keep spouses on same rank
        with g.subgraph(name=f"rank_same_{i}") as s:
            s.attr(rank='same')
            s.node(a)
            s.node(b)
        # connect spouses to marriage node
        g.edge(a, mn, dir='none', constraint='true')
        g.edge(b, mn, dir='none', constraint='true')

    # Add father->child via marriage node if both parents present, otherwise direct father->child
    for cid in selected_ids:
        info = fields.get(cid, {})
        f = info.get('father')
        m = info.get('mother')
        if f and m:
            pair = tuple(sorted((f, m)))
            mn = marriage_node_for_pair.get(pair)
            if mn:
                g.edge(mn, cid)
                continue
        # fallback: use father only if present
        if f:
            if f not in fields:
                g.node(f, label_for(f, dynasty_query=dynasty_query))
            g.edge(f, cid)

    return g


def select_by_dynasty(chars, dynasty_query):
    # Build fields for all characters
    fields = {}
    for cid, block in chars.items():
        fields[cid] = extract_fields(block)
    selected = set()
    q = dynasty_query.strip()

    # Exact-match policy: only select characters whose id exactly equals the query
    # or whose extracted `dynasty` field exactly equals the query.
    for cid, info in fields.items():
        if cid == q:
            selected.add(cid)
            continue
        d = info.get('dynasty')
        if d and str(d).strip() == q:
            selected.add(cid)
            continue

        # As a final fallback, check for an explicit exact assignment in the block text
        block_text = chars.get(cid, '')
        try:
            if re.search(r'dynasty\s*=\s*"?%s"?' % re.escape(q), block_text):
                selected.add(cid)
                continue
        except re.error:
            # if regex fails for unexpected input, fall back to exact substring match
            if q and q == q and q in block_text:
                selected.add(cid)
                continue

    return selected, fields


def main():
    p = argparse.ArgumentParser(description='Visualize dynasty family tree')
    p.add_argument('dynasty', nargs='?', help='Dynasty identifier or prefix to visualize')
    p.add_argument('--file', '-f', default=os.path.join('main_menu', 'setup', 'start', '05_characters.txt'),
                   help='Path to characters file (relative to repo root or absolute)')
    p.add_argument('--engine', default='dot', help='Graphviz engine (dot, neato, etc)')
    p.add_argument('--format', choices=['png','pdf','svg','jpg'], default='png',
                   help='Output format (default: png)')
    p.add_argument('--no-view', action='store_true', help="Don't automatically open the viewer")
    p.add_argument('--debug', action='store_true', help='Print debug selection info')
    p.add_argument('--set-config', action='store_true', help='Open dialog to set base game and mod directories and exit')
    args = p.parse_args()

    # Ensure a sensible default for output format (fixes mimetype complaints when only dynasty provided)
    try:
        if not getattr(args, 'format', None):
            args.format = 'png'
    except Exception:
        args.format = 'png'

    file_path = args.file
    if not os.path.isabs(file_path):
        # assume workspace root is this script's parent folder
        script_dir = os.path.dirname(os.path.abspath(__file__))
        repo_root = os.path.dirname(script_dir)
        file_path = os.path.join(repo_root, file_path)

    # config file lives next to this script in tools (use existing settings.json)
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')


    def load_config():
        try:
            import json
            if os.path.isfile(config_path):
                with open(config_path, 'r', encoding='utf-8') as cf:
                    data = json.load(cf)
                    # accept either legacy names or desired keys
                    return {
                        'base_game_dir': data.get('base_game') or data.get('base_game_dir') or '',
                        'mod_dir': data.get('mod_folder') or data.get('mod_dir') or ''
                    }
        except Exception:
            pass
        return {'base_game_dir': '', 'mod_dir': ''}

    def save_config(cfg):
        try:
            import json
            out = {'base_game': cfg.get('base_game_dir',''), 'mod_folder': cfg.get('mod_dir','')}
            with open(config_path, 'w', encoding='utf-8') as cf:
                json.dump(out, cf, indent=2)
        except Exception:
            pass

    cfg = load_config()

    def ask_config_gui(initial_cfg):
        # use tkinter dialogs to ask for base/mod dirs and dynasty
        try:
            import tkinter as tk
            from tkinter import simpledialog, filedialog
            root = tk.Tk()
            root.withdraw()

            base = filedialog.askdirectory(title='Select base game root directory', initialdir=initial_cfg.get('base_game_dir', '.'))
            if not base:
                root.destroy()
                return None
            mod = filedialog.askdirectory(title='Select mod directory (or cancel to use base game)', initialdir=initial_cfg.get('mod_dir', base))
            # ask dynasty name
            dynasty = simpledialog.askstring('Dynasty', 'Enter dynasty identifier (e.g. ashikaga_dynasty):')
            root.destroy()
            if not dynasty:
                return None
            return {'base_game_dir': base, 'mod_dir': mod or '', 'dynasty': dynasty}
        except Exception:
            return None

    def ask_config_console(initial_cfg):
        print('Running interactive setup (console)')
        base = input(f"Base game directory [{initial_cfg.get('base_game_dir','')}]: ").strip() or initial_cfg.get('base_game_dir','')
        if not base:
            return None
        mod = input(f"Mod directory [{initial_cfg.get('mod_dir','')}]: ").strip() or initial_cfg.get('mod_dir','')
        dynasty = input('Dynasty identifier (e.g. ashikaga_dynasty): ').strip()
        if not dynasty:
            return None
        return {'base_game_dir': base, 'mod_dir': mod, 'dynasty': dynasty}

    # If user requested config dialog only, run it and exit
    if args.set_config:
        # prefer GUI, fallback to console
        newcfg = ask_config_gui(cfg) or ask_config_console(cfg)
        if newcfg:
            save_config({'base_game_dir': newcfg.get('base_game_dir',''), 'mod_dir': newcfg.get('mod_dir','')})
            print('Config saved to', config_path)
            sys.exit(0)
        else:
            print('No changes made.')
            sys.exit(1)

    # If dynasty argument missing, prompt user via dialog or console, also set base/mod dirs if missing
    if not args.dynasty:
        # ensure base/mod present in config; if missing ask user to set them
        if not cfg.get('base_game_dir'):
            newcfg = ask_config_gui(cfg) or ask_config_console(cfg)
            if newcfg:
                cfg.update({'base_game_dir': newcfg.get('base_game_dir',''), 'mod_dir': newcfg.get('mod_dir','')})
                save_config(cfg)
            else:
                print('Setup cancelled.', file=sys.stderr)
                sys.exit(2)

        # ask for dynasty via dialog/console
        dyn = None
        try:
            import tkinter as tk
            from tkinter import simpledialog
            root = tk.Tk()
            root.withdraw()
            dyn = simpledialog.askstring('Dynasty', 'Enter dynasty identifier (e.g. ashikaga_dynasty):')
            root.destroy()
        except Exception:
            pass
        if not dyn:
            # fallback to console
            dyn = input('Dynasty identifier (e.g. ashikaga_dynasty): ').strip()
        if not dyn:
            print('No dynasty provided.', file=sys.stderr)
            sys.exit(2)
        args.dynasty = dyn

        # if config has mod/base dirs, prefer mod characters file
        mod_chars = os.path.join(cfg.get('mod_dir',''), 'main_menu', 'setup', 'start', '05_characters.txt')
        base_chars = os.path.join(cfg.get('base_game_dir',''), 'main_menu', 'setup', 'start', '05_characters.txt')
        if cfg.get('mod_dir') and os.path.isfile(mod_chars):
            file_path = mod_chars
        elif cfg.get('base_game_dir') and os.path.isfile(base_chars):
            file_path = base_chars

    # Show a small non-blocking 'Generating image...' dialog immediately after the dynasty
    # has been provided so the user sees progress right away.
    render_root = None
    render_win = None
    try:
        import tkinter as tk
        from tkinter import Toplevel, Label
        render_root = tk.Tk()
        render_root.withdraw()
        render_win = Toplevel(render_root)
        render_win.title('Generating image')
        Label(render_win, text='Generating image... Please wait.').pack(padx=16, pady=12)
        render_win.update_idletasks()
        render_win.update()
    except Exception:
        render_root = None
        render_win = None

    try:
        chars = parse_characters(file_path)
    except FileNotFoundError:
        # close progress dialog if open
        try:
            if render_win:
                render_win.destroy()
            if render_root:
                render_root.destroy()
        except Exception:
            pass
        print('Characters file not found:', file_path, file=sys.stderr)
        sys.exit(2)

    # If the file defines a top-level `character_db = { ... }`, expand inner entries
    if 'character_db' in chars and len(chars) == 1:
        inner = parse_inner_characters(chars['character_db'])
        if inner:
            chars = inner

    selected, fields = select_by_dynasty(chars, args.dynasty)
    if args.debug:
        print(f'Total characters parsed: {len(fields)}')
        print(f'Initial selection size: {len(selected)}')
        # print some selected ids
        for i, cid in enumerate(sorted(selected)):
            if i < 200:
                info = fields.get(cid, {})
                print(f'  SELECTED: {cid}  name={info.get("name")!r} dynasty={info.get("dynasty")!r} father={info.get("father")!r} mother={info.get("mother")!r}')
        if len(selected) > 200:
            print(f'  ...and {len(selected)-200} more')
    if not selected:
        # Show an error dialog if possible, otherwise print to stderr
        # close progress dialog if open
        try:
            if render_win:
                render_win.destroy()
            if render_root:
                render_root.destroy()
        except Exception:
            pass
        try:
            import tkinter as tk
            from tkinter import messagebox
            root_err = tk.Tk()
            root_err.withdraw()
            messagebox.showerror('Dynasty Not Found', f'No characters found for dynasty "{args.dynasty}".\nCheck the identifier and try again.')
            try:
                root_err.destroy()
            except Exception:
                pass
        except Exception:
            print(f'No characters found for dynasty "{args.dynasty}". Check the identifier and try again.', file=sys.stderr)
        sys.exit(3)

    # Expand selection to include immediate parents so the tree shows connections
    expanded = set(selected)
    for cid in list(selected):
        info = fields.get(cid, {})
        for parent in (info.get('father'), info.get('mother')):
            if parent:
                expanded.add(parent)

    # load localisations from mod then base game (cfg from earlier)
    loc_map = load_localisations(cfg.get('mod_dir',''), cfg.get('base_game_dir',''))
    if args.debug:
        print(f'Localisation entries loaded: {len(loc_map)}')
        # show a few matches for selected ids
        for cid in list(selected)[:40]:
            for key in (cid, cid + '_title', cid + '_name', cid + '_desc'):
                if key in loc_map:
                    print(f'  LOC: {key} -> {loc_map[key]}')

    g = build_graph(chars, expanded, fields, localisations=loc_map, dynasty_query=args.dynasty)
    g.engine = args.engine
    # Respect requested output format (default: png)
    try:
        g.format = args.format or 'png'
    except Exception:
        g.format = 'png'

    # output into the same directory as this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    outname = os.path.join(script_dir, args.dynasty)
    # progress dialog was created earlier (immediately after dynasty input)

    try:
        if args.no_view:
            filepath = g.render(filename=outname, cleanup=True)
            print('Graph written to', filepath)
        else:
            # Render to file, then attempt to open it with `xdg-open` while
            # suppressing stdout/stderr from the viewer to avoid desktop warnings
            try:
                filepath = g.render(filename=outname, cleanup=True)
            except Exception as e:
                # close rendering dialog if open
                try:
                    if render_win:
                        render_win.destroy()
                    if render_root:
                        render_root.destroy()
                except Exception:
                    pass
                print('Failed to render graph for viewing:', e, file=sys.stderr)
                print('Graph not written.', file=sys.stderr)
                sys.exit(4)

            # close rendering dialog if open
            try:
                if render_win:
                    render_win.destroy()
                if render_root:
                    render_root.destroy()
            except Exception:
                pass

            print('Graph written to', filepath)
            # Ask the user whether to open the image (GUI dialog if available)
            open_now = True
            try:
                import tkinter as tk
                from tkinter import messagebox
                root = tk.Tk()
                root.withdraw()
                try:
                    open_now = messagebox.askyesno('Dynasty Rendered', f'Graph written to:\n{filepath}\n\nOpen now?')
                finally:
                    try:
                        root.destroy()
                    except Exception:
                        pass
            except Exception:
                # no GUI available; default to opening
                open_now = True

            try:
                import shutil
                import subprocess
                devnull = open(os.devnull, 'wb')
                xdg = shutil.which('xdg-open')
                if open_now and xdg:
                    subprocess.Popen([xdg, filepath], stdout=devnull, stderr=devnull)
            except Exception:
                # ignore viewer errors; file exists on disk
                pass
    except Exception as e:
        print('Failed to render graph:', e, file=sys.stderr)
        print('Ensure Graphviz is installed and `dot` is in your PATH.', file=sys.stderr)
        sys.exit(4)


if __name__ == '__main__':
    main()
