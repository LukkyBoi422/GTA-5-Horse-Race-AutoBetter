"""
GTA V - Inside Track Auto-Bettor
=================================
F7  - Coord finder: hover any UI element, press F7 to print its coords
F8  - Debug OCR dump
F9  - Start auto-betting loop
F10 - Stop and close terminal

Loop per cycle:
  1. Click PLACE BET
  2. Click first horse
  3. Set bet amount to bet_presets preset via > button
  4. Click PLACE to confirm
  5. Wait for race, poll for AGAIN, click it
  6. Repeat
"""

import ctypes
import json
import os
import re
import shutil
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timezone

import cv2
import keyboard
import mss
import numpy as np
import pytesseract
from pytesseract import Output

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# When running as a PyInstaller exe, __file__ points inside the temp
# extraction folder. Use sys.executable's directory instead so config.json
# is always created next to the exe (or next to main.py during development).
if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(_BASE_DIR, "config.json")
LOG_PATH    = os.path.join(_BASE_DIR, "log.json")

DEFAULT_CONFIG = {
    "monitor": 1,   # which monitor GTA is on — set automatically by EZ Config (step 5)
    "monitor_configured": False,   # set to true once EZ Config step 5 has been completed

    "game_window_title": "Grand Theft Auto V",   # also substring-matched as a fallback, in case the exact title differs
    "tesseract_path": r"C:\Program Files\Tesseract-OCR\tesseract.exe",

    "start_hotkey":  "f9",
    "stop_hotkey":   "f10",
    "pause_hotkey":  "f6",
    "debug_hotkey":  "f8",
    "coords_hotkey": "f7",   # only active during EZ Config setup, not during normal operation
    
    "show_overlay":  False,  # show live stats overlay (races, wagered, profit) — positioned to not interfere with OCR

    "bet_presets": "LOW",
    "preset_LOW":    1500,
    "preset_MEDIUM": 3500,
    "preset_HIGH":   7500,
    "preset_MAX":    10000,
    # win% cutoffs that decide which preset above gets used
    "bet_presets_threshold_max":    50,
    "bet_presets_threshold_high":   40,
    "bet_presets_threshold_medium": 30,

    "startup_delay_seconds": 1,
    "after_horse_select_seconds": 1.0,
    "post_bet_delay_seconds": 1,
    "again_poll_interval_seconds": 0.5,
    "after_again_delay_seconds": 1,
    "click_hold_seconds": 0.05,   # how long to hold the mouse button down per click. 0.05 is fine for 30+ fps. if clicks don't register (e.g. at 15 fps), raise to 0.15 or 0.20
    "click_delay_seconds": 0.15,
    "human_mouse": True,   # true = gradual bezier mouse movement with jitter; false = instant teleport

    # Retry behavior for each step of the loop
    "place_bet_retry_attempts": 10,
    "place_bet_retry_delay_seconds": 0.75,
    "horse_select_retry_attempts": 15,
    "horse_select_retry_delay_seconds": 1.0,
    "horse_not_found_retry_delay_seconds": 2.0,
    "place_confirm_retry_attempts": 10,
    "place_confirm_retry_delay_seconds": 1.0,
    "verify_delay_seconds": 0.5,                # how often to re-check for the horse screen after a PLACE BET click
    "verify_horse_screen_timeout_seconds": 3.0, # total time to wait for the horse screen to load before retrying

    # ALL coordinates below are ABSOLUTE screen coordinates.
    # They default to null — run the script once to see the setup wizard,
    # which explains exactly how to get each value using F7.
    # Re-calibrate any time you move GTA to a different monitor or change resolution.

    # Absolute screen coords of the > (increase) button on the bet screen.
    "increase_button_x": None,
    "increase_button_y": None,

    # Absolute screen coords of the bet amount display (used only by debug OCR).
    "bet_amount_x": None,
    "bet_amount_y": None,
    "bet_amount_crop_width": 100,
    "bet_amount_crop_height": 25,

    # Absolute screen coords — the PLACE BET button on the main Inside Track
    # screen that you click to ENTER the horse selection screen.
    # Use F7 while hovering over it to get these coords.
    "place_bet_enter_x": None,
    "place_bet_enter_y": None,

    # Absolute screen coords — the PLACE confirm button you click AFTER
    # selecting a horse to confirm and submit the bet.
    # Fallback only — OCR tries to find it first.
    "place_button_x": None,
    "place_button_y": None,

    "bet_click_cooldown": 0.2,
    "max_bet_adjust_attempts": 120,

    "odds_click_x_offset": 0,
    "odds_click_y_offset": 0,
    "confidence_threshold": 40,
    "horse_row_confidence_offset": 20,
    "window_wait_poll_seconds": 2,

    "log_all_bets": False,
    "close_terminal_on_stop": True
}


def load_config():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        print(f"[config] Created default config at {CONFIG_PATH}")
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_PATH, "r") as f:
        user = json.load(f)
    merged = dict(DEFAULT_CONFIG)
    merged.update(user)
    return merged


CONFIG = load_config()

# ---------------------------------------------------------------------------
# Locate Tesseract: bundled copy (if you packaged one in with PyInstaller,
# see BUNDLING TESSERACT below) > tesseract_path from config > PATH.
# Fails with a clear message instead of a raw traceback if none is found -
# important for anyone running a built exe who didn't set anything up.
# ---------------------------------------------------------------------------

def _bundled_dir():
    """Where PyInstaller-bundled data files live at runtime. Onefile builds
    extract to a temp folder (sys._MEIPASS); onedir builds keep them next to
    the exe. Returns _BASE_DIR when not frozen (normal `python main.py`)."""
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    return _BASE_DIR


def _find_tesseract():
    candidates = [
        os.path.join(_bundled_dir(), "tesseract_bin", "tesseract.exe"),  # bundled with the exe, see below
        CONFIG.get("tesseract_path"),                                    # explicit override in config.json
        shutil.which("tesseract"),                                       # found on PATH
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


_TESS_PATH = _find_tesseract()
if _TESS_PATH is None:
    print("=" * 60)
    print("  Tesseract OCR was not found - this app can't read the screen")
    print("  without it.")
    print()
    print("  Install it from:")
    print("  https://github.com/UB-Mannheim/tesseract/wiki")
    print()
    print(f"  Then either let the installer add it to PATH, or set")
    print(f"  \"tesseract_path\" in:")
    print(f"  {CONFIG_PATH}")
    print("=" * 60)
    input("Press Enter to exit...")
    sys.exit(1)

pytesseract.pytesseract.tesseract_cmd = _TESS_PATH

# --- BUNDLING TESSERACT (so recipients of your .exe don't install anything) ---
# 1. Copy your entire Tesseract-OCR install folder (usually
#    "C:\Program Files\Tesseract-OCR") into your project as "tesseract_bin".
#    You need the whole folder, not just tesseract.exe - it needs its DLLs
#    and the tessdata subfolder with eng.traineddata.
# 2. Build with the folder included as data (adjust the exe name):
#      pyinstaller --onefile --add-data "tesseract_bin;tesseract_bin" main.py
#    (onedir builds work too - drop --onefile - just make sure
#    "tesseract_bin" ends up next to the exe either way)
# 3. That's it - _find_tesseract() above checks that bundled path first,
#    so a recipient with nothing installed will still work, and anyone who
#    already has Tesseract on PATH is unaffected.
# Expect the exe to grow by roughly 100-150MB - that's Tesseract's language
# data and DLLs, not something you can trim much.

# ---------------------------------------------------------------------------
# Race / Horse odds math (port of inside_track.js)
# ---------------------------------------------------------------------------

class Horse:
    def __init__(self, left, right):
        self.odds = (left, right)
        self.percentage = None
        self.true_percentage = None

    def calculate_percentage(self):
        l, r = self.odds
        self.percentage = (1 / ((l / r) + 1)) * 100

    def get_odds_percentage(self):
        return self.percentage

    def set_true_percentage(self, p):
        self.true_percentage = p

    def get_true_percentage(self):
        return self.true_percentage


class Race:
    def __init__(self):
        self.horses = []

    def add_horse(self, horse):
        self.horses.append(horse)

    def calculate_odds(self):
        for h in self.horses:
            h.calculate_percentage()
        total = sum(h.get_odds_percentage() for h in self.horses)
        for h in self.horses:
            h.set_true_percentage((h.get_odds_percentage() / total) * 100)

    def summary(self):
        return [(h.get_odds_percentage(), h.get_true_percentage()) for h in self.horses]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log_bet(preset: str, amount: int, horse: dict = None):
    if not CONFIG.get("log_all_bets", False):
        return
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "preset": preset,
        "amount": amount,
        "horse_odds": (horse or {}).get("raw"),
        "bet_presets": (horse or {}).get("true_pct"),
    }
    data = []
    if os.path.exists(LOG_PATH):
        try:
            with open(LOG_PATH, "r") as f:
                data = json.load(f)
            if not isinstance(data, list):
                data = []
        except (json.JSONDecodeError, OSError):
            data = []
    data.append(record)
    with open(LOG_PATH, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[log] Logged: {preset} ${amount}  ({record['timestamp']})")

# ---------------------------------------------------------------------------
# Low-level Windows mouse (virtual-desktop aware)
# ---------------------------------------------------------------------------

PUL = ctypes.POINTER(ctypes.c_ulong)

class _MI(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", PUL)]

class _II(ctypes.Union):
    _fields_ = [("mi", _MI)]

class _IN(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", _II)]

class _PT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

_MOUSE = 0; _MOVE = 0x0001; _DN = 0x0002; _UP = 0x0004
_ABS  = 0x8000; _VDESK = 0x4000


def _virt():
    g = ctypes.windll.user32.GetSystemMetrics
    return g(76), g(77), g(78), g(79)


def _send(flags, dx=0, dy=0):
    extra = ctypes.pointer(ctypes.c_ulong(0))
    inp = _IN(_MOUSE, _II(mi=_MI(dx, dy, 0, flags, 0, extra)))
    ctypes.windll.user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(_IN))


def move_to(sx, sy):
    """Instantly warp the cursor to (sx, sy) in absolute screen coords."""
    vx, vy, vw, vh = _virt()
    _send(_MOVE | _ABS | _VDESK,
          int((sx - vx) * 65535 / max(vw-1, 1)),
          int((sy - vy) * 65535 / max(vh-1, 1)))


def _human_move_to(tx, ty):
    """
    Move the cursor from its current position to (tx, ty) along a
    quadratic Bezier curve with small per-step random jitter — mimics
    natural, slightly-wobbly human wrist movement.

    Speed is randomised (0.18 – 0.30 s total travel time) and step count
    scales with distance so fast short moves still feel snappy.
    """
    import random, math
    cx, cy = get_mouse_pos()

    dist = math.hypot(tx - cx, ty - cy)
    if dist < 2:          # already there
        return

    # Random control point offset (pulls the path into a gentle arc)
    steps = max(12, int(dist / 18))
    travel_time = random.uniform(0.18, 0.30)
    delay = travel_time / steps

    # Bezier control point — perpendicular offset up to ±15 % of distance
    perp_mag = random.uniform(-0.15, 0.15) * dist
    mx, my = (cx + tx) / 2, (cy + ty) / 2
    # Perpendicular direction
    dx, dy = tx - cx, ty - cy
    length = math.hypot(dx, dy) or 1
    px, py = -dy / length, dx / length
    cpx, cpy = mx + px * perp_mag, my + py * perp_mag

    for i in range(1, steps + 1):
        t = i / steps
        # Quadratic Bezier
        bx = (1-t)**2 * cx + 2*(1-t)*t * cpx + t**2 * tx
        by = (1-t)**2 * cy + 2*(1-t)*t * cpy + t**2 * ty
        # Small random jitter (±1–2 px), tapers off near destination
        jitter_scale = 1.0 - t * 0.7
        jx = random.uniform(-2, 2) * jitter_scale
        jy = random.uniform(-2, 2) * jitter_scale
        move_to(int(bx + jx), int(by + jy))
        time.sleep(delay)

    # Final snap to exact target with no jitter
    move_to(tx, ty)


def click(sx, sy):
    if CONFIG.get("human_mouse", True):
        _human_move_to(sx, sy)
    else:
        move_to(sx, sy)
    time.sleep(0.05)
    # Hold the button down long enough to register on low-FPS systems.
    # At 15 FPS a frame is ~67ms, so we hold for at least 2 frames (~150ms)
    # to guarantee the game polls the input while the button is pressed.
    hold = CONFIG.get("click_hold_seconds", 0.15)
    _send(_DN); time.sleep(hold); _send(_UP)
    time.sleep(CONFIG["click_delay_seconds"])


def get_mouse_pos():
    pt = _PT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y

# ---------------------------------------------------------------------------
# Screen capture
# ---------------------------------------------------------------------------

def grab():
    """Capture the full virtual desktop (all monitors combined).
    Returns (image_bgr, monitor_dict) or (None, None) on failure.
    Used when we need absolute-coord screen captures."""
    try:
        with mss.MSS() as sct:
            mon = sct.monitors[0]
            shot = sct.grab(mon)
            return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR), mon
    except Exception as e:
        print(f"[grab] Screen capture failed: {e}")
        return None, None


def grab_gta():
    """Capture only the GTA window client area.

    Returns (image_bgr, win_rect) where win_rect is
    {"left","top","width","height"} in absolute screen coords.

    OCR pixel positions from this image are WINDOW-relative (0,0 = top-left
    of the game). To get an absolute screen coord for clicking, add
    win_rect["left"] / win_rect["top"] back.

    Using a tight crop means Tesseract only sees the game UI — no taskbar,
    second monitor, or other desktop noise — which makes OCR far more
    reliable for horse name/odds detection.

    Returns (None, None) if the window can't be found.
    """
    win = get_gta_window_rect()
    if win is None:
        return None, None
    try:
        with mss.MSS() as sct:
            shot = sct.grab(win)
            return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR), win
    except Exception as e:
        print(f"[grab_gta] Capture failed: {e}")
        return None, None

# ---------------------------------------------------------------------------
# GTA window focus
# ---------------------------------------------------------------------------

def find_gta_hwnd():
    """Returns the GTA window handle, or 0 if not found."""
    title = CONFIG.get("game_window_title", "Grand Theft Auto V")
    hwnd = ctypes.windll.user32.FindWindowW(None, title)
    if hwnd == 0:
        found = []
        def _cb(h, _):
            n = ctypes.windll.user32.GetWindowTextLengthW(h)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                ctypes.windll.user32.GetWindowTextW(h, buf, n + 1)
                if title.lower() in buf.value.lower():
                    found.append(h)
            return True
        ctypes.windll.user32.EnumWindows(
            ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)(_cb), 0)
        hwnd = found[0] if found else 0
    return hwnd


def focus_gta():
    hwnd = find_gta_hwnd()
    if hwnd:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        time.sleep(0.1)


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def get_gta_window_rect():
    """Returns {"left","top","width","height"} for the GTA client area in
    absolute screen coords - same shape as an mss monitor dict, so it's a
    drop-in replacement everywhere 'mon' is used. Returns None if the window
    can't be found.

    Every pixel coordinate in config.json (increase_button_x/y,
    bet_amount_x/y, place_button_x/y, bet_amount_region) is relative to THIS
    rect, not a monitor - so they keep working no matter which monitor the
    game is on or where the window is positioned. They still depend on the
    game's resolution/aspect ratio staying the same, just not on monitor/
    window placement."""
    hwnd = find_gta_hwnd()
    if not hwnd:
        return None

    rect = _RECT()
    if not ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None

    pt = _PT(0, 0)
    ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(pt))

    return {"left": pt.x, "top": pt.y, "width": width, "height": height}

# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------

def _ocr_data(img_bgr, preprocess=False):
    scale = 2
    big = cv2.resize(img_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    if preprocess:
        gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
        _, big = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY)
    return pytesseract.image_to_data(big, config=r"--psm 11", output_type=Output.DICT), scale


def _ocr_digits(crop_bgr):
    """Read a single integer from a tight crop."""
    scale = 3
    big  = cv2.resize(crop_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
    text = pytesseract.image_to_string(
        gray, config=r"--psm 7 -c tessedit_char_whitelist=0123456789,$")
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


ODDS_RE = re.compile(r"^(\d{1,3})\s*/\s*(\d{1,2})$")


def _parse_odds(token: str):
    t = token.strip().upper().replace("O", "0")
    if t in ("EVENS", "EVEN"):
        return (1, 1)
    m = ODDS_RE.match(t)
    return (int(m.group(1)), int(m.group(2))) if m else None

# ---------------------------------------------------------------------------
# Horse selection
# ---------------------------------------------------------------------------

_UI_SKIP = {
    "SELECT", "HORSE", "SINGLE", "EVENT", "CANCEL", "PLACE",
    "MAIN", "INSIDE", "TRACK", "AGAIN", "BET", "THE", "AND",
}


def find_horse_rows(img_bgr):
    """Find horse name rows. Returns list of (x, y) sorted top-to-bottom."""
    h, w = img_bgr.shape[:2]
    y_min, y_max = int(h * 0.20), int(h * 0.95)
    x_min = int(w * 0.05)
    data, scale = _ocr_data(img_bgr)
    thresh = max(CONFIG["confidence_threshold"] - CONFIG.get("horse_row_confidence_offset", 20), 0)
    rows = {}
    for i, word in enumerate(data["text"]):
        wrd = word.strip().upper()
        if not wrd or len(wrd) < 3 or not wrd.isalpha() or wrd in _UI_SKIP:
            continue
        conf = int(float(data["conf"][i])) if data["conf"][i] not in ("", "-1") else -1
        if conf < thresh:
            continue
        x  = data["left"][i]  // scale
        y  = data["top"][i]   // scale
        tw = data["width"][i] // scale
        th = data["height"][i]// scale
        cx, cy = x + tw // 2, y + th // 2
        if not (y_min <= cy <= y_max) or cx < x_min:
            continue
        bucket = (cy // 40) * 40
        if bucket not in rows or cx < rows[bucket][0]:
            rows[bucket] = (cx, cy)
    return sorted(rows.values(), key=lambda r: r[1])


def find_horse_tokens(img_bgr):
    """Try odds OCR — often fails on GTA's font but worth trying."""
    tokens = []; seen = set()
    for pre in (False, True):
        data, scale = _ocr_data(img_bgr, preprocess=pre)
        for i, word in enumerate(data["text"]):
            raw = word.strip()
            if not raw:
                continue
            parsed = _parse_odds(raw)
            if parsed is None:
                continue
            conf = int(float(data["conf"][i])) if data["conf"][i] not in ("", "-1") else -1
            if conf < 0:
                continue
            x  = data["left"][i]  // scale; y  = data["top"][i]   // scale
            tw = data["width"][i] // scale; th = data["height"][i]// scale
            key = (x // 20, y // 20)
            if key in seen:
                continue
            seen.add(key)
            tokens.append({"raw": raw, "odds": parsed,
                            "cx": x + tw//2, "cy": y + th//2, "conf": conf})
    tokens.sort(key=lambda t: t["cy"])
    return tokens


def pick_best_horse(tokens):
    """
    Given a list of odds tokens, use Race/Horse math (port of inside_track.js)
    to find the horse with the highest true win probability.
    Returns (best_index, best_true_pct).
    """
    race = Race()
    for t in tokens:
        l, r = t["odds"]
        race.add_horse(Horse(l, r))
    race.calculate_odds()
    summary = race.summary()  # list of (odds_pct, true_pct)

    # Pick highest true percentage
    best_i = max(range(len(summary)), key=lambda i: summary[i][1])
    best_true_pct = summary[best_i][1]

    print(f"[horse] Race odds analysis:")
    for i, (t, (pct, true_pct)) in enumerate(zip(tokens, summary)):
        marker = "  ← PICK" if i == best_i else ""
        print(f"  [{i}] {t['raw']:>6}  odds%={pct:5.1f}  true%={true_pct:5.1f}{marker}")

    return best_i, best_true_pct


def amount_from_true_pct(true_pct: float):
    """
    Pick bet amount tier based on true win probability, using the cutoffs
    and dollar amounts from config.json:
      >= bet_presets_threshold_max    → MAX    (preset_MAX)
      >= bet_presets_threshold_high   → HIGH   (preset_HIGH)
      >= bet_presets_threshold_medium → MEDIUM (preset_MEDIUM)
      below all of those              → LOW    (preset_LOW)
    """
    if true_pct >= CONFIG.get("bet_presets_threshold_max", 50):
        return CONFIG.get("preset_MAX", 10000), "MAX"
    elif true_pct >= CONFIG.get("bet_presets_threshold_high", 40):
        return CONFIG.get("preset_HIGH", 7500), "HIGH"
    elif true_pct >= CONFIG.get("bet_presets_threshold_medium", 30):
        return CONFIG.get("preset_MEDIUM", 3500), "MEDIUM"
    else:
        return CONFIG.get("preset_LOW", 1500), "LOW"


def _ocr_row_band(img_bgr, cy, band_h=30):
    """
    Crop a horizontal band around cy and run OCR looking for odds tokens.
    Returns a list of parsed odds dicts found in that band (window-relative coords).
    """
    h, w = img_bgr.shape[:2]
    y1 = max(0, cy - band_h)
    y2 = min(h, cy + band_h)
    band = img_bgr[y1:y2, :]
    tokens = []
    seen = set()
    for pre in (False, True):
        data, scale = _ocr_data(band, preprocess=pre)
        for i, word in enumerate(data["text"]):
            raw = word.strip()
            if not raw:
                continue
            parsed = _parse_odds(raw)
            if parsed is None:
                continue
            conf = int(float(data["conf"][i])) if data["conf"][i] not in ("", "-1") else -1
            if conf < 0:
                continue
            x  = data["left"][i]  // scale
            y  = data["top"][i]   // scale
            tw = data["width"][i] // scale
            th = data["height"][i]// scale
            key = (x // 20,)
            if key in seen:
                continue
            seen.add(key)
            tokens.append({
                "raw": raw, "odds": parsed,
                "cx": x + tw // 2,
                "cy": y1 + y + th // 2,   # offset back to window coords
                "conf": conf,
            })
    return tokens


def click_best_horse(img_bgr, win):
    """
    Find all horses on screen, pick the one with the best true win %,
    click it, and return the token dict. Falls back to row detection.

    img_bgr is a GTA-window crop (from grab_gta). OCR pixel coords are
    window-relative — add win["left"]/win["top"] to get absolute click coords.

    Strategy:
      1. Try full-screen odds OCR (fast when GTA's font cooperates).
      2. If that gets < 2 tokens, detect horse name rows, then scan each
         row's horizontal band for odds individually (more reliable on
         GTA's stylised font).
      3. If we still have odds for at least 2 horses, pick the best by
         true win %.
      4. Last resort: click the first row (topmost horse).
    """
    # --- Pass 1: full-screen odds scan ---
    tokens = find_horse_tokens(img_bgr)

    # --- Pass 2: row-band scan if full-screen got too few ---
    if len(tokens) < 2:
        rows = find_horse_rows(img_bgr)
        if rows:
            band_tokens = []
            for rx, ry in rows:
                found = _ocr_row_band(img_bgr, ry)
                if found:
                    # take the rightmost token on this row (odds sit to the right)
                    best = max(found, key=lambda t: t["cx"])
                    best["row_cx"] = rx   # store the horse-name click x
                    best["row_cy"] = ry
                    band_tokens.append(best)
            if len(band_tokens) >= 2:
                tokens = band_tokens
                print(f"[horse] Row-band scan found {len(tokens)} odds tokens")
            elif rows:
                # Still nothing useful — log and fall through to row fallback
                print(f"[horse] Odds OCR failed on all {len(rows)} row bands")

    # --- Pick best from whatever tokens we have ---
    if len(tokens) >= 2:
        best_i, true_pct = pick_best_horse(tokens)
        chosen = tokens[best_i]
        # Prefer the horse-name click position if row-band scan provided it
        click_x = chosen.get("row_cx", chosen["cx"])
        click_y = chosen.get("row_cy", chosen["cy"])
        sx = win["left"] + click_x + CONFIG["odds_click_x_offset"]
        sy = win["top"]  + click_y + CONFIG["odds_click_y_offset"]
        print(f"[horse] Clicking best horse {chosen['raw']} at ({sx},{sy})")
        focus_gta(); click(sx, sy)
        chosen["true_pct"] = true_pct
        return chosen
    elif len(tokens) == 1:
        chosen = tokens[0]
        click_x = chosen.get("row_cx", chosen["cx"])
        click_y = chosen.get("row_cy", chosen["cy"])
        sx = win["left"] + click_x + CONFIG["odds_click_x_offset"]
        sy = win["top"]  + click_y + CONFIG["odds_click_y_offset"]
        print(f"[horse] Only one odds found ({chosen['raw']}), clicking at ({sx},{sy})")
        focus_gta(); click(sx, sy)
        chosen["true_pct"] = 0
        return chosen

    # --- Last resort: click topmost horse row ---
    rows = find_horse_rows(img_bgr)
    if len(rows) < 4:
        print(f"[horse] Only {len(rows)} rows found — screen still loading.")
        return None

    rx, ry = rows[0]
    sx = win["left"] + rx + CONFIG["odds_click_x_offset"]
    sy = win["top"]  + ry + CONFIG["odds_click_y_offset"]
    print(f"[horse] Clicking first horse row at ({sx},{sy})  [{len(rows)} rows found]")
    focus_gta(); click(sx, sy)
    return {"raw": "row", "odds": None, "cx": rx, "cy": ry, "conf": -1, "true_pct": 0}

# ---------------------------------------------------------------------------
# PLACE BET — enter betting screen (main Inside Track screen button)
# ---------------------------------------------------------------------------

def click_place_bet_enter():
    """
    Click the PLACE BET button on the main Inside Track screen to open
    the horse-selection screen.  Uses place_bet_enter_x/y from config —
    no OCR needed since this is a fixed, user-calibrated coord.
    """
    px = CONFIG.get("place_bet_enter_x")
    py = CONFIG.get("place_bet_enter_y")
    if px is None or py is None:
        print("[bet] place_bet_enter_x/y not set — use F7 to calibrate.")
        return False
    print(f"[bet] Clicking PLACE BET (enter) at ({px},{py})")
    focus_gta()
    click(int(px), int(py))
    return True


# ---------------------------------------------------------------------------
# PLACE button — confirm bet after horse selection
# ---------------------------------------------------------------------------

def click_place(img_bgr, win):
    """
    Click the PLACE confirm button (after horse selection).

    If place_button_x/y are set in config, uses them directly — reliable
    and instant. Falls back to OCR only if the coord isn't configured.
    """
    # Prefer the calibrated coord — OCR often picks up false matches from
    # other PLACE/BET text still visible on screen.
    px = CONFIG.get("place_button_x")
    py = CONFIG.get("place_button_y")
    if px is not None and py is not None:
        print(f"[bet] Clicking PLACE confirm at ({px},{py})")
        focus_gta(); click(int(px), int(py))
        return True

    # Fallback: OCR scan (only reached if place_button_x/y not set)
    thresh = CONFIG["confidence_threshold"]
    data, scale = _ocr_data(img_bgr)
    place = None
    bet   = None
    for i, word in enumerate(data["text"]):
        wrd = word.strip().upper()
        if wrd not in ("PLACE", "BET"):
            continue
        conf = int(float(data["conf"][i])) if data["conf"][i] not in ("", "-1") else -1
        if conf < thresh:
            continue
        x  = data["left"][i]  // scale
        y  = data["top"][i]   // scale
        tw = data["width"][i] // scale
        th = data["height"][i]// scale
        box = (x, y, x + tw, y + th, conf)
        if wrd == "PLACE":
            place = box
        elif wrd == "BET":
            bet = box

    if place:
        if bet:
            pcy = (place[1] + place[3]) // 2
            bcy = (bet[1]   + bet[3])   // 2
            if abs(pcy - bcy) <= max(place[3] - place[1], bet[3] - bet[1]) * 2:
                cx = (min(place[0], bet[0]) + max(place[2], bet[2])) // 2
                cy = (min(place[1], bet[1]) + max(place[3], bet[3])) // 2
                sx, sy = win["left"] + cx, win["top"] + cy
                print(f"[bet] Clicking PLACE BET (OCR) at ({sx},{sy})  conf={min(place[4],bet[4])}")
                focus_gta(); click(sx, sy)
                return True
        cx = (place[0] + place[2]) // 2
        cy = (place[1] + place[3]) // 2
        sx, sy = win["left"] + cx, win["top"] + cy
        print(f"[bet] Clicking PLACE (OCR) at ({sx},{sy})  conf={place[4]}")
        focus_gta(); click(sx, sy)
        return True

    print("[bet] PLACE button not found — set place_button_x/y in config.json.")
    return False

# ---------------------------------------------------------------------------
# Bet amount — time-based hold-click on the > button
#
# Measured anchor points (hold duration to reach each amount from $100):
#   $1,500  →  3.5 s
#   $7,500  →  6.5 s
#   $10,000 → 13.5 s
#
# Durations for intermediate targets are linearly interpolated between the
# two nearest anchors.  The hold is capped at the interpolated time so the
# bet never overshoots or keeps clicking indefinitely.
# ---------------------------------------------------------------------------

_BET_ANCHORS = [
    (100,   0.0),   # starting value — no hold needed
    (1500,  3.5),
    (7500,  6.5),
    (10000, 13.5),
]


def _hold_duration_for(target: int) -> float:
    """
    Return how many seconds to hold the > button to reach `target` from $100.
    Linearly interpolates between the measured anchor points above.
    """
    # Clamp to the table range
    if target <= _BET_ANCHORS[0][0]:
        return 0.0
    if target >= _BET_ANCHORS[-1][0]:
        return _BET_ANCHORS[-1][1]

    for i in range(len(_BET_ANCHORS) - 1):
        lo_amt, lo_t = _BET_ANCHORS[i]
        hi_amt, hi_t = _BET_ANCHORS[i + 1]
        if lo_amt <= target <= hi_amt:
            frac = (target - lo_amt) / (hi_amt - lo_amt)
            return lo_t + frac * (hi_t - lo_t)

    return _BET_ANCHORS[-1][1]


def hold_click(sx, sy, duration):
    if CONFIG.get("human_mouse", True):
        _human_move_to(sx, sy)
    else:
        move_to(sx, sy)
    time.sleep(0.05)
    _send(_DN)
    time.sleep(duration)
    _send(_UP)


def set_bet_amount(target: int) -> bool:
    """
    Hold the > button for the pre-calculated duration that corresponds to
    `target`, then release.  No OCR loop — timing is derived from measured
    anchor points so the bet lands at (or very close to) the target amount.

    The GTA bet counter always resets to $100 when the screen opens, so
    we always hold from zero.
    """
    bx = CONFIG.get("increase_button_x")
    by = CONFIG.get("increase_button_y")
    if bx is None or by is None:
        print("[bet] increase_button_x/y not set — use F7 to calibrate.")
        return False

    # increase_button_x/y are absolute screen coords
    abs_x, abs_y = int(bx), int(by)

    duration = _hold_duration_for(target)
    print(f"[bet] Holding > for {duration:.2f}s to reach ${target:,}")

    focus_gta()
    hold_click(abs_x, abs_y, duration)

    print(f"[bet] Hold released — bet should be ~${target:,}")
    return True

# ---------------------------------------------------------------------------
# AGAIN button
# ---------------------------------------------------------------------------

def _read_payout(img_bgr):
    """
    Try to OCR a dollar payout amount from the race results screen.
    GTA displays the winnings as a large "$X,XXX" or "$XX,XXX" number.
    Returns the integer payout, or None if nothing reliable is found.
    """
    data, scale = _ocr_data(img_bgr)
    candidates = []
    for i, word in enumerate(data["text"]):
        raw = word.strip().replace(",", "").replace("$", "")
        if not raw.isdigit():
            continue
        val = int(raw)
        if val < 100 or val > 500000:
            continue
        conf = int(float(data["conf"][i])) if data["conf"][i] not in ("", "-1") else -1
        if conf < CONFIG["confidence_threshold"]:
            continue
        candidates.append(val)
    if not candidates:
        return None
    return max(candidates)   # largest number on screen = payout


def find_again(img_bgr, win):
    """img_bgr is a GTA-window crop — add win offsets to get absolute coords."""
    data, scale = _ocr_data(img_bgr)
    thresh = CONFIG["confidence_threshold"]
    for i, word in enumerate(data["text"]):
        if word.strip().upper() != "AGAIN":
            continue
        conf = int(float(data["conf"][i])) if data["conf"][i] not in ("", "-1") else -1
        if conf < thresh:
            continue
        x  = data["left"][i]  // scale; y  = data["top"][i]   // scale
        tw = data["width"][i] // scale; th = data["height"][i]// scale
        return win["left"] + x + tw//2, win["top"] + y + th//2
    return None

# ---------------------------------------------------------------------------
# Live Stats Overlay
# ---------------------------------------------------------------------------

_overlay_window = None
_overlay_thread = None


class StatsOverlay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)  # Remove title bar and make non-draggable
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        
        # Position at very top-left corner of screen
        self.root.geometry("240x120+0+0")
        
        # Dark theme
        self.root.configure(bg="#1a1a1a")
        
        # Stats labels
        self.races_label = tk.Label(self.root, text="Races: 0", 
                                     font=("Consolas", 11, "bold"),
                                     fg="#00ff00", bg="#1a1a1a", anchor="w")
        self.races_label.pack(fill="x", padx=10, pady=(10, 2))
        
        self.wagered_label = tk.Label(self.root, text="Wagered: $0", 
                                       font=("Consolas", 11, "bold"),
                                       fg="#ffffff", bg="#1a1a1a", anchor="w")
        self.wagered_label.pack(fill="x", padx=10, pady=2)
        
        self.profit_label = tk.Label(self.root, text="Profit: $0", 
                                      font=("Consolas", 11, "bold"),
                                      fg="#ffffff", bg="#1a1a1a", anchor="w")
        self.profit_label.pack(fill="x", padx=10, pady=2)
        
        self.status_label = tk.Label(self.root, text="● Running", 
                                      font=("Consolas", 9),
                                      fg="#00ff00", bg="#1a1a1a", anchor="w")
        self.status_label.pack(fill="x", padx=10, pady=(5, 10))
        
        # Update stats every 500ms
        self.update_stats()
        
    def update_stats(self):
        try:
            self.races_label.config(text=f"Races: {_session_races}")
            self.wagered_label.config(text=f"Wagered: ${_session_wagered:,}")
            
            profit = _session_profit
            color = "#00ff00" if profit >= 0 else "#ff4444"
            sign = "+" if profit >= 0 else ""
            self.profit_label.config(text=f"Profit: {sign}${profit:,}", fg=color)
            
            if not _running:
                self.status_label.config(text="● Stopped", fg="#ff4444")
            elif _paused:
                self.status_label.config(text="● Paused", fg="#ffaa00")
            else:
                self.status_label.config(text="● Running", fg="#00ff00")
            
            self.root.after(500, self.update_stats)
        except:
            pass  # Window closed
            
    def run(self):
        self.root.mainloop()


def _start_overlay():
    global _overlay_window, _overlay_thread
    if not CONFIG.get("show_overlay", False):
        return
    if _overlay_window is not None:
        return
    
    def _overlay_worker():
        global _overlay_window
        try:
            _overlay_window = StatsOverlay()
            _overlay_window.run()
        except:
            pass
        finally:
            _overlay_window = None
    
    _overlay_thread = threading.Thread(target=_overlay_worker, daemon=True)
    _overlay_thread.start()
    print("[overlay] Stats overlay started")


def _stop_overlay():
    global _overlay_window
    if _overlay_window and _overlay_window.root:
        try:
            _overlay_window.root.quit()
            _overlay_window.root.destroy()
        except:
            pass
        _overlay_window = None


# ---------------------------------------------------------------------------
# Auto-bet loop
# ---------------------------------------------------------------------------

_running = False
_paused  = False
_thread: threading.Thread | None = None

# Session stats — reset each time start() is called
_session_races    = 0
_session_wagered  = 0
_session_profit   = 0   # positive = net win, negative = net loss


def toggle_pause():
    global _paused
    if not _running:
        print("[hotkey] Not running — can't pause.")
        return
    _paused = not _paused
    if _paused:
        print(f"[hotkey] {CONFIG['pause_hotkey'].upper()} – PAUSED. Press {CONFIG['pause_hotkey'].upper()} again to resume.")
    else:
        print(f"[hotkey] {CONFIG['pause_hotkey'].upper()} – RESUMED.")


def _print_stats():
    net = _session_profit
    sign = "+" if net >= 0 else ""
    print(f"\n  ┌─ Session ────────────────────────────────")
    print(f"  │  Races     : {_session_races}")
    print(f"  │  Wagered   : ${_session_wagered:,}")
    print(f"  │  Net P/L   : {sign}${net:,}")
    print(f"  └──────────────────────────────────────────\n")


def _resolve_preset():
    name   = CONFIG.get("bet_presets", "LOW").upper()
    amount = CONFIG.get(f"preset_{name}")
    if amount is None:
        print(f"[!] Unknown preset '{name}', defaulting to LOW.")
        name, amount = "LOW", CONFIG.get("preset_LOW", 1500)
    return name, amount


def _sleep(seconds):
    end = time.time() + seconds
    while _running and time.time() < end:
        if _paused:
            time.sleep(0.25)
            continue
        time.sleep(min(0.25, end - time.time()))


def wait_for_gta_window():
    """Blocks (while respecting _running/stop) until the GTA window is
    found. Returns False if stopped while waiting, True once found."""
    printed = False
    while _running:
        if get_gta_window_rect() is not None:
            return True
        if not printed:
            title = CONFIG.get("game_window_title", "Grand Theft Auto V")
            print(f"[loop] Waiting for '{title}' window…")
            printed = True
        time.sleep(CONFIG.get("window_wait_poll_seconds", 2))
    return False


def _loop():
    global _running, _paused, _session_races, _session_wagered, _session_profit
    delay = CONFIG["startup_delay_seconds"]
    print(f"[loop] Starting in {delay}s…")
    _sleep(delay)

    cycle = 0
    while _running:
        # Pause check at the start of each cycle
        while _paused and _running:
            time.sleep(0.25)
            continue
        cycle += 1
        print(f"\n[loop] ── Cycle {cycle} ──────────────────────────────────")

        # Step 1: Click PLACE BET to enter the horse-select screen.
        # After clicking, poll for the horse-select UI to appear (up to
        # verify_horse_screen_timeout_seconds). GTA's screen transition
        # takes a variable amount of time, so a single short check isn't
        # reliable — we keep checking until horses appear or we give up.
        entered_horse_screen = False

        for attempt in range(CONFIG["place_bet_retry_attempts"]):
            if click_place_bet_enter():
                print(f"[loop] PLACE BET click sent — waiting for horse screen…")

                timeout  = CONFIG.get("verify_horse_screen_timeout_seconds", 3.0)
                poll     = CONFIG.get("verify_delay_seconds", 0.5)
                deadline = time.time() + timeout
                while _running and time.time() < deadline:
                    time.sleep(poll)
                    verify_img, verify_win = grab_gta()
                    if verify_img is None: continue
                    tokens = find_horse_tokens(verify_img)
                    rows   = find_horse_rows(verify_img)
                    if len(tokens) >= 1 or len(rows) >= 4:
                        print("[loop] Horse screen confirmed — proceeding.")
                        entered_horse_screen = True
                        break

                if entered_horse_screen:
                    break

                print("[loop] Horse screen didn't load in time — retrying PLACE BET…")
            else:
                print(f"[loop] PLACE BET not found (attempt {attempt+1}/{CONFIG['place_bet_retry_attempts']})…")

            _sleep(CONFIG["place_bet_retry_delay_seconds"])

        if not entered_horse_screen:
            print("[loop] Couldn't enter horse-select screen — retrying cycle.")
            continue

        _sleep(CONFIG["after_horse_select_seconds"])
        if not _running: break

        # Step 2: Click best horse (by true win probability)
        horse = None
        for attempt in range(CONFIG["horse_select_retry_attempts"]):
            img, win = grab_gta()
            if img is None:
                _sleep(CONFIG["horse_select_retry_delay_seconds"]); continue
            horse = click_best_horse(img, win)
            if horse: break
            print(f"[loop] No horse yet (attempt {attempt+1}/{CONFIG['horse_select_retry_attempts']}), "
                  f"retrying in {CONFIG['horse_select_retry_delay_seconds']}s…")
            _sleep(CONFIG["horse_select_retry_delay_seconds"])
        if not horse:
            print("[loop] Couldn't find a horse — retrying cycle.")
            _sleep(CONFIG["horse_not_found_retry_delay_seconds"]); continue
        _sleep(CONFIG["after_horse_select_seconds"])
        if not _running: break

        # Step 3: Determine bet amount from horse's true win %
        true_pct = horse.get("true_pct", 0)
        amount, preset = amount_from_true_pct(true_pct)
        print(f"[loop] true%={true_pct:.1f}  →  {preset} ${amount}")

        set_bet_amount(amount)
        if not _running: break
        time.sleep(0.5)

        # Step 4: Click PLACE to confirm bet
        placed = False
        for attempt in range(CONFIG["place_confirm_retry_attempts"]):
            img, win = grab_gta()
            if img is None:
                _sleep(CONFIG["place_confirm_retry_delay_seconds"]); continue
            if click_place(img, win):
                log_bet(preset, amount, horse)
                _session_wagered += amount
                placed = True
                break
            print(f"[loop] PLACE not found (attempt {attempt+1}/{CONFIG['place_confirm_retry_attempts']}), "
                  f"retrying in {CONFIG['place_confirm_retry_delay_seconds']}s…")
            _sleep(CONFIG["place_confirm_retry_delay_seconds"])
        if not placed:
            print("[loop] Couldn't confirm bet — retrying cycle.")
            continue

        # Step 5: Wait for race to finish
        wait = CONFIG["post_bet_delay_seconds"]
        print(f"[loop] Race running, waiting {wait}s…")
        _sleep(wait)
        if not _running: break

        # Step 6: Poll for AGAIN and click it
        interval = CONFIG["again_poll_interval_seconds"]
        print(f"[loop] Watching for AGAIN…")
        payout = None
        while _running:
            img, win = grab_gta()
            if img is None:
                time.sleep(interval); continue
            pos = find_again(img, win)
            if pos is None:
                time.sleep(interval); continue

            # Try to read the payout before dismissing the screen
            payout = _read_payout(img)
            sx, sy = pos
            print(f"[loop] AGAIN at ({sx},{sy}) — clicking once…")
            focus_gta(); click(sx, sy)

            time.sleep(CONFIG["verify_delay_seconds"])
            verify_img, verify_win = grab_gta()
            if verify_img is None or find_again(verify_img, verify_win) is None:
                print("[loop] AGAIN dismissed after 1 click.")
                break

            print("[loop] AGAIN still visible — retrying with 1 click…")
            focus_gta(); click(sx, sy)
            time.sleep(CONFIG["verify_delay_seconds"])
            verify_img, verify_win = grab_gta()
            if verify_img is None or find_again(verify_img, verify_win) is None:
                print("[loop] AGAIN dismissed after retry.")
                break
            print("[loop] AGAIN still visible after retry; continuing to poll.")
        if not _running: break

        # Step 7: Record result and print session stats
        _session_races += 1
        if payout is not None and payout > amount:
            _session_profit += payout - amount
            print(f"[race] WIN  +${payout - amount:,}  (bet ${amount:,}, payout ${payout:,})")
        else:
            _session_profit -= amount
            print(f"[race] LOSS  -${amount:,}")
        _print_stats()

        # Step 8: Wait for horse-select screen to reload, then loop back
        _sleep(CONFIG["after_again_delay_seconds"])

    print("[loop] Stopped.")


def start():
    global _running, _paused, _thread, _session_races, _session_wagered, _session_profit
    if _running:
        print("[hotkey] Already running."); return
    _session_races   = 0
    _session_wagered = 0
    _session_profit  = 0
    _paused = False
    _running = True
    _start_overlay()
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()
    print(f"[hotkey] {CONFIG['start_hotkey'].upper()} – loop started.")


def stop():
    global _running
    _running = False
    print(f"[hotkey] {CONFIG['stop_hotkey'].upper()} – stopping…")
    if _session_races > 0:
        _print_stats()
    _stop_overlay()
    time.sleep(0.5)
    if CONFIG.get("close_terminal_on_stop", True):
        os._exit(0)
    else:
        print("[hotkey] Loop stopped. Terminal staying open (close_terminal_on_stop=false) — "
              f"press {CONFIG['start_hotkey'].upper()} to run again, or Ctrl+C to exit.")

# ---------------------------------------------------------------------------
# Debug
# ---------------------------------------------------------------------------

def debug_ocr():
    img, mon = grab()
    cv2.imwrite("debug_raw.png", img)
    data, scale = _ocr_data(img)
    overlay = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    print("\n[debug] OCR words:")
    for i, word in enumerate(data["text"]):
        w = word.strip()
        if not w: continue
        x, y, bw, bh = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        cv2.rectangle(overlay, (x, y), (x+bw, y+bh), (0,255,0), 2)
        print(f"  '{w}'  conf={data['conf'][i]}  pos=({x//scale},{y//scale})")
    cv2.imwrite("debug_ocr_overlay.png", overlay)

    bx = CONFIG.get("increase_button_x")
    by = CONFIG.get("increase_button_y")
    if bx and by:
        print(f"[debug] increase_button at ({bx},{by}) [absolute, from config]")

    ax, ay = get_mouse_pos()
    print(f"[debug] Mouse absolute: ({ax},{ay})")
    print("[debug] Saved debug_raw.png + debug_ocr_overlay.png")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# The 4 coords the EZ wizard captures, in order.
_EZ_STEPS = [
    {
        "key_x": "place_bet_enter_x",
        "key_y": "place_bet_enter_y",
        "label": "PLACE BET  (main Inside Track menu — opens horse selection)",
        "hint":  "On the main Inside Track screen, hover over the PLACE BET button.",
    },
    {
        "key_x": "bet_amount_x",
        "key_y": "bet_amount_y",
        "label": "BET AMOUNT  (the number showing your current bet, e.g. $100)",
        "hint":  "Click any horse so the bet screen opens,\n"
                 "  then hover over the dollar amount displayed (e.g. $100).",
    },
    {
        "key_x": "increase_button_x",
        "key_y": "increase_button_y",
        "label": "BET INCREASE  (the  >  button that raises the bet amount)",
        "hint":  "On the bet screen, hover over the > (increase) button.",
    },
    {
        "key_x": "place_button_x",
        "key_y": "place_button_y",
        "label": "PLACE  (confirm button that submits the bet)",
        "hint":  "On the bet screen, hover over the PLACE confirm button.",
    },
]


def _save_coord(key_x, key_y, x, y):
    """Write a single x/y pair into config.json immediately."""
    CONFIG[key_x] = x
    CONFIG[key_y] = y
    try:
        with open(CONFIG_PATH, "r") as f:
            data = json.load(f)
    except Exception:
        data = {}
    data[key_x] = x
    data[key_y] = y
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)


def _ez_config_wizard():
    """
    Interactive step-by-step coord capture.
    User hovers over each button and presses F7 to save it, then moves on.
    """
    w = 62
    coords_key = CONFIG.get("coords_hotkey", "f7").upper()
    print()
    print("=" * w)
    print("  EZ CONFIG — step-by-step coordinate setup")
    print("=" * w)
    print()
    print("  You will be guided through 6 steps.")
    print(f"  For each button: hover your mouse over it in GTA,")
    print(f"  then press  {coords_key}  to save it and move to the next step.")
    print()
    print("  TIP: Alt-Tab between this window and GTA freely.")
    print("  Press  Ctrl+C  at any time to cancel.")
    print("=" * w)

    captured = {}

    for step_num, step in enumerate(_EZ_STEPS, 1):
        kx, ky = step["key_x"], step["key_y"]

        # Skip steps that are already configured
        if CONFIG.get(kx) is not None and CONFIG.get(ky) is not None:
            print(f"\n  Step {step_num}/5 — {step['label']}")
            print(f"  Already set to ({CONFIG[kx]}, {CONFIG[ky]}) — skipping.")
            captured[kx] = CONFIG[kx]
            captured[ky] = CONFIG[ky]
            continue

        print(f"\n  Step {step_num}/5 — {step['label']}")
        print(f"  {step['hint']}")
        print(f"  Press  {coords_key}  when your cursor is over it.")

        done = threading.Event()
        result = {}

        def _capture(kx=kx, ky=ky):
            ax, ay = get_mouse_pos()
            result["x"] = ax
            result["y"] = ay
            _save_coord(kx, ky, ax, ay)
            captured[kx] = ax
            captured[ky] = ay
            print(f"\n  ✓ Saved  {kx}: {ax},  {ky}: {ay}")
            done.set()

        keyboard.add_hotkey(CONFIG.get("coords_hotkey", "f7"), _capture)
        try:
            while not done.wait(timeout=0.2):
                pass
        except KeyboardInterrupt:
            keyboard.remove_hotkey(CONFIG.get("coords_hotkey", "f7"))
            print("\n\n  Setup cancelled.")
            sys.exit(0)
        keyboard.remove_hotkey(CONFIG.get("coords_hotkey", "f7"))

    # Step 5 — monitor selection
    print(f"\n  Step 5/6 — Which monitor is GTA V running on?")
    print()
    with mss.MSS() as sct:
        monitors = sct.monitors[1:]   # skip index 0 (virtual combined desktop)
    for i, m in enumerate(monitors, 1):
        print(f"    [{i}]  {m['width']}x{m['height']}  at ({m['left']},{m['top']})")
    print()

    mon_choice = None
    while mon_choice is None:
        try:
            raw = input(f"  Enter monitor number (1–{len(monitors)}): ").strip()
            val = int(raw)
            if 1 <= val <= len(monitors):
                mon_choice = val
            else:
                print(f"  Please enter a number between 1 and {len(monitors)}.")
        except (ValueError, KeyboardInterrupt):
            print("\n\n  Setup cancelled.")
            sys.exit(0)

    CONFIG["monitor"] = mon_choice
    CONFIG["monitor_configured"] = True
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg_data = json.load(f)
    except Exception:
        cfg_data = {}
    cfg_data["monitor"] = mon_choice
    cfg_data["monitor_configured"] = True
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg_data, f, indent=2)
    print(f"  ✓ Saved  monitor: {mon_choice}  ({monitors[mon_choice-1]['width']}x{monitors[mon_choice-1]['height']})")

    # Step 6 — overlay preference
    print(f"\n  Step 6/6 — Live Stats Overlay")
    print()
    print("  Show a small always-on-top window with live stats?")
    print("  (Races, wagered, profit — positioned in top-right corner)")
    print()
    print("  [y]  Yes, show overlay")
    print("  [n]  No, stats in terminal only (default)")
    print()

    overlay_choice = None
    while overlay_choice is None:
        try:
            raw = input("  Enter y or n [n]: ").strip().lower()
            if raw in ("", "n", "no"):
                overlay_choice = False
            elif raw in ("y", "yes"):
                overlay_choice = True
            else:
                print("  Please enter 'y' or 'n'.")
        except KeyboardInterrupt:
            print("\n\n  Setup cancelled.")
            sys.exit(0)

    CONFIG["show_overlay"] = overlay_choice
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg_data = json.load(f)
    except Exception:
        cfg_data = {}
    cfg_data["show_overlay"] = overlay_choice
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg_data, f, indent=2)
    print(f"  ✓ Saved  show_overlay: {overlay_choice}")

    print()
    print("=" * w)
    print("  All done!  Launching auto-bettor…")
    print("=" * w)
    print()


def _manual_config_wizard():
    """Print instructions for manual config.json editing and exit."""
    w = 62
    print()
    print("=" * w)
    print("  MANUAL CONFIG")
    print("=" * w)
    print()
    print("  Edit the following values in config.json:")
    print()
    print('    "place_bet_enter_x": <X>,   // PLACE BET button (main screen)')
    print('    "place_bet_enter_y": <Y>,')
    print('    "place_button_x":    <X>,   // PLACE confirm button (bet screen)')
    print('    "place_button_y":    <Y>,')
    print('    "increase_button_x": <X>,   // > increase button (bet screen)')
    print('    "increase_button_y": <Y>,')
    print('    "bet_amount_x":      <X>,   // bet amount number (bet screen)')
    print('    "bet_amount_y":      <Y>,')
    print()
    print("  To find a coordinate: open GTA V, hover your mouse over the")
    print("  button, note the pixel position from a screen-coordinate tool")
    print("  (e.g. Windows Snipping Tool, ShareX, or similar), and enter")
    print("  the X and Y values into config.json.")
    print()
    print(f"  config.json is at:")
    print(f"  {CONFIG_PATH}")
    print()
    print("  Save config.json and restart the script when done.")
    print("=" * w)
    print()
    input("  Press Enter to exit…")
    sys.exit(0)


def check_config_wizard():
    """
    If any required coordinate is null, show the setup menu and either
    run EZ Config (guided F7 capture) or print manual instructions.
    Only shown when something is actually missing — never during normal runs.
    """
    needs = (
        CONFIG.get("place_bet_enter_x") is None or
        CONFIG.get("place_bet_enter_y") is None or
        CONFIG.get("place_button_x")    is None or
        CONFIG.get("place_button_y")    is None or
        CONFIG.get("increase_button_x") is None or
        CONFIG.get("increase_button_y") is None or
        not CONFIG.get("monitor_configured", False)
    )
    if not needs:
        return

    w = 62
    print()
    print("=" * w)
    print("  GTA V Inside Track Auto Bettor — FIRST TIME SETUP")
    print("=" * w)
    print()
    print("  Some required button coordinates are not yet configured.")
    print("  How would you like to set them up?")
    print()
    print("  [1]  MANUAL CONFIG")
    print("       Edit config.json yourself with a text editor.")
    print()
    print("  [2]  EZ CONFIG  (recommended)")
    print("       Guided setup — hover over each button in GTA and")
    print(f"       press  {CONFIG.get('coords_hotkey','F7').upper()}  to save it automatically.")
    print()
    print("=" * w)

    choice = ""
    while choice not in ("1", "2"):
        try:
            choice = input("  Enter 1 or 2: ").strip()
        except KeyboardInterrupt:
            print("\n  Cancelled.")
            sys.exit(0)

    if choice == "1":
        _manual_config_wizard()
    else:
        _ez_config_wizard()


def main():
    check_config_wizard()

    preset, amount = _resolve_preset()
    print("=" * 55)
    print("  GTA V Inside Track Auto Bettor")
    print("=" * 55)
    print(f"  bet_presets : {preset}  (${amount})")
    print(f"  monitor    : {CONFIG['monitor']}")
    print(f"  overlay    : {'ON' if CONFIG.get('show_overlay', False) else 'OFF'}")
    print(f"  logging    : {'ON → ' + LOG_PATH if CONFIG['log_all_bets'] else 'OFF'}")
    print()
    print(f"  {CONFIG['debug_hotkey'].upper():<4} Debug OCR dump")
    print(f"  {CONFIG['start_hotkey'].upper():<4} Start betting loop")
    print(f"  {CONFIG['pause_hotkey'].upper():<4} Pause/resume loop")
    print(f"  {CONFIG['stop_hotkey'].upper():<4} Stop{' and close terminal' if CONFIG.get('close_terminal_on_stop', True) else ''}")
    print("=" * 55)
    print()

    keyboard.add_hotkey(CONFIG["debug_hotkey"], debug_ocr)
    keyboard.add_hotkey(CONFIG["start_hotkey"], start)
    keyboard.add_hotkey(CONFIG["pause_hotkey"], toggle_pause)
    keyboard.add_hotkey(CONFIG["stop_hotkey"],  stop)
    keyboard.wait()


if __name__ == "__main__":
    if "--list-monitors" in sys.argv:
        with mss.MSS() as sct:
            for i, m in enumerate(sct.monitors):
                print(f"  {i}: {m}{'  ← virtual combined, dont use' if i == 0 else ''}")
    else:
        main()