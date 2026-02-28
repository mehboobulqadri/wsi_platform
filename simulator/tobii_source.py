"""
tobii_source.py
Tobii Pro eye tracker gaze source — implements GazeSource interface.

Converts Tobii normalized screen coordinates (0.0–1.0) to WSI level-0
coordinates using viewport information from the viewer.

Coordinate chain:
    Tobii normalized → screen pixels → browser canvas → WSI coords
"""

import sys
import time
import threading
from typing import Optional, Iterator

from gaze_source import GazeSource, GazePoint

try:
    import tobii_research as tr
    TOBII_AVAILABLE = True
except ImportError:
    TOBII_AVAILABLE = False


class TobiiGazeSource(GazeSource):
    """
    Real eye-tracking gaze via Tobii Pro SDK.

    Usage:
        source = TobiiGazeSource(
            screen_width=1920,
            screen_height=1080,
            browser_offset_x=0,   # 0 if browser is fullscreen (F11)
            browser_offset_y=0,
        )
        source.set_viewport(bounds, container_width=1920)
        source.start()
        for point in source.get_stream():
            # point.wsi_x, point.wsi_y are in WSI level-0 space
            pass
    """

    def __init__(
        self,
        tracker=None,
        target_frequency=120,
        screen_width=1920,
        screen_height=1080,
        browser_offset_x=0,
        browser_offset_y=0,
    ):
        if not TOBII_AVAILABLE:
            print("[tobii] ERROR: tobii-research not installed.")
            print("[tobii] Install: uv pip install tobii-research")
            sys.exit(1)

        self.target_frequency = target_frequency
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.browser_offset_x = browser_offset_x
        self.browser_offset_y = browser_offset_y

        self._tracker = tracker
        self._running = False
        self._start_time = 0.0

        # Viewport state (updated by set_viewport)
        self._viewport = None          # type: Optional[dict]
        self._container_width = 1920
        self._container_height = 1080

        # Gaze buffer — filled by SDK callback thread, drained by get_stream
        self._gaze_buffer = []         # type: list
        self._buffer_lock = threading.Lock()

        # Stats
        self._total_received = 0
        self._total_valid = 0
        self._total_in_canvas = 0

    def start(self):
        """Start receiving gaze data from the tracker."""
        if self._tracker is None:
            from tobii_calibration import find_tracker, set_frequency
            self._tracker = find_tracker()
            set_frequency(self._tracker, self.target_frequency)

        self._running = True
        self._start_time = time.time()
        self._gaze_buffer = []
        self._total_received = 0
        self._total_valid = 0
        self._total_in_canvas = 0

        self._tracker.subscribe_to(
            tr.EYETRACKER_GAZE_DATA,
            self._gaze_callback,
        )

        actual_freq = self._tracker.get_gaze_output_frequency()
        print("[tobii] Streaming at {} Hz".format(actual_freq))
        print("[tobii] Screen: {}x{}".format(
            self.screen_width, self.screen_height
        ))
        print("[tobii] Browser offset: ({}, {})".format(
            self.browser_offset_x, self.browser_offset_y
        ))
        print("[tobii] Container: {}x{}".format(
            self._container_width, self._container_height
        ))

    def stop(self):
        """Stop receiving gaze data."""
        self._running = False
        if self._tracker is not None:
            try:
                self._tracker.unsubscribe_from(
                    tr.EYETRACKER_GAZE_DATA,
                    self._gaze_callback,
                )
            except Exception:
                pass

        # Print stats
        if self._total_received > 0:
            print("[tobii] Session stats:")
            print("[tobii]   Total samples:   {}".format(self._total_received))
            print("[tobii]   Valid gaze:      {} ({:.1f}%)".format(
                self._total_valid,
                100.0 * self._total_valid / self._total_received,
            ))
            print("[tobii]   In canvas:       {} ({:.1f}%)".format(
                self._total_in_canvas,
                100.0 * self._total_in_canvas / self._total_received,
            ))

        print("[tobii] Stopped")

    def add_fixation_target(self, wsi_x, wsi_y):
        """Not used for real eye tracking — gaze comes from eyes."""
        pass

    def set_viewport(self, bounds, container_width=1920):
        """
        Update current viewport info from viewer.
        Called every time the user zooms/pans.

        Args:
            bounds: dict with x_min, y_min, x_max, y_max (WSI level-0 px)
            container_width: viewer canvas width in screen pixels
        """
        self._viewport = bounds
        self._container_width = max(container_width, 1)

        # Estimate container height from viewport aspect ratio
        if bounds is not None:
            vp_w = float(bounds["x_max"] - bounds["x_min"])
            vp_h = float(bounds["y_max"] - bounds["y_min"])
            if vp_w > 0:
                aspect = vp_h / vp_w
                self._container_height = max(1, int(self._container_width * aspect))

    def set_browser_offset(self, offset_x, offset_y):
        """Update browser canvas offset on screen."""
        self.browser_offset_x = offset_x
        self.browser_offset_y = offset_y
        print("[tobii] Browser offset: ({}, {})".format(offset_x, offset_y))

    def _gaze_callback(self, gaze_data):
        """Called by Tobii SDK on each sample. Runs in SDK's internal thread."""
        if not self._running:
            return
        with self._buffer_lock:
            self._gaze_buffer.append(gaze_data)

    def _process_sample(self, gaze_data):
        """
        Convert one Tobii sample to a GazePoint in WSI coordinates.
        Returns None if invalid or outside viewer canvas.
        """
        self._total_received += 1

        if self._viewport is None:
            return None

        # --- Extract gaze from both eyes ---
        left_pt = gaze_data["left_gaze_point_on_display_area"]
        right_pt = gaze_data["right_gaze_point_on_display_area"]
        left_ok = gaze_data["left_gaze_point_validity"] == 1
        right_ok = gaze_data["right_gaze_point_validity"] == 1

        # Average valid eyes
        if left_ok and right_ok:
            norm_x = (left_pt[0] + right_pt[0]) / 2.0
            norm_y = (left_pt[1] + right_pt[1]) / 2.0
        elif left_ok:
            norm_x = left_pt[0]
            norm_y = left_pt[1]
        elif right_ok:
            norm_x = right_pt[0]
            norm_y = right_pt[1]
        else:
            return None  # no valid gaze

        self._total_valid += 1

        # --- Step 1: Normalized (0–1) → screen pixels ---
        screen_x = norm_x * self.screen_width
        screen_y = norm_y * self.screen_height

        # --- Step 2: Screen pixels → browser canvas pixels ---
        canvas_x = screen_x - self.browser_offset_x
        canvas_y = screen_y - self.browser_offset_y

        # Reject if outside the viewer canvas
        if canvas_x < 0 or canvas_x > self._container_width:
            return None
        if canvas_y < 0 or canvas_y > self._container_height:
            return None

        self._total_in_canvas += 1

        # --- Step 3: Canvas pixels → WSI level-0 coordinates ---
        bounds = self._viewport
        vp_wsi_w = float(bounds["x_max"] - bounds["x_min"])
        vp_wsi_h = float(bounds["y_max"] - bounds["y_min"])

        wsi_x = bounds["x_min"] + (canvas_x / self._container_width) * vp_wsi_w
        wsi_y = bounds["y_min"] + (canvas_y / self._container_height) * vp_wsi_h

        elapsed = (time.time() - self._start_time) * 1000.0

        return GazePoint(
            timestamp_ms=elapsed,
            wsi_x=wsi_x,
            wsi_y=wsi_y,
            is_saccade=False,    # no saccade detection in raw stream
            fixation_id=-1,      # no fixation detection in raw stream
            source="tobii",
        )

    def get_stream(self):
        """
        Yield GazePoints as they arrive from the tracker.
        Blocks when no data is available.
        """
        while self._running:
            # Drain the buffer
            batch = []
            with self._buffer_lock:
                if self._gaze_buffer:
                    batch = self._gaze_buffer[:]
                    self._gaze_buffer = []

            if not batch:
                time.sleep(0.004)  # ~250 Hz poll, won't miss 120 Hz data
                continue

            for raw_sample in batch:
                point = self._process_sample(raw_sample)
                if point is not None:
                    yield point