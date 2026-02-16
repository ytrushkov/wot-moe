# WoT Console Overlay (TankVision)

Live Marks of Excellence tracker for **World of Tanks Console** (Xbox / PlayStation).
Reads damage numbers from the game HUD via screen capture + OCR, calculates your MoE progress in real time, and pushes updates to a browser-source overlay for OBS.

## How it works

1. **Screen capture** grabs a region of the screen containing the damage counter.
2. **Template-matching OCR** extracts the damage numbers from each frame.
3. **EMA calculator** tracks your combined damage across battles and projects your current MoE percentage.
4. **WebSocket server** pushes live state to a built-in HTML/CSS/JS overlay.
5. **Post-battle API correction** polls the Wargaming API after each battle to replace the estimated MoE with the server-side value (the in-game HUD sums tracking + spotting assist, but WG uses `max(tracking, spotting)`, so our estimate trends high).

Add the overlay as a Browser Source in OBS and you'll see your MoE % update live during every match.

## Requirements

- Python 3.10+
- A Wargaming developer API key (optional — a default public key is included)
- OBS Studio (or any streaming tool that supports browser sources)

## Installation

```bash
# Clone the repository
git clone https://github.com/ytrushkov/wot-moe.git
cd wot-moe

# Install in a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .

# Install dev dependencies (for running tests)
pip install -e ".[dev]"
```

## Configuration

Create a `config.toml` in the project root (or wherever you run the app from). All fields are optional — sensible defaults are built in.

```toml
[player]
gamertag = "YourGamerTag"   # enables online mode (API lookup, post-battle correction)
platform = "xbox"           # "xbox" or "ps"

[api]
application_id = ""         # leave blank to use the bundled public key

[ocr]
roi_x = 0                  # top-left X of the damage counter region
roi_y = 0                  # top-left Y
roi_width = 300             # width in pixels
roi_height = 100            # height in pixels
sample_rate = 2             # captures per second
confidence_threshold = 0.8  # OCR match confidence (0-1)

[moe]
current_moe_percent = 0.0   # starting MoE % (used if no persisted state exists)
target_damage = 0           # manual override; 0 = auto-fetch from threshold provider

[overlay]
opacity = 0.85
scale = 1.0
layout = "standard"         # "standard" or "compact"
color_blind_mode = false

[server]
http_port = 5173            # overlay served here
ws_port = 5174              # WebSocket data pushed here
```

### Online vs offline mode

- **Online** (`gamertag` set): the app resolves your account, detects the last-played tank, fetches MoE thresholds, restores persisted EMA, and corrects estimates via the API after each battle.
- **Offline** (`gamertag` empty): the app uses `target_damage` and `current_moe_percent` from config. No API calls are made.

## Usage

```bash
# Run with default config.toml in the current directory
tankvision

# Or specify a config path
tankvision /path/to/config.toml
```

Then add a **Browser Source** in OBS pointing to:

```
http://localhost:5173
```

Optional URL parameters for the overlay:

| Parameter     | Example             | Description                       |
|---------------|---------------------|-----------------------------------|
| `ws_port`     | `?ws_port=5174`     | WebSocket port (if non-default)   |
| `layout`      | `?layout=compact`   | Compact layout variant            |
| `colorblind`  | `?colorblind=true`  | Color-blind-friendly palette      |
| `opacity`     | `?opacity=0.7`      | Override overlay opacity           |
| `scale`       | `?scale=1.5`        | Scale the overlay up/down         |

## Running tests

```bash
pytest
```

## Project structure

```
src/tankvision/
  __main__.py              # entry point — startup, main loop, shutdown
  config.py                # TOML config loading with defaults
  capture/
    screen_capture.py      # mss-based screen grabbing
  ocr/
    preprocessor.py        # image preprocessing (threshold, upscale)
    template_matcher.py    # digit recognition via template matching
    ocr_pipeline.py        # frame → DamageReading pipeline
  calculation/
    ema.py                 # EMA math (update, project, battles-to-target)
    battle_detector.py     # detects battle start/end from damage resets
    moe_calculator.py      # MoE state machine + post-battle correction
  data/
    wargaming_api.py       # async Wargaming WOTX API client
    threshold_provider.py  # MoE damage thresholds (cache + remote fetch)
    session_store.py       # SQLite persistence (EMA state, battle log, sessions)
  server/
    websocket_server.py    # WebSocket + HTTP server for the overlay
  overlay/
    index.html             # browser-source overlay
    overlay.js             # WebSocket client + DOM updates
    overlay.css            # overlay styling
```

## License

MIT
