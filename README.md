# GTA-5-Horse-Race-AutoBetter

Automation script for the GTA V horse racing betting interface. Supports both **GTA V Legacy** and **GTA V Enhanced**.

Development and testing has primarily been performed on GTA V Enhanced. The script is designed to be highly configurable through `config.json` and can be adapted to different screen resolutions and configurations by changing the required coordinates.

## Configuration Video

The following video demonstrates the configuration process and explains which values need to be changed in `config.json`.

[Watch the configuration video on YouTube](https://www.youtube.com/watch?v=yNOm-2pjr-I)

## Controls

| Key   | Function                              |
| ----- | ------------------------------------- |
| `F7`  | Display the current mouse coordinates |
| `F9`  | Start the script                      |
| `F10` | Stop the script                       |

## Configuration

All betting-related settings are controlled through:

```text
config.json
```

The screen coordinates must be configured for the system the script is running on.

### Getting Coordinates

Move the mouse over the required button or interface element and press `F7`.

The script will output information similar to:

```text
[coords] Absolute: (-789,388)   Monitor-relative: (-789,388)
[coords] → increase_button_x/y  use: -789, 388
[coords] → bet_amount_region     hover corners, compute [x1,y1,w,h]
```

Use the X and Y values shown after `use:` in the corresponding configuration fields.

For example:

```json
"bet_amount_x": 1319,
"bet_amount_y": 518
```

The values above are examples only. Coordinates will vary depending on the monitor resolution, display scaling, game window position, and other system-specific settings.

## Required Coordinate Settings

The following configuration values need to be set:

```json
"increase_button_x": 1510,
"increase_button_y": 508,

"bet_amount_x": 1321,
"bet_amount_y": 511,
"bet_amount_crop_width": 100,
"bet_amount_crop_height": 25,

"place_button_x": 3203,
"place_button_y": 733
```

### Configuration Reference

| Setting                  | Description                                     |
| ------------------------ | ----------------------------------------------- |
| `increase_button_x`      | X coordinate of the bet increase button         |
| `increase_button_y`      | Y coordinate of the bet increase button         |
| `bet_amount_x`           | X coordinate of the bet amount detection region |
| `bet_amount_y`           | Y coordinate of the bet amount detection region |
| `bet_amount_crop_width`  | Width of the bet amount detection region        |
| `bet_amount_crop_height` | Height of the bet amount detection region       |
| `place_button_x`         | X coordinate of the Place Bet button            |
| `place_button_y`         | Y coordinate of the Place Bet button            |

Each button coordinate should be obtained using `F7` and entered into the appropriate field in `config.json`.

## Bet Amount Detection

The script uses an OCR region to read the current bet amount.

The relevant configuration is:

```json
"bet_amount_x": 1321,
"bet_amount_y": 511,
"bet_amount_crop_width": 100,
"bet_amount_crop_height": 25
```

`bet_amount_x` and `bet_amount_y` define the starting position of the detection region.

`bet_amount_crop_width` and `bet_amount_crop_height` define the size of the region that is captured and processed by OCR.

The detection region must cover the displayed bet amount accurately for OCR to work correctly.

## Installation

### Tesseract OCR

The script requires Tesseract OCR for reading the displayed bet amount.

Tesseract OCR 5.5.3:

https://github.com/tesseract-ocr/tesseract/releases/tag/5.5.3

Install Tesseract before running the script.

## Setup

1. Download or clone the repository.
2. Install Tesseract OCR.
3. Open `config.json`.
4. Start GTA V.
5. Position the game as it will be when the script is running.
6. Use `F7` to obtain the required coordinates.
7. Enter the coordinates into `config.json`.
8. Configure the bet amount OCR region.
9. Save `config.json`.
10. Press `F9` to start the script.
11. Press `F10` to stop the script.

## Compatibility

| Version        | Status                                   |
| -------------- | ---------------------------------------- |
| GTA V Enhanced | Primary development and testing platform |
| GTA V Legacy   | Supported                                |

Different resolutions, display scaling settings, and game window positions may require different coordinate and OCR region values.

## Troubleshooting

If the script does not interact with the correct buttons, verify the following:

* The coordinates in `config.json` match the current screen configuration.
* GTA V is positioned consistently with the coordinates that were configured.
* The `bet_amount` OCR region covers the displayed bet amount.
* Tesseract OCR is installed correctly.
* Windows display scaling has not changed since the coordinates were configured.

Example coordinates included in this README are for demonstration purposes and should not be used as default values.

## Project Status

This project is under active development. GTA V Enhanced is the primary testing environment, while compatibility with GTA V Legacy is also maintained.
