# GTA-5-Horse-Race-AutoBetter

Automation script for the GTA V Inside Track horse racing interface. Automatically analyzes odds, picks the horse with the highest true win probability, sets the correct bet amount, and repeats — hands free.

Supports both **GTA V Legacy** and **GTA V Enhanced**.

## Features

- **Odds analysis** — calculates true win probability from displayed odds and always bets on the statistically best horse
- **Tiered betting** — automatically scales bet size (LOW / MEDIUM / HIGH / MAX) based on how confident the odds are
- **Time-based bet setting** — holds the increase button for a calibrated duration instead of slow OCR polling
- **Human-like mouse movement** — gradual bezier cursor movement with random micro-jitter (optional)
- **Session P&L tracking** — running win/loss total printed after every race
- **EZ Config wizard** — guided first-time setup, hover each button and press F7 to save

## Requirements

- Windows
- Python 3.10+
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract/releases/tag/5.5.3)

Install Python dependencies:

```
pip install opencv-python pytesseract mss keyboard numpy
```

## Setup

1. Install Tesseract OCR from the link above. Let the installer add it to PATH, or set `tesseract_path` in `config.json`.
2. Clone or download this repository.
3. Run the script:
   ```
   python main.py
   ```
4. On first launch, the **EZ Config wizard** will appear. Follow the 4 steps to configure your button coordinates — no manual editing required.

## EZ Config

The wizard guides you through 4 buttons in order. For each one, switch to GTA V, hover your mouse over the button, then press **F7** to save it.

| Step | Button | Where |
|------|--------|--------|
| 1 | PLACE BET | Main Inside Track screen |
| 2 | Bet amount | The `$100` number on the bet screen |
| 3 | `>` increase | The increase button on the bet screen |
| 4 | PLACE | The confirm button on the bet screen |

Coordinates are saved to `config.json` immediately. You can Alt-Tab freely between GTA and the terminal during setup.

## Controls

| Key | Function |
|-----|----------|
| `F9` | Start betting loop |
| `F10` | Stop (and print session stats) |
| `F8` | Debug OCR dump (developer) |

F7 is only active during the EZ Config wizard.

## Configuration

All settings live in `config.json` next to the script. Key options:

| Setting | Default | Description |
|---------|---------|-------------|
| `bet_presets` | `"LOW"` | Fixed preset to use if odds OCR fails (`LOW`/`MEDIUM`/`HIGH`/`MAX`) |
| `preset_LOW` | `1500` | Dollar amount for LOW tier |
| `preset_MEDIUM` | `3500` | Dollar amount for MEDIUM tier |
| `preset_HIGH` | `7500` | Dollar amount for HIGH tier |
| `preset_MAX` | `10000` | Dollar amount for MAX tier |
| `bet_presets_threshold_max` | `50` | Win% to use MAX bet |
| `bet_presets_threshold_high` | `40` | Win% to use HIGH bet |
| `bet_presets_threshold_medium` | `30` | Win% to use MEDIUM bet |
| `human_mouse` | `true` | Gradual bezier mouse movement with jitter |
| `log_all_bets` | `false` | Write every bet to `log.json` |
| `close_terminal_on_stop` | `true` | Close terminal on F10 |

## How bet amounts work

The script holds the `>` button for a calculated duration based on measured timings:

| Target | Hold time |
|--------|-----------|
| $1,500 | 3.5s |
| $7,500 | 6.5s |
| $10,000 | 13.5s |

Amounts in between are linearly interpolated. If your machine runs at a different speed, adjust the timings in the `_BET_ANCHORS` table in `main.py`.

## Session stats

After every race the terminal prints:

```
  ┌─ Session ────────────────────────────────
  │  Races     : 5
  │  Wagered   : $12,500
  │  Net P/L   : +$3,200
  └──────────────────────────────────────────
```

Stats reset each time you press F9 to start.

## Reconfiguring

If you move GTA to a different monitor or change your resolution, delete the coordinate values in `config.json` (set them to `null`) and restart the script to re-run the EZ Config wizard.

## Compatibility

| Version | Status |
|---------|--------|
| GTA V Enhanced | Primary testing platform |
| GTA V Legacy | Supported |

## Troubleshooting

**Script clicks wrong buttons** — recalibrate coordinates by setting them to `null` in `config.json` and restarting.

**Horse not being clicked** — the odds OCR uses a row-band fallback automatically. If it still misses, check that GTA is the active window and not obscured.

**Bet amount is wrong** — the hold timings in `_BET_ANCHORS` are measured on a specific machine. Adjust the seconds values to match your setup.

**Tesseract not found** — install from the link above, or set `tesseract_path` in `config.json` to the full path of `tesseract.exe`.
