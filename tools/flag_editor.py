#!/usr/bin/env python3
"""EU5 Flag Editor - simple PySide6 app
See README.md for usage/requirements.
"""
from __future__ import annotations  # enables X|Y union syntax and built-in generics on Python 3.8+
import sys
import os
import json
import tempfile
import platform
from pathlib import Path
from functools import partial

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QPushButton,
    QFileDialog,
    QGraphicsView,
    QGraphicsScene,
    QGraphicsPixmapItem,
    QSplitter,
    QSlider,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QInputDialog,
    QColorDialog,
    QGraphicsItem,
    QAbstractItemView,
    QSpinBox,
)
from PySide6.QtGui import QPixmap, QImage, Qt, QDrag, QPainter, QColor
from PySide6.QtGui import QIcon
from PySide6.QtCore import QByteArray, QMimeData, QPointF, QSize

from PIL import Image, ImageOps
import re
import colorsys
import math
import hashlib


class DraggableListWidget(QListWidget):
    def __init__(self, asset_type: str = "colored_emblems", *args, **kwargs):
        super().__init__(*args, **kwargs)
        # asset_type is the key in MainWindow.assets this list represents
        self.asset_type = asset_type

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if not item:
            return
        name = item.text()
        # parent MainWindow stores assets dict; walk up to find it
        mw = self.window()
        path = None
        if hasattr(mw, "assets") and self.asset_type in mw.assets:
            path = mw.assets[self.asset_type].get(name)
        if not path:
            return
        # debug: starting drag suppressed
        drag = QDrag(self)
        md = QMimeData()
        payload = json.dumps({"path": str(path), "type": "colored"})
        # set type according to asset_type
        p = json.loads(payload)
        if self.asset_type == "textured_emblems":
            p["type"] = "textured"
        else:
            p["type"] = "colored"
        md.setData("application/x-eu5-asset", QByteArray(json.dumps(p).encode("utf-8")))
        drag.setMimeData(md)
        img = load_image(path)
        drag.setPixmap(pil2pixmap(img.resize((64, 64))))
        drag.exec(Qt.CopyAction)

    def mousePressEvent(self, event):
        # Let the base class process the event first (selection/drag logic)
        super().mousePressEvent(event)
        try:
            item = self.itemAt(event.pos())
            # only update current item if it's different to avoid unnecessary scrolling
            if item and item is not self.currentItem():
                self.setCurrentItem(item)
        except Exception:
            pass


class DropGraphicsView(QGraphicsView):
    def __init__(self, scene, owner=None):
        super().__init__(scene)
        self.owner = owner
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-eu5-asset"):
            # debug suppressed
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-eu5-asset"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not event.mimeData().hasFormat("application/x-eu5-asset"):
            event.ignore()
            return
        try:
            md = event.mimeData().data("application/x-eu5-asset")
            payload = json.loads(bytes(md).decode("utf-8"))
            path = Path(payload["path"])
            # drop payload received
            typ = payload.get("type", "colored")
            # compute drop scene position
            if hasattr(event, "position"):
                posf = event.position()
                vx, vy = posf.x(), posf.y()
            else:
                vp = event.pos()
                vx, vy = vp.x(), vp.y()
            scene_pos = self.mapToScene(int(vx), int(vy))
            if self.owner:
                self.owner.add_emblem_to_scene(path, typ, scene_pos)
                event.acceptProposedAction()
            else:
                # fallback: directly add
                pil = load_image(path)
                it = EmblemItem(path, pil, texture_type=typ)
                it.setPos(
                    scene_pos
                    - QPointF(it.pixmap().width() / 2, it.pixmap().height() / 2)
                )
                self.scene().addItem(it)
                event.acceptProposedAction()
        except Exception:
            event.ignore()

    def keyPressEvent(self, event):
        """Handle arrow keys to nudge selected emblems.

        Arrow keys move by 1px; Shift+arrow moves by 5px.
        """
        key = event.key()
        step = 1
        try:
            if event.modifiers() & Qt.ShiftModifier:
                step = 5
        except Exception:
            pass

        dx = dy = 0
        if key == Qt.Key_Left:
            dx = -step
        elif key == Qt.Key_Right:
            dx = step
        elif key == Qt.Key_Up:
            dy = -step
        elif key == Qt.Key_Down:
            dy = step
        else:
            return super().keyPressEvent(event)

        moved = False
        try:
            for it in self.scene().selectedItems():
                # EmblemItem may not be defined yet at import time; detect by attribute
                if hasattr(it, "image_path"):
                    it.setPos(it.pos() + QPointF(dx, dy))
                    moved = True
        except Exception:
            moved = False

        if moved:
            event.accept()
        else:
            super().keyPressEvent(event)


APP_DIR = Path(__file__).resolve().parents[1]
DEFAULT_BASE = ""  # leave empty; user picks
# settings.json lives in the same directory as this script (tools/ folder)
SETTINGS_FILE = Path(__file__).resolve().parent / "settings.json"
CANVAS_W, CANVAS_H = 384, 256

# Helper: load image via PIL (works with pillow-dds plugin if installed)


def load_image(path: Path):
    try:
        img = Image.open(path)
        img = img.convert("RGBA")
        return img
    except Exception as e:
        # fallback: create placeholder
        img = Image.new("RGBA", (64, 64), (200, 50, 50, 255))
        return img


def pil2pixmap(img: Image.Image):
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(data, img.width, img.height, QImage.Format_RGBA8888)
    if qimg.isNull():
        # Fallback for platforms where RGBA8888 isn't supported
        data = img.tobytes("raw", "BGRA")
        qimg = QImage(data, img.width, img.height, QImage.Format_ARGB32)
    return QPixmap.fromImage(qimg)


def _get_cache_dir() -> Path:
    try:
        if platform.system() == "Windows":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            return base / "eu5-flag-editor" / "cache" / "thumbs"
        elif platform.system() == "Darwin":
            return Path.home() / "Library" / "Caches" / "eu5-flag-editor" / "thumbs"
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        return base / "eu5-flag-editor" / "thumbs"
    except Exception:
        return Path(tempfile.gettempdir()) / "eu5-flag-editor" / "thumbs"

try:
    THUMB_CACHE_DIR = _get_cache_dir()
    THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    THUMB_CACHE_DIR = Path(tempfile.gettempdir()) / "eu5-flag-editor" / "thumbs"
    try:
        THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        THUMB_CACHE_DIR = None


def _thumb_cache_key(path: Path, size: tuple[int, int]):
    try:
        mtime = int(path.stat().st_mtime)
    except Exception:
        mtime = 0
    key = f"{str(path.resolve())}|{mtime}|{size[0]}x{size[1]}"
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()
    cache_parent = THUMB_CACHE_DIR if THUMB_CACHE_DIR is not None else Path(tempfile.gettempdir()) / "eu5-flag-editor" / "thumbs"
    return cache_parent / f"{h}.png"


def get_cached_pixmap(path: Path, max_size: tuple[int, int]):
    """Return QPixmap from cache or generate a thumbnail and cache it."""
    cache_path = _thumb_cache_key(path, max_size)
    if cache_path.exists():
        try:
            img = Image.open(cache_path).convert("RGBA")
            return pil2pixmap(img)
        except Exception:
            try:
                cache_path.unlink()
            except Exception:
                pass
    # generate and save
    try:
        img = Image.open(path)
        img = img.convert("RGBA")
        w, h = img.width, img.height
        tw, th = max_size
        nw = tw
        nh = int(tw * h / w) if w else th
        if nh > th:
            nh = th
            nw = int(th * w / h) if h else tw
        thumb = img.resize((max(1, nw), max(1, nh)), Image.LANCZOS)
        try:
            thumb.save(cache_path, format="PNG")
        except Exception:
            pass
        return pil2pixmap(thumb)
    except Exception:
        img = Image.new("RGBA", (max_size[0], max_size[1]), (200, 50, 50, 255))
        return pil2pixmap(img)


# Simple replacement of mask colors in colored emblems
# Use the exact mask RGBs observed in the game's colored emblem assets
# (these are treated as opaque mask pixels; transparent areas remain transparent)
MASK_COLORS = [
    (0, 0, 129),  # blue -> color1 (observed)
    (0, 252, 129),  # green/teal -> color2 (observed)
    (247, 0, 129),  # pink -> color3 (observed)
]

# enable detailed recolour debug by setting env var EU5_FLAG_DEBUG_COLORS=1
DEBUG_COLOR_RECOLOR = bool(os.environ.get("EU5_FLAG_DEBUG_COLORS"))


def recolor_colored_emblem(img: Image.Image, color_vals):
    # color_vals: list of (r,g,b) or None, length up to 3
    px = img.convert("RGBA")
    data = px.getdata()
    out = []
    # debug counters for mask hits
    mask_hits = {0: 0, 1: 0, 2: 0, None: 0}
    # Precompute mask vectors
    masks = MASK_COLORS
    # use euclidean distance threshold to allow for different mask RGB values (e.g., 129 blue)
    mask_thresh = 140.0
    mask_thresh_sq = mask_thresh * mask_thresh
    for p in data:
        r, g, b, a = p
        if a == 0:
            out.append((0, 0, 0, 0))
            continue
        # find nearest mask by squared distance
        best_idx = None
        best_ds = None
        for idx, mask in enumerate(masks):
            mr, mg, mb = mask
            ds = (r - mr) ** 2 + (g - mg) ** 2 + (b - mb) ** 2
            if best_ds is None or ds < best_ds:
                best_ds = ds
                best_idx = idx
        if best_ds is not None and best_ds <= mask_thresh_sq:
            idx = best_idx
            cv = (
                color_vals[idx]
                if idx < len(color_vals) and color_vals[idx] is not None
                else (r, g, b)
            )
            mask_hits[idx] = mask_hits.get(idx, 0) + 1
            out.append((cv[0], cv[1], cv[2], a))
        else:
            mask_hits[None] = mask_hits.get(None, 0) + 1
            out.append((r, g, b, a))
    res = Image.new("RGBA", px.size)
    res.putdata(out)
    if DEBUG_COLOR_RECOLOR:
        try:
            # compute most common output RGB colors (ignore alpha==0)
            freq = {}
            for (r, g, b, a) in out:
                if a == 0:
                    continue
                freq[(r, g, b)] = freq.get((r, g, b), 0) + 1
            top = sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:8]
            print(f"[RecolorDebug] mask_hits={mask_hits} color_vals={color_vals} top_colors={top}")
            # sample a few non-transparent pixels before/after for inspection
            sample_before = []
            sample_after = []
            for p_before, p_after in zip(data, out):
                if p_before[3] == 0:
                    continue
                sample_before.append((p_before[0], p_before[1], p_before[2]))
                sample_after.append((p_after[0], p_after[1], p_after[2]))
                if len(sample_before) >= 6:
                    break
            print(f"[RecolorDebug] sample_before={sample_before} sample_after={sample_after}")
        except Exception:
            pass
    return res


def _normalize_color_tuple(col):
    """Normalize a color tuple which may be (0-1 floats) or (0-255 ints).

    Returns a (r,g,b) tuple with integers 0-255 or None if input invalid/None.
    """
    if not col:
        return None
    try:
        # if floats in 0..1 range
        if all(isinstance(x, float) and 0.0 <= x <= 1.0 for x in col):
            return (int(col[0] * 255), int(col[1] * 255), int(col[2] * 255))
        # if already ints 0..255
        if all(isinstance(x, int) for x in col):
            return (int(col[0]), int(col[1]), int(col[2]))
        # fallback: try numeric conversion
        return (int(col[0]), int(col[1]), int(col[2]))
    except Exception:
        return None


def recolor_pattern(
    img: Image.Image, color1_rgb, color2_rgb, color3_rgb=None, masks=None
):
    """Recolor pattern by detecting mask colors robustly.

    Strategy:
    - Gather distinct non-transparent colors and their counts.
    - For each distinct color, check distance to canonical masks (red, yellow, white).
      If close enough, map to the corresponding slot (color1/color2).
    - If no canonical masks are found, fall back to mapping the most frequent
      non-background colors to color1 and color2.
    This handles patterns that use slightly different RGB mask values.
    """
    px = img.convert("RGBA")
    data = list(px.getdata())
    # collect non-transparent color counts
    counts = {}
    for r, g, b, a in data:
        if a == 0:
            continue
        key = (r, g, b)
        counts[key] = counts.get(key, 0) + 1

    # canonical mask colors: red->color1, yellow->color2, white->color3
    red_mask = (255, 0, 0)
    yellow_mask = (255, 255, 0)
    white_mask = (255, 255, 255)

    # mapping from actual color -> target slot (1,2,3)
    mapping = {}

    def dist_sq(c1, c2):
        return (c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2 + (c1[2] - c2[2]) ** 2

    # thresholds (squared distances)
    close_thresh = 60**2

    # If masks are provided (pattern-specific exact colors), use exact matching
    if masks and any(masks):
        red_m, yellow_m, white_m = masks
        for col in counts:
            if red_m and col == red_m:
                mapping[col] = 1
            elif yellow_m and col == yellow_m:
                mapping[col] = 2
            elif white_m and col == white_m:
                mapping[col] = 3
    else:
        for col in counts:
            if dist_sq(col, red_mask) <= close_thresh:
                mapping[col] = 1
            elif dist_sq(col, yellow_mask) <= close_thresh:
                mapping[col] = 2
            elif dist_sq(col, white_mask) <= close_thresh:
                # white maps to color3 by convention
                mapping[col] = 3

    # If no canonical masks detected, pick top-2 frequent colors as color1/color2
    if not any(v in (1, 2) for v in mapping.values()):
        # sort colors by freq descending
        freq_sorted = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        if freq_sorted:
            # choose top two frequent colors and assign darker -> color1, lighter -> color2
            top_colors = [freq_sorted[i][0] for i in range(min(2, len(freq_sorted)))]
            if len(top_colors) == 1:
                mapping[top_colors[0]] = 1
            else:

                def luminance(c):
                    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

                c0, c1 = top_colors[0], top_colors[1]
                if luminance(c0) <= luminance(c1):
                    mapping[c0] = 1
                    mapping[c1] = 2
                else:
                    mapping[c1] = 1
                    mapping[c0] = 2

    out = []
    for r, g, b, a in data:
        if a == 0:
            out.append((0, 0, 0, 0))
            continue
        key = (r, g, b)
        slot = mapping.get(key)
        if slot == 1:
            cr, cg, cb = color1_rgb
            out.append((cr, cg, cb, a))
        elif slot == 2:
            cr, cg, cb = color2_rgb
            out.append((cr, cg, cb, a))
        elif slot == 3 and color3_rgb is not None:
            cr, cg, cb = color3_rgb
            out.append((cr, cg, cb, a))
        else:
            out.append((r, g, b, a))

    res = Image.new("RGBA", px.size)
    res.putdata(out)
    return res


def analyze_image_colors(img: Image.Image, name: str | None = None, top_n: int = 8):
    """Prints the most frequent non-transparent colors and distances to canonical masks."""
    px = img.convert("RGBA")
    data = list(px.getdata())
    counts = {}
    for r, g, b, a in data:
        if a == 0:
            continue
        counts[(r, g, b)] = counts.get((r, g, b), 0) + 1
    total = sum(counts.values())
    freq_sorted = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)

    # diagnostic output removed
    def dist(c1, c2):
        return math.sqrt(
            (c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2 + (c1[2] - c2[2]) ** 2
        )

    canonical = {
        "red_mask": (255, 0, 0),
        "yellow_mask": (255, 255, 0),
        "white": (255, 255, 255),
    }
    # diagnostic output removed


def detect_pattern_mask_colors(img: Image.Image):
    """Detect the exact RGB colors used in a pattern as mask colors for red/yellow/white.

    Returns a tuple (red_color, yellow_color, white_color) where each is an (r,g,b)
    triple or None if not found. This chooses the nearest actual pixel color to the
    canonical red/yellow/white values among non-transparent pixels.
    """
    px = img.convert("RGBA")
    data = list(px.getdata())
    counts = {}
    for r, g, b, a in data:
        if a == 0:
            continue
        counts[(r, g, b)] = counts.get((r, g, b), 0) + 1
    if not counts:
        return (None, None, None)

    def nearest_color(target):
        best = None
        bestd = None
        for col in counts:
            d = (
                (col[0] - target[0]) ** 2
                + (col[1] - target[1]) ** 2
                + (col[2] - target[2]) ** 2
            )
            if bestd is None or d < bestd:
                bestd = d
                best = col
        return best

    red = nearest_color((255, 0, 0))
    yellow = nearest_color((255, 255, 0))
    white = nearest_color((255, 255, 255))
    return (red, yellow, white)


def infer_emblem_mask_colors(img: Image.Image):
    """Infer the actual RGB values used in a colored emblem for mask slots.

    Returns a list of length 3 with (r,g,b) tuples or None for each slot.
    Uses nearest-neighbor to MASK_COLORS on the most frequent colors in the image.
    """
    px = img.convert("RGBA")
    data = list(px.getdata())
    counts = {}
    for r, g, b, a in data:
        if a == 0:
            continue
        counts[(r, g, b)] = counts.get((r, g, b), 0) + 1
    if not counts:
        return [None, None, None]

    # map actual color -> nearest mask index
    mapping = {}
    for col in counts:
        best_idx = None
        best_ds = None
        for idx, mask in enumerate(MASK_COLORS):
            ds = (
                (col[0] - mask[0]) ** 2
                + (col[1] - mask[1]) ** 2
                + (col[2] - mask[2]) ** 2
            )
            if best_ds is None or ds < best_ds:
                best_ds = ds
                best_idx = idx
        # only accept mapping if reasonably close
        mapping.setdefault(best_idx, []).append((col, counts[col]))

    # for each slot pick the most frequent actual color
    result = [None, None, None]
    for idx in range(3):
        lst = mapping.get(idx)
        if not lst:
            continue
        lst_sorted = sorted(lst, key=lambda x: x[1], reverse=True)
        result[idx] = lst_sorted[0][0]
    return result


class EmblemItem(QGraphicsPixmapItem):
    def __init__(
        self, image_path: Path, pil_image: Image.Image, texture_type: str = "colored"
    ):
        super().__init__()
        self.setFlags(
            QGraphicsPixmapItem.ItemIsMovable
            | QGraphicsPixmapItem.ItemIsSelectable
            | QGraphicsPixmapItem.ItemIsFocusable
        )
        self.image_path = Path(image_path)
        self.base_image = pil_image  # original PIL image
        self.texture_type = texture_type
        # support independent X/Y scaling
        self.scale_x = 1.0
        self.scale_y = 1.0
        # mirroring flags
        self.mirror_x = False
        self.mirror_y = False
        # rotation (degrees clockwise)
        self.rotation = 0.0
        # mask slots (list of ints) used to mask this emblem by current pattern
        self.mask_slots = []
        # per-emblem color values as (r,g,b) 0-255 or None; index 0..2
        self.color_vals = [None, None, None]
        self.update_pixmap()

    def update_pixmap(self, color_vals=None):
        # legacy: if external_pixmap supplied via caller, that will be set directly
        img = self.base_image
        cvs = color_vals if color_vals is not None else self.color_vals
        if self.texture_type == "colored" and any(c is not None for c in cvs):
            img = recolor_colored_emblem(self.base_image, cvs)
        pix = pil2pixmap(img)
        if self.scale_x != 1.0 or self.scale_y != 1.0:
            pix = pix.scaled(
                int(pix.width() * self.scale_x),
                int(pix.height() * self.scale_y),
                Qt.IgnoreAspectRatio,
                Qt.SmoothTransformation,
            )
        self.setPixmap(pix)

    def set_color(self, idx: int, qcolor: QColor | None):
        """Set a single color index (0..2) from a QColor or None and update."""
        if idx < 0 or idx > 2:
            return
        if qcolor is None:
            self.color_vals[idx] = None
        else:
            self.color_vals[idx] = (qcolor.red(), qcolor.green(), qcolor.blue())
        # pixmap update is performed by the MainWindow to allow pattern masking
        return

    def set_scale_factor(self, s: float):
        # uniform setter: apply to both axes
        if s is None:
            return
        try:
            s = float(s)
        except Exception:
            return
        if s < 0.1:
            s = 0.1
        if s > 1.0:
            s = 1.0
        self.scale_x = s
        self.scale_y = s
        # pixmap update is performed by the MainWindow to allow pattern masking
        return

    def set_scale_x(self, s: float):
        if s is None:
            return
        try:
            s = float(s)
        except Exception:
            return
        if s < 0.1:
            s = 0.1
        if s > 1.0:
            s = 1.0
        self.scale_x = s
        return

    def set_scale_y(self, s: float):
        if s is None:
            return
        try:
            s = float(s)
        except Exception:
            return
        if s < 0.1:
            s = 0.1
        if s > 1.0:
            s = 1.0
        self.scale_y = s
        return

    def set_rotation(self, deg: float):
        try:
            self.rotation = float(deg)
        except Exception:
            return
        return


class SettingsWindow(QWidget):
    """Persistent settings window to choose base game and mod folders.

    Shows current paths in read-only line edits with buttons to change them.
    """

    def __init__(self, parent: "MainWindow"):
        super().__init__(parent)
        # make this widget a top-level window (not embedded child)
        try:
            self.setWindowFlags(self.windowFlags() | Qt.Window)
        except Exception:
            pass
        self.parent = parent
        self.setWindowTitle("Settings")
        self.setMinimumWidth(600)
        layout = QVBoxLayout(self)

        # Base game row
        row_base = QHBoxLayout()
        lbl_base = QLabel("Base game folder:")
        self.base_le = QLineEdit()
        self.base_le.setReadOnly(True)
        btn_base = QPushButton("Choose...")
        btn_base.clicked.connect(self.choose_base)
        row_base.addWidget(lbl_base)
        row_base.addWidget(self.base_le, 1)
        row_base.addWidget(btn_base)
        layout.addLayout(row_base)

        # Mod folder row
        row_mod = QHBoxLayout()
        lbl_mod = QLabel("Mod folder (optional):")
        self.mod_le = QLineEdit()
        self.mod_le.setReadOnly(True)
        btn_mod = QPushButton("Choose...")
        btn_mod.clicked.connect(self.choose_mod)
        row_mod.addWidget(lbl_mod)
        row_mod.addWidget(self.mod_le, 1)
        row_mod.addWidget(btn_mod)
        layout.addLayout(row_mod)

        # Actions
        actions = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.save_and_apply)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        actions.addStretch()
        actions.addWidget(save_btn)
        actions.addWidget(close_btn)
        layout.addLayout(actions)

        self.refresh()

    def refresh(self):
        s = self.parent.settings
        self.base_le.setText(s.get("base_game", "") or "")
        self.mod_le.setText(s.get("mod_folder", "") or "")

    def choose_base(self):
        dlg = QFileDialog(self, "Select base game folder")
        dlg.setFileMode(QFileDialog.Directory)
        if dlg.exec():
            self.base_le.setText(dlg.selectedFiles()[0])

    def choose_mod(self):
        dlg = QFileDialog(self, "Select mod folder")
        dlg.setFileMode(QFileDialog.Directory)
        if dlg.exec():
            self.mod_le.setText(dlg.selectedFiles()[0])

    def save_and_apply(self):
        # write to parent settings and re-scan assets
        self.parent.settings["base_game"] = self.base_le.text()
        self.parent.settings["mod_folder"] = self.mod_le.text()
        self.parent.save_settings()
        try:
            self.parent.scan_assets()
        except Exception:
            pass
        self.close()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EU5 Flag Editor")
        self.resize(1000, 600)
        self.settings = self.load_settings()
        self.assets = {"patterns": {}, "colored_emblems": {}, "textured_emblems": {}}
        self.emblem_saved_colors = {}
        self.setup_ui()
        if self.settings.get("base_game"):
            try:
                self.scan_assets()
            except Exception:
                # Asset scanning failed (e.g. game path doesn't exist on this OS,
                # permission denied, corrupt files). Window still opens with empty lists.
                pass

    def load_settings(self):
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return {"base_game": "", "mod_folder": ""}

    def save_settings(self):
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        h = QHBoxLayout(central)

        left = QVBoxLayout()
        self.patterns_list = QListWidget()
        self.patterns_list.setSelectionMode(QAbstractItemView.SingleSelection)
        # search box for patterns
        self.patterns_search = QLineEdit()
        self.patterns_search.setPlaceholderText("Search patterns...")
        self.patterns_search.textChanged.connect(self.filter_patterns)
        self.emblems_list = DraggableListWidget(asset_type="colored_emblems")
        self.emblems_list.setDragEnabled(True)
        left.addWidget(QLabel("Patterns (patterns)"))
        left.addWidget(self.patterns_list)
        left.addWidget(self.patterns_search)
        left.addWidget(QLabel("Colored Emblems (colored_emblems)"))
        # search box for emblems
        self.emblems_search = QLineEdit()
        self.emblems_search.setPlaceholderText("Search emblems...")
        self.emblems_search.textChanged.connect(self.filter_emblems)
        left.addWidget(self.emblems_list)
        # textured emblems list (hidden by default)
        self.textured_emblems_list = DraggableListWidget(asset_type="textured_emblems")
        self.textured_emblems_list.setDragEnabled(True)
        self.textured_emblems_list.setVisible(False)
        left.addWidget(self.textured_emblems_list)
        left.addWidget(self.emblems_search)

        # toggle button to switch between colored and textured emblem views
        self.toggle_emblem_btn = QPushButton("Show Textured Emblems")
        self.toggle_emblem_btn.setCheckable(True)
        self.toggle_emblem_btn.clicked.connect(self.toggle_emblem_list)
        left.addWidget(self.toggle_emblem_btn)

        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self.open_settings)
        left.addWidget(settings_btn)

        h.addLayout(left, 1)

        # Center canvas
        self.scene = QGraphicsScene(0, 0, CANVAS_W, CANVAS_H)
        self.view = DropGraphicsView(self.scene, owner=self)
        self.view.setFixedSize(CANVAS_W + 10, CANVAS_H + 10)
        self.view.setDragMode(QGraphicsView.RubberBandDrag)
        h.addWidget(self.view, 0)

        # Right panel controls
        right = QVBoxLayout()
        form = QFormLayout()
        # pattern colour pickers (default values from named colors file if available)
        self.col1_btn = QPushButton("Pattern Colour 1")
        self.col2_btn = QPushButton("Pattern Colour 2")
        self.col3_btn = QPushButton("Pattern Colour 3")
        self.col1_label = QLabel("Pattern Colour 1:")
        self.col2_label = QLabel("Pattern Colour 2:")
        self.col3_label = QLabel("Pattern Colour 3:")
        self.col1_btn.clicked.connect(self.pick_color1)
        self.col2_btn.clicked.connect(self.pick_color2)
        self.col3_btn.clicked.connect(self.pick_color3)
        form.addRow(self.col1_label, self.col1_btn)
        form.addRow(self.col2_label, self.col2_btn)
        form.addRow(self.col3_label, self.col3_btn)

        # emblem-specific colour pickers (for selected emblem). first two correspond to the pattern colours
        self.em_col1_btn = QPushButton("Emblem Colour 1")
        self.em_col2_btn = QPushButton("Emblem Colour 2")
        self.em_col3_btn = QPushButton("Emblem Colour 3")
        self.em_col1_label = QLabel("Emblem Colour 1:")
        self.em_col2_label = QLabel("Emblem Colour 2:")
        self.em_col3_label = QLabel("Emblem Colour 3:")
        self.em_col1_btn.clicked.connect(self.pick_emblem_color1)
        self.em_col2_btn.clicked.connect(self.pick_emblem_color2)
        self.em_col3_btn.clicked.connect(self.pick_emblem_color3)
        form.addRow(self.em_col1_label, self.em_col1_btn)
        form.addRow(self.em_col2_label, self.em_col2_btn)
        form.addRow(self.em_col3_label, self.em_col3_btn)

        right.addLayout(form)

        # color is chosen via color picker buttons above (RGB only)

        # Apply button removed; emblem pickers update immediately

        scale_label = QLabel("Scale (selected)")
        # separate X and Y scale controls (percent)
        # Scale X
        sx_label = QLabel("Scale X (selected)")
        self.scale_x_slider = QSlider(Qt.Horizontal)
        self.scale_x_slider.setRange(10, 100)
        self.scale_x_slider.setValue(100)
        right.addWidget(sx_label)
        right.addWidget(self.scale_x_slider)
        sx_row = QHBoxLayout()
        sx_btn25 = QPushButton("25%")
        sx_btn50 = QPushButton("50%")
        sx_btn100 = QPushButton("100%")
        self.scale_x_spin = QSpinBox()
        self.scale_x_spin.setRange(10, 100)
        self.scale_x_spin.setValue(100)
        self.scale_x_spin.setSuffix("%")
        self.scale_x_spin.setFixedWidth(80)
        sx_btn25.clicked.connect(lambda: self.scale_x_spin.setValue(25))
        sx_btn50.clicked.connect(lambda: self.scale_x_spin.setValue(50))
        sx_btn100.clicked.connect(lambda: self.scale_x_spin.setValue(100))
        sx_row.addWidget(sx_btn25)
        sx_row.addWidget(sx_btn50)
        sx_row.addWidget(sx_btn100)
        sx_row.addStretch()
        sx_row.addWidget(self.scale_x_spin)
        right.addLayout(sx_row)

        # Scale Y
        sy_label = QLabel("Scale Y (selected)")
        self.scale_y_slider = QSlider(Qt.Horizontal)
        self.scale_y_slider.setRange(10, 100)
        self.scale_y_slider.setValue(100)
        right.addWidget(sy_label)
        right.addWidget(self.scale_y_slider)
        sy_row = QHBoxLayout()
        sy_btn25 = QPushButton("25%")
        sy_btn50 = QPushButton("50%")
        sy_btn100 = QPushButton("100%")
        self.scale_y_spin = QSpinBox()
        self.scale_y_spin.setRange(10, 100)
        self.scale_y_spin.setValue(100)
        self.scale_y_spin.setSuffix("%")
        self.scale_y_spin.setFixedWidth(80)
        sy_btn25.clicked.connect(lambda: self.scale_y_spin.setValue(25))
        sy_btn50.clicked.connect(lambda: self.scale_y_spin.setValue(50))
        sy_btn100.clicked.connect(lambda: self.scale_y_spin.setValue(100))
        sy_row.addWidget(sy_btn25)
        sy_row.addWidget(sy_btn50)
        sy_row.addWidget(sy_btn100)
        sy_row.addStretch()
        sy_row.addWidget(self.scale_y_spin)
        right.addLayout(sy_row)

        # Mirror controls
        mirror_row = QHBoxLayout()
        self.mirror_x_btn = QPushButton("Mirror X")
        self.mirror_x_btn.setCheckable(True)
        self.mirror_y_btn = QPushButton("Mirror Y")
        self.mirror_y_btn.setCheckable(True)
        self.mirror_x_btn.clicked.connect(self.mirror_x_toggled)
        self.mirror_y_btn.clicked.connect(self.mirror_y_toggled)
        mirror_row.addWidget(self.mirror_x_btn)
        mirror_row.addWidget(self.mirror_y_btn)
        # mask button
        self.mask_btn = QPushButton("Mask...")
        self.mask_btn.clicked.connect(self.set_mask_for_selected)
        mirror_row.addWidget(self.mask_btn)
        mirror_row.addStretch()
        right.addLayout(mirror_row)

        # Rotation control
        rot_row = QHBoxLayout()
        rot_label = QLabel("Rotation (°)")
        self.rotation_spin = QSpinBox()
        self.rotation_spin.setRange(-180, 180)
        self.rotation_spin.setValue(0)
        self.rotation_spin.setSuffix("°")
        self.rotation_spin.setFixedWidth(100)
        self.rotation_spin.valueChanged.connect(self.rotation_changed)
        rot_row.addWidget(rot_label)
        rot_row.addWidget(self.rotation_spin)
        rot_row.addStretch()
        right.addLayout(rot_row)

        # wire up synchronization: sliders <-> spinboxes and handlers
        self.scale_x_spin.valueChanged.connect(
            lambda v: self.scale_x_slider.setValue(v)
        )
        self.scale_x_slider.valueChanged.connect(
            lambda v: (
                self.scale_x_spin.blockSignals(True),
                self.scale_x_spin.setValue(v),
                self.scale_x_spin.blockSignals(False),
                self.scale_x_changed(v),
            )
        )
        self.scale_y_spin.valueChanged.connect(
            lambda v: self.scale_y_slider.setValue(v)
        )
        self.scale_y_slider.valueChanged.connect(
            lambda v: (
                self.scale_y_spin.blockSignals(True),
                self.scale_y_spin.setValue(v),
                self.scale_y_spin.blockSignals(False),
                self.scale_y_changed(v),
            )
        )

        # center controls: center both axes or individually
        ctr_row = QHBoxLayout()
        center_both_btn = QPushButton("Center Both")
        center_both_btn.setToolTip("Center the selected emblem on both X and Y axes")
        center_both_btn.clicked.connect(lambda: self.center_selected("both"))
        center_x_btn = QPushButton("Center X")
        center_x_btn.setToolTip("Center the selected emblem on the X axis")
        center_x_btn.clicked.connect(lambda: self.center_selected("x"))
        center_y_btn = QPushButton("Center Y")
        center_y_btn.setToolTip("Center the selected emblem on the Y axis")
        center_y_btn.clicked.connect(lambda: self.center_selected("y"))
        ctr_row.addWidget(center_both_btn)
        ctr_row.addWidget(center_x_btn)
        ctr_row.addWidget(center_y_btn)
        right.addLayout(ctr_row)

        delete_btn = QPushButton("Remove Selected")
        delete_btn.setToolTip("Remove the selected emblem from the canvas")
        delete_btn.clicked.connect(self.delete_selected)
        right.addWidget(delete_btn)

        # Import/Export CoA buttons
        import_btn = QPushButton("Import CoA Block")
        import_btn.setToolTip("Import a CoA block from the clipboard")
        import_btn.clicked.connect(self.import_coa)
        right.addWidget(import_btn)

        export_btn = QPushButton("Export CoA Block")
        export_btn.clicked.connect(self.export_coa)
        right.addWidget(export_btn)

        # Layer list for reordering emblems
        right.addWidget(QLabel("Layers"))
        self.layer_list = QListWidget()
        self.layer_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.layer_list.itemClicked.connect(self._layer_item_clicked)
        right.addWidget(self.layer_list)
        layer_btn_row = QHBoxLayout()
        raise_btn = QPushButton("Up")
        raise_btn.clicked.connect(self.raise_layer)
        lower_btn = QPushButton("Down")
        lower_btn.clicked.connect(self.lower_layer)
        layer_btn_row.addWidget(raise_btn)
        layer_btn_row.addWidget(lower_btn)
        layer_btn_row.addStretch()
        right.addLayout(layer_btn_row)

        h.addLayout(right, 1)

        # connect list interactions
        self.patterns_list.itemDoubleClicked.connect(self.select_pattern)
        # respond to selection changes in the scene so controls reflect selected emblem
        self.scene.selectionChanged.connect(self.on_selection_changed)
        # double-click or context action to add emblem to scene as a fallback
        # colored list double-click
        self.emblems_list.itemDoubleClicked.connect(
            partial(self._double_click_add_from_list, self.emblems_list)
        )
        # textured list double-click
        self.textured_emblems_list.itemDoubleClicked.connect(
            partial(self._double_click_add_from_list, self.textured_emblems_list)
        )

        # show loaded pattern as background when selected
        self.current_pattern_item = None
        self.current_pattern_name = None
        # default: hide extra colour controls until a pattern/emblem is selected
        try:
            self.update_pattern_color_controls(1)
            self.update_emblem_color_controls(1)
        except Exception:
            pass

    def scan_assets(self):
        # Treat empty strings as no-folder; prefer mod files over base files
        base_str = self.settings.get("base_game", "")
        base = Path(base_str) if base_str else None
        mod_str = self.settings.get("mod_folder", "")
        mod = Path(mod_str) if mod_str else None

        def gather(subpath):
            results = {}

            def scan_root(root_path):
                found = {}
                p = root_path / subpath
                if p.exists():
                    try:
                        entries = list(p.iterdir())
                    except Exception:
                        entries = []
                    for f in entries:
                        if f.suffix.lower() in [".dds", ".png", ".tga"]:
                            found[f.name] = f.resolve()
                parts = subpath.parts
                if parts and parts[0] == "game":
                    alt = root_path.joinpath(*parts[1:])
                    if alt.exists():
                        try:
                            entries = list(alt.iterdir())
                        except Exception:
                            entries = []
                        for f in entries:
                            if f.suffix.lower() in [".dds", ".png", ".tga"]:
                                found[f.name] = f.resolve()
                return found

            if base and base.exists():
                results.update(scan_root(base))
            if mod and mod.exists():
                # mod overrides base; update after base
                results.update(scan_root(mod))
            return results

        self.assets["patterns"] = gather(
            Path("game") / "main_menu" / "gfx" / "coat_of_arms" / "patterns"
        )
        self.assets["colored_emblems"] = gather(
            Path("game") / "main_menu" / "gfx" / "coat_of_arms" / "colored_emblems"
        )
        self.assets["textured_emblems"] = gather(
            Path("game") / "main_menu" / "gfx" / "coat_of_arms" / "textured_emblems"
        )
        self.populate_lists()
        # load named colors file if available
        named_colors_path = None
        if base:
            p = (
                base
                / Path("game")
                / "main_menu"
                / "common"
                / "named_colors"
                / "01_coa.txt"
            )
            if p.exists():
                named_colors_path = p
        # do not use hardcoded paths; only use provided base/mod folders
        # if named colors not found in base, leave as empty and proceed
        self.named_colors = {}
        if named_colors_path:
            try:
                self.named_colors = self.parse_named_colors(named_colors_path)
            except Exception:
                self.named_colors = {}

        # initialize color pickers with defaults from named colors
        # prefer 'white' and 'yellow_mid' if available
        def set_default_btn(btn, qname):
            if self.named_colors.get(qname):
                r, g, b = self.named_colors[qname]
                qc = QColor(int(r * 255), int(g * 255), int(b * 255))
            else:
                qc = QColor(255, 255, 255)
            btn._qcolor = qc
            btn.setStyleSheet(f"background-color: {qc.name()};")

        set_default_btn(self.col1_btn, "white")
        set_default_btn(self.col2_btn, "yellow_mid")
        set_default_btn(self.col3_btn, "white")

        # set emblem picker defaults to the known emblem mask colours
        try:
            for idx, b in enumerate(
                (self.em_col1_btn, self.em_col2_btn, self.em_col3_btn)
            ):
                mc = MASK_COLORS[idx]
                qc = QColor(mc[0], mc[1], mc[2])
                b._qcolor = qc
                b.setStyleSheet(f"background-color: {qc.name()};")
        except Exception:
            pass

        # auto-select a sensible default pattern if present
        default_pattern = "pattern_solid.dds"
        if default_pattern in self.assets.get("patterns", {}):
            # find the matching item in the list and select it
            for i in range(self.patterns_list.count()):
                it = self.patterns_list.item(i)
                if it and it.text() == default_pattern:
                    self.patterns_list.setCurrentItem(it)
                    try:
                        self.select_pattern(it)
                    except Exception:
                        pass
                    break

    def pick_color1(self):
        init = getattr(self.col1_btn, "_qcolor", QColor(255, 255, 255))
        c = QColorDialog.getColor(
            init, self, "Pick Pattern Colour 1", QColorDialog.ShowAlphaChannel
        )
        if c.isValid():
            self.col1_btn._qcolor = c
            self.col1_btn.setStyleSheet(f"background-color: {c.name()};")
            # update pattern display if any
            self._update_pattern_display()

    def pick_color2(self):
        init = getattr(self.col2_btn, "_qcolor", QColor(255, 255, 255))
        c = QColorDialog.getColor(
            init, self, "Pick Pattern Colour 2", QColorDialog.ShowAlphaChannel
        )
        if c.isValid():
            self.col2_btn._qcolor = c
            self.col2_btn.setStyleSheet(f"background-color: {c.name()};")
            # update pattern display if any
            self._update_pattern_display()

    def pick_color3(self):
        init = getattr(self.col3_btn, "_qcolor", QColor(255, 255, 255))
        c = QColorDialog.getColor(
            init, self, "Pick Pattern Colour 3", QColorDialog.ShowAlphaChannel
        )
        if c.isValid():
            self.col3_btn._qcolor = c
            self.col3_btn.setStyleSheet(f"background-color: {c.name()};")
            # update pattern display if any
            self._update_pattern_display()

    def pick_emblem_color1(self):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, EmblemItem)]
        if not sel:
            QMessageBox.information(self, "Color", "Select an emblem first")
            return
        item = sel[0]
        if getattr(item, "texture_type", "colored") != "colored":
            QMessageBox.information(self, "Color", "Textured emblems have no colors")
            return
        # initial color: emblem color1 if present, else emblem fallback to global color1
        init = getattr(self.em_col1_btn, "_qcolor", None)
        if init is None:
            # try emblem current color
            cv = item.color_vals[0]
            if cv:
                init = QColor(int(cv[0]), int(cv[1]), int(cv[2]))
            else:
                init = getattr(self.col1_btn, "_qcolor", QColor(255, 255, 255))
        c = QColorDialog.getColor(
            init, self, "Pick Emblem Colour 1", QColorDialog.ShowAlphaChannel
        )
        if c.isValid():
            self.em_col1_btn._qcolor = c
            self.em_col1_btn.setStyleSheet(f"background-color: {c.name()};")
            item.set_color(0, c)
            self.update_emblem_pixmap(item)
            # save this choice for this emblem texture (normalize values)
            try:
                vals = [
                    _normalize_color_tuple(v) if v is not None else None
                    for v in item.color_vals
                ]
                self.emblem_saved_colors[item.image_path.name] = vals
            except Exception:
                pass

    def pick_emblem_color2(self):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, EmblemItem)]
        if not sel:
            QMessageBox.information(self, "Color", "Select an emblem first")
            return
        item = sel[0]
        if getattr(item, "texture_type", "colored") != "colored":
            QMessageBox.information(self, "Color", "Textured emblems have no colors")
            return
        init = getattr(self.em_col2_btn, "_qcolor", None)
        if init is None:
            cv = item.color_vals[1]
            if cv:
                init = QColor(int(cv[0]), int(cv[1]), int(cv[2]))
            else:
                init = getattr(self.col2_btn, "_qcolor", QColor(255, 255, 255))
        c = QColorDialog.getColor(
            init, self, "Pick Emblem Colour 2", QColorDialog.ShowAlphaChannel
        )
        if c.isValid():
            self.em_col2_btn._qcolor = c
            self.em_col2_btn.setStyleSheet(f"background-color: {c.name()};")
            item.set_color(1, c)
            self.update_emblem_pixmap(item)
            try:
                vals = [
                    _normalize_color_tuple(v) if v is not None else None
                    for v in item.color_vals
                ]
                self.emblem_saved_colors[item.image_path.name] = vals
            except Exception:
                pass

    def pick_emblem_color3(self):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, EmblemItem)]
        if not sel:
            QMessageBox.information(self, "Color", "Select an emblem first")
            return
        item = sel[0]
        if getattr(item, "texture_type", "colored") != "colored":
            QMessageBox.information(self, "Color", "Textured emblems have no colors")
            return
        init = getattr(self.em_col3_btn, "_qcolor", None)
        if init is None:
            cv = item.color_vals[2]
            if cv:
                init = QColor(int(cv[0]), int(cv[1]), int(cv[2]))
            else:
                init = QColor(255, 255, 255)
        c = QColorDialog.getColor(
            init, self, "Pick Emblem Colour 3", QColorDialog.ShowAlphaChannel
        )
        if c.isValid():
            self.em_col3_btn._qcolor = c
            self.em_col3_btn.setStyleSheet(f"background-color: {c.name()};")
            item.set_color(2, c)
            try:
                vals = [
                    _normalize_color_tuple(v) if v is not None else None
                    for v in item.color_vals
                ]
                self.emblem_saved_colors[item.image_path.name] = vals
            except Exception:
                pass
            self.update_emblem_pixmap(item)

    def set_mask_for_selected(self):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, EmblemItem)]
        if not sel:
            QMessageBox.information(self, "Mask", "Select an emblem first")
            return
        item = sel[0]
        text, ok = QInputDialog.getText(
            self,
            "Mask slots",
            "Enter mask slots (space-separated integers), or leave blank to clear:",
        )
        if not ok:
            return
        text = (text or "").strip()
        if not text:
            item.mask_slots = []
            self.mask_btn.setText("Mask...")
            self.update_emblem_pixmap(item)
            return
        parts = re.split(r"\s+", text)
        slots = []
        for p in parts:
            try:
                v = int(p)
                if v >= 1:
                    slots.append(v)
            except Exception:
                continue
        item.mask_slots = slots
        self.mask_btn.setText("Mask: " + " ".join(str(s) for s in slots))
        self.update_emblem_pixmap(item)

    def rotation_changed(self, val: int):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, EmblemItem)]
        if not sel:
            return
        for item in sel:
            item.set_rotation(val)
            try:
                self.update_emblem_pixmap(item)
            except Exception:
                pass

    def populate_lists(self):
        # Build a flattened list of assets to generate thumbnails for, then show progress
        patterns = sorted(self.assets.get("patterns", {}).items())
        colored = sorted(self.assets.get("colored_emblems", {}).items())
        textured = sorted(self.assets.get("textured_emblems", {}).items())

        total = len(patterns) + len(colored) + len(textured)
        progress = None
        if total > 8:
            progress = QProgressDialog(
                "Generating thumbnails...", "Cancel", 0, total, self
            )
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(200)
            progress.setAutoClose(True)
            progress.setValue(0)

        # patterns
        self.patterns_list.clear()
        idx = 0
        for name, path in sorted(self.assets["patterns"].items()):
            item = QListWidgetItem(name)
            try:
                # load from cache or generate and cache synchronously
                pix = get_cached_pixmap(Path(path), (96, 96))
                item.setIcon(QIcon(pix))
            except Exception:
                pass
            self.patterns_list.addItem(item)
            idx += 1
            if progress:
                progress.setValue(min(total, progress.value() + 1))
        self.emblems_list.clear()
        for name, path in sorted(self.assets["colored_emblems"].items()):
            item = QListWidgetItem(name)
            try:
                pix = get_cached_pixmap(Path(path), (64, 64))
                item.setIcon(QIcon(pix))
            except Exception:
                pass
            self.emblems_list.addItem(item)
            idx += 1
            if progress:
                progress.setValue(min(total, progress.value() + 1))
        # textured emblems
        self.textured_emblems_list.clear()
        for name, path in sorted(self.assets.get("textured_emblems", {}).items()):
            item = QListWidgetItem(name)
            try:
                pix = get_cached_pixmap(Path(path), (64, 64))
                item.setIcon(QIcon(pix))
            except Exception:
                pass
            self.textured_emblems_list.addItem(item)
            idx += 1
            if progress:
                progress.setValue(min(total, progress.value() + 1))
        # clear any search filters
        try:
            self.patterns_search.setText("")
        except Exception:
            pass
        try:
            self.emblems_search.setText("")
        except Exception:
            pass

    def filter_patterns(self, text: str):
        t = (text or "").lower()
        for i in range(self.patterns_list.count()):
            it = self.patterns_list.item(i)
            if not it:
                continue
            it.setHidden(t not in it.text().lower())

    def filter_emblems(self, text: str):
        t = (text or "").lower()
        for lst in (self.emblems_list, self.textured_emblems_list):
            for i in range(lst.count()):
                it = lst.item(i)
                if not it:
                    continue
                it.setHidden(t not in it.text().lower())

    def open_settings(self):
        # open persistent settings window
        try:
            if (
                getattr(self, "settings_window", None)
                and self.settings_window.isVisible()
            ):
                self.settings_window.raise_()
                return
        except Exception:
            pass
        self.settings_window = SettingsWindow(self)
        self.settings_window.show()

    def _double_click_add_from_list(
        self, lst: DraggableListWidget, item: QListWidgetItem
    ):
        # Helper for double-click: add emblem from specified list
        name = item.text()
        path = self.assets.get(lst.asset_type, {}).get(name)
        if not path:
            return
        typ = "textured" if lst.asset_type == "textured_emblems" else "colored"
        self.add_emblem_to_scene(Path(path), typ)

    def toggle_emblem_list(self):
        # toggle between colored and textured emblem lists
        show_textured = self.toggle_emblem_btn.isChecked()
        if show_textured:
            self.emblems_list.setVisible(False)
            self.textured_emblems_list.setVisible(True)
            self.toggle_emblem_btn.setText("Show Colored Emblems")
        else:
            self.emblems_list.setVisible(True)
            self.textured_emblems_list.setVisible(False)
            self.toggle_emblem_btn.setText("Show Textured Emblems")

    def select_pattern(self, item: QListWidgetItem):
        name = item.text()
        path = self.assets["patterns"].get(name)
        if not path:
            return
        img = load_image(path)
        # detect exact mask colors for this pattern (red/yellow/white)
        try:
            masks = detect_pattern_mask_colors(img)
            self._current_pattern_masks = masks
        except Exception:
            self._current_pattern_masks = (None, None, None)
        # set the pattern colour pickers to the detected mask colours (exact)
        try:
            if self._current_pattern_masks and any(self._current_pattern_masks):
                m1, m2, m3 = self._current_pattern_masks
                if m1:
                    qc = QColor(m1[0], m1[1], m1[2])
                    self.col1_btn._qcolor = qc
                    self.col1_btn.setStyleSheet(f"background-color: {qc.name()};")
                if m2:
                    qc = QColor(m2[0], m2[1], m2[2])
                    self.col2_btn._qcolor = qc
                    self.col2_btn.setStyleSheet(f"background-color: {qc.name()};")
                if m3:
                    qc = QColor(m3[0], m3[1], m3[2])
                    self.col3_btn._qcolor = qc
                    self.col3_btn.setStyleSheet(f"background-color: {qc.name()};")
        except Exception:
            pass
        # recolor according to current pickers
        qc1 = getattr(self.col1_btn, "_qcolor", QColor(255, 255, 255))
        qc1 = getattr(self.col1_btn, "_qcolor", QColor(255, 255, 255))
        qc2 = getattr(self.col2_btn, "_qcolor", QColor(255, 255, 255))
        qc3 = getattr(self.col3_btn, "_qcolor", QColor(255, 255, 255))
        c1 = (qc1.red(), qc1.green(), qc1.blue())
        c2 = (qc2.red(), qc2.green(), qc2.blue())
        c3 = (qc3.red(), qc3.green(), qc3.blue())
        pil = recolor_pattern(
            img, c1, c2, c3, masks=getattr(self, "_current_pattern_masks", None)
        )
        # ensure transparent areas of patterns are filled with colour1 so background
        # doesn't appear as empty/white in the canvas
        try:
            bg = Image.new("RGBA", pil.size, (c1[0], c1[1], c1[2], 255))
            bg.paste(pil, (0, 0), pil)
            pil_f = bg
        except Exception:
            pil_f = pil
        pix = pil2pixmap(pil_f)
        # remove old background
        if self.current_pattern_item:
            self.scene.removeItem(self.current_pattern_item)
        bg = QGraphicsPixmapItem(pix)
        # fill the canvas exactly and keep pattern locked in the very back
        bg.setPos(0, 0)
        bg.setZValue(-1000)
        try:
            # make non-interactive and non-selectable
            bg.setFlag(QGraphicsItem.ItemIsSelectable, False)
            bg.setFlag(QGraphicsItem.ItemIsMovable, False)
        except Exception:
            pass
        bg.setAcceptHoverEvents(False)
        bg.setAcceptedMouseButtons(Qt.NoButton)
        self.scene.addItem(bg)
        self.current_pattern_item = bg
        # remember selected pattern filename for export
        self.current_pattern_name = name

        # store original pattern pil for later recolor updates
        self._current_pattern_pil = img
        # determine how many colour slots this pattern uses and update controls
        try:
            slots = self._determine_pattern_slots(
                img, getattr(self, "_current_pattern_masks", None)
            )
        except Exception:
            slots = 1
        self.update_pattern_color_controls(slots)

    def _update_pattern_display(self):
        if not getattr(self, "current_pattern_item", None) or not getattr(
            self, "_current_pattern_pil", None
        ):
            return
        qc1 = getattr(self.col1_btn, "_qcolor", QColor(255, 255, 255))
        qc2 = getattr(self.col2_btn, "_qcolor", QColor(255, 255, 255))
        c1 = (qc1.red(), qc1.green(), qc1.blue())
        c2 = (qc2.red(), qc2.green(), qc2.blue())
        qc3 = getattr(self.col3_btn, "_qcolor", QColor(255, 255, 255))
        c3 = (qc3.red(), qc3.green(), qc3.blue())
        pil = recolor_pattern(
            self._current_pattern_pil,
            c1,
            c2,
            c3,
            masks=getattr(self, "_current_pattern_masks", None),
        )
        # fill transparent pattern areas with colour1 so canvas has a solid background
        try:
            bg = Image.new("RGBA", pil.size, (c1[0], c1[1], c1[2], 255))
            bg.paste(pil, (0, 0), pil)
            pil = bg
        except Exception:
            pass
        pil = pil.resize((CANVAS_W, CANVAS_H))
        pix = pil2pixmap(pil)
        self.current_pattern_item.setPixmap(pix)

    def _determine_pattern_slots(self, img: Image.Image, masks=None) -> int:
        # return 1/2/3 depending on how many color slots the pattern uses
        # try mask-based detection first
        try:
            # Build color frequency map
            px = img.convert("RGBA")
            counts = {}
            total = 0
            for r, g, b, a in px.getdata():
                if a == 0:
                    continue
                counts[(r, g, b)] = counts.get((r, g, b), 0) + 1
                total += 1

            # helper: nearest actual color to a target
            def nearest_and_count(target):
                best = None
                bestd = None
                for col, cnt in counts.items():
                    d = (
                        (col[0] - target[0]) ** 2
                        + (col[1] - target[1]) ** 2
                        + (col[2] - target[2]) ** 2
                    )
                    if bestd is None or d < bestd:
                        bestd = d
                        best = (col, cnt, d)
                return best  # (col, count, dist_sq) or None

            # check canonical masks red/yellow/white
            slots_found = set()
            thresh_sq = (90) ** 2
            rc = nearest_and_count((255, 0, 0))
            yc = nearest_and_count((255, 255, 0))
            wc = nearest_and_count((255, 255, 255))
            # require both proximity and non-trivial pixel count to consider slot present
            if rc and rc[2] <= thresh_sq and rc[1] >= max(8, total * 0.001):
                slots_found.add(1)
            if yc and yc[2] <= thresh_sq and yc[1] >= max(8, total * 0.001):
                slots_found.add(2)
            if wc and wc[2] <= thresh_sq and wc[1] >= max(8, total * 0.001):
                slots_found.add(3)

            # if no clear canonical masks detected, fall back to number of distinct colors
            if not slots_found:
                uniq = len(counts)
                if uniq >= 3:
                    return 3
                if uniq == 2:
                    return 2
                return 1
            return max(slots_found)
        except Exception:
            return 1

    def update_pattern_color_controls(self, slots: int):
        # ensure slot is 1..3
        if slots < 1:
            slots = 1
        if slots > 3:
            slots = 3
        # slot 1 always visible
        self.col1_label.setVisible(True)
        self.col1_btn.setVisible(True)
        # slot 2
        show2 = slots >= 2
        self.col2_label.setVisible(show2)
        self.col2_btn.setVisible(show2)
        # slot 3
        show3 = slots >= 3
        self.col3_label.setVisible(show3)
        self.col3_btn.setVisible(show3)

    def update_emblem_color_controls(self, slots: int):
        # slots: number of emblem colour slots (1..3). Show/hide accordingly.
        # allow slots==0 to hide all emblem colour controls for textured emblems
        if slots <= 0:
            # hide all emblem controls
            self.em_col1_label.setVisible(False)
            self.em_col1_btn.setVisible(False)
            self.em_col2_label.setVisible(False)
            self.em_col2_btn.setVisible(False)
            self.em_col3_label.setVisible(False)
            self.em_col3_btn.setVisible(False)
            return
        if slots < 1:
            slots = 1
        if slots > 3:
            slots = 3
        self.em_col1_label.setVisible(True)
        self.em_col1_btn.setVisible(True)
        show2 = slots >= 2
        self.em_col2_label.setVisible(show2)
        self.em_col2_btn.setVisible(show2)
        show3 = slots >= 3
        self.em_col3_label.setVisible(show3)
        self.em_col3_btn.setVisible(show3)
        # update all emblems so they are masked by the new pattern
        for it in [i for i in self.scene.items() if isinstance(i, EmblemItem)]:
            self.update_emblem_pixmap(it)

    def add_emblem_to_scene(
        self, path: Path, typ: str = "colored", scene_pos: QPointF = None
    ):
        pil = load_image(path)
        # debug: analyze colors for african assets to help tune recolour thresholds
        try:
            if "african" in path.name.lower():
                analyze_image_colors(pil, path.name)
        except Exception:
            pass
        it = EmblemItem(path, pil, texture_type=typ)
        # textured emblems do not have colors: leave color_vals as all None
        if it.texture_type == "colored":
            # if there are saved colors for this texture, apply them to the item
            try:
                saved = self.emblem_saved_colors.get(it.image_path.name)
                if saved:
                    # ensure list length 3 and normalize values to 0-255 ints
                    vals = list(saved[:3]) + [None] * (3 - len(saved))
                    it.color_vals = [
                        _normalize_color_tuple(v) if v is not None else None for v in vals
                    ]
                else:
                    # no saved colors: infer which mask slots are actually present in the emblem
                    inferred = infer_emblem_mask_colors(it.base_image)
                    new_vals = [None, None, None]
                    if inferred:
                        for idx in range(min(3, len(inferred))):
                            if inferred[idx]:
                                new_vals[idx] = _normalize_color_tuple(inferred[idx])
                    it.color_vals = new_vals
            except Exception:
                # fallback: leave color_vals as None
                pass
        if scene_pos is None:
            # place at center
            scene_pos = QPointF(CANVAS_W / 2, CANVAS_H / 2)
        it.setPos(
            scene_pos - QPointF(it.pixmap().width() / 2, it.pixmap().height() / 2)
        )
        # place emblem above existing emblems and above the pattern
        # compute highest z among existing EmblemItems
        existing_emblems = [i for i in self.scene.items() if isinstance(i, EmblemItem)]
        max_z = max((i.zValue() for i in existing_emblems), default=-999)
        it.setZValue(max_z + 1)
        self.scene.addItem(it)
        # render emblem pixmap with current pattern masking
        self.update_emblem_pixmap(it)
        self.update_layer_list()

    def parse_named_colors(self, path: Path):
        text = Path(path).read_text(encoding="utf-8-sig")  # utf-8-sig strips BOM if present
        colors = {}
        # simple regex for lines like: name = hsv360 { 0 0 92 } or name = rgb { 1 0 1 }
        pattern = re.compile(
            r"^\s*([a-zA-Z0-9_]+)\s*=\s*(hsv360|rgb)\s*\{\s*([^}]+)\}",
            re.IGNORECASE | re.MULTILINE,
        )
        for m in pattern.finditer(text):
            name = m.group(1)
            typ = m.group(2).lower()
            vals = m.group(3).strip()
            parts = re.split(r"\s+", vals)
            try:
                if typ == "rgb":
                    # values 0-1
                    r = float(parts[0])
                    g = float(parts[1])
                    b = float(parts[2])
                    colors[name] = (r, g, b)
                elif typ == "hsv360":
                    h = float(parts[0])
                    s = float(parts[1])
                    v = float(parts[2])
                    # convert to 0-1 for colorsys: h/360, s/100, v/100
                    rf, gf, bf = colorsys.hsv_to_rgb(h / 360.0, s / 100.0, v / 100.0)
                    colors[name] = (rf, gf, bf)
            except Exception:
                continue
        return colors

    def dragEnterEvent(self, e):
        e.accept()

    def dropEvent(self, e):
        e.accept()

    # override view drop behaviour by installing event filter
    def eventFilter(self, obj, event):
        return False

    def apply_color_to_selected(self):
        # deprecated: removed Apply button; emblem pickers update immediately
        return

    def scale_changed(self, val):
        # kept for backward compatibility: set both axes
        sel = [i for i in self.scene.selectedItems() if isinstance(i, EmblemItem)]
        if not sel:
            return
        item = sel[0]
        s = val / 100.0
        item.set_scale_factor(s)
        self.update_emblem_pixmap(item)

    def scale_x_changed(self, val):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, EmblemItem)]
        if not sel:
            return
        item = sel[0]
        sx = val / 100.0
        item.set_scale_x(sx)
        self.update_emblem_pixmap(item)

    def scale_y_changed(self, val):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, EmblemItem)]
        if not sel:
            return
        item = sel[0]
        sy = val / 100.0
        item.set_scale_y(sy)
        self.update_emblem_pixmap(item)

    def mirror_x_toggled(self, checked):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, EmblemItem)]
        if not sel:
            return
        for item in sel:
            item.mirror_x = bool(checked)
            self.update_emblem_pixmap(item)

    def mirror_y_toggled(self, checked):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, EmblemItem)]
        if not sel:
            return
        for item in sel:
            item.mirror_y = bool(checked)
            self.update_emblem_pixmap(item)

    def export_coa(self):
        # collect current pattern filename (if any)
        pattern_name = getattr(self, "current_pattern_name", None)
        # prompt for country TAG
        name = "TAG"
        tag, ok = QInputDialog.getText(
            self,
            "Country TAG",
            "Enter country TAG (e.g., ENG, FRA, TUR):",
            text=name,
        )
        if ok and tag.strip():
            name = tag.strip()
        # build block
        lines = [f"{name} = {{"]
        if pattern_name:
            lines.append(f'    pattern = "{pattern_name}"')
        # colors: export picked colors as rgb { r g b }
        qc1 = getattr(self.col1_btn, "_qcolor", None)
        qc2 = getattr(self.col2_btn, "_qcolor", None)
        qc3 = getattr(self.col3_btn, "_qcolor", None)

        def qc_to_rgb_tuple(qc):
            if not qc:
                return None
            return (qc.red(), qc.green(), qc.blue())

        pattern_c1 = qc_to_rgb_tuple(qc1)
        pattern_c2 = qc_to_rgb_tuple(qc2)
        pattern_c3 = qc_to_rgb_tuple(qc3)

        def find_named_color(rgb):
            # try to match rgb tuple (0-255) to a named color (self.named_colors values are 0-1)
            if not rgb or not getattr(self, "named_colors", None):
                return None
            try:
                rr = rgb[0] / 255.0
                gg = rgb[1] / 255.0
            except Exception:
                return None
            bb = rgb[2] / 255.0
            best = None
            bestd = None
            for name, v in (self.named_colors or {}).items():
                try:
                    dr = rr - v[0]
                    dg = gg - v[1]
                    db = bb - v[2]
                    d = dr * dr + dg * dg + db * db
                    if bestd is None or d < bestd:
                        bestd = d
                        best = name
                except Exception:
                    continue
            # threshold: require reasonably close (squared distance)
            if bestd is not None and bestd <= (0.02 * 0.02 * 3):
                return best
            return None

        def fmt_rgb_line(name, rgb, alias_map=None):
            # alias_map: mapping of color slot names to previously emitted token (e.g., 'color1'->'color1')
            if rgb is None:
                return None
            # try to find named color
            named = find_named_color(rgb)
            if named:
                return f'    {name} = "{named}"'
            # try aliasing to previous slots if identical
            if alias_map:
                for other_name, other_rgb in alias_map.items():
                    if (
                        other_rgb
                        and int(other_rgb[0]) == int(rgb[0])
                        and int(other_rgb[1]) == int(rgb[1])
                        and int(other_rgb[2]) == int(rgb[2])
                    ):
                        return f"    {name} = {other_name}"
            r = int(rgb[0])
            g = int(rgb[1])
            b = int(rgb[2])
            return f"    {name} = rgb {{ {r} {g} {b} }}"

        # determine how many pattern slots are used (1..3)
        try:
            if getattr(self, "_current_pattern_pil", None):
                # use robust detection which checks color proximity and frequencies
                pattern_slots = self._determine_pattern_slots(
                    self._current_pattern_pil,
                    getattr(self, "_current_pattern_masks", None),
                )
            else:
                pattern_slots = 1
        except Exception:
            pattern_slots = 1

        # emit pattern color lines only for slots actually used
        alias_map = {}
        if pattern_slots >= 1:
            c1_line = fmt_rgb_line("color1", pattern_c1, alias_map)
            if c1_line:
                lines.append(c1_line)
                alias_map["color1"] = pattern_c1
        if pattern_slots >= 2:
            c2_line = fmt_rgb_line("color2", pattern_c2, alias_map)
            if c2_line:
                lines.append(c2_line)
                alias_map["color2"] = pattern_c2
        if pattern_slots >= 3:
            c3_line = fmt_rgb_line("color3", pattern_c3, alias_map)
            if c3_line:
                lines.append(c3_line)
                alias_map["color3"] = pattern_c3
        # iterate through scene items in z-order (lowest to highest), excluding background
        emblems = [i for i in self.scene.items() if isinstance(i, EmblemItem)]
        # sort by zValue to preserve layering
        emblems.sort(key=lambda e: e.zValue())
        for e in emblems:
            tex = e.image_path.name
            if getattr(e, "texture_type", "colored") == "colored":
                lines.append("    colored_emblem = {")
                lines.append(f'        texture = "{tex}"')
                # emblem colors: export explicit rgb by default
                ev = e.color_vals
                try:
                    inferred_emblem = infer_emblem_mask_colors(e.base_image)
                except Exception:
                    inferred_emblem = [None, None, None]

                saved = getattr(self, "emblem_saved_colors", {}).get(tex)
                # prefer explicit per-item colors to determine how many slots this emblem uses
                explicit_count = len([c for c in ev if c])
                if explicit_count > 0:
                    emblem_slots = explicit_count
                else:
                    # fall back to saved per-texture
                    saved_count = len([c for c in saved if c]) if saved else 0
                    if saved_count > 0:
                        emblem_slots = saved_count
                    else:
                        # fall back to inferred emblem mask colors
                        inferred_count = (
                            len([c for c in inferred_emblem if c])
                            if inferred_emblem
                            else 0
                        )
                        emblem_slots = inferred_count if inferred_count > 0 else 1

                # export emblem colours for this emblem instance according to emblem_slots
                for idx in range(emblem_slots):
                    pname = ("color1", "color2", "color3")[idx]
                    # priority: explicit item color -> saved per-texture -> inferred emblem mask
                    rgb_to_use = None
                    if idx < len(ev) and ev[idx]:
                        rgb_to_use = ev[idx]
                    elif saved and idx < len(saved) and saved[idx]:
                        rgb_to_use = saved[idx]
                    elif (
                        inferred_emblem
                        and idx < len(inferred_emblem)
                        and inferred_emblem[idx]
                    ):
                        rgb_to_use = inferred_emblem[idx]

                    if not rgb_to_use:
                        continue
                    r = int(rgb_to_use[0])
                    g = int(rgb_to_use[1])
                    b = int(rgb_to_use[2])
                    lines.append(f"        {pname} = rgb {{ {r} {g} {b} }}")
            else:
                # textured emblem: add textured_emblem block without color overrides
                lines.append("    textured_emblem = {")
                lines.append(f'        texture = "{tex}"')
            # instance
            pos = e.pos()
            # position is relative to top-left; convert to normalized center
            w = e.pixmap().width()
            h = e.pixmap().height()
            center_x = (pos.x() + w / 2) / CANVAS_W
            center_y = (pos.y() + h / 2) / CANVAS_H
            # use per-axis scales when exporting
            scale_x = getattr(e, "scale_x", getattr(e, "scale_factor", 1.0))
            scale_y = getattr(e, "scale_y", getattr(e, "scale_factor", 1.0))
            lines.append("        instance = {")
            lines.append(f"            position = {{ {center_x:.3f} {center_y:.3f} }}")
            lines.append(f"            scale = {{ {scale_x:.3f} {scale_y:.3f} }}")
            # optional rotation (degrees clockwise)
            rot = float(getattr(e, "rotation", 0.0) or 0.0)
            if rot != 0.0:
                lines.append(f"            rotation = {{ {rot:.1f} }}")
            # optional mask slots
            ms = getattr(e, "mask_slots", []) or []
            if ms:
                lines.append(
                    "            mask = { " + " ".join(str(int(x)) for x in ms) + " }"
                )
            lines.append("        }")
            lines.append("    }")
        lines.append("}")
        # copy the CoA block to the clipboard instead of writing to a file
        text = "\n".join(lines)
        try:
            QApplication.clipboard().setText(text)
            QMessageBox.information(self, "Copied", "CoA block copied to clipboard")
        except Exception:
            QMessageBox.information(
                self, "Exported", "Generated CoA block (clipboard copy failed)"
            )

    def import_coa(self):
        """Import a CoA block from the clipboard.

        This will try to detect a pattern filename (*.dds) and up to three
        `rgb { r g b }` colour lines from the clipboard text and apply them
        to the current canvas if matching assets are available.
        """
        txt = QApplication.clipboard().text() or ""
        if not txt.strip():
            QMessageBox.information(self, "Import CoA", "Clipboard is empty")
            return

        # Reset scene and pattern state before importing so import is deterministic
        try:
            # remove all existing items (pattern background + emblems)
            self.scene.clear()
        except Exception:
            pass
        try:
            if getattr(self, "current_pattern_item", None):
                try:
                    # ensure reference removed
                    self.current_pattern_item = None
                except Exception:
                    self.current_pattern_item = None
        except Exception:
            pass
        # reset stored pattern state
        try:
            self.current_pattern_name = None
        except Exception:
            pass
        try:
            self._current_pattern_pil = None
            self._current_pattern_masks = (None, None, None)
        except Exception:
            pass
        # reset pattern colour pickers to sensible defaults
        try:
            from PySide6.QtGui import QColor

            self.col1_btn._qcolor = QColor(255, 255, 255)
            self.col1_btn.setStyleSheet(f"background-color: {QColor(255,255,255).name()};")
            self.col2_btn._qcolor = QColor(255, 255, 0)
            self.col2_btn.setStyleSheet(f"background-color: {QColor(255,255,0).name()};")
            self.col3_btn._qcolor = QColor(255, 255, 255)
            self.col3_btn.setStyleSheet(f"background-color: {QColor(255,255,255).name()};")
        except Exception:
            pass

        # try to find an explicit pattern assignment first: pattern = "name.dds"
        m = re.search(r'pattern\s*=\s*["\']?([^"\']+?\.dds)["\']?', txt, re.IGNORECASE)
        if not m:
            # fallback: find any .dds filename in the text
            m = re.search(r'["\']?([^"\']+?\.dds)["\']?', txt, re.IGNORECASE)
        if m:
            pname = os.path.basename(m.group(1))
            patterns = self.assets.get("patterns", {})
            if pname in patterns:
                # select and display the pattern
                items = self.patterns_list.findItems(pname, Qt.MatchExactly)
                if items:
                    try:
                        self.patterns_list.setCurrentItem(items[0])
                        self.select_pattern(items[0])
                    except Exception:
                        pass
            else:
                QMessageBox.information(
                    self, "Import CoA", f'Pattern "{pname}" not found in assets'
                )

        # parse top-level pattern color assignments (color1/color2/color3)
        # Only consider assignments before any emblem blocks so per-emblem
        # color lines don't override the pattern colours.
        color_assigns = {}
        head_txt = txt
        mblk = re.search(r'(?:colored_emblem|textured_emblem)\s*=', txt, re.IGNORECASE)
        if mblk:
            head_txt = txt[: mblk.start()]
        for m in re.finditer(
            r'(color[1-9])\s*=\s*(?:rgb\s*\{\s*([0-9]+)\s+([0-9]+)\s+([0-9]+)\s*\}|"([a-zA-Z0-9_]+)"|([a-zA-Z0-9_]+))',
            head_txt,
            re.IGNORECASE,
        ):
            key = m.group(1).lower()
            # groups: 2,3,4 => rgb numbers; 5 => quoted name; 6 => unquoted token
            if m.group(2) and m.group(3) and m.group(4):
                try:
                    r = int(m.group(2))
                    g = int(m.group(3))
                    b = int(m.group(4))
                    color_assigns[key] = (r, g, b)
                except Exception:
                    continue
            elif m.group(5):
                color_assigns[key] = ("named", m.group(5))
            elif m.group(6):
                token = m.group(6)
                if token.lower().startswith("color"):
                    color_assigns[key] = ("alias", token.lower())
                else:
                    color_assigns[key] = ("named", token)

        # resolve assignments into actual QColor values
        resolved = {}

        # helper to resolve potential aliases
        def resolve_color_token(tok, _seen=None):
            # prevent infinite alias recursion by tracking seen alias keys
            if _seen is None:
                _seen = set()
            if not tok:
                return None
            # support three shapes of token values:
            # - (r,g,b) numeric tuple
            # - ("named", name)
            # - ("alias", "color1")
            # handle numeric tuple first
            if isinstance(tok, tuple) and len(tok) >= 3 and isinstance(tok[0], (int, float)):
                try:
                    r = int(tok[0])
                    g = int(tok[1])
                    b = int(tok[2])
                    return QColor(r, g, b)
                except Exception:
                    return None

            if isinstance(tok, tuple) and len(tok) >= 2 and isinstance(tok[0], str):
                if tok[0] == "named":
                    name = tok[1]
                    # try named_colors (values stored as 0..1 floats)
                    nc = (self.named_colors or {}).get(name)
                    if nc:
                        return QColor(int(nc[0] * 255), int(nc[1] * 255), int(nc[2] * 255))
                    # fallback common names
                    common = {
                        "black": (0, 0, 0),
                        "white": (255, 255, 255),
                        "red": (255, 0, 0),
                        "yellow": (255, 255, 0),
                        "blue": (0, 0, 255),
                        "green": (0, 255, 0),
                    }
                    c = common.get(name.lower())
                    if c:
                        return QColor(c[0], c[1], c[2])
                    return None
                if tok[0] == "alias":
                    alias_key = tok[1]
                    if alias_key in _seen:
                        return None
                    _seen.add(alias_key)
                    return resolve_color_token(color_assigns.get(alias_key), _seen)
            return None

        for slot in ("color1", "color2", "color3"):
            val = color_assigns.get(slot)
            qc = resolve_color_token(val)
            if qc:
                if slot == "color1":
                    btn = self.col1_btn
                elif slot == "color2":
                    btn = self.col2_btn
                else:
                    btn = self.col3_btn
                btn._qcolor = qc
                btn.setStyleSheet(f"background-color: {qc.name()};")
        try:
            self._update_pattern_display()
        except Exception:
            pass

        QMessageBox.information(
            self, "Import CoA", "Imported pattern/colour data from clipboard"
        )
        # Now parse emblem blocks and recreate emblem instances (preserve z-order)
        # Remove existing emblem items
        try:
            for it in [i for i in self.scene.items() if isinstance(i, EmblemItem)]:
                self.scene.removeItem(it)
        except Exception:
            pass

        # collect emblem blocks using simple brace matching to handle nested blocks
        lines = txt.splitlines()
        i = 0
        blocks = []  # list of (type, block_text)
        while i < len(lines):
            line = lines[i].strip()
            if line.lower().startswith("colored_emblem") or line.lower().startswith(
                "textured_emblem"
            ):
                typ = (
                    "colored"
                    if line.lower().startswith("colored_emblem")
                    else "textured"
                )
                # consume until matching braces closed
                brace = 0
                blk_lines = []
                # advance until first '{'
                while i < len(lines):
                    l = lines[i]
                    blk_lines.append(l)
                    brace += l.count("{") - l.count("}")
                    i += 1
                    if brace == 0:
                        break
                blocks.append((typ, "\n".join(blk_lines)))
                continue
            i += 1

        # Add emblems in the order they were exported (blocks list order)
        z = 0
        for typ, blk in blocks:
            # parse texture name
            m = re.search(r'texture\s*=\s*["\']?([^"\']+)["\']?', blk, re.IGNORECASE)
            if not m:
                continue
            texname = os.path.basename(m.group(1))
            # find texture path in assets
            asset_map = self.assets.get(
                "colored_emblems" if typ == "colored" else "textured_emblems", {}
            )
            path = asset_map.get(texname)
            if not path:
                # try the other list as fallback
                other_map = self.assets.get(
                    "textured_emblems" if typ == "colored" else "colored_emblems", {}
                )
                path = other_map.get(texname)
                if path:
                    # adjust type based on where found
                    typ = (
                        "colored"
                        if other_map is self.assets.get("colored_emblems")
                        else "textured"
                    )
            if not path:
                # cannot find texture, skip
                continue

            # parse per-emblem colors (support rgb, named, or alias to pattern colors)
            colors = [None, None, None]
            for idx in range(3):
                cname = f"color{idx+1}"
                mcol = re.search(
                    rf'{cname}\s*=\s*(?:rgb\s*\{{\s*([0-9]+)\s+([0-9]+)\s+([0-9]+)\s*\}}|"([a-zA-Z0-9_]+)"|([a-zA-Z0-9_]+))',
                    blk,
                    re.IGNORECASE,
                )
                if not mcol:
                    continue
                # groups: 1,2,3 -> rgb numbers; 4 -> quoted name; 5 -> unquoted token
                if mcol.group(1) and mcol.group(2) and mcol.group(3):
                    try:
                        colors[idx] = (
                            int(mcol.group(1)), int(mcol.group(2)), int(mcol.group(3))
                        )
                    except Exception:
                        colors[idx] = None
                    continue
                token = mcol.group(4) or mcol.group(5)
                if not token:
                    continue
                token = token.strip()
                # alias to pattern color (e.g., color1)
                if token.lower().startswith("color"):
                    try:
                        qc = resolve_color_token(color_assigns.get(token.lower()))
                        if qc:
                            colors[idx] = (qc.red(), qc.green(), qc.blue())
                        else:
                            colors[idx] = None
                    except Exception:
                        colors[idx] = None
                    continue
                # named color lookup
                try:
                    nc = (self.named_colors or {}).get(token)
                    if nc:
                        colors[idx] = (int(nc[0] * 255), int(nc[1] * 255), int(nc[2] * 255))
                    else:
                        colors[idx] = None
                except Exception:
                    colors[idx] = None

            # parse top-level mask for this emblem block
            mask_slots = []
            mmask = re.search(r"mask\s*=\s*\{\s*([^}]+)\s*\}", blk, re.IGNORECASE)
            if mmask:
                try:
                    parts = re.split(r"\s+", mmask.group(1).strip())
                    for p in parts:
                        try:
                            v = int(p)
                            if v >= 1:
                                mask_slots.append(v)
                        except Exception:
                            continue
                except Exception:
                    mask_slots = []

            # Collect instance blocks: each `instance = { ... }` defines one placement.
            # instance bodies contain nested braces (position = { x y }, scale = { x y })
            # so we must use a brace-depth counter rather than a simple regex.
            # If no instance blocks are present fall back to a single implicit instance
            # using any top-level position/scale/rotation in the emblem block.
            def _extract_instance_bodies(text):
                """Return list of strings, one per `instance = { ... }` body (nested-brace aware)."""
                bodies = []
                search_from = 0
                while True:
                    m = re.search(r'instance\s*=\s*\{', text[search_from:], re.IGNORECASE)
                    if not m:
                        break
                    start = search_from + m.end()  # position just after the opening '{'
                    depth = 1
                    i = start
                    while i < len(text) and depth > 0:
                        if text[i] == '{':
                            depth += 1
                        elif text[i] == '}':
                            depth -= 1
                        i += 1
                    # text[start:i-1] is the body inside the outermost braces
                    bodies.append(text[start:i - 1])
                    search_from = i
                return bodies

            raw_instances = _extract_instance_bodies(blk)

            def _parse_instance(inst_text):
                """Return (cx, cy, sx, sy, rotation) from an instance body string."""
                cx = cy = None
                sx = sy = 1.0
                rotation = 0.0
                mpos = re.search(
                    r'position\s*=\s*\{\s*([0-9\.]+)\s+([0-9\.]+)\s*\}',
                    inst_text, re.IGNORECASE,
                )
                if mpos:
                    try:
                        cx = float(mpos.group(1))
                        cy = float(mpos.group(2))
                    except Exception:
                        cx = cy = None
                mscale = re.search(
                    r'scale\s*=\s*\{\s*([0-9\.\-]+)\s+([0-9\.\-]+)\s*\}',
                    inst_text, re.IGNORECASE,
                )
                if mscale:
                    try:
                        sx = float(mscale.group(1))
                        sy = float(mscale.group(2))
                    except Exception:
                        sx = sy = 1.0
                # rotation: bare value (rotation = -90) or braced (rotation = { -90 })
                mrot = re.search(
                    r'rotation\s*=\s*(?:\{\s*)?([0-9\.\-]+)(?:\s*\})?',
                    inst_text, re.IGNORECASE,
                )
                if mrot:
                    try:
                        rotation = float(mrot.group(1))
                    except Exception:
                        rotation = 0.0
                return cx, cy, sx, sy, rotation

            if raw_instances:
                instances = [_parse_instance(t) for t in raw_instances]
            else:
                # No explicit instance blocks — parse top-level position/scale/rotation
                # from the emblem block itself (old-style single-instance format).
                cx = cy = None
                sx = sy = 1.0
                rotation = 0.0
                mpos = re.search(
                    r'position\s*=\s*\{\s*([0-9\.]+)\s+([0-9\.]+)\s*\}',
                    blk, re.IGNORECASE,
                )
                if mpos:
                    try:
                        cx = float(mpos.group(1))
                        cy = float(mpos.group(2))
                    except Exception:
                        cx = cy = None
                mscale = re.search(
                    r'scale\s*=\s*\{\s*([0-9\.\-]+)\s+([0-9\.\-]+)\s*\}',
                    blk, re.IGNORECASE,
                )
                if mscale:
                    try:
                        sx = float(mscale.group(1))
                        sy = float(mscale.group(2))
                    except Exception:
                        sx = sy = 1.0
                mrot = re.search(
                    r'rotation\s*=\s*(?:\{\s*)?([0-9\.\-]+)(?:\s*\})?',
                    blk, re.IGNORECASE,
                )
                if mrot:
                    try:
                        rotation = float(mrot.group(1))
                    except Exception:
                        rotation = 0.0
                instances = [(cx, cy, sx, sy, rotation)]

            # create one EmblemItem per instance
            try:
                pil = load_image(path)
            except Exception:
                continue

            for cx, cy, sx, sy, rotation in instances:
                try:
                    item = EmblemItem(Path(path), pil, texture_type=typ)
                    try:
                        print(
                            f"[Import] emblem={texname} typ={typ} parsed_colors={colors}"
                            f" sx={sx} sy={sy} rot={rotation} mask={mask_slots} pos=({cx},{cy})"
                        )
                    except Exception:
                        pass
                    # apply colours for colored emblems
                    if typ == "colored":
                        for idx in range(3):
                            if colors[idx]:
                                item.color_vals[idx] = _normalize_color_tuple(colors[idx])
                    try:
                        print(f"[Import] assigned_color_vals={item.color_vals}")
                    except Exception:
                        pass

                    # scale: abs value used for sizing; negative scale means mirror
                    mirror_x = sx < 0
                    mirror_y = sy < 0
                    try:
                        item.set_scale_x(abs(sx))
                    except Exception:
                        item.scale_x = abs(sx)
                    try:
                        item.set_scale_y(abs(sy))
                    except Exception:
                        item.scale_y = abs(sy)
                    item.mirror_x = mirror_x
                    item.mirror_y = mirror_y
                    try:
                        item.set_rotation(rotation)
                    except Exception:
                        item.rotation = rotation
                    try:
                        item.mask_slots = mask_slots
                    except Exception:
                        item.mask_slots = []
                    # render pixmap so size is correct before positioning
                    try:
                        self.update_emblem_pixmap(item)
                    except Exception:
                        pass
                    # convert normalised canvas coords to scene pixel position
                    if cx is not None and cy is not None:
                        pix = item.pixmap()
                        tx = cx * CANVAS_W - pix.width() / 2
                        ty = cy * CANVAS_H - pix.height() / 2
                        item.setPos(QPointF(tx, ty))
                    else:
                        pix = item.pixmap()
                        item.setPos(
                            QPointF(
                                (CANVAS_W - pix.width()) / 2,
                                (CANVAS_H - pix.height()) / 2,
                            )
                        )
                    item.setZValue(z)
                    z += 1
                    self.scene.addItem(item)
                    try:
                        self.update_emblem_pixmap(item)
                    except Exception:
                        pass
                except Exception:
                    continue

        # refresh view and scene after import so all pixmaps and UI reflect changes
        try:
            self.scene.update()
        except Exception:
            pass
        try:
            self.view.viewport().update()
        except Exception:
            pass
        try:
            QApplication.processEvents()
        except Exception:
            pass

        self.update_layer_list()
        QMessageBox.information(self, "Import CoA", "Imported emblems from CoA block")

    def on_selection_changed(self):
        try:
            sel = [i for i in self.scene.selectedItems() if isinstance(i, EmblemItem)]
        except RuntimeError:
            return
        if not sel:
            return
        item = sel[0]
        # update scale slider to match item
        # sync X/Y scale controls with selected item
        try:
            self.scale_x_spin.blockSignals(True)
            self.scale_y_spin.blockSignals(True)
            self.scale_x_slider.blockSignals(True)
            self.scale_y_slider.blockSignals(True)
            sx = int(getattr(item, "scale_x", getattr(item, "scale_factor", 1.0)) * 100)
            sy = int(getattr(item, "scale_y", getattr(item, "scale_factor", 1.0)) * 100)
            self.scale_x_spin.setValue(sx)
            self.scale_x_slider.setValue(sx)
            self.scale_y_spin.setValue(sy)
            self.scale_y_slider.setValue(sy)
        finally:
            try:
                self.scale_x_spin.blockSignals(False)
                self.scale_y_spin.blockSignals(False)
                self.scale_x_slider.blockSignals(False)
                self.scale_y_slider.blockSignals(False)
            except Exception:
                pass
        # update mirror button states for selected emblem
        try:
            if getattr(item, "texture_type", "colored") != "colored":
                # textured emblems can still be mirrored visually
                pass
            self.mirror_x_btn.setChecked(bool(getattr(item, "mirror_x", False)))
            self.mirror_y_btn.setChecked(bool(getattr(item, "mirror_y", False)))
        except Exception:
            pass
        # update rotation and mask UI
        try:
            rot = float(getattr(item, "rotation", 0.0))
            self.rotation_spin.blockSignals(True)
            self.rotation_spin.setValue(int(rot))
            self.rotation_spin.blockSignals(False)
        except Exception:
            pass
        try:
            ms = getattr(item, "mask_slots", []) or []
            if ms:
                self.mask_btn.setText("Mask: " + " ".join(str(s) for s in ms))
            else:
                self.mask_btn.setText("Mask...")
        except Exception:
            pass
        # textured emblems have no colors: hide controls and disable swatches
        if getattr(item, "texture_type", "colored") != "colored":
            self.update_emblem_color_controls(0)
            for b in (self.em_col1_btn, self.em_col2_btn, self.em_col3_btn):
                b.setEnabled(False)
            return

        try:
            inferred_slots = infer_emblem_mask_colors(item.base_image)
            num_slots = len([s for s in inferred_slots if s])
            if num_slots == 0:
                num_slots = 1
        except Exception:
            num_slots = 1

        # show/hide emblem controls accordingly
        self.update_emblem_color_controls(num_slots)

        # enable and set swatches for visible emblem buttons
        global_c1 = getattr(self.col1_btn, "_qcolor", QColor(255, 255, 255))
        global_c2 = getattr(self.col2_btn, "_qcolor", QColor(255, 255, 255))
        inferred = (
            infer_emblem_mask_colors(item.base_image)
            if item.base_image
            else MASK_COLORS
        )
        for idx, b in enumerate((self.em_col1_btn, self.em_col2_btn, self.em_col3_btn)):
            if not b.isVisible():
                b.setEnabled(False)
                continue
            b.setEnabled(True)
            cv = item.color_vals[idx]
            if cv:
                qc = QColor(int(cv[0]), int(cv[1]), int(cv[2]))
            else:
                if inferred and idx < len(inferred) and inferred[idx]:
                    qc = QColor(
                        int(inferred[idx][0]),
                        int(inferred[idx][1]),
                        int(inferred[idx][2]),
                    )
                else:
                    if idx == 0:
                        qc = global_c1
                    elif idx == 1:
                        qc = global_c2
                    else:
                        qc = QColor(255, 255, 255)
            b._qcolor = qc
            b.setStyleSheet(f"background-color: {qc.name()};")
        self.update_layer_list()

    def center_selected(self, axis: str = "both"):
        """Center the selected emblem.

        axis: 'both', 'x', or 'y'
        """
        sel = [i for i in self.scene.selectedItems() if isinstance(i, EmblemItem)]
        if not sel:
            QMessageBox.information(self, "Center", "Select an emblem first")
            return
        item = sel[0]
        cur = item.pos()
        w = item.pixmap().width()
        h = item.pixmap().height()
        # compute top-left target based on axis
        if axis == "both":
            tx = CANVAS_W / 2 - w / 2
            ty = CANVAS_H / 2 - h / 2
        elif axis == "x":
            tx = CANVAS_W / 2 - w / 2
            ty = cur.y()
        elif axis == "y":
            tx = cur.x()
            ty = CANVAS_H / 2 - h / 2
        else:
            tx = cur.x()
            ty = cur.y()
        item.setPos(QPointF(tx, ty))

    def delete_selected(self):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, EmblemItem)]
        if not sel:
            QMessageBox.information(self, "Delete", "Select an emblem first")
            return
        for item in sel:
            self.scene.removeItem(item)
        self.update_layer_list()

    def update_layer_list(self):
        try:
            self.layer_list.blockSignals(True)
            self.layer_list.clear()
            emblems = sorted(
                [i for i in self.scene.items() if isinstance(i, EmblemItem)],
                key=lambda e: e.zValue(),
                reverse=True,
            )
            sel = self.scene.selectedItems()
            for e in emblems:
                name = e.image_path.name
                item = QListWidgetItem(name)
                item.setData(Qt.UserRole, id(e))
                if e in sel:
                    item.setSelected(True)
                    self.layer_list.setCurrentItem(item)
                self.layer_list.addItem(item)
        except Exception:
            pass
        finally:
            try:
                self.layer_list.blockSignals(False)
            except Exception:
                pass

    def _layer_item_clicked(self, item):
        target_id = item.data(Qt.UserRole)
        self.scene.blockSignals(True)
        for e in self.scene.items():
            if isinstance(e, EmblemItem) and id(e) == target_id:
                self.scene.clearSelection()
                e.setSelected(True)
                break
        self.scene.blockSignals(False)

    def raise_layer(self):
        emblems = sorted(
            [i for i in self.scene.items() if isinstance(i, EmblemItem)],
            key=lambda e: e.zValue(),
            reverse=True,
        )
        sel = [i for i in emblems if i.isSelected()]
        if not sel:
            return
        item = sel[0]
        idx = emblems.index(item)
        if idx == 0:
            return
        above = emblems[idx - 1]
        item.setZValue(above.zValue())
        above.setZValue(item.zValue() - 1)
        self.update_layer_list()

    def lower_layer(self):
        emblems = sorted(
            [i for i in self.scene.items() if isinstance(i, EmblemItem)],
            key=lambda e: e.zValue(),
            reverse=True,
        )
        sel = [i for i in emblems if i.isSelected()]
        if not sel:
            return
        item = sel[0]
        idx = emblems.index(item)
        if idx == len(emblems) - 1:
            return
        below = emblems[idx + 1]
        item.setZValue(below.zValue())
        below.setZValue(item.zValue() + 1)
        self.update_layer_list()

    def reset_canvas(self):
        """Remove all emblems, clear pattern and reset colour pickers to defaults."""
        # remove emblem items
        try:
            for it in [i for i in self.scene.items() if isinstance(i, EmblemItem)]:
                try:
                    self.scene.removeItem(it)
                except Exception:
                    pass
        except Exception:
            pass

        # remove pattern background if present
        if getattr(self, "current_pattern_item", None):
            try:
                self.scene.removeItem(self.current_pattern_item)
            except Exception:
                pass
        self.current_pattern_item = None
        self.current_pattern_name = None
        self._current_pattern_pil = None

        # reset pattern colour pickers to sensible defaults
        try:
            qc1 = QColor(255, 255, 255)
            qc2 = QColor(255, 255, 0)
            qc3 = QColor(255, 255, 255)
            nc = getattr(self, "named_colors", {}) or {}
            if nc.get("white"):
                v = nc.get("white")
                qc1 = QColor(v[0], v[1], v[2])
                qc3 = QColor(v[0], v[1], v[2])
            if nc.get("yellow_mid"):
                v = nc.get("yellow_mid")
                qc2 = QColor(v[0], v[1], v[2])
        except Exception:
            qc1 = QColor(255, 255, 255)
            qc2 = QColor(255, 255, 0)
            qc3 = QColor(255, 255, 255)

        for btn, qc in (
            (self.col1_btn, qc1),
            (self.col2_btn, qc2),
            (self.col3_btn, qc3),
        ):
            try:
                btn._qcolor = qc
                btn.setStyleSheet(f"background-color: {qc.name()};")
            except Exception:
                pass

        # update controls and refresh view
        try:
            self.update_pattern_color_controls(1)
            self.update_emblem_color_controls(1)
        except Exception:
            pass
        try:
            self.view.viewport().update()
        except Exception:
            pass

        # set to default pattern (no emblems) if available
        try:
            default_pattern = "pattern_solid.dds"
            if default_pattern in self.assets.get("patterns", {}):
                for i in range(self.patterns_list.count()):
                    it = self.patterns_list.item(i)
                    if it and it.text() == default_pattern:
                        try:
                            self.patterns_list.setCurrentItem(it)
                            self.select_pattern(it)
                        except Exception:
                            pass
                        break
        except Exception:
            pass
        self.update_layer_list()

    def render_emblem_pixmap(self, item: EmblemItem):
        """Render an emblem PIL -> QPixmap applying recolor and pattern masking if present."""
        # recolor emblem
        pil = item.base_image
        if item.texture_type == "colored" and any(
            c is not None for c in item.color_vals
        ):
            pil = recolor_colored_emblem(item.base_image, item.color_vals)
        # scale emblem according to per-axis scales
        sx = getattr(item, "scale_x", getattr(item, "scale_factor", 1.0))
        sy = getattr(item, "scale_y", getattr(item, "scale_factor", 1.0))
        if sx != 1.0 or sy != 1.0:
            neww = int(pil.width * sx)
            newh = int(pil.height * sy)
            if neww <= 0:
                neww = 1
            if newh <= 0:
                newh = 1
            pil = pil.resize((neww, newh), Image.Resampling.LANCZOS)

        # apply mirroring flags if set
        try:
            if getattr(item, "mirror_x", False):
                pil = ImageOps.mirror(pil)
            if getattr(item, "mirror_y", False):
                pil = ImageOps.flip(pil)
        except Exception:
            pass

        # apply pattern-based masking if requested and pattern available
        try:
            if getattr(item, "mask_slots", None) and getattr(
                self, "_current_pattern_pil", None
            ):
                slots = set(item.mask_slots or [])
                # compute top-left in pattern coords from item's scene position
                try:
                    pos = item.pos()
                    tx = int(pos.x())
                    ty = int(pos.y())
                except Exception:
                    tx = 0
                    ty = 0

                # crop pattern region corresponding to emblem size
                pat = self._current_pattern_pil.convert("RGBA")
                ew, eh = pil.width, pil.height
                # create a transparent crop same size
                try:
                    # ensure crop box within pattern bounds
                    box = (tx, ty, tx + ew, ty + eh)
                    crop = Image.new("RGBA", (ew, eh), (0, 0, 0, 0))
                    # attempt to paste overlapped area
                    pat_w, pat_h = pat.size
                    inter_l = max(0, tx)
                    inter_t = max(0, ty)
                    inter_r = min(pat_w, tx + ew)
                    inter_b = min(pat_h, ty + eh)
                    if inter_r > inter_l and inter_b > inter_t:
                        region = pat.crop((inter_l, inter_t, inter_r, inter_b))
                        paste_x = inter_l - tx
                        paste_y = inter_t - ty
                        crop.paste(region, (paste_x, paste_y))
                except Exception:
                    crop = Image.new("RGBA", (ew, eh), (0, 0, 0, 0))

                # determine mask canonical colors
                masks = getattr(self, "_current_pattern_masks", None)
                if not masks:
                    masks = detect_pattern_mask_colors(self._current_pattern_pil)

                # mapping function: returns 1/2/3 or None
                def pixel_mask_index(px):
                    if not px or px[3] == 0:
                        return None
                    r, g, b, a = px
                    best_idx = None
                    best_ds = None
                    for idx, mcol in enumerate(masks):
                        if not mcol:
                            continue
                        ds = (
                            (r - mcol[0]) ** 2 + (g - mcol[1]) ** 2 + (b - mcol[2]) ** 2
                        )
                        if best_ds is None or ds < best_ds:
                            best_ds = ds
                            best_idx = idx + 1
                    return best_idx

                # apply mask: keep emblem pixel only if pattern mask index in slots
                try:
                    em_data = pil.getdata()
                    pat_data = crop.getdata()
                    out = []
                    for ep, pp in zip(em_data, pat_data):
                        pm = pixel_mask_index(pp)
                        if pm is not None and pm in slots:
                            out.append(ep)
                        else:
                            # transparent
                            out.append((0, 0, 0, 0))
                    new = Image.new("RGBA", pil.size)
                    new.putdata(out)
                    pil = new
                except Exception:
                    pass
        except Exception:
            pass

        # apply rotation around center (PIL rotates counter-clockwise, so negate for clockwise)
        try:
            ang = float(getattr(item, "rotation", 0.0) or 0.0)
            if ang != 0.0:
                pil = pil.rotate(-ang, resample=Image.BICUBIC, expand=True)
        except Exception:
            pass

        # optional debug: dump the resulting emblem PIL to a temp dir for inspection
        try:
            if os.environ.get("EU5_FLAG_DUMP_EMBLEM"):
                texname = item.image_path.name.replace(".dds", "")
                import time

                ts = int(time.time() * 1000)
                outp = Path(tempfile.gettempdir()) / f"emblem_debug_{texname}_{ts}.png"
                try:
                    pil.save(outp)
                    print(f"[DumpEmblem] saved {outp}")
                except Exception:
                    pass
        except Exception:
            pass

        return pil2pixmap(pil)

    def update_emblem_pixmap(self, item: EmblemItem):
        try:
            # preserve visual center when pixmap size changes
            try:
                old_pix = item.pixmap()
                old_w = old_pix.width()
                old_h = old_pix.height()
            except Exception:
                old_w = old_h = 0
            center_x = item.pos().x() + old_w / 2
            center_y = item.pos().y() + old_h / 2

            pix = self.render_emblem_pixmap(item)
            item.setPixmap(pix)
            # restore top-left so center remains visually in same scene position
            try:
                new_w = pix.width()
                new_h = pix.height()
                new_x = center_x - new_w / 2
                new_y = center_y - new_h / 2
                item.setPos(QPointF(new_x, new_y))
            except Exception:
                pass
        except Exception:
            pass


def main():
    app = QApplication(sys.argv)
    try:
        win = MainWindow()
        win.show()
        return app.exec()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        try:
            # Use the existing QApplication instance to show the error
            QMessageBox.critical(None, "EU5 Flag Editor Error", tb)
        except Exception:
            print(f"FATAL:\n{tb}")
        return 1


if __name__ == "__main__":
    sys.exit(main())