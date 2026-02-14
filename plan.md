# Plan: Garage Tank Name OCR + Visual Calibration

## Problem
The current "active tank" detection uses the WG API's `last_battle_time`, which only reflects the most recently **played** tank — not the tank currently **selected** in the garage. The user may have switched tanks in-game without playing a battle, so the API is stale.

## Solution
OCR the tank name from the garage screen (center-bottom carousel) using PaddleOCR, continuously polling to detect tank switches. Provide a visual drag overlay (PyQt6) for users to position the capture area.

---

## Implementation Steps

### Step 1: Add `[garage]` config section
**File:** `src/tankvision/config.py`, `config.example.toml`

Add a new config section for the garage tank name ROI, separate from the damage OCR ROI:
```toml
[garage]
roi_x = 0
roi_y = 0
roi_width = 400
roi_height = 60
poll_interval = 3.0   # seconds between garage OCR polls
```

### Step 2: Create `GarageDetector` class
**New file:** `src/tankvision/ocr/garage_detector.py`

Responsibilities:
- Owns a separate `ScreenCapture` for the garage ROI
- Uses PaddleOCR to read the tank name text from the captured region
- Fuzzy-matches the OCR'd text against the WG encyclopedia vehicle names to resolve `tank_id`
- Exposes an async method: `poll() -> (tank_id, tank_name) | None`
- Tracks the "current" tank and detects changes

Key design decisions:
- PaddleOCR is initialized once (heavy init) and reused
- The vehicle encyclopedia is fetched once at startup and cached as a `{name_lower: (tank_id, short_name)}` lookup dict
- Fuzzy matching uses `difflib.get_close_matches()` (stdlib, no extra deps) to handle OCR imperfections
- Returns `None` when the garage screen isn't visible (e.g., during a battle) — no false switches

### Step 3: Integrate into the main loop
**File:** `src/tankvision/__main__.py`

- At startup: if `[garage]` ROI is configured (non-zero), create a `GarageDetector`
- Run a background async task `_garage_poll_loop()` that:
  - Every `poll_interval` seconds, calls `garage_detector.poll()`
  - If the tank changes: update `tank_id`, `tank_name`, fetch new thresholds, take new API snapshot, restart session tracking, broadcast updated state
- The API-based `detect_active_tank` remains as the **startup fallback** when no garage ROI is configured

### Step 4: Build the visual calibration overlay
**New file:** `src/tankvision/calibration/roi_picker.py`

A PyQt6 transparent fullscreen overlay window where the user:
1. Sees their entire screen through a semi-transparent layer
2. Drags a rectangle over the tank name area
3. Clicks "Confirm" to save the ROI coordinates
4. Coordinates are written to `config.toml` under `[garage]`

Implementation:
- `QMainWindow` with `Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint`
- Semi-transparent background (`setAttribute(Qt.WA_TranslucentBackground)`)
- Mouse drag draws a rubber-band selection rectangle
- On confirm: write coordinates to config file, close window
- Add a CLI entry point: `tankvision --calibrate` to launch this

### Step 5: Update `__main__.py` CLI for `--calibrate` flag
**File:** `src/tankvision/__main__.py`

- Parse `--calibrate` argument
- If present: launch the ROI picker UI, save result, exit
- Otherwise: run the normal overlay loop

### Step 6: Tests
- **Unit tests** for `GarageDetector`: mock PaddleOCR output, test fuzzy matching logic
- **Unit tests** for tank switch detection (same tank → no event, different tank → event)
- **Integration test**: verify PaddleOCR can read a sample garage screenshot (provide a test image fixture)
