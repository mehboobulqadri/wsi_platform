"""
analyze_session.py
Read a gaze session JSONL file, compute per-tile dwell times,
output CSV + heatmap overlay on slide thumbnail.

Usage:
    python analyze_session.py <session.jsonl> <slide.svs> [OPTIONS]

Example:
    python analyze_session.py ../simulator/logs/session_CMU-1_svs_20250108.jsonl C:\\slides\\CMU-1.svs

Output:
    dwell_map.csv       — per-tile dwell times and fixation counts
    heatmap.png         — visual overlay on slide thumbnail
    session_summary.txt — text summary of the session
"""

import os
import sys
import json
import argparse
from collections import defaultdict

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import Normalize
from PIL import Image

import openslide
from openslide import OpenSlide


def parse_args():
    p = argparse.ArgumentParser(description="Analyze gaze session")
    p.add_argument("session", help="Path to JSONL session file")
    p.add_argument("slide", help="Path to WSI file (.svs)")
    p.add_argument("--tile-size", type=int, default=256,
                    help="Tile size in WSI level-0 pixels (default: 256)")
    p.add_argument("--output-dir", default="./output",
                    help="Output directory (default: ./output)")
    p.add_argument("--thumbnail-size", type=int, default=2048,
                    help="Max thumbnail dimension in pixels (default: 2048)")
    return p.parse_args()


def load_session(path):
    # type: (str) -> tuple
    """Load JSONL session file. Returns (header_dict, list_of_gaze_dicts)."""
    header = None
    gaze_records = []
    events = []

    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                print("[warn] Skipping malformed line {}".format(line_num))
                continue

            rec_type = obj.get("type", "")

            if rec_type == "session_header":
                header = obj
            elif rec_type == "gaze":
                gaze_records.append(obj)
            else:
                events.append(obj)

    if header is None:
        print("[warn] No session header found in {}".format(path))
        header = {}

    print("[analyze] Loaded {} gaze samples, {} events".format(
        len(gaze_records), len(events)
    ))
    return header, gaze_records, events


def compute_dwell_map(gaze_records, tile_size, slide_width, slide_height):
    # type: (list, int, int, int) -> pd.DataFrame
    """
    Compute per-tile dwell time and fixation count.

    Each gaze sample contributes (1/sampling_rate) seconds of dwell time
    to the tile it falls in. Saccade samples are excluded.
    """
    tile_dwell = defaultdict(float)      # (col, row) -> total ms
    tile_fixations = defaultdict(set)    # (col, row) -> set of fixation IDs
    tile_sample_count = defaultdict(int) # (col, row) -> count

    max_col = (slide_width + tile_size - 1) // tile_size
    max_row = (slide_height + tile_size - 1) // tile_size

    skipped_saccade = 0
    skipped_oob = 0
    counted = 0

    for rec in gaze_records:
        if rec.get("sac", False):
            skipped_saccade += 1
            continue

        wx = rec.get("wx", 0)
        wy = rec.get("wy", 0)

        col = int(wx) // tile_size
        row = int(wy) // tile_size

        if col < 0 or col >= max_col or row < 0 or row >= max_row:
            skipped_oob += 1
            continue

        key = (col, row)
        tile_dwell[key] += 1  # each sample = 1 count (convert to ms later)
        tile_sample_count[key] += 1

        fid = rec.get("fid", -1)
        if fid >= 0:
            tile_fixations[key].add(fid)

        counted += 1

    print("[analyze] Counted: {}  Saccades skipped: {}  Out-of-bounds: {}".format(
        counted, skipped_saccade, skipped_oob
    ))

    # Build DataFrame
    rows = []
    for (col, row), count in tile_dwell.items():
        rows.append({
            "tile_col": col,
            "tile_row": row,
            "wsi_x_min": col * tile_size,
            "wsi_y_min": row * tile_size,
            "wsi_x_max": min((col + 1) * tile_size, slide_width),
            "wsi_y_max": min((row + 1) * tile_size, slide_height),
            "sample_count": int(count),
            "fixation_count": len(tile_fixations.get((col, row), set())),
        })

    if not rows:
        print("[analyze] WARNING: No gaze data mapped to tiles!")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.sort_values(["sample_count"], ascending=False).reset_index(drop=True)
    return df


def generate_heatmap(df, slide_path, tile_size, thumb_max, output_path):
    # type: (pd.DataFrame, str, int, int, str) -> None
    """Generate heatmap overlay on slide thumbnail."""
    if df.empty:
        print("[analyze] No data for heatmap.")
        return

    slide = OpenSlide(slide_path)
    dims = slide.dimensions  # (width, height)

    # Compute thumbnail size preserving aspect ratio
    ratio = min(thumb_max / dims[0], thumb_max / dims[1])
    thumb_w = int(dims[0] * ratio)
    thumb_h = int(dims[1] * ratio)
    thumbnail = slide.get_thumbnail((thumb_w, thumb_h))
    thumbnail = thumbnail.convert("RGB")
    slide.close()

    # Scale factor: WSI pixels → thumbnail pixels
    scale_x = thumb_w / float(dims[0])
    scale_y = thumb_h / float(dims[1])

    # Create figure
    fig, ax = plt.subplots(1, 1, figsize=(14, 14 * thumb_h / thumb_w))
    ax.imshow(thumbnail)

    # Normalize sample counts for color mapping
    max_count = df["sample_count"].max()
    if max_count == 0:
        max_count = 1
    norm = Normalize(vmin=0, vmax=max_count)
    cmap = plt.cm.hot

    for _, row in df.iterrows():
        x = row["wsi_x_min"] * scale_x
        y = row["wsi_y_min"] * scale_y
        w = (row["wsi_x_max"] - row["wsi_x_min"]) * scale_x
        h = (row["wsi_y_max"] - row["wsi_y_min"]) * scale_y

        color = cmap(norm(row["sample_count"]))
        alpha = min(0.8, 0.2 + 0.6 * norm(row["sample_count"]))

        rect = mpatches.Rectangle(
            (x, y), w, h,
            linewidth=0,
            facecolor=color,
            alpha=alpha,
        )
        ax.add_patch(rect)

    ax.set_title("Gaze Dwell-Time Heatmap\n(tile size: {} WSI px)".format(tile_size),
                  fontsize=12, color="white")
    ax.axis("off")

    # Colorbar
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, fraction=0.03, pad=0.02)
    cbar.set_label("Gaze samples per tile", fontsize=10)

    fig.patch.set_facecolor("#1a1a1a")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight",
                facecolor="#1a1a1a")
    plt.close(fig)
    print("[analyze] Heatmap saved: {}".format(output_path))


def write_summary(header, df, events, output_path):
    # type: (dict, pd.DataFrame, list, str) -> None
    """Write a human-readable session summary."""
    lines = []
    lines.append("=" * 60)
    lines.append("GAZE SESSION SUMMARY")
    lines.append("=" * 60)
    lines.append("")

    # Session info
    lines.append("Slide: {}".format(header.get("slide", "unknown")))
    lines.append("Dimensions: {}".format(header.get("slide_dimensions", "?")))
    lines.append("Objective: {}x".format(header.get("objective_power", "?")))
    lines.append("MPP: {}".format(header.get("mpp_x", "?")))
    lines.append("Start: {}".format(header.get("start_time", "?")))
    lines.append("")

    config = header.get("simulator_config", {})
    lines.append("Simulator config:")
    for k, v in config.items():
        lines.append("  {}: {}".format(k, v))
    lines.append("")

    # Click events
    clicks = [e for e in events if e.get("type") == "click_target"]
    lines.append("Click targets: {}".format(len(clicks)))
    for i, c in enumerate(clicks):
        lines.append("  #{}: ({:.0f}, {:.0f})".format(
            i + 1, c.get("wsi_x", 0), c.get("wsi_y", 0)
        ))
    lines.append("")

    # Dwell stats
    if not df.empty:
        total_samples = df["sample_count"].sum()
        total_fixations = df["fixation_count"].sum()
        tiles_visited = len(df)

        lines.append("Tiles visited: {}".format(tiles_visited))
        lines.append("Total gaze samples (fixation only): {}".format(total_samples))
        lines.append("Total fixations: {}".format(total_fixations))
        lines.append("")

        lines.append("Top 10 tiles by dwell:")
        lines.append("  {:>6s}  {:>6s}  {:>8s}  {:>5s}".format(
            "col", "row", "samples", "fixns"
        ))
        for _, row in df.head(10).iterrows():
            lines.append("  {:6d}  {:6d}  {:8d}  {:5d}".format(
                int(row["tile_col"]),
                int(row["tile_row"]),
                int(row["sample_count"]),
                int(row["fixation_count"]),
            ))
    else:
        lines.append("No gaze data mapped to tiles.")

    lines.append("")
    lines.append("=" * 60)

    text = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)
    print("[analyze] Summary saved: {}".format(output_path))
    print("")
    print(text)


def main():
    args = parse_args()

    if not os.path.isfile(args.session):
        print("ERROR: Session file not found: {}".format(args.session))
        sys.exit(1)

    if not os.path.isfile(args.slide):
        print("ERROR: Slide file not found: {}".format(args.slide))
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    # Load session
    header, gaze_records, events = load_session(args.session)

    # Get slide dimensions
    slide = OpenSlide(args.slide)
    slide_w, slide_h = slide.dimensions
    slide.close()
    print("[analyze] Slide: {} x {}".format(slide_w, slide_h))

    # Compute dwell map
    print("[analyze] Computing dwell map (tile_size={})...".format(args.tile_size))
    df = compute_dwell_map(gaze_records, args.tile_size, slide_w, slide_h)

    # Save CSV
    csv_path = os.path.join(args.output_dir, "dwell_map.csv")
    if not df.empty:
        df.to_csv(csv_path, index=False)
        print("[analyze] CSV saved: {}".format(csv_path))

    # Generate heatmap
    heatmap_path = os.path.join(args.output_dir, "heatmap.png")
    generate_heatmap(df, args.slide, args.tile_size,
                     args.thumbnail_size, heatmap_path)

    # Write summary
    summary_path = os.path.join(args.output_dir, "session_summary.txt")
    write_summary(header, df, events, summary_path)


if __name__ == "__main__":
    main()