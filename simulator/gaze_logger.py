"""
gaze_logger.py
Logs gaze data to JSONL files.
"""

import os
import json
import time
from datetime import datetime

from gaze_source import GazePoint


class GazeLogger:
    """Appends gaze points to a JSONL file, one JSON object per line."""

    def __init__(self, output_dir, slide_info, simulator_config):
        # type: (str, dict, dict) -> None
        """
        Create a new session log file.

        Args:
            output_dir: directory to write log files
            slide_info: dict from /slide/info
            simulator_config: dict of simulator parameters
        """
        os.makedirs(output_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        slide_name = slide_info.get("filename", "unknown")
        safe_name = slide_name.replace(".", "_")
        filename = "session_{}_{}.jsonl".format(safe_name, timestamp)
        self.filepath = os.path.join(output_dir, filename)

        self._file = open(self.filepath, "w", encoding="utf-8")
        self._count = 0

        # Write header as first line
        header = {
            "type": "session_header",
            "slide": slide_name,
            "slide_dimensions": slide_info.get("slide_dimensions"),
            "objective_power": slide_info.get("objective_power"),
            "mpp_x": slide_info.get("mpp_x"),
            "mpp_y": slide_info.get("mpp_y"),
            "start_time": datetime.now().isoformat(),
            "simulator_config": simulator_config,
        }
        self._write_line(header)
        print("[logger] Session file: {}".format(self.filepath))

    def log(self, point):
        # type: (GazePoint) -> None
        """Log a single gaze point."""
        record = point.to_dict()
        record["type"] = "gaze"
        self._write_line(record)
        self._count += 1

        # Flush every 100 samples to avoid data loss
        if self._count % 100 == 0:
            self._file.flush()

    def log_event(self, event_type, data=None):
        # type: (str, dict) -> None
        """Log a non-gaze event (click, viewport change, etc.)."""
        record = {
            "type": event_type,
            "t": round(time.time() * 1000, 1),
        }
        if data:
            record.update(data)
        self._write_line(record)

    def _write_line(self, obj):
        # type: (dict) -> None
        self._file.write(json.dumps(obj, separators=(",", ":")) + "\n")

    def close(self):
        # type: () -> None
        """Flush and close the log file."""
        if self._file and not self._file.closed:
            self._file.flush()
            self._file.close()
            print("[logger] Closed. {} gaze samples written to {}".format(
                self._count, self.filepath
            ))

    @property
    def sample_count(self):
        # type: () -> int
        return self._count