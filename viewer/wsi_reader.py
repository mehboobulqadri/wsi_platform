"""
wsi_reader.py
Wrapper around OpenSlide + DeepZoomGenerator for tile-based WSI serving.
"""

import os
import math
from typing import Dict, List, Optional, Tuple

import openslide
from openslide import OpenSlide, OpenSlideError
from openslide.deepzoom import DeepZoomGenerator
from PIL import Image


class WSIReader:
    """
    Reads a Whole Slide Image and generates Deep Zoom tiles.

    Coordinate note:
        All 'wsi' coordinates in this system refer to the Deep Zoom
        max-level coordinate space. With limit_bounds=True (default),
        this is the bounding box of the tissue region, NOT necessarily
        the full level-0 pixel space. For most slides the difference
        is zero or negligible.
    """

    def __init__(self, slide_path, tile_size=256, overlap=0):
        # type: (str, int, int) -> None
        if not os.path.isfile(slide_path):
            raise FileNotFoundError("Slide not found: {}".format(slide_path))

        self.slide_path = slide_path
        self.tile_size = tile_size
        self.overlap = overlap

        self.slide = OpenSlide(slide_path)
        self.dz = DeepZoomGenerator(
            self.slide,
            tile_size=tile_size,
            overlap=overlap,
            limit_bounds=True,
        )

    def get_info(self):
        # type: () -> Dict
        """Return slide metadata as a JSON-serializable dictionary."""
        props = self.slide.properties

        objective_power = props.get(openslide.PROPERTY_NAME_OBJECTIVE_POWER)
        mpp_x = props.get(openslide.PROPERTY_NAME_MPP_X)
        mpp_y = props.get(openslide.PROPERTY_NAME_MPP_Y)
        bounds_x = props.get(openslide.PROPERTY_NAME_BOUNDS_X, "0")
        bounds_y = props.get(openslide.PROPERTY_NAME_BOUNDS_Y, "0")

        max_level = self.dz.level_count - 1
        max_dims = self.dz.level_dimensions[max_level]

        return {
            "filename": os.path.basename(self.slide_path),
            # Full level-0 slide dimensions
            "slide_dimensions": list(self.slide.dimensions),
            # OpenSlide pyramid info
            "level_count": self.slide.level_count,
            "level_dimensions": [list(d) for d in self.slide.level_dimensions],
            "level_downsamples": [float(d) for d in self.slide.level_downsamples],
            # Scan metadata
            "objective_power": float(objective_power) if objective_power else None,
            "mpp_x": float(mpp_x) if mpp_x else None,
            "mpp_y": float(mpp_y) if mpp_y else None,
            # Bounds offset (for converting DZ coords → level-0 coords)
            "bounds_x": int(bounds_x),
            "bounds_y": int(bounds_y),
            # DeepZoom tile info
            "tile_size": self.tile_size,
            "overlap": self.overlap,
            "dz_level_count": self.dz.level_count,
            "dz_max_level": max_level,
            "dz_max_dimensions": list(max_dims),
            "dz_level_tiles": [list(t) for t in self.dz.level_tiles],
        }

    def get_tile(self, level, col, row):
        # type: (int, int, int) -> Image.Image
        """
        Get a single tile as a PIL Image.
        Raises ValueError if coordinates are out of range.
        """
        if level < 0 or level >= self.dz.level_count:
            raise ValueError(
                "Level {} out of range [0, {})".format(level, self.dz.level_count)
            )

        tiles_x, tiles_y = self.dz.level_tiles[level]
        if col < 0 or col >= tiles_x or row < 0 or row >= tiles_y:
            raise ValueError(
                "Tile ({}, {}) out of range at level {} (max: {}, {})".format(
                    col, row, level, tiles_x - 1, tiles_y - 1
                )
            )

        tile = self.dz.get_tile(level, (col, row))

        # Ensure RGB (some formats return RGBA)
        if tile.mode != "RGB":
            tile = tile.convert("RGB")

        return tile

    def get_magnification_at_dz_level(self, dz_level):
        # type: (int) -> Optional[float]
        """Calculate effective magnification at a given DZ level."""
        obj_str = self.slide.properties.get(openslide.PROPERTY_NAME_OBJECTIVE_POWER)
        if obj_str is None:
            return None

        objective = float(obj_str)
        max_level = self.dz.level_count - 1
        downsample = 2.0 ** (max_level - dz_level)
        return objective / downsample

    def get_dz_level_for_magnification(self, target_mag):
        # type: (float) -> Optional[int]
        """Find the DeepZoom level closest to a target magnification."""
        obj_str = self.slide.properties.get(openslide.PROPERTY_NAME_OBJECTIVE_POWER)
        if obj_str is None:
            return None

        objective = float(obj_str)
        max_level = self.dz.level_count - 1

        if target_mag <= 0:
            return 0
        if target_mag >= objective:
            return max_level

        ratio = objective / target_mag
        diff = math.log2(ratio)
        dz_level = int(round(max_level - diff))
        return max(0, min(max_level, dz_level))

    def close(self):
        # type: () -> None
        """Release the OpenSlide handle."""
        self.slide.close()