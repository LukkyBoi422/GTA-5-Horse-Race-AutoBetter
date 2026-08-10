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
    "monitor": 1,   # unused now - capture is based on the GTA window itself, not a monitor. Kept so old configs still load.

    "game_window_title": "Grand Theft Auto V",   # also substring-matched as a fallback, in case the exact title differs
    "tesseract_path": r"C:\Program Files\Tesseract-OCR\tesseract.exe",

    "start_hotkey":  "f9",
    "stop_hotkey":   "f10",
    "debug_hotkey":  "f8",
    "coords_hotkey": "f7",

    "bet_presets": "LOW",
    "preset_LOW":    1500,
    "preset_MEDIUM": 3500,
    "preset_HIGH":   7500,
    "preset_MAX":    10000,
    # win% cutoffs that decide which preset above gets used - a horse's
    # true win probability >= threshold_max uses MAX, >= threshold_high
    # uses HIGH, >= threshold_medium uses MEDIUM, anything lower uses LOW.
    "bet_presets_threshold_max":    50,
    "bet_presets_threshold_high":   40,
    "bet_presets_threshold_medium": 30,

    "startup_delay_seconds": 1,
    "after_horse_select_seconds": 1.0,
    "post_bet_delay_seconds": 1,
    "again_poll_interval_seconds": 0.5,
    "after_again_delay_seconds": 1,
    "click_delay_seconds": 0.15,

    # Retry behavior for each step of the loop
    "place_bet_retry_attempts": 10,              # attempts to find+click PLACE BET entering the horse screen
    "place_bet_retry_delay_seconds": 0.75,
    "horse_select_retry_attempts": 15,           # attempts to find a horse to click
    "horse_select_retry_delay_seconds": 1.0,
    "horse_not_found_retry_delay_seconds": 2.0,  # extra pause before retrying the whole cycle if no horse is ever found
    "place_confirm_retry_attempts": 10,          # attempts to find+click PLACE to confirm the bet
    "place_confirm_retry_delay_seconds": 1.0,
    "verify_delay_seconds": 0.35,                # pause before re-checking the screen after a click (horse-screen load, AGAIN dismissal)

    # Every coordinate below is relative to the GTA window's client area
    # top-left corner (not a monitor) - so they keep working regardless of
    # which monitor the game is on or where the window sits. Still needs
    # recalibrating (coords_hotkey) if the game's resolution/aspect changes.

    # Coords of the > increase button on the bet screen.
    # Default values are for 1920x1080 GTA — use coords_hotkey to recalibrate if different.
    "increase_button_x": 2883,
    "increase_button_y": 985,
    # Coords of the bet amount number (the $100 display) on the bet screen.
    "bet_amount_x": 1319,
    "bet_amount_y": 518,
    "bet_amount_crop_width": 100,   # half-width of the OCR crop box around bet_amount_x/y (total box width = 2x this)
    "bet_amount_crop_height": 25,   # half-height of the OCR crop box
    # Fallback coords for the PLACE BET button if OCR can't find it.
    # Default is for 1920x1080 — use coords_hotkey to recalibrate.
    "place_button_x": 1302,
    "place_button_y": 776,
    "bet_amount_region": None,
    "bet_click_cooldown": 0.2,
    "max_bet_adjust_attempts": 120,
    "odds_click_x_offset": 0,
    "odds_click_y_offset": 0,
    "confidence_threshold": 40,
    "horse_row_confidence_offset": 20,   # how much lower than confidence_threshold to accept for the horse-name-row fallback
    "window_wait_poll_seconds": 2,       # how often to re-check for the GTA window while waiting for it to appear

    "log_all_bets": False,
    "close_terminal_on_stop": True   # set false to keep the window open after F10 - loop stops, but you can press start_hotkey again
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
    vx, vy, vw, vh = _virt()
    _send(_MOVE | _ABS | _VDESK,
          int((sx - vx) * 65535 / max(vw-1, 1)),
          int((sy - vy) * 65535 / max(vh-1, 1)))


def click(sx, sy):
    move_to(sx, sy); time.sleep(0.1)
    _send(_DN); time.sleep(0.05); _send(_UP)
    time.sleep(CONFIG["click_delay_seconds"])


def get_mouse_pos():
    pt = _PT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y

# ---------------------------------------------------------------------------
# Screen capture
# ---------------------------------------------------------------------------

def grab():
    """Returns (image, rect) for the GTA window's client area, or (None, None)
    if the window isn't found. Callers must handle the None case."""
    win = get_gta_window_rect()
    if win is None:
        return None, None
    with mss.MSS() as sct:
        shot = sct.grab(win)
        return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR), win

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


def click_best_horse(img_bgr, mon):
    """
    Find all horses on screen, pick the one with the best true win %,
    click it, and return (token, true_pct). Falls back to row detection.
    """
    tokens = find_horse_tokens(img_bgr)
    if len(tokens) >= 2:
        best_i, true_pct = pick_best_horse(tokens)
        chosen = tokens[best_i]
        sx = mon["left"] + chosen["cx"] + CONFIG["odds_click_x_offset"]
        sy = mon["top"]  + chosen["cy"] + CONFIG["odds_click_y_offset"]
        print(f"[horse] Clicking best horse {chosen['raw']} at ({sx},{sy})")
        focus_gta(); click(sx, sy)
        chosen["true_pct"] = true_pct
        return chosen
    elif len(tokens) == 1:
        chosen = tokens[0]
        sx = mon["left"] + chosen["cx"] + CONFIG["odds_click_x_offset"]
        sy = mon["top"]  + chosen["cy"] + CONFIG["odds_click_y_offset"]
        print(f"[horse] Only one odds found ({chosen['raw']}), clicking at ({sx},{sy})")
        focus_gta(); click(sx, sy)
        chosen["true_pct"] = 0
        return chosen

    # Fall back: find horse rows by name text
    # Need at least 4 rows — if fewer, screen hasn't loaded yet
    rows = find_horse_rows(img_bgr)
    if len(rows) < 4:
        print(f"[horse] Only {len(rows)} rows found — screen still loading.")
        return None

    # Just pick the first (topmost) horse row
    rx, ry = rows[0]
    sx = mon["left"] + rx + CONFIG["odds_click_x_offset"]
    sy = mon["top"]  + ry + CONFIG["odds_click_y_offset"]
    print(f"[horse] Clicking first horse row at ({sx},{sy})  [{len(rows)} rows found]")
    focus_gta(); click(sx, sy)
    return {"raw": "row", "odds": None, "cx": rx, "cy": ry, "conf": -1, "true_pct": 0}

# ---------------------------------------------------------------------------
# PLACE button
# ---------------------------------------------------------------------------

def click_place(img_bgr, mon):
    """
    Click the PLACE BET button once.

    OCR can sometimes return only the word PLACE even though the actual
    clickable control is the larger PLACE BET button.  Prefer the center of
    the whole PLACE/BET text when both words are detected, otherwise use
    the PLACE word center.  The caller is responsible for retrying if the
    screen did not change.
    """
    thresh = CONFIG["confidence_threshold"]
    data, scale = _ocr_data(img_bgr)

    place = None
    bet = None

    for i, word in enumerate(data["text"]):
        wrd = word.strip().upper()
        if wrd not in ("PLACE", "BET"):
            continue

        conf = int(float(data["conf"][i])) if data["conf"][i] not in ("", "-1") else -1
        if conf < thresh:
            continue

        x  = data["left"][i] // scale
        y  = data["top"][i] // scale
        tw = data["width"][i] // scale
        th = data["height"][i] // scale
        box = (x, y, x + tw, y + th, conf)

        if wrd == "PLACE":
            place = box
        elif wrd == "BET":
            bet = box

    if place:
        if bet:
            # Only combine BET when it is on roughly the same row as PLACE.
            pcx = (place[0] + place[2]) // 2
            pcy = (place[1] + place[3]) // 2
            bcx = (bet[0] + bet[2]) // 2
            bcy = (bet[1] + bet[3]) // 2

            if abs(pcy - bcy) <= max(place[3] - place[1], bet[3] - bet[1]) * 2:
                cx = (min(place[0], bet[0]) + max(place[2], bet[2])) // 2
                cy = (min(place[1], bet[1]) + max(place[3], bet[3])) // 2
                conf = min(place[4], bet[4])
                sx, sy = mon["left"] + cx, mon["top"] + cy
                print(f"[bet] Clicking PLACE BET center at ({sx},{sy})  conf={conf}")
                focus_gta()
                click(sx, sy)
                return True

        cx = (place[0] + place[2]) // 2
        cy = (place[1] + place[3]) // 2
        sx, sy = mon["left"] + cx, mon["top"] + cy
        print(f"[bet] Clicking PLACE at ({sx},{sy})  conf={place[4]}")
        focus_gta()
        click(sx, sy)
        return True

    px = CONFIG.get("place_button_x")
    py = CONFIG.get("place_button_y")
    if px is not None and py is not None:
        sx, sy = mon["left"] + px, mon["top"] + py
        print(f"[bet] PLACE OCR miss — using fallback coords ({sx},{sy})")
        focus_gta()
        click(sx, sy)
        return True

    print("[bet] PLACE button not found and no fallback coords set.")
    return False

# ---------------------------------------------------------------------------
# Bet amount — find > button, read amount from a TIGHT crop just left of it
# ---------------------------------------------------------------------------

def _find_increase_button(img_bgr):
    """OCR scan for > button. Returns image-relative (cx, cy) or None."""
    data, scale = _ocr_data(img_bgr)
    for i, word in enumerate(data["text"]):
        if word.strip() not in (">", "»", "›"):
            continue
        conf = int(float(data["conf"][i])) if data["conf"][i] not in ("", "-1") else -1
        if conf < CONFIG["confidence_threshold"]:
            continue
        x  = data["left"][i]  // scale; y  = data["top"][i]   // scale
        tw = data["width"][i] // scale; th = data["height"][i]// scale
        return x + tw//2, y + th//2
    return None


def hold_click(sx, sy, duration):
    move_to(sx, sy)
    time.sleep(0.1)
    _send(_DN)
    time.sleep(duration)
    _send(_UP)


def set_bet_amount(target: int) -> bool:
    """
    Click > and keep reading the on-screen number until it hits target.
    Reads the number from a tight crop just left of the > button.
    Stops as soon as the value meets or exceeds target.
    """
    bx = CONFIG.get("increase_button_x")
    by = CONFIG.get("increase_button_y")
    if bx is None or by is None:
        print("[bet] increase_button_x/y not set — use F7 to calibrate.")
        return True

    win = get_gta_window_rect()
    if win is None:
        print("[bet] GTA window not found - can't set bet amount.")
        return False
    abs_x = win["left"] + bx
    abs_y = win["top"]  + by

    # The bet amount number is at window-relative ~(1319, 518) on a 1920x1080
    # game resolution. Use bet_amount_region from config if set, otherwise
    # use a fixed crop around the known position.
    amt_x = CONFIG.get("bet_amount_x", 1319)
    amt_y = CONFIG.get("bet_amount_y", 518)

    def read_amount():
        img, _ = grab()
        cw = CONFIG.get("bet_amount_crop_width", 100)
        ch = CONFIG.get("bet_amount_crop_height", 25)
        x1 = max(0, amt_x - cw)
        x2 = amt_x + cw
        y1 = max(0, amt_y - ch)
        y2 = amt_y + ch
        val = _ocr_digits(img[y1:y2, x1:x2])
        # Ignore anything above the max possible bet — it's the payout number
        max_bet = CONFIG.get("preset_MAX", 10000)
        if val is not None and val > max_bet:
            return None
        return val

    current = read_amount()
    if current is None:
        print("[bet] Couldn't read bet amount — clicking anyway.")
        current = 100  # assume default

    if current >= target:
        print(f"[bet] Already at ${current}, target ${target}.")
        return True

    print(f"[bet] ${current} → ${target}…")
    cooldown = CONFIG.get("bet_click_cooldown", 0.2)
    cap = CONFIG.get("max_bet_adjust_attempts", 120)
    attempts = 0

    while attempts < cap:
        click(abs_x, abs_y)
        attempts += 1
        time.sleep(cooldown)

        val = read_amount()
        if val is not None:
            current = val
            if current >= target:
                print(f"[bet] Reached ${current}.")
                return True

    print(f"[bet] Stopped at ${current} after {attempts} attempts.")
    return True

# ---------------------------------------------------------------------------
# AGAIN button
# ---------------------------------------------------------------------------

def find_again(img_bgr, mon):
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
        return mon["left"] + x + tw//2, mon["top"] + y + th//2
    return None

# ---------------------------------------------------------------------------
# Auto-bet loop
# ---------------------------------------------------------------------------

_running = False
_thread: threading.Thread | None = None


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
    global _running
    delay = CONFIG["startup_delay_seconds"]
    print(f"[loop] Starting in {delay}s…")
    _sleep(delay)

    cycle = 0
    while _running:
        cycle += 1
        print(f"\n[loop] ── Cycle {cycle} ──────────────────────────────────")

        # Step 1: Click PLACE BET to enter the horse-select screen.
        # Do not assume the click worked just because OCR found the button:
        # verify that the horse-select UI appears before continuing.
        entered_horse_screen = False

        for attempt in range(CONFIG["place_bet_retry_attempts"]):
            img, mon = grab()

            if click_place(img, mon):
                print(f"[loop] PLACE BET click sent — verifying horse screen…")

                # Give the UI a moment to react, then check for horse odds/rows.
                _sleep(CONFIG["verify_delay_seconds"])
                verify_img, verify_mon = grab()

                tokens = find_horse_tokens(verify_img)
                rows = find_horse_rows(verify_img)

                if len(tokens) >= 1 or len(rows) >= 4:
                    print("[loop] PLACE BET click confirmed — horse screen loaded.")
                    entered_horse_screen = True
                    break

                print("[loop] PLACE BET click did not change the screen — retrying…")
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
            img, mon = grab()
            horse = click_best_horse(img, mon)
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
            img, mon = grab()
            if click_place(img, mon):
                log_bet(preset, amount, horse)
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
        while _running:
            img, mon = grab()
            pos = find_again(img, mon)
            if pos is None:
                time.sleep(interval); continue
            sx, sy = pos
            print(f"[loop] AGAIN at ({sx},{sy}) — clicking once…")
            focus_gta(); click(sx, sy)

            # Verify the click actually dismissed AGAIN. If it is still visible,
            # retry with one additional click instead of doing a 3-click burst.
            time.sleep(CONFIG["verify_delay_seconds"])
            verify_img, verify_mon = grab()
            if find_again(verify_img, verify_mon) is None:
                print("[loop] AGAIN dismissed after 1 click.")
                break

            print("[loop] AGAIN still visible — retrying with 1 click…")
            focus_gta(); click(sx, sy)
            time.sleep(CONFIG["verify_delay_seconds"])
            verify_img, verify_mon = grab()
            if find_again(verify_img, verify_mon) is None:
                print("[loop] AGAIN dismissed after retry.")
                break
            print("[loop] AGAIN still visible after retry; continuing to poll.")
        if not _running: break

        # Step 7: Wait for horse-select screen to reload, then loop back
        _sleep(CONFIG["after_again_delay_seconds"])

    print("[loop] Stopped.")


def start():
    global _running, _thread
    if _running:
        print("[hotkey] Already running."); return
    _running = True
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()
    print(f"[hotkey] {CONFIG['start_hotkey'].upper()} – loop started.")


def stop():
    global _running
    _running = False
    print(f"[hotkey] {CONFIG['stop_hotkey'].upper()} – stopping…")
    time.sleep(0.5)
    if CONFIG.get("close_terminal_on_stop", True):
        os._exit(0)
    else:
        print("[hotkey] Loop stopped. Terminal staying open (close_terminal_on_stop=false) — "
              f"press {CONFIG['start_hotkey'].upper()} to run again, or Ctrl+C to exit.")

# ---------------------------------------------------------------------------
# Debug / calibration
# ---------------------------------------------------------------------------

def coords_finder():
    ax, ay = get_mouse_pos()
    with mss.MSS() as sct:
        mon = sct.monitors[CONFIG["monitor"]]
    rx, ry = ax - mon["left"], ay - mon["top"]
    print(f"\n[coords] Absolute: ({ax},{ay})   Monitor-relative: ({rx},{ry})")
    print(f"[coords] → increase_button_x/y  use: {rx}, {ry}")
    print(f"[coords] → bet_amount_region     hover corners, compute [x1,y1,w,h]")


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

    region = CONFIG.get("bet_amount_region")
    if region:
        rx, ry, rw, rh = region
        val = _ocr_digits(img[ry:ry+rh, rx:rx+rw])
        print(f"\n[debug] bet_amount_region={region}  OCR reads: ${val}")
    else:
        print("\n[debug] bet_amount_region not set in config.json")

    bx = CONFIG.get("increase_button_x")
    by = CONFIG.get("increase_button_y")
    if bx and by:
        print(f"[debug] increase_button at ({bx},{by}) [from config]")
    else:
        btn = _find_increase_button(img)
        print(f"[debug] > button OCR: {btn}")

    ax, ay = get_mouse_pos()
    rx2, ry2 = ax - mon["left"], ay - mon["top"]
    print(f"[debug] Mouse monitor-relative: ({rx2},{ry2})")
    print("[debug] Saved debug_raw.png + debug_ocr_overlay.png")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    preset, amount = _resolve_preset()
    print("=" * 55)
    print("  GTA V Inside Track Auto Bettor")
    print("=" * 55)
    print(f"  bet_presets : {preset}  (${amount})")
    print(f"  monitor    : {CONFIG['monitor']}")
    print(f"  logging    : {'ON → ' + LOG_PATH if CONFIG['log_all_bets'] else 'OFF'}")
    print()
    print(f"  {CONFIG['coords_hotkey'].upper():<4} Coord finder great for making configs")
    print(f"  {CONFIG['debug_hotkey'].upper():<4} Debug OCR dump [REALLY ONLY FOR DEVELOPERS LIKE ME]")
    print(f"  {CONFIG['start_hotkey'].upper():<4} Start betting loop")
    print(f"  {CONFIG['stop_hotkey'].upper():<4} Stop{' and close terminal' if CONFIG.get('close_terminal_on_stop', True) else ''}")
    print("=" * 55)
    print()

    keyboard.add_hotkey(CONFIG["coords_hotkey"], coords_finder)
    keyboard.add_hotkey(CONFIG["debug_hotkey"],  debug_ocr)
    keyboard.add_hotkey(CONFIG["start_hotkey"],  start)
    keyboard.add_hotkey(CONFIG["stop_hotkey"],   stop)
    keyboard.wait()


if __name__ == "__main__":
    if "--list-monitors" in sys.argv:
        with mss.MSS() as sct:
            for i, m in enumerate(sct.monitors):
                print(f"  {i}: {m}{'  ← virtual combined, dont use' if i == 0 else ''}")
    else:
        main()