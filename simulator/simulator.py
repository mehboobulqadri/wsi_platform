"""
simulator.py
Gaze Simulator — connects to WSI Viewer, generates simulated gaze.

Usage:
    python simulator.py [OPTIONS]

Examples:
    python simulator.py --mode manual --sigma 17
    python simulator.py --mode auto --sigma 17 --rate 60
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
    p = argparse.ArgumentParser(description="WSI Gaze Simulator")
    p.add_argument("--viewer-url", default="http://127.0.0.1:8000",
                    help="Viewer server URL")
    p.add_argument("--mode", choices=["manual", "auto"], default="manual",
                    help="manual = click targets, auto = random within viewport")
    p.add_argument("--sigma", type=float, default=17.0,
                    help="Gaussian noise in SCREEN pixels (default: 17)")
    p.add_argument("--rate", type=int, default=120,
                    help="Sampling rate Hz (default: 120)")
    p.add_argument("--fix-min", type=int, default=200,
                    help="Min fixation duration ms")
    p.add_argument("--fix-max", type=int, default=500,
                    help="Max fixation duration ms")
    p.add_argument("--auto-interval", type=float, default=0.8,
                    help="Seconds between auto fixations")
    p.add_argument("--log-dir", default="./logs",
                    help="Directory for JSONL logs")
    return p.parse_args()


def fetch_slide_info(viewer_url):
    # type: (str) -> dict
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


def main():
    args = parse_args()

    slide_info = fetch_slide_info(args.viewer_url)

    source = SimulatedGazeSource(
        sampling_rate=args.rate,
        fixation_duration_range=(args.fix_min, args.fix_max),
        saccade_duration_range=(30, 80),
        sigma_screen_pixels=args.sigma,
        mode=args.mode,
        auto_interval=args.auto_interval,
    )

    config = {
        "mode": args.mode,
        "sigma_screen": args.sigma,
        "sampling_rate": args.rate,
        "fixation_range": [args.fix_min, args.fix_max],
    }
    logger = GazeLogger(args.log_dir, slide_info, config)

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
    print("")

    if args.mode == "manual":
        print("=" * 55)
        print("  MANUAL MODE")
        print("  Ctrl+Click in viewer to place fixation targets")
        print("  G = toggle gaze render  |  C = clear dots")
        print("  Ctrl+C here to stop")
        print("=" * 55)
    else:
        print("=" * 55)
        print("  AUTO MODE")
        print("  Gaze auto-generates within current viewport")
        print("  Pan/zoom to change area")
        print("  Ctrl+C here to stop")
        print("=" * 55)
    print("")

    shutdown_event = threading.Event()

    def handle_shutdown(signum=None, frame=None):
        print("\n[sim] Shutting down...")
        source.stop()
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_shutdown)

    # Thread: listen for messages FROM viewer
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

    # Thread: generate gaze and send
    def gaze_thread():
        sample_count = 0
        last_status = time.time()

        try:
            source.start()

            for point in source.get_stream():
                if shutdown_event.is_set():
                    break

                try:
                    ws.send(json.dumps(point.to_ws_message()))
                except Exception:
                    if shutdown_event.is_set():
                        break
                    print("[sim] WS send failed")
                    break

                logger.log(point)
                sample_count += 1

                now = time.time()
                if now - last_status > 2.0:
                    print("[sim] Samples: {}  |  FID: {}  |  sigma_wsi: {:.1f}".format(
                        sample_count,
                        point.fixation_id,
                        source._effective_sigma,
                    ))
                    last_status = now

        except Exception as e:
            if not shutdown_event.is_set():
                print("[sim] Gaze error: {}".format(e))

    gaze = threading.Thread(target=gaze_thread, daemon=True)
    gaze.start()

    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=1.0)
    except KeyboardInterrupt:
        handle_shutdown()

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