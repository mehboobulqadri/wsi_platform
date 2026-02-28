# WSI Gaze Annotation Platform

A minimal research tool for viewing Whole Slide Images (WSI) and recording
eye-tracking gaze data mapped to tissue coordinates. Built for pathology
eye-tracking experiments.

Supports simulated gaze (for development and testing) and Tobii Pro eye
trackers (for real experiments) through a swappable gaze source interface.

## Status

- [x] Phase 1 — WSI tile server + browser viewer
- [x] Phase 2 — WebSocket relay + click event forwarding
- [x] Phase 3 — Gaze simulator (fixation-saccade model + Gaussian noise)
- [x] Phase 4 — Per-tile dwell-time analysis + heatmap generation
- [x] Phase 5 — Tobii Pro eye tracker integration

## Architecture

```
VIEWER (App 1)                    GAZE SOURCE (App 2)
─────────────────                 ──────────────────────────
FastAPI tile server               GazeSource interface
+ Browser UI (OpenSeadragon)      │  ├─ SimulatedGazeSource
+ WebSocket hub                   │  │   (Gaussian noise,
+ Coordinate HUD                  │  │    fixation-saccade model)
+ Gaze dot renderer               │  └─ TobiiGazeSource
                                  │      (Tobii Pro SDK,
                    ◄── WS ──►    │       screen→WSI mapping)
                    port 8000     + JSONL session logging

                  ANALYZER (App 3)
                  ────────────────
                  Post-hoc analysis
                  + Per-tile dwell times
                  + Heatmap overlay
                  + Session summary
```

**Key design:** The viewer renders dots it receives — it knows nothing about
where they come from. The gaze source generates coordinates — it knows nothing
about rendering. Swapping simulator for Tobii requires changing one CLI flag.

## Prerequisites

- **Python 3.10+** (required for Tobii SDK; simulator-only works on 3.9+)
- Windows (tested), macOS/Linux should work
- [uv](https://github.com/astral-sh/uv) package manager (recommended) or pip
- For Tobii: Tobii Pro Eye Tracker Manager installed, tracker calibrated

### Get a Test Slide

```bash
curl -L -o CMU-1.svs https://openslide.cs.cmu.edu/download/openslide-testdata/Aperio/CMU-1.svs
```

~280 MB Aperio SVS format. Also supports: `.ndpi`, `.scn`, `.tiff`, `.mrxs`.

## Installation

### Viewer

```powershell
cd viewer
uv venv
.\.venv\Scripts\activate
uv pip install -r requirements.txt
```

### Simulator / Tobii Client

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

### Simulated Gaze (No Eye Tracker Needed)

**Terminal 1 — Viewer:**
```powershell
cd viewer
.\.venv\Scripts\activate
python app.py "C:\slides\CMU-1.svs"
```
Open browser to http://127.0.0.1:8000

**Terminal 2 — Simulator:**
```powershell
cd simulator
.\.venv\Scripts\activate
python simulator.py --source simulator --mode manual --sigma 17
```

Ctrl+Click on tissue in the browser. Red dots appear with Gaussian scatter.

### Real Eye Tracker (Tobii Pro)

**Step 1 — Test tracker:**
```powershell
cd simulator
.\.venv\Scripts\activate
python tobii_calibration.py
```

**Step 2 — Start viewer** (same as above), then press **F11** for fullscreen.

**Step 3 — Start with Tobii:**
```powershell
python simulator.py --source tobii --screen-width 1920 --screen-height 1080 --browser-offset-x 0 --browser-offset-y 0
```

Look at the slide — red dots appear where your eyes fixate.

### Analyze a Session

```powershell
cd analyzer
.\.venv\Scripts\activate
python analyze_session.py ^
    ..\simulator\logs\session_CMU-1_svs_20250227.jsonl ^
    C:\slides\CMU-1.svs ^
    --tile-size 512
```

## Viewer Controls

| Action              | Input                        |
|---------------------|------------------------------|
| Pan                 | Click + drag                 |
| Zoom                | Scroll wheel                 |
| Place fixation      | Ctrl + click                 |
| Toggle gaze render  | G key (all → latest → off)  |
| Clear gaze dots     | C key                        |

### HUD (Top-Left Overlay)

| Field          | Description                                     |
|----------------|-------------------------------------------------|
| File           | Loaded slide filename                           |
| Magnification  | Effective magnification at current zoom         |
| Cursor WSI     | Level-0 coordinates under mouse                |
| DZ Level       | Current Deep Zoom pyramid level                 |
| Viewport       | Visible region in WSI coordinates               |
| Gaze           | Render mode (all / latest / off)                |
| WS             | WebSocket status                                |

## Gaze Source Options

### Simulator

| Flag               | Default  | Description                    |
|--------------------|----------|--------------------------------|
| `--mode`           | manual   | `manual` or `auto`             |
| `--sigma`          | 17.0     | Gaussian noise (screen pixels) |
| `--rate`           | 120      | Sampling rate Hz               |
| `--fix-min`        | 200      | Min fixation duration ms       |
| `--fix-max`        | 500      | Max fixation duration ms       |
| `--auto-interval`  | 0.8      | Seconds between auto fixations |

### Tobii

| Flag                  | Default  | Description                        |
|-----------------------|----------|------------------------------------|
| `--screen-width`      | 1920     | Monitor resolution width           |
| `--screen-height`     | 1080     | Monitor resolution height          |
| `--browser-offset-x`  | 0        | Screen X offset to viewer canvas   |
| `--browser-offset-y`  | 0        | Screen Y offset to viewer canvas   |
| `--tobii-frequency`   | 120      | Tracker output frequency Hz        |

**Browser offset:** Use `(0, 0)` with fullscreen browser (F11). For windowed
mode, estimate pixels from screen edge to where the slide canvas begins.

## Coordinate System

```
TOBII NORMALIZED (0.0–1.0)     Eye tracker output
    │
    ├── × screen resolution
    ▼
SCREEN PIXELS                   Physical monitor pixels
    │
    ├── − browser offset
    ▼
VIEWER CANVAS PIXELS            CSS pixels in browser
    │
    ├── OpenSeadragon viewport inverse
    ▼
WSI LEVEL-0 PIXELS              Slide pixels at scan resolution
    │
    ├── × mpp (microns per pixel)
    ▼
PHYSICAL (microns)              Actual tissue coordinates
```

All logged coordinates are in **WSI level-0 space**.

## Gaze Simulation Model

**Fixation:** N samples from `Normal(target, σ_wsi)` where
`σ_wsi = σ_screen × (viewport_width / canvas_width)`.
Duration: 200–500 ms.

**Saccade:** Smooth ease-in-out interpolation. Duration: 30–80 ms.
Marked `is_saccade: true` in logs.

**Zoom-adaptive sigma:** Visual scatter on screen stays constant (~17px ≈
0.5° visual angle) regardless of zoom. At high mag, σ_wsi is small.
At low mag, σ_wsi is large. Screen appearance is identical.

## Log Format (JSONL)

**Header:**
```json
{"type":"session_header","slide":"CMU-1.svs","slide_dimensions":[46000,32914],"objective_power":20.0,"mpp_x":0.499,"start_time":"2025-01-08T14:30:22","simulator_config":{"source":"tobii","frequency":120}}
```

**Gaze:**
```json
{"type":"gaze","t":123.4,"wx":51234.2,"wy":37891.7,"sac":false,"fid":0,"src":"simulator"}
```

| Key  | Meaning                          |
|------|----------------------------------|
| `t`  | ms since session start           |
| `wx` | WSI level-0 x                    |
| `wy` | WSI level-0 y                    |
| `sac`| True during saccade              |
| `fid`| Fixation ID (-1 for saccade/tobii)|
| `src`| `"simulator"` or `"tobii"`       |

## Analyzer Output

| File                  | Content                              |
|-----------------------|--------------------------------------|
| `dwell_map.csv`       | Per-tile dwell times and fixations   |
| `heatmap.png`         | Visual overlay on slide thumbnail    |
| `session_summary.txt` | Human-readable statistics            |

## Project Structure

```
wsi_platform/
├── .gitignore
├── LICENSE
├── README.md
├── viewer/
│   ├── requirements.txt
│   ├── app.py
│   ├── wsi_reader.py
│   └── static/
│       ├── index.html
│       ├── style.css
│       └── viewer.js
├── simulator/
│   ├── requirements.txt
│   ├── simulator.py
│   ├── gaze_source.py
│   ├── gaze_logger.py
│   ├── tobii_source.py
│   └── tobii_calibration.py
└── analyzer/
    ├── requirements.txt
    └── analyze_session.py
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `WS: disconnected` | `uv pip install websockets` in viewer venv |
| DLL error on openslide | `uv pip install openslide-bin` |
| No Tobii tracker found | Check USB, open Eye Tracker Manager |
| Low Tobii validity | Run calibration in Eye Tracker Manager |
| Gaze offset from expected position | Check `--browser-offset-x/y`, use F11 fullscreen |
| `tobii-research` won't install | Need Python 3.10+, check with `python --version` |
| Dots too spread / too tight | Adjust `--sigma` (screen pixels, default 17) |
| Heatmap blank | Verify JSONL has gaze records, try `--tile-size 512` |