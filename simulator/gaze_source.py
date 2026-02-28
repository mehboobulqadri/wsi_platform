"""
gaze_source.py
Abstract gaze source + simulated gaze with zoom-adaptive sigma.

v2 — sigma is in screen pixels, converted dynamically using viewport.
"""

import time
import math
import random
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List, Tuple, Iterator


@dataclass
class GazePoint:
    """Single gaze sample."""
    timestamp_ms: float
    wsi_x: float
    wsi_y: float
    is_saccade: bool
    fixation_id: int
    source: str

    def to_dict(self):
        return {
            "t": round(self.timestamp_ms, 1),
            "wx": round(self.wsi_x, 1),
            "wy": round(self.wsi_y, 1),
            "sac": self.is_saccade,
            "fid": self.fixation_id,
            "src": self.source,
        }

    def to_ws_message(self):
        return {
            "type": "gaze_point",
            "wsi_x": round(self.wsi_x, 1),
            "wsi_y": round(self.wsi_y, 1),
            "is_saccade": self.is_saccade,
            "fixation_id": self.fixation_id,
        }


class GazeSource(ABC):

    @abstractmethod
    def start(self):
        # type: () -> None
        pass

    @abstractmethod
    def stop(self):
        # type: () -> None
        pass

    @abstractmethod
    def add_fixation_target(self, wsi_x, wsi_y):
        # type: (float, float) -> None
        pass

    @abstractmethod
    def set_viewport(self, bounds, container_width=1920):
        # type: (dict, int) -> None
        pass

    @abstractmethod
    def get_stream(self):
        # type: () -> Iterator[GazePoint]
        pass


class SimulatedGazeSource(GazeSource):
    """
    Generates simulated gaze with fixation-saccade model.

    Sigma is specified in SCREEN pixels (constant visual angle).
    Internally converted to WSI pixels using current viewport zoom.

    At 60cm viewing distance on a 24" 1920x1080 monitor:
      1 degree visual angle ~ 35 screen pixels
      0.5 degree (foveal accuracy) ~ 17 screen pixels
    """

    def __init__(
        self,
        sampling_rate=120,
        fixation_duration_range=(200, 500),
        saccade_duration_range=(30, 80),
        sigma_screen_pixels=17.0,
        mode="manual",
        auto_interval=0.8,
    ):
        # type: (int, Tuple[int,int], Tuple[int,int], float, str, float) -> None

        self.sampling_rate = sampling_rate
        self.sample_interval = 1.0 / sampling_rate
        self.fixation_duration_range = fixation_duration_range
        self.saccade_duration_range = saccade_duration_range
        self.sigma_screen = sigma_screen_pixels
        self.mode = mode
        self.auto_interval = auto_interval

        self._running = False
        self._targets = []             # type: List[Tuple[float, float]]
        self._target_lock = threading.Lock()
        self._viewport = None          # type: Optional[dict]
        self._container_width = 1920   # default until viewer reports
        self._effective_sigma = sigma_screen_pixels  # WSI pixels (updated on viewport change)
        self._fixation_id = 0
        self._start_time = 0.0

    def start(self):
        # type: () -> None
        self._running = True
        self._start_time = time.time()
        self._fixation_id = 0
        print("[gaze] Started (mode={}, sigma_screen={}, rate={} Hz)".format(
            self.mode, self.sigma_screen, self.sampling_rate
        ))

    def stop(self):
        # type: () -> None
        self._running = False
        print("[gaze] Stopped")

    def add_fixation_target(self, wsi_x, wsi_y):
        # type: (float, float) -> None
        with self._target_lock:
            self._targets.append((wsi_x, wsi_y))
        print("[gaze] Target: ({:.0f}, {:.0f})  sigma_wsi={:.1f}  queue={}".format(
            wsi_x, wsi_y, self._effective_sigma, len(self._targets)
        ))

    def set_viewport(self, bounds, container_width=1920):
        # type: (dict, int) -> None
        """
        Update viewport info and recompute effective sigma.

        sigma_wsi = sigma_screen * (viewport_wsi_width / container_screen_width)

        This makes the visual scatter constant on screen regardless of zoom.
        """
        self._viewport = bounds
        self._container_width = max(container_width, 1)

        viewport_wsi_width = float(bounds["x_max"] - bounds["x_min"])
        wsi_per_screen = viewport_wsi_width / self._container_width
        self._effective_sigma = self.sigma_screen * wsi_per_screen

    def _get_next_target(self):
        # type: () -> Optional[Tuple[float, float]]
        if self.mode == "manual":
            with self._target_lock:
                if self._targets:
                    return self._targets.pop(0)
            return None

        elif self.mode == "auto":
            vp = self._viewport
            if vp is None:
                return None
            x = random.uniform(vp["x_min"], vp["x_max"])
            y = random.uniform(vp["y_min"], vp["y_max"])
            return (x, y)

        return None

    def _elapsed_ms(self):
        # type: () -> float
        return (time.time() - self._start_time) * 1000.0

    def get_stream(self):
        # type: () -> Iterator[GazePoint]
        prev_target = None  # type: Optional[Tuple[float, float]]

        while self._running:
            target = self._get_next_target()

            if target is None:
                time.sleep(0.05)
                continue

            # Saccade to new target
            if prev_target is not None:
                for pt in self._generate_saccade(prev_target, target):
                    if not self._running:
                        return
                    yield pt

            # Fixation at target
            for pt in self._generate_fixation(target):
                if not self._running:
                    return
                yield pt

            prev_target = target

            if self.mode == "auto":
                pause = self.auto_interval * random.uniform(0.5, 1.5)
                time.sleep(pause)

    def _generate_fixation(self, target):
        # type: (Tuple[float, float]) -> Iterator[GazePoint]
        tx, ty = target
        duration_ms = random.uniform(
            self.fixation_duration_range[0],
            self.fixation_duration_range[1],
        )
        n_samples = max(1, int(duration_ms / 1000.0 * self.sampling_rate))
        fid = self._fixation_id
        self._fixation_id += 1

        sigma = self._effective_sigma

        for i in range(n_samples):
            if not self._running:
                return

            noise_x = random.gauss(0, sigma)
            noise_y = random.gauss(0, sigma)

            yield GazePoint(
                timestamp_ms=self._elapsed_ms(),
                wsi_x=tx + noise_x,
                wsi_y=ty + noise_y,
                is_saccade=False,
                fixation_id=fid,
                source="simulator",
            )
            time.sleep(self.sample_interval)

    def _generate_saccade(self, start, end):
        # type: (Tuple[float, float], Tuple[float, float]) -> Iterator[GazePoint]
        duration_ms = random.uniform(
            self.saccade_duration_range[0],
            self.saccade_duration_range[1],
        )
        n_samples = max(2, int(duration_ms / 1000.0 * self.sampling_rate))
        sx, sy = start
        ex, ey = end

        for i in range(n_samples):
            if not self._running:
                return

            t = i / float(n_samples - 1)
            t_smooth = t * t * (3.0 - 2.0 * t)

            x = sx + (ex - sx) * t_smooth
            y = sy + (ey - sy) * t_smooth

            yield GazePoint(
                timestamp_ms=self._elapsed_ms(),
                wsi_x=x,
                wsi_y=y,
                is_saccade=True,
                fixation_id=-1,
                source="simulator",
            )
            time.sleep(self.sample_interval)