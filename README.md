# WSI Gaze Annotation Platform

A minimal research tool for viewing Whole Slide Images (WSI) and recording
eye-tracking gaze data mapped to tissue coordinates. Built for pathology
eye-tracking experiments.

Supports simulated gaze (for development) and Tobii Pro eye trackers
(for real experiments) through a swappable gaze source interface.

## Status

- [x] Phase 1 — WSI tile server + browser viewer
- [x] Phase 2 — WebSocket relay + click event forwarding
- [x] Phase 3 — Gaze simulator (fixation-saccade model + Gaussian noise)
- [x] Phase 4 — Per-tile dwell-time analysis + heatmap generation
- [ ] Phase 5 — Tobii Pro eye tracker integration

## Architecture

Two standalone applications communicating over WebSocket:

```
VIEWER (App 1)                    SIMULATOR / TRACKER (App 2)
─────────────────                 ──────────────────────────
FastAPI tile server               Python gaze generator
+ Browser UI (OpenSeadragon)      + GazeSource interface
+ WebSocket hub                   │  ├─ SimulatedGazeSource
+ Coordinate HUD                  │  └─ TobiiGazeSource (Phase 5)
+ Gaze dot renderer               + JSONL session logging
                    ◄── WS ──►
                    port 8000

                  ANALYZER (App 3)
                  ────────────────
                  Post-hoc analysis
                  + Per-tile dwell times
                  + Heatmap overlay
                  + Session summary
```

**Key design principle:** The viewer knows nothing about gaze logic.
It renders dots it receives over WebSocket. The simulator/tracker knows
nothing about rendering. Swapping data sources requires changing one
flag — zero viewer changes.

## Prerequisites

- Python 3.9+
- Windows (tested), macOS/Linux should work
- [uv](https://github.com/astral-sh/uv) package manager (recommended) or pip

### Get a Test Slide

```bash
curl -L -o CMU-1.svs https://openslide.cs.cmu.edu/download/openslide-testdata/Aperio/CMU-1.svs
```

~280 MB Aperio SVS format. Supported formats: `.svs`, `.ndpi`, `.scn`,
`.tiff` (pyramidal), `.mrxs`.

## Installation

### Viewer

```powershell
cd viewer
uv venv
.\.venv\Scripts\activate
uv pip install -r requirements.txt
```

### Simulator

```powershell
cd simulator
uv venv
.\.venv\Scripts\activate
uv pip install -r requirements.txt
```

### Analyzer

```powershell
cd analyzer
uv venv
.\.venv\Scripts\activate
uv pip install -r requirements.txt
```

## Quick Start

### 1. Start the Viewer

```powershell
cd viewer
.\.venv\Scripts\activate
python app.py "C:\slides\CMU-1.svs"
```

Open browser to **http://127.0.0.1:8000**

### 2. Start the Simulator

```powershell
cd simulator
.\.venv\Scripts\activate
python simulator.py --mode manual --sigma 17
```

### 3. Use It

- **Pan:** click + drag
- **Zoom:** scroll wheel
- **Place fixation target:** Ctrl + click on tissue
- Red dots appear with Gaussian scatter around your click
- Gray dots show saccade path between fixations

### 4. Analyze

After stopping the simulator (Ctrl+C), analyze the session:

```powershell
cd analyzer
.\.venv\Scripts\activate
python analyze_session.py ^
    ..\simulator\logs\session_CMU-1_svs_20250227.jsonl ^
    C:\slides\CMU-1.svs ^
    --tile-size 512
```

Outputs: `dwell_map.csv`, `heatmap.png`, `session_summary.txt`

## Viewer Controls

| Action              | Input                        |
|---------------------|------------------------------|
| Pan                 | Click + drag                 |
| Zoom                | Scroll wheel                 |
| Place fixation      | Ctrl + click                 |
| Toggle gaze render  | G key (all → latest → off)  |
| Clear gaze dots     | C key                        |

### HUD (top-left overlay)

| Field          | Description                                          |
|----------------|------------------------------------------------------|
| File           | Loaded slide filename                                |
| Magnification  | Effective optical magnification at current zoom      |
| Cursor WSI     | Level-0 slide coordinates under mouse cursor         |
| DZ Level       | Current Deep Zoom pyramid level being displayed      |
| Viewport       | Bounding box of visible region in WSI coordinates    |
| Gaze           | Current gaze render mode                             |
| WS             | WebSocket connection status                          |

## Simulator

### Modes

| Mode     | Behavior                                            |
|----------|-----------------------------------------------------|
| `manual` | Waits for Ctrl+Click. Each click = fixation target. |
| `auto`   | Random fixations within current viewport.           |

### Options

| Flag               | Default                 | Description                          |
|--------------------|-------------------------|--------------------------------------|
| `--viewer-url`     | `http://127.0.0.1:8000` | Viewer server URL                    |
| `--mode`           | `manual`                | `manual` or `auto`                   |
| `--sigma`          | `17.0`                  | Gaussian noise in screen pixels      |
| `--rate`           | `120`                   | Sampling rate in Hz                  |
| `--fix-min`        | `200`                   | Minimum fixation duration (ms)       |
| `--fix-max`        | `500`                   | Maximum fixation duration (ms)       |
| `--auto-interval`  | `0.8`                   | Seconds between auto fixations       |
| `--log-dir`        | `./logs`                | Output directory for session files   |

### Zoom-Adaptive Sigma

Sigma (Gaussian noise) is defined in **screen pixels**, not WSI pixels.
This means the visual scatter on screen stays constant (~17px ≈ 0.5° visual
angle) regardless of zoom level.

```
σ_wsi = σ_screen × (viewport_wsi_width / container_screen_width)
```

At high magnification: tight cluster in tissue space.
At low magnification: wider cluster in tissue space.
On screen: always the same visual size.

## Coordinate System

```
SCREEN SPACE                    Physical monitor pixels (Tobii output)
    │
    ├── subtract viewer window offset
    ▼
VIEWER CANVAS SPACE             CSS pixels in browser viewport
    │
    ├── OpenSeadragon inverse viewport transform
    ▼
WSI LEVEL-0 SPACE               Slide pixels at scanned resolution
    │
    ├── multiply by mpp (microns per pixel)
    ▼
PHYSICAL SPACE                  Microns on tissue
```

All logged coordinates are in **WSI level-0 space** — zoom-independent,
directly mapping to tissue locations.

## Gaze Simulation Model

### Fixation

```
duration ~ Uniform(200, 500) ms
N_samples = duration / 1000 × sampling_rate

For each sample:
    gaze_x = target_x + Normal(0, σ_wsi)
    gaze_y = target_y + Normal(0, σ_wsi)
```

### Saccade

Smooth ease-in-out interpolation between fixation targets:

```
t_smooth = t² × (3 - 2t)
position = lerp(start, end, t_smooth)
```

Saccade duration: 30–80ms. Marked `is_saccade: true` in logs.

## Log File Format (JSONL)

One JSON object per line. Short keys to keep file size manageable at 120 Hz.

**Header (line 1):**
```json
{
  "type": "session_header",
  "slide": "CMU-1.svs",
  "slide_dimensions": [46000, 32914],
  "objective_power": 20.0,
  "mpp_x": 0.499,
  "start_time": "2025-01-08T14:30:22",
  "simulator_config": {"mode": "manual", "sigma_screen": 17.0, "sampling_rate": 120}
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
| `sac` | True during saccade                        |
| `fid` | Fixation ID (-1 during saccade)            |
| `src` | Source: `"simulator"` or `"tobii"`         |

## Analyzer

### Usage

```powershell
python analyze_session.py <session.jsonl> <slide.svs> [OPTIONS]
```

### Options

| Flag               | Default  | Description                               |
|--------------------|----------|-------------------------------------------|
| `--tile-size`      | `256`    | Tile size in WSI level-0 pixels           |
| `--output-dir`     | `./output` | Output directory                        |
| `--thumbnail-size` | `2048`   | Max thumbnail dimension for heatmap       |

### Output

| File                  | Content                                    |
|-----------------------|--------------------------------------------|
| `dwell_map.csv`       | Per-tile: col, row, sample_count, fixations |
| `heatmap.png`         | Visual overlay on slide thumbnail           |
| `session_summary.txt` | Human-readable session statistics           |

## Project Structure

```
wsi_platform/
├── .gitignore
├── LICENSE
├── README.md
│
├── viewer/                     # App 1: WSI Viewer
│   ├── requirements.txt
│   ├── app.py                  # FastAPI server + WebSocket hub
│   ├── wsi_reader.py           # OpenSlide wrapper + tile generation
│   └── static/
│       ├── index.html
│       ├── viewer.js           # OpenSeadragon + HUD + gaze overlay
│       └── style.css
│
├── simulator/                  # App 2: Gaze Source
│   ├── requirements.txt
│   ├── gaze_source.py          # GazeSource ABC + SimulatedGazeSource
│   ├── gaze_logger.py          # JSONL session logger
│   └── simulator.py            # CLI entry point
│
└── analyzer/                   # App 3: Post-hoc Analysis
    ├── requirements.txt
    └── analyze_session.py      # Dwell mapping + heatmap
```

## API Reference

### HTTP Endpoints

| Endpoint                          | Method | Response            |
|-----------------------------------|--------|---------------------|
| `/`                               | GET    | Viewer HTML page    |
| `/slide/info`                     | GET    | Slide metadata JSON |
| `/tiles/{level}/{col}/{row}.jpeg` | GET    | DZI tile image      |
| `/ws`                             | WS     | Bidirectional relay  |

### WebSocket Messages

**Browser → Server:**
```json
{"type": "viewport_update", "bounds_wsi": {"x_min":0,"y_min":0,"x_max":46000,"y_max":32914}, "container_width": 1920}
{"type": "click", "wsi_x": 31752, "wsi_y": 10731}
```

**Simulator → Server → Browser:**
```json
{"type": "gaze_point", "wsi_x": 31755.3, "wsi_y": 10728.9, "is_saccade": false, "fixation_id": 0}
```

## Troubleshooting

| Problem                              | Solution                                                  |
|--------------------------------------|-----------------------------------------------------------|
| `WS: disconnected` in viewer HUD    | `uv pip install websockets` in viewer venv, restart       |
| DLL error on `import openslide`      | `uv pip install openslide-bin` or add OpenSlide to PATH   |
| Gray squares (tiles not loading)     | Check terminal for errors. Verify slide path.             |
| No dots on Ctrl+Click               | Check simulator terminal. Confirm WS connected.           |
| `ModuleNotFoundError: websockets.sync` | `uv pip install "websockets>=11.0"`                     |
| Heatmap is blank                     | Check that JSONL has gaze records. Run with `--tile-size 512`. |