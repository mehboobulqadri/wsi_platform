"""
simulator.py
Gaze source client — connects to WSI Viewer, streams gaze data.

Usage:
    python simulator.py [OPTIONS]

Simulator mode (default):
    python simulator.py --source simulator --mode manual --sigma 17
    python simulator.py --source simulator --mode auto --sigma 17

Tobii eye tracker mode:
    python simulator.py --source tobii --screen-width 1920 --screen-height 1080
"""

import sys
import json
import time
import signal
import argparse
import threading

import requests
import websockets.sync.client

from gaze_source import SimulatedGazeSource
from gaze_logger import GazeLogger


def parse_args():
    p = argparse.ArgumentParser(description="WSI Gaze Source")

    # Source selection
    p.add_argument("--source", choices=["simulator", "tobii"],
                    default="simulator",
                    help="Gaze source (default: simulator)")

    # Viewer connection
    p.add_argument("--viewer-url", default="http://127.0.0.1:8000",
                    help="Viewer server URL")
    p.add_argument("--log-dir", default="./logs",
                    help="Directory for JSONL logs")

    # Simulator options
    p.add_argument("--mode", choices=["manual", "auto"], default="manual",
                    help="Simulator mode")
    p.add_argument("--sigma", type=float, default=17.0,
                    help="Gaussian noise in screen pixels")
    p.add_argument("--rate", type=int, default=120,
                    help="Sampling rate Hz")
    p.add_argument("--fix-min", type=int, default=200,
                    help="Min fixation duration ms")
    p.add_argument("--fix-max", type=int, default=500,
                    help="Max fixation duration ms")
    p.add_argument("--auto-interval", type=float, default=0.8,
                    help="Seconds between auto fixations")

    # Tobii options
    p.add_argument("--screen-width", type=int, default=1920,
                    help="Monitor width pixels")
    p.add_argument("--screen-height", type=int, default=1080,
                    help="Monitor height pixels")
    p.add_argument("--browser-offset-x", type=int, default=0,
                    help="Pixels from screen left edge to viewer canvas")
    p.add_argument("--browser-offset-y", type=int, default=0,
                    help="Pixels from screen top edge to viewer canvas")
    p.add_argument("--tobii-frequency", type=int, default=120,
                    help="Tobii output frequency Hz")

    return p.parse_args()


def fetch_slide_info(viewer_url):
    """Get slide metadata from viewer HTTP API."""
    url = "{}/slide/info".format(viewer_url)
    print("[sim] Fetching slide info from {} ...".format(url))
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        info = resp.json()
        print("[sim]   Slide: {}".format(info.get("filename")))
        print("[sim]   Dimensions: {}".format(info.get("slide_dimensions")))
        print("[sim]   Objective: {}x".format(info.get("objective_power")))
        return info
    except Exception as e:
        print("[sim] ERROR: Cannot reach viewer — {}".format(e))
        print("[sim] Start the viewer first:")
        print("[sim]   cd viewer && python app.py <slide.svs>")
        sys.exit(1)


def create_source(args):
    """Create the appropriate GazeSource based on --source flag."""

    if args.source == "simulator":
        source = SimulatedGazeSource(
            sampling_rate=args.rate,
            fixation_duration_range=(args.fix_min, args.fix_max),
            saccade_duration_range=(30, 80),
            sigma_screen_pixels=args.sigma,
            mode=args.mode,
            auto_interval=args.auto_interval,
        )
        config = {
            "source": "simulator",
            "mode": args.mode,
            "sigma_screen": args.sigma,
            "sampling_rate": args.rate,
            "fixation_range": [args.fix_min, args.fix_max],
        }
        return source, config

    elif args.source == "tobii":
        try:
            from tobii_source import TobiiGazeSource
        except SystemExit:
            raise
        except Exception as e:
            print("[sim] ERROR loading Tobii source: {}".format(e))
            print("[sim] Use --source simulator instead.")
            sys.exit(1)

        source = TobiiGazeSource(
            target_frequency=args.tobii_frequency,
            screen_width=args.screen_width,
            screen_height=args.screen_height,
            browser_offset_x=args.browser_offset_x,
            browser_offset_y=args.browser_offset_y,
        )
        config = {
            "source": "tobii",
            "screen": [args.screen_width, args.screen_height],
            "browser_offset": [args.browser_offset_x, args.browser_offset_y],
            "frequency": args.tobii_frequency,
        }
        return source, config

    else:
        print("[sim] Unknown source: {}".format(args.source))
        sys.exit(1)


def print_banner(args):
    """Print startup banner."""
    print("")
    if args.source == "simulator":
        if args.mode == "manual":
            print("=" * 55)
            print("  SIMULATOR — MANUAL MODE")
            print("  Ctrl+Click in viewer to place fixation targets")
            print("  G = toggle gaze render  |  C = clear dots")
            print("  Ctrl+C here to stop")
            print("=" * 55)
        else:
            print("=" * 55)
            print("  SIMULATOR — AUTO MODE")
            print("  Gaze auto-generates within current viewport")
            print("  Pan/zoom to change area")
            print("  Ctrl+C here to stop")
            print("=" * 55)
    else:
        print("=" * 55)
        print("  TOBII PRO EYE TRACKER")
        print("  Look at the slide — gaze maps to tissue in real time")
        print("  Best results: browser fullscreen (F11), offset (0,0)")
        print("  Ctrl+C here to stop")
        print("=" * 55)
    print("")


def main():
    args = parse_args()

    # Connect to viewer and get slide info
    slide_info = fetch_slide_info(args.viewer_url)

    # Create gaze source
    source, config = create_source(args)

    # Create logger
    logger = GazeLogger(args.log_dir, slide_info, config)

    # Connect WebSocket
    ws_url = args.viewer_url.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = ws_url + "/ws"
    print("[sim] Connecting to {} ...".format(ws_url))

    try:
        ws = websockets.sync.client.connect(ws_url)
    except Exception as e:
        print("[sim] ERROR: WebSocket failed — {}".format(e))
        logger.close()
        sys.exit(1)

    print("[sim] Connected!")
    print_banner(args)

    # Shutdown coordination
    shutdown_event = threading.Event()

    def handle_shutdown(signum=None, frame=None):
        print("\n[sim] Shutting down...")
        source.stop()
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_shutdown)

    # ---- Thread 1: Listen for messages from viewer ----
    def listen_thread():
        try:
            while not shutdown_event.is_set():
                try:
                    raw = ws.recv(timeout=0.5)
                    msg = json.loads(raw)
                except TimeoutError:
                    continue
                except Exception:
                    if shutdown_event.is_set():
                        break
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "click":
                    wsi_x = msg.get("wsi_x", 0)
                    wsi_y = msg.get("wsi_y", 0)
                    source.add_fixation_target(wsi_x, wsi_y)
                    logger.log_event("click_target", {
                        "wsi_x": round(wsi_x, 1),
                        "wsi_y": round(wsi_y, 1),
                    })

                elif msg_type == "viewport_update":
                    bounds = msg.get("bounds_wsi")
                    cw = msg.get("container_width", 1920)
                    if bounds:
                        source.set_viewport(bounds, container_width=cw)

        except Exception as e:
            if not shutdown_event.is_set():
                print("[sim] Listen error: {}".format(e))

    listener = threading.Thread(target=listen_thread, daemon=True)
    listener.start()

    # ---- Thread 2: Generate/receive gaze and send to viewer ----
    def gaze_thread():
        sample_count = 0
        last_status = time.time()

        try:
            source.start()

            for point in source.get_stream():
                if shutdown_event.is_set():
                    break

                # Send to viewer
                try:
                    ws.send(json.dumps(point.to_ws_message()))
                except Exception:
                    if shutdown_event.is_set():
                        break
                    print("[sim] WS send failed")
                    break

                # Log to file
                logger.log(point)
                sample_count += 1

                # Status every 2 seconds
                now = time.time()
                if now - last_status > 2.0:
                    parts = ["Samples: {}".format(sample_count)]
                    if args.source == "simulator":
                        parts.append("FID: {}".format(point.fixation_id))
                        if hasattr(source, "_effective_sigma"):
                            parts.append("sigma_wsi: {:.1f}".format(
                                source._effective_sigma
                            ))
                    else:
                        parts.append("WSI: ({:.0f}, {:.0f})".format(
                            point.wsi_x, point.wsi_y
                        ))
                    print("[sim] " + "  |  ".join(parts))
                    last_status = now

        except Exception as e:
            if not shutdown_event.is_set():
                print("[sim] Gaze error: {}".format(e))

    gaze = threading.Thread(target=gaze_thread, daemon=True)
    gaze.start()

    # ---- Wait for shutdown ----
    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=1.0)
    except KeyboardInterrupt:
        handle_shutdown()

    # ---- Cleanup ----
    gaze.join(timeout=3)
    listener.join(timeout=3)

    try:
        ws.close()
    except Exception:
        pass

    logger.close()
    print("[sim] Done.")


if __name__ == "__main__":
    main()