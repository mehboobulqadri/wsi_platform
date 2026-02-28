"""
tobii_calibration.py
Simple calibration wrapper for Tobii Pro eye trackers.

Requires: tobii-research package (Python 3.10+)

Run standalone to test your tracker:
    python tobii_calibration.py
"""

import sys
import time

try:
    import tobii_research as tr
    TOBII_AVAILABLE = True
except ImportError:
    TOBII_AVAILABLE = False


def check_tobii():
    """Verify tobii_research is importable."""
    if not TOBII_AVAILABLE:
        print("=" * 60)
        print("  tobii-research package is NOT installed")
        print("=" * 60)
        print("")
        print("Install with:")
        print("  uv pip install tobii-research")
        print("")
        v = sys.version_info
        print("Your Python: {}.{}.{}".format(v.major, v.minor, v.micro))
        if v.minor < 10:
            print("")
            print("WARNING: tobii-research requires Python 3.10+")
            print("You are on 3.{}. Upgrade Python first.".format(v.minor))
        print("=" * 60)
        sys.exit(1)


def find_tracker():
    """Find and return the first available Tobii Pro eye tracker."""
    check_tobii()

    print("[tobii] Searching for eye trackers...")
    trackers = tr.find_all_eyetrackers()

    if not trackers:
        print("[tobii] ERROR: No eye trackers found.")
        print("[tobii] Check:")
        print("[tobii]   1. USB cable is connected")
        print("[tobii]   2. Tobii Pro Eye Tracker Manager can see the device")
        print("[tobii]   3. No other application is using the tracker")
        sys.exit(1)

    tracker = trackers[0]
    print("[tobii] Found: {} (serial: {})".format(
        tracker.model, tracker.serial_number
    ))
    print("[tobii] Address: {}".format(tracker.address))
    print("[tobii] Frequency: {} Hz".format(tracker.get_gaze_output_frequency()))
    print("[tobii] Available frequencies: {}".format(
        tracker.get_all_gaze_output_frequencies()
    ))

    if len(trackers) > 1:
        print("[tobii] NOTE: {} trackers found, using first one.".format(
            len(trackers)
        ))

    return tracker


def set_frequency(tracker, target_hz):
    """Set the tracker's output frequency."""
    available = tracker.get_all_gaze_output_frequencies()
    if target_hz in available:
        tracker.set_gaze_output_frequency(target_hz)
        print("[tobii] Frequency set to {} Hz".format(target_hz))
    else:
        current = tracker.get_gaze_output_frequency()
        print("[tobii] WARNING: {} Hz not available.".format(target_hz))
        print("[tobii]   Available: {}".format(available))
        print("[tobii]   Using current: {} Hz".format(current))


def quick_test(tracker, duration_s=5):
    """Record gaze data for a few seconds and print diagnostics."""
    check_tobii()

    samples = []

    def callback(gaze_data):
        samples.append(gaze_data)

    print("")
    print("[tobii] Recording {} seconds of gaze data...".format(duration_s))
    print("[tobii] Look at different parts of your screen.")
    print("")

    tracker.subscribe_to(tr.EYETRACKER_GAZE_DATA, callback)
    time.sleep(duration_s)
    tracker.unsubscribe_from(tr.EYETRACKER_GAZE_DATA, callback)

    print("[tobii] Received {} samples ({:.1f} Hz effective)".format(
        len(samples),
        len(samples) / duration_s if duration_s > 0 else 0,
    ))

    if not samples:
        print("[tobii] WARNING: No gaze data received!")
        print("[tobii] Run calibration in Tobii Pro Eye Tracker Manager first.")
        return False

    # Print last 5 samples
    print("")
    print("[tobii] Last 5 samples:")
    print("  {:>8s}  {:>8s}  {:>5s}  {:>8s}  {:>8s}  {:>5s}".format(
        "L_x", "L_y", "L_ok", "R_x", "R_y", "R_ok"
    ))
    for s in samples[-5:]:
        lp = s["left_gaze_point_on_display_area"]
        rp = s["right_gaze_point_on_display_area"]
        lv = s["left_gaze_point_validity"]
        rv = s["right_gaze_point_validity"]
        print("  {:8.4f}  {:8.4f}  {:>5s}  {:8.4f}  {:8.4f}  {:>5s}".format(
            lp[0], lp[1], str(lv == 1),
            rp[0], rp[1], str(rv == 1),
        ))

    # Validity stats
    valid_left = sum(1 for s in samples if s["left_gaze_point_validity"] == 1)
    valid_right = sum(1 for s in samples if s["right_gaze_point_validity"] == 1)
    valid_either = sum(
        1 for s in samples
        if s["left_gaze_point_validity"] == 1
        or s["right_gaze_point_validity"] == 1
    )
    valid_both = sum(
        1 for s in samples
        if s["left_gaze_point_validity"] == 1
        and s["right_gaze_point_validity"] == 1
    )

    total = len(samples)
    print("")
    print("[tobii] Validity:")
    print("  Left eye:   {}/{} ({:.1f}%)".format(
        valid_left, total, 100.0 * valid_left / total
    ))
    print("  Right eye:  {}/{} ({:.1f}%)".format(
        valid_right, total, 100.0 * valid_right / total
    ))
    print("  Both eyes:  {}/{} ({:.1f}%)".format(
        valid_both, total, 100.0 * valid_both / total
    ))
    print("  Either eye: {}/{} ({:.1f}%)".format(
        valid_either, total, 100.0 * valid_either / total
    ))

    ratio = valid_either / total if total > 0 else 0

    print("")
    if ratio < 0.3:
        print("[tobii] POOR — tracker cannot see your eyes.")
        print("[tobii]   Adjust tracker position and run calibration.")
        return False
    elif ratio < 0.7:
        print("[tobii] FAIR — some data loss. Run calibration for better results.")
        return True
    else:
        print("[tobii] GOOD — tracker is working well.")
        return True


if __name__ == "__main__":
    check_tobii()
    tracker = find_tracker()
    set_frequency(tracker, 120)
    ok = quick_test(tracker, duration_s=5)
    print("")
    if ok:
        print("Ready to use with: python simulator.py --source tobii")
    else:
        print("Fix tracking issues before running experiments.")