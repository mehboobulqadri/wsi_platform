# WSI Gaze Annotation Platform

A minimal research tool for viewing Whole Slide Images (WSI) and simulating
eye-tracking gaze data. Built to validate gaze-to-tissue coordinate mapping
before integrating a real eye tracker (Tobii Pro).

## Architecture

Two standalone applications communicating over WebSocket:

```
VIEWER (App 1)                    SIMULATOR (App 2)
─────────────────                 ─────────────────
FastAPI tile server               Python gaze generator
+ Browser UI (OpenSeadragon)      + Fixation-saccade model
+ WebSocket hub                   + Gaussian noise (zoom-adaptive)
+ Coordinate HUD                  + JSONL session logging
                    ◄── WS ──►
                    port 8000
```

The viewer knows nothing about gaze logic. It renders dots it receives.
The simulator knows nothing about rendering. It generates coordinates.
They connect over WebSocket. Replacing the simulator with Tobii requires
changing only the gaze source class — zero viewer changes.

## Prerequisites

- Python 3.9+
- Windows (tested), macOS/Linux should work
- [uv](https://github.com/astral-sh/uv) package manager (or pip)
- A WSI file (.svs, .ndpi, .tiff) — or use the free test slide below

### Get a Test Slide

```
curl -L -o CMU-1.svs https://openslide.cs.cmu.edu/download/openslide-testdata/Aperio/CMU-1.svs
```

~280 MB. Place it in an easy path like `C:\slides\CMU-1.svs`.

## Installation

### Viewer

```powershell
cd wsi_platform\viewer
uv venv
.\.venv\Scripts\activate
uv pip install -r requirements.txt
```

### Simulator

```powershell
cd wsi_platform\simulator
uv venv
.\.venv\Scripts\activate
uv pip install -r requirements.txt
```

## Quick Start

Open two terminals:

**Terminal 1 — Viewer:**
```powershell
cd wsi_platform\viewer
.\.venv\Scripts\activate
python app.py "C:\slides\CMU-1.svs"
```
Open browser to http://127.0.0.1:8000

**Terminal 2 — Simulator:**
```powershell
cd wsi_platform\simulator
.\.venv\Scripts\activate
python simulator.py --mode manual --sigma 17
```

In the browser: **Ctrl+Click** on tissue to place fixation targets.
Red dots appear with Gaussian scatter around each click.

## Viewer Controls

| Action              | How                          |
|---------------------|------------------------------|
| Pan                 | Click + drag                 |
| Zoom                | Scroll wheel                 |
| Place fixation      | Ctrl + Click                 |
| Toggle gaze mode    | G key (all → latest → off)  |
| Clear gaze dots     | C key                        |

### HUD (top-left overlay)

Shows in real-time:
- **File** — loaded slide filename
- **Magnification** — effective optical magnification at current zoom
- **Cursor WSI** — (x, y) coordinates in level-0 slide space under cursor
- **DZ Level** — current Deep Zoom pyramid level being displayed
- **Viewport** — bounding box of visible region in WSI coordinates
- **Gaze mode** — current render mode
- **WS** — WebSocket connection status (green = connected)

## Simulator Modes

### Manual Mode (default)

```powershell
python simulator.py --mode manual
```

Waits for Ctrl+Click events from the viewer. Each click becomes a fixation
target. The simulator generates noisy gaze samples around that point for
200–500ms, then waits for the next click. Saccade interpolation is generated
between consecutive clicks.

### Auto Mode

```powershell
python simulator.py --mode auto --auto-interval 1.0
```

Generates fixation targets automatically at random positions within the
current viewport. Pan and zoom in the viewer to change the active area.

## Simulator Options

| Flag               | Default | Description                                  |
|--------------------|---------|----------------------------------------------|
| `--viewer-url`     | http://127.0.0.1:8000 | Viewer server URL              |
| `--mode`           | manual  | `manual` or `auto`                           |
| `--sigma`          | 17.0    | Gaussian noise in screen pixels              |
| `--rate`           | 120     | Sampling rate in Hz                          |
| `--fix-min`        | 200     | Minimum fixation duration (ms)               |
| `--fix-max`        | 500     | Maximum fixation duration (ms)               |
| `--auto-interval`  | 0.8     | Seconds between auto fixations               |
| `--log-dir`        | ./logs  | Output directory for JSONL session files      |

## Coordinate System

Three coordinate spaces with two transforms:

```
SCREEN SPACE                    (Tobii output: physical monitor pixels)
    │
    ├─ subtract viewer window offset
    ▼
VIEWER CANVAS SPACE             (CSS pixels in browser viewport)
    │
    ├─ OpenSeadragon inverse viewport transform
    ▼
WSI LEVEL-0 SPACE               (slide pixels at scanned resolution)
    convert to physical: multiply by mpp (microns per pixel)
```

All logged gaze coordinates are in **WSI level-0 space**. This means:
- They are zoom-independent (same coordinates regardless of magnification)
- They map directly to tissue locations
- They can be compared across sessions at different zoom levels

## Gaze Simulation Model

### Fixation

Each fixation generates N samples at the configured sampling rate:

```
N = duration_ms / 1000 × sampling_rate

For each sample:
    gaze_x = target_x + Normal(0, σ_wsi)
    gaze_y = target_y + Normal(0, σ_wsi)
```

**Zoom-adaptive sigma:**
```
σ_wsi = σ_screen × (viewport_wsi_width / container_screen_width)
```

This ensures the visual scatter on screen is constant (~17px) regardless
of zoom level. At high magnification, σ_wsi is small (tight cluster in
tissue space). At low magnification, σ_wsi is large (but still ~17px on
screen).

### Saccade

Between fixations, a smooth interpolation:
```
t_smooth = t² × (3 - 2t)    # ease in-out
x = lerp(start_x, end_x, t_smooth)
y = lerp(start_y, end_y, t_smooth)
```

Saccade samples are marked `is_saccade: true` and should be excluded
from dwell-time analysis.

## Log File Format

Session logs are saved as JSONL (one JSON object per line).

**File naming:** `session_{slide}_{timestamp}.jsonl`

**Header (line 1):**
```json
{
  "type": "session_header",
  "slide": "CMU-1.svs",
  "slide_dimensions": [46000, 32914],
  "objective_power": 20.0,
  "mpp_x": 0.499,
  "mpp_y": 0.499,
  "start_time": "2025-01-08T14:30:22",
  "simulator_config": {
    "mode": "manual",
    "sigma_screen": 17.0,
    "sampling_rate": 120,
    "fixation_range": [200, 500]
  }
}
```

**Gaze samples:**
```json
{"type":"gaze","t":123.4,"wx":51234.2,"wy":37891.7,"sac":false,"fid":0,"src":"simulator"}
```

| Key   | Meaning                                    |
|-------|--------------------------------------------|
| `t`   | Milliseconds since session start           |
| `wx`  | WSI level-0 x coordinate                   |
| `wy`  | WSI level-0 y coordinate                   |
| `sac` | True if this sample is during a saccade    |
| `fid` | Fixation ID (-1 during saccade)            |
| `src` | Source identifier ("simulator" or "tobii") |

**Events (clicks, viewport changes):**
```json
{"type":"click_target","t":1718234567123.0,"wsi_x":31751.8,"wsi_y":10731.1}
```

## Project Structure

```
wsi_platform/
├── README.md
├── viewer/                     # App 1: WSI Viewer
│   ├── .venv/
│   ├── requirements.txt
│   ├── app.py                  # FastAPI server + WebSocket hub
│   ├── wsi_reader.py           # OpenSlide wrapper + tile generation
│   └── static/
│       ├── index.html          # Single-page viewer
│       ├── viewer.js           # OpenSeadragon + HUD + gaze overlay
│       └── style.css           # Minimal dark theme
│
├── simulator/                  # App 2: Gaze Simulator
│   ├── .venv/
│   ├── requirements.txt
│   ├── gaze_source.py          # GazeSource ABC + SimulatedGazeSource
│   ├── gaze_logger.py          # JSONL file writer
│   ├── simulator.py            # CLI entry point
│   └── logs/                   # Session log files (auto-created)
│
└── analyzer/                   # Phase 4: Post-hoc analysis
    ├── requirements.txt
    └── analyze_session.py      # Dwell-time heatmap generation
```

## API Reference

### Viewer HTTP Endpoints

| Endpoint                        | Method | Description                    |
|---------------------------------|--------|--------------------------------|
| `/`                             | GET    | Serve viewer HTML              |
| `/slide/info`                   | GET    | Slide metadata JSON            |
| `/tiles/{level}/{col}/{row}.jpeg` | GET  | Single DZI tile                |
| `/ws`                           | WS     | Bidirectional gaze/event relay |

### WebSocket Messages

**Browser → Server (relayed to simulator):**
```json
{"type": "viewport_update", "bounds_wsi": {...}, "container_width": 1920}
{"type": "click", "wsi_x": 51234, "wsi_y": 37891}
```

**Simulator → Server → Browser:**
```json
{"type": "gaze_point", "wsi_x": 51240, "wsi_y": 37895, "is_saccade": false, "fixation_id": 3}
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `WS: disconnected` in HUD | `uv pip install websockets` in viewer venv, restart server |
| DLL error on import openslide | `uv pip install openslide-bin` or manually add OpenSlide to PATH |
| Tiles not loading (gray squares) | Check terminal for 404s. Verify slide path is correct. |
| No dots appearing 