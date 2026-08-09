"""
GTA V Enhanced - Inside Track FULLY AUTOMATED betting bot

No test loop - 100% automatic gambling once started.
"""

import ctypes
import json
import os
import re
import sys
import time
import threading

import cv2
import keyboard
import mss
import numpy as np
import pytesseract
from pytesseract import Output


# ============================== TESSERACT ==============================

pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)


# ============================== CONFIG ==============================

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "config.json"
)


DEFAULT_CONFIG = {
    "monitor": 2,
    "confidence_threshold": 40,

    # General timing
    "cooldown_seconds": 1.0,
    "startup_delay_seconds": 5.0,
    "race_finish_wait_seconds": 8.0,
    
    # Betting flow delays
    "button_click_delay_seconds": 0.5,
    "after_place_click_delay": 1.0,
    "after_amount_set_delay_seconds": 0.5,
    "after_confirm_delay": 0.5,
    "after_again_delay_seconds": 1.0,

    # Main controls
    "start_hotkey": "f9",
    "stop_hotkey": "f10",

    # OCR regions
    "odds_region_width_fraction": 0.25,
    "odds_region_height": 60,

    # Offsets for clicking detected elements
    "odds_click_x_offset": 0,
    "odds_click_y_offset": 0,

    # ------------------------------------------------------------------
    # Bet amounts presets
    # ------------------------------------------------------------------
    "bet_ammounts": {
        "LOW": 1500,
        "MEDIUM": 3500,
        "HIGH": 7500,
        "MAX": 10000
    },

    "default_bet_ammounts": "LOW",

    # ------------------------------------------------------------------
    # Bet logging
    # ------------------------------------------------------------------
    "LogAllbets": True,
    "bet_log_file": "log.json",

    # ------------------------------------------------------------------
    # Calibration - REQUIRED for automation
    # ------------------------------------------------------------------
    "increase_button_x": None,
    "increase_button_y": None,

    # [x, y, width, height] - region showing current bet amount
    "bet_amount_region": None,

    # Bet amount adjustment timing
    "bet_click_cooldown": 0.05,
    "max_bet_adjust_attempts": 100,
    
    # ------------------------------------------------------------------
    # Automation settings
    # ------------------------------------------------------------------
    "betting_strategy": "RIGHTMOST",
    "auto_continue": True,
    "confirm_button_text": "PLACE BET",
}


def load_config():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        print(f"[config] Created default config at: {CONFIG_PATH}")
        return dict(DEFAULT_CONFIG)

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user_config = json.load(f)
    except json.JSONDecodeError as e:
        print(f"[!] Invalid JSON in config: {e}")
        return dict(DEFAULT_CONFIG)

    merged = dict(DEFAULT_CONFIG)
    merged.update(user_config)
    
    # Merge nested dicts
    default_bet_ammounts = dict(DEFAULT_CONFIG["bet_ammounts"])
    user_bet_ammounts = user_config.get("bet_ammounts", {})
    if isinstance(user_bet_ammounts, dict):
        default_bet_ammounts.update(user_bet_ammounts)
    merged["bet_ammounts"] = default_bet_ammounts

    return merged


CONFIG = load_config()


# ============================== CONSTANTS ==============================

MONITOR_INDEX = CONFIG["monitor"]
CONFIDENCE_THRESHOLD = CONFIG["confidence_threshold"]
COOLDOWN_SECONDS = CONFIG["cooldown_seconds"]


# ============================== RUNTIME STATE ==============================

_running = False
_start_time = None
_last_bet_time = 0
_current_bet_amount = 0


# ============================== BET LOGGING ==============================

LOG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    CONFIG.get("bet_log_file", "log.json")
)


def log_bet(bet_data):
    if not CONFIG.get("LogAllbets", False):
        return

    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
            if not isinstance(logs, list):
                logs = []
        else:
            logs = []
    except (json.JSONDecodeError, OSError):
        logs = []

    logs.append(bet_data)

    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=2)
    except OSError as e:
        print(f"[!] Could not write bet log: {e}")


def get_bet_ammounts_amount(bet_ammounts):
    presets = CONFIG.get("bet_ammounts", {})
    if bet_ammounts not in presets:
        available = ", ".join(presets.keys())
        raise ValueError(f"Unknown bet_ammounts '{bet_ammounts}'. Available: {available}")
    return presets[bet_ammounts]


# ======================= SCREEN CAPTURE =======================

def grab_screen():
    with mss.MSS() as sct:
        monitor = sct.monitors[MONITOR_INDEX]
        shot = sct.grab(monitor)
        img = np.array(shot)
        img_bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        return img_bgr, monitor


def grab_region(region):
    """
    Capture a specific region [x, y, width, height] in monitor-relative coordinates.
    Returns just the image.
    """
    with mss.MSS() as sct:
        monitor = {
            "left": region[0],
            "top": region[1],
            "width": region[2],
            "height": region[3]
        }
        shot = sct.grab(monitor)
        img = np.array(shot)
        img_bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        return img_bgr


# ======================= LOW-LEVEL INPUT =======================

PUL = ctypes.POINTER(ctypes.c_ulong)


class MouseInput(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", PUL)]


class Input_I(ctypes.Union):
    _fields_ = [("mi", MouseInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", Input_I)]


INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


def _virtual_screen_rect():
    get_system_metrics = ctypes.windll.user32.GetSystemMetrics
    return (
        get_system_metrics(SM_XVIRTUALSCREEN),
        get_system_metrics(SM_YVIRTUALSCREEN),
        get_system_metrics(SM_CXVIRTUALSCREEN),
        get_system_metrics(SM_CYVIRTUALSCREEN)
    )


def _send(flags, dx=0, dy=0):
    extra = ctypes.pointer(ctypes.c_ulong(0))
    mouse_input = MouseInput(dx, dy, 0, flags, 0, extra)
    input_event = Input(INPUT_MOUSE, Input_I(mi=mouse_input))
    ctypes.windll.user32.SendInput(1, ctypes.pointer(input_event), ctypes.sizeof(Input))


def focus_gta_window():
    hwnd = ctypes.windll.user32.FindWindowW(None, "Grand Theft Auto V")
    if hwnd == 0:
        found = []
        def enum_handler(h, _):
            length = ctypes.windll.user32.GetWindowTextLengthW(h)
            if length > 0:
                buffer = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(h, buffer, length + 1)
                if "grand theft auto" in buffer.value.lower():
                    found.append((h, buffer.value))
            return True
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        ctypes.windll.user32.EnumWindows(EnumWindowsProc(enum_handler), 0)
        if found:
            hwnd, title = found[0]
            print(f"[focus] Found window: '{title}'")
        else:
            print("[focus] GTA window not found.")
            return False
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.1)
    return True


def move_to(screen_x, screen_y):
    vx, vy, vw, vh = _virtual_screen_rect()
    norm_x = int((screen_x - vx) * 65535 / max(vw - 1, 1))
    norm_y = int((screen_y - vy) * 65535 / max(vh - 1, 1))
    _send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, norm_x, norm_y)


def mouse_down():
    _send(MOUSEEVENTF_LEFTDOWN)


def mouse_up():
    _send(MOUSEEVENTF_LEFTUP)


def move_and_click(screen_x, screen_y, delay=None):
    if delay is None:
        delay = CONFIG.get("button_click_delay_seconds", 0.1)
    move_to(screen_x, screen_y)
    time.sleep(delay)
    mouse_down()
    time.sleep(0.05)
    mouse_up()


def click_at(x, y):
    """Quick click without extra delays."""
    move_to(x, y)
    time.sleep(0.05)
    mouse_down()
    time.sleep(0.05)
    mouse_up()


# ======================= MOUSE CALIBRATION =======================

class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def get_mouse_position():
    point = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def print_mouse_position():
    abs_x, abs_y = get_mouse_position()
    with mss.MSS() as sct:
        monitor = sct.monitors[MONITOR_INDEX]
    rel_x = abs_x - monitor["left"]
    rel_y = abs_y - monitor["top"]
    print(f"[coords] Absolute: ({abs_x}, {abs_y})")
    print(f"[coords] Monitor-relative: ({rel_x}, {rel_y})")
    print("[coords] Use monitor-relative for increase_button_x/y")
    print("[coords] For bet_amount_region, record top-left and size")


# ======================= OCR HELPERS =======================

def ocr_text_in_image(img_bgr, whitelist=None):
    """
    Perform OCR on an image and return all detected text with confidence.
    Returns list of (text, confidence, x, y, w, h)
    """
    scale = 2
    img_big = cv2.resize(img_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    
    config = r"--psm 7"  # Single line mode for UI elements
    if whitelist:
        config += f" -c tessedit_char_whitelist={whitelist}"
    
    data = pytesseract.image_to_data(img_big, config=config, output_type=Output.DICT)
    
    results = []
    for i, word in enumerate(data["text"]):
        cleaned = word.strip()
        if not cleaned:
            continue
        try:
            conf = int(float(data["conf"][i]))
        except (ValueError, TypeError):
            conf = -1
        
        if conf >= CONFIDENCE_THRESHOLD:
            x = data["left"][i] // scale
            y = data["top"][i] // scale
            w = data["width"][i] // scale
            h = data["height"][i] // scale
            results.append((cleaned, conf, x, y, w, h))
    
    return results


def find_text_boxes(img_bgr, target_text, case_sensitive=False):
    """
    Find all occurrences of target_text in the image.
    Returns list of (x, y, w, h, confidence) in monitor-relative coordinates.
    """
    scale = 2
    img_big = cv2.resize(img_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    
    data = pytesseract.image_to_data(img_big, config=r"--psm 11", output_type=Output.DICT)
    
    boxes = []
    target = target_text if case_sensitive else target_text.upper()
    
    for i, word in enumerate(data["text"]):
        cleaned = word.strip()
        if not cleaned:
            continue
        
        check = cleaned if case_sensitive else cleaned.upper()
        
        if target in check or check in target:
            try:
                conf = int(float(data["conf"][i]))
            except (ValueError, TypeError):
                conf = -1
            
            if conf >= CONFIDENCE_THRESHOLD:
                x = data["left"][i] // scale
                y = data["top"][i] // scale
                w = data["width"][i] // scale
                h = data["height"][i] // scale
                boxes.append((x, y, w, h, conf))
    
    return boxes


def find_place_boxes(img_bgr):
    """Find all PLACE buttons on screen."""
    return find_text_boxes(img_bgr, "PLACE")


def find_confirm_button(img_bgr):
    """Find the confirm/place bet button."""
    confirm_text = CONFIG.get("confirm_button_text", "PLACE BET")
    boxes = find_text_boxes(img_bgr, confirm_text)
    return boxes


def find_again_boxes(img_bgr):
    """Find AGAIN button."""
    return find_text_boxes(img_bgr, "AGAIN")


# ======================= BET AMOUNT HANDLING =======================

def read_bet_amount():
    """
    Read the current bet amount from the configured region using OCR.
    Returns the numeric amount or None if not found.
    """
    region = CONFIG.get("bet_amount_region")
    if not region:
        print("[!] bet_amount_region not configured in config.json")
        return None
    
    try:
        img = grab_region(region)
        # OCR with digits and $ only
        results = ocr_text_in_image(img, whitelist="0123456789$,")
        
        for text, conf, x, y, w, h in results:
            # Extract numbers from text like "$1,500" or "1500"
            cleaned = re.sub(r'[^\d]', '', text)
            if cleaned:
                try:
                    amount = int(cleaned)
                    print(f"[amount] Detected: ${amount} (confidence: {conf})")
                    return amount
                except ValueError:
                    continue
        
        print(f"[amount] Could not parse amount from OCR results: {results}")
        return None
        
    except Exception as e:
        print(f"[!] Error reading bet amount: {e}")
        return None


def click_increase_button():
    """Click the bet increase (+) button."""
    x = CONFIG.get("increase_button_x")
    y = CONFIG.get("increase_button_y")
    
    if x is None or y is None:
        print("[!] increase_button_x/y not configured in config.json")
        return False
    
    # Convert to absolute screen coordinates
    with mss.MSS() as sct:
        monitor = sct.monitors[MONITOR_INDEX]
        abs_x = monitor["left"] + x
        abs_y = monitor["top"] + y
    
    click_at(abs_x, abs_y)
    return True


def adjust_bet_amount(target_amount):
    """
    Adjust the bet amount to match the target using the increase button.
    Reads current amount and clicks increase until target is reached.
    """
    print(f"[adjust] Target bet amount: ${target_amount}")
    
    max_attempts = CONFIG.get("max_bet_adjust_attempts", 60)
    cooldown = CONFIG.get("bet_click_cooldown", 0.05)
    
    for attempt in range(max_attempts):
        if not _running:
            return False
        
        current = read_bet_amount()
        
        if current is None:
            print(f"[adjust] Attempt {attempt+1}: Could not read amount, retrying...")
            time.sleep(0.1)
            continue
        
        print(f"[adjust] Attempt {attempt+1}: Current=${current}, Target=${target_amount}")
        
        if current >= target_amount:
            print(f"[adjust] Target reached! Final amount: ${current}")
            _current_bet_amount = current
            return True
        
        # Click increase button
        if not click_increase_button():
            return False
        
        time.sleep(cooldown)
    
    print(f"[!] Failed to reach target amount after {max_attempts} attempts")
    return False


# ======================= ODDS DETECTION =======================

def find_odds_near_place(img_bgr, place_x, place_y, place_w, place_h, monitor):
    """
    Look for odds text near a PLACE button.
    Typically odds are displayed above or to the left of the PLACE button.
    Returns the odds string or None.
    """
    # Define search region above and to the left of PLACE button
    odds_width_frac = CONFIG.get("odds_region_width_fraction", 0.25)
    odds_height = CONFIG.get("odds_region_height", 60)
    
    region_width = int(place_w / odds_width_frac)
    region_height = odds_height
    
    # Region above the PLACE button
    left = max(0, place_x - region_width)
    top = max(0, place_y - region_height)
    width = place_w + region_width
    height = region_height + place_h
    
    # Extract region from image
    region = img_bgr[top:top+height, left:left+width]
    
    if region.size == 0:
        return None
    
    # OCR for odds (looking for patterns like "2/1", "5/2", etc)
    results = ocr_text_in_image(region, whitelist="0123456789/")
    
    odds_found = []
    for text, conf, x, y, w, h in results:
        # Look for odds pattern like "2/1", "5/2", "10/1"
        if re.match(r'^\d+/\d+$', text):
            odds_found.append((text, conf))
    
    if odds_found:
        # Return the highest confidence odds
        best = max(odds_found, key=lambda x: x[1])
        print(f"[odds] Found odds: {best[0]} (confidence: {best[1]})")
        return best[0]
    
    return None


# ======================= BETTING ACTIONS =======================

def click_place_button(place_box, monitor):
    """Click a PLACE button given its bounding box."""
    x, y, w, h, conf = place_box
    
    click_x = monitor["left"] + x + w // 2
    click_y = monitor["top"] + y + h // 2
    
    print(f"[place] Clicking PLACE at ({click_x}, {click_y}) [conf={conf}]")
    
    focus_gta_window()
    move_and_click(click_x, click_y, delay=CONFIG.get("button_click_delay_seconds", 0.5))
    
    return True


def click_confirm_button(confirm_box, monitor):
    """Click the confirm/place bet button."""
    x, y, w, h, conf = confirm_box
    
    click_x = monitor["left"] + x + w // 2
    click_y = monitor["top"] + y + h // 2
    
    print(f"[confirm] Clicking confirm at ({click_x}, {click_y})")
    
    move_and_click(click_x, click_y, delay=CONFIG.get("button_click_delay_seconds", 0.5))
    return True


def click_again_button(again_box, monitor):
    """Click the AGAIN button to start next race."""
    x, y, w, h, conf = again_box
    
    click_x = monitor["left"] + x + w // 2
    click_y = monitor["top"] + y + h // 2
    
    print(f"[again] Clicking AGAIN at ({click_x}, {click_y})")
    
    move_and_click(click_x, click_y, delay=CONFIG.get("button_click_delay_seconds", 0.5))
    return True


def select_horse_and_bet(img_bgr, monitor):
    """
    Main betting workflow:
    1. Find all PLACE buttons
    2. Select horse (rightmost or by strategy)
    3. Click PLACE
    4. Adjust bet amount
    5. Confirm bet
    """
    global _current_bet_amount
    
    # Find all PLACE buttons
    place_boxes = find_place_boxes(img_bgr)
    
    if not place_boxes:
        print("[!] No PLACE buttons found")
        return False
    
    print(f"[bet] Found {len(place_boxes)} PLACE button(s)")
    
    # Select horse based on strategy
    strategy = CONFIG.get("betting_strategy", "RIGHTMOST")
    
    if strategy == "RIGHTMOST":
        # Select rightmost PLACE button (typically the last horse)
        selected = max(place_boxes, key=lambda box: box[0])
        print("[bet] Strategy: RIGHTMOST horse selected")
    else:
        # Default to first/leftmost
        selected = place_boxes[0]
        print("[bet] Strategy: LEFTMOST horse selected")
    
    # Get odds for logging
    odds = find_odds_near_place(img_bgr, selected[0], selected[1], 
                                 selected[2], selected[3], monitor)
    
    # Click PLACE to open bet dialog
    if not click_place_button(selected, monitor):
        return False
    
    time.sleep(CONFIG.get("after_place_click_delay", 1.0))
    
    # Get target bet amount
    try:
        _, target_amount = get_configured_bet_amount()
    except ValueError as e:
        print(f"[!] {e}")
        target_amount = 1500  # fallback
    
    # Adjust bet amount
    if not adjust_bet_amount(target_amount):
        print("[!] Failed to adjust bet amount")
        return False
    
    time.sleep(CONFIG.get("after_amount_set_delay_seconds", 0.2))
    
    # Look for and click confirm button
    confirm_found = False
    for _ in range(5):  # Retry a few times
        img_bgr, monitor = grab_screen()
        confirm_boxes = find_confirm_button(img_bgr)
        
        if confirm_boxes:
            click_confirm_button(confirm_boxes[0], monitor)
            confirm_found = True
            break
        
        time.sleep(0.2)
    
    if not confirm_found:
        print("[!] Confirm button not found, attempting to proceed anyway")
    
    time.sleep(CONFIG.get("after_confirm_delay", 0.5))
    
    # Log the bet
    bet_data = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "strategy": strategy,
        "amount": _current_bet_amount,
        "odds": odds,
        "place_confidence": selected[4]
    }
    log_bet(bet_data)
    print(f"[bet] Bet placed: ${bet_data['amount']} on horse with odds {odds}")
    
    return True


# ======================= AUTOMATION LOOP =======================

def run_automation_loop():
    """
    Main automation loop - fully automatic betting.
    States: WAITING_FOR_RACE -> PLACING_BET -> RACE_RUNNING -> WAITING_FOR_RACE
    """
    global _last_bet_time
    
    if not _running:
        return
    
    if not wait_until_start_delay_finished():
        return
    
    print("[+] AUTOMATION STARTED - 100% Automatic Betting")
    print("[+] Press F10 to stop at any time")
    
    state = "WAITING_FOR_RACE"  # or "RACE_RUNNING"
    race_start_time = None
    
    while _running:
        try:
            img_bgr, monitor = grab_screen()
            
            if state == "WAITING_FOR_RACE":
                # Look for PLACE buttons - race is ready to bet
                place_boxes = find_place_boxes(img_bgr)
                
                if place_boxes:
                    print(f"[state] Race ready - {len(place_boxes)} horses available")
                    
                    # Place the bet
                    if select_horse_and_bet(img_bgr, monitor):
                        state = "RACE_RUNNING"
                        race_start_time = time.time()
                        _last_bet_time = time.time()
                    else:
                        print("[!] Failed to place bet, will retry...")
                
                # Also check for AGAIN button (in case we missed the transition)
                again_boxes = find_again_boxes(img_bgr)
                if again_boxes and CONFIG.get("auto_continue", True):
                    print("[state] Found AGAIN button, clicking to continue...")
                    click_again_button(again_boxes[0], monitor)
                    time.sleep(CONFIG.get("after_again_delay_seconds", 1.0))
            
            elif state == "RACE_RUNNING":
                # Wait for race to finish - look for AGAIN button
                elapsed = time.time() - race_start_time if race_start_time else 0
                min_wait = CONFIG.get("race_finish_wait_seconds", 8.0)
                
                # Only check for AGAIN after minimum wait time
                if elapsed >= min_wait:
                    again_boxes = find_again_boxes(img_bgr)
                    
                    if again_boxes:
                        print("[state] Race finished - AGAIN button detected")
                        
                        if CONFIG.get("auto_continue", True):
                            click_again_button(again_boxes[0], monitor)
                            time.sleep(CONFIG.get("after_again_delay_seconds", 1.0))
                        
                        state = "WAITING_FOR_RACE"
                        race_start_time = None
                    else:
                        # Still waiting for race to end
                        pass
                else:
                    # Still in minimum wait period
                    pass
            
            # Small delay between checks
            time.sleep(0.2)
            
        except KeyboardInterrupt:
            print("\n[!] Keyboard interrupt")
            stop_script()
            break
        except Exception as e:
            print(f"[!] Automation error: {e}")
            time.sleep(0.5)
    
    print("[+] Automation loop ended")


# ======================= RUNTIME CONTROL =======================

def is_running():
    return _running


def start_script():
    global _running, _start_time
    if _running:
        print("[!] Script already running.")
        return
    _running = True
    _start_time = time.time()
    print("[+] Script started.")


def stop_script():
    global _running
    if not _running:
        print("[!] Script not running.")
        return
    _running = False
    print("[+] Script stopped.")


def wait_until_start_delay_finished():
    delay = float(CONFIG.get("startup_delay_seconds", 5.0))
    end_time = time.time() + delay
    
    print(f"[+] Starting in {delay:.1f}s...")
    
    while _running and time.time() < end_time:
        remaining = end_time - time.time()
        print(f"\r[+] Starting in {max(0, remaining):.1f}s ", end="", flush=True)
        time.sleep(0.1)
    
    print()
    
    if not _running:
        print("[!] Startup cancelled.")
        return False
    
    print("[+] Starting automation!")
    return True


# ======================= HOTKEY SETUP =======================

def setup_hotkeys():
    start_key = CONFIG.get("start_hotkey", "f9")
    stop_key = CONFIG.get("stop_hotkey", "f10")
    
    keyboard.add_hotkey(start_key, start_script)
    keyboard.add_hotkey(stop_key, stop_script)
    
    print(f"[+] Start hotkey: {start_key.upper()}")
    print(f"[+] Stop hotkey:  {stop_key.upper()}")


# ======================= CONFIG SUMMARY =======================

def print_config_summary():
    _, amount = get_configured_bet_amount()
    
    print("\n========== CONFIG ==========")
    print(f"Monitor:            {MONITOR_INDEX}")
    print(f"Start hotkey:       {CONFIG['start_hotkey']}")
    print(f"Stop hotkey:        {CONFIG['stop_hotkey']}")
    print(f"Target bet:         ${amount}")
    print(f"Strategy:           {CONFIG.get('betting_strategy', 'RIGHTMOST')}")
    print(f"Auto-continue:      {CONFIG.get('auto_continue', True)}")
    
    # Check calibration
    inc_x = CONFIG.get("increase_button_x")
    inc_y = CONFIG.get("increase_button_y")
    bet_region = CONFIG.get("bet_amount_region")
    
    if inc_x is not None and inc_y is not None and bet_region is not None:
        print("Calibration:        CONFIGURED")
    else:
        print("Calibration:        NOT CONFIGURED - Run calibration first!")
        print("  Required in config.json:")
        print("    - increase_button_x/y")
        print("    - bet_amount_region [x, y, width, height]")
    
    print(f"Logging:            {CONFIG.get('LogAllbets', False)}")
    print("============================\n")


# ======================= CLEAN SHUTDOWN =======================

def shutdown():
    global _running
    _running = False
    try:
        keyboard.unhook_all()
    except Exception:
        pass
    print("[+] Shutdown complete.")


# ======================= MAIN =======================

def main():
    print("\n========================================")
    print(" GTA V HORSE RACE - FULLY AUTOMATED")
    print(" 100% Automatic - No Test Loop")
    print("========================================")
    
    print_config_summary()
    setup_hotkeys()
    
    print("[+] Ready. Press F9 to start automation.")
    print("[+] Press F10 to stop.")
    print()
    
    worker = None
    
    try:
        while True:
            # Start worker when F9 is pressed
            if _running and (worker is None or not worker.is_alive()):
                worker = threading.Thread(
                    target=run_automation_loop,
                    name="AutomationLoop",
                    daemon=True
                )
                worker.start()
            
            # Clear finished workers
            if worker is not None and not worker.is_alive():
                worker = None
            
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        print("\n[!] Ctrl+C received.")
    finally:
        shutdown()


if __name__ == "__main__":
    main()