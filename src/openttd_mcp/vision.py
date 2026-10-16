"""Vision sensor helpers for map screenshots and tile-centered crops."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from openttd_mcp.render_capture import SCREENSHOT_BLITTER, capture_giant_screenshot

if TYPE_CHECKING:
    from openttd_mcp.admin import AdminClient, AdminLifecycleConfig


DEFAULT_WINDOW = 384
DEFAULT_REFRESH = True
# Advertised crop-side bounds. These back the tool schema and runtime validation:
# FastMCP generates the live tool schema from the wrapper's annotation alone, so
# they are carried into the served schema by `server.ViewMapWindow` and re-checked
# in `_handle_view_map` for the non-MCP callers. An unbounded window would hand
# the model the full multi-megapixel giant screenshot and defeat the crop.
WINDOW_MIN = 64
WINDOW_MAX = 2048
GIANT_PX_PER_TILE_X = 32
GIANT_PX_PER_TILE_Y = 16
GIANT_ORIGIN_PY = 118


def map_side(map_log2: int) -> int:
    """Return the side length, in tiles, for a square OpenTTD map."""
    return 2**map_log2


def tile_to_xy(tile: int, map_log2: int) -> tuple[int, int]:
    """Return ``(x, y)`` for an OpenTTD TileXY tile id."""
    side = map_side(map_log2)
    return tile % side, tile // side


def in_bounds(tile: int, map_log2: int) -> bool:
    """Return whether ``tile`` is inside the square map."""
    side = map_side(map_log2)
    return 0 <= tile < side * side


def tile_to_giant_px(tile: int, map_log2: int) -> tuple[int, int]:
    """Return final-PNG giant screenshot coordinates for ``tile``."""
    side = map_side(map_log2)
    tx, ty = tile_to_xy(tile, map_log2)
    px = GIANT_PX_PER_TILE_X * (ty - tx) + GIANT_PX_PER_TILE_X * (side - 2)
    py = GIANT_PX_PER_TILE_Y * (ty + tx) + GIANT_ORIGIN_PY
    return px, py


def giant_px_to_tile_xy(px: float, py: float, map_log2: int) -> tuple[float, float]:
    """Return tile-space ``(x, y)`` coordinates for giant screenshot pixels."""
    side = map_side(map_log2)
    difference = px / GIANT_PX_PER_TILE_X - (side - 2)
    total = (py - GIANT_ORIGIN_PY) / GIANT_PX_PER_TILE_Y
    return (total - difference) / 2, (total + difference) / 2


def tile_frame_for_crop(bounds: tuple[int, int, int, int], map_log2: int) -> dict:
    """Return tile-space facts for a PIL crop box."""
    x0, y0, x1, y1 = bounds
    # PIL's x1/y1 bounds are exclusive; right/bottom corners use the last
    # pixels that are actually present in the crop.
    corner_pixels = {
        "top_left": (x0, y0),
        "top_right": (x1 - 1, y0),
        "bottom_right": (x1 - 1, y1 - 1),
        "bottom_left": (x0, y1 - 1),
    }
    side = map_side(map_log2)

    def tile_point(px: int, py: int) -> dict:
        x, y = giant_px_to_tile_xy(px, py, map_log2)
        return {
            "x": x,
            "y": y,
            "in_map": 0 <= x < side and 0 <= y < side,
        }

    corners = {name: tile_point(px, py) for name, (px, py) in corner_pixels.items()}
    xs = [corner["x"] for corner in corners.values()]
    ys = [corner["y"] for corner in corners.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    rendered_center_px = (x0 + (x1 - x0) // 2, y0 + (y1 - y0) // 2)
    rendered_center_tile = tile_point(*rendered_center_px)
    if rendered_center_tile["in_map"]:
        tile_x = int(rendered_center_tile["x"])
        tile_y = int(rendered_center_tile["y"])
        rendered_center_tile["tile"] = tile_y * side + tile_x

    return {
        "frame_corner_tiles": corners,
        "rendered_center_tile": rendered_center_tile,
        "tile_extent": {"x": max_x - min_x, "y": max_y - min_y},
        "visible_diamond_tile_bbox": {
            "min_x": min_x,
            "min_y": min_y,
            "max_x": max_x,
            "max_y": max_y,
        },
    }


def _crop_axis(center: int, window: int, img_dim: int) -> tuple[int, int]:
    if window <= 0:
        raise ValueError(f"window must be positive, got {window}")
    if img_dim < 0:
        raise ValueError(f"image dimension must be non-negative, got {img_dim}")
    if window >= img_dim:
        return 0, img_dim

    start = max(0, min(center - window // 2, img_dim - window))
    return start, start + window


def crop_box(
    center_px: int, center_py: int, window: int, img_w: int, img_h: int
) -> tuple[int, int, int, int]:
    """Return a clamped crop box centered as closely as possible on a pixel."""
    x0, x1 = _crop_axis(center_px, window, img_w)
    y0, y1 = _crop_axis(center_py, window, img_h)
    return x0, y0, x1, y1


class ScreenshotSource:
    """Lazy source for full-map giant screenshots and per-call crop artifacts."""

    _GIANT_BASENAME = "viewmap-giant"
    _CAPTURE_TIMEOUT = 30.0
    _POLL_INTERVAL = 0.1

    def __init__(
        self,
        admin_config: AdminLifecycleConfig,
        screenshot_dir: Path,
        crops_dir: Path | None = None,
        *,
        map_log2: int,
        blitter: str = SCREENSHOT_BLITTER,
    ) -> None:
        self.admin_config = admin_config
        self.screenshot_dir = Path(screenshot_dir)
        self.crops_dir = (
            Path(crops_dir) if crops_dir is not None else self.screenshot_dir.parent
        )
        self.map_log2 = map_log2
        self.blitter = blitter
        self._client: AdminClient | None = None
        self._giant_path: Path | None = None
        # Capture state. The sim runs in real time, so there is no automatic
        # invalidation; view_map decides whether to refresh the render.
        self._capture_id = 0
        self._captured_at: float | None = None
        self._mutations_since_capture = 0

    def _ensure_client(self) -> AdminClient:
        if self._client is None:
            from openttd_mcp.admin import AdminClient

            client = AdminClient(self.admin_config)
            # If the TCP connect succeeds but the login handshake times out, the
            # half-open socket must be closed here — it is neither cached nor
            # reachable by close() otherwise, so it would leak on every retry.
            try:
                client.connect(timeout=60)
            except Exception:
                client.close()
                raise
            self._client = client
        return self._client

    def note_mutation(self) -> None:
        """Record a map-changing action taken since the current capture.

        The map runs in real time and captures are model-driven (see
        ``view_map``), so there is no automatic invalidation. This only bumps
        a counter surfaced to the model as ``mutations_since_capture`` so it can
        judge how far the cached render has drifted from live state. It is a
        no-op before the first capture (nothing to be stale against yet).
        """
        if self._capture_id > 0:
            self._mutations_since_capture += 1

    def _refresh(self) -> None:
        """Render a fresh full-map giant screenshot and update capture state."""
        self._render_giant()
        self._capture_id += 1
        self._captured_at = time.time()
        self._mutations_since_capture = 0

    def _render_giant(self) -> None:
        """Render the whole-map giant screenshot and settle ``_giant_path``.

        Always renders. The whole-map render is expensive (rcon + full render +
        async PNG write), so cached renders can be cropped many times rather
        than re-rendered.
        """
        # Invalidate the cached path up front: a failed render must not leave
        # metadata claiming the fixed path, or a late-arriving PNG from a timed-
        # out render would be served under the previous capture's staleness
        # fields. Delete the previous render too, so any file that reappears is
        # unambiguously the new shot — robust across coarse-mtime filesystems
        # where an `mtime >= request_time` comparison could miss or hang.
        self._giant_path = None
        try:
            giant_path = capture_giant_screenshot(
                self._ensure_client(),
                self.screenshot_dir,
                self._GIANT_BASENAME,
                timeout=self._CAPTURE_TIMEOUT,
                poll_interval=self._POLL_INTERVAL,
            )
        except (ConnectionError, OSError):
            # A dropped admin socket (server peer-close, idle reset) must not
            # break view_map for the rest of the episode: discard the stale
            # client and reconnect once. A genuine timeout raises
            # AdminWaitTimeout (not OSError), so a live-but-slow server is left
            # to the settle-poll below rather than being needlessly reconnected.
            self.close()
            giant_path = capture_giant_screenshot(
                self._ensure_client(),
                self.screenshot_dir,
                self._GIANT_BASENAME,
                timeout=self._CAPTURE_TIMEOUT,
                poll_interval=self._POLL_INTERVAL,
            )
        self._giant_path = giant_path

    def crop_for_tile(
        self, tile: int, window: int, *, refresh: bool = DEFAULT_REFRESH, call_id: str
    ) -> dict:
        """Crop the latest capture around a tile and return reference metadata.

        Refreshes by default, or crops the most recent render when requested. On
        first use it always takes one bootstrap capture. Every crop carries the capture's
        wall-clock age and the mutation count since it, so the model can see how
        stale the frame is — the sim advances in real time between tool calls.
        """
        if not in_bounds(tile, self.map_log2):
            side = map_side(self.map_log2)
            raise ValueError(f"tile {tile} is outside map bounds 0..{side * side - 1}")

        from PIL import Image

        if refresh or self._giant_path is None or not self._giant_path.exists():
            self._refresh()
        giant_path = self._giant_path
        assert giant_path is not None
        center_x, center_y = tile_to_giant_px(tile, self.map_log2)
        self.crops_dir.mkdir(parents=True, exist_ok=True)
        crop_path = self.crops_dir / f"{call_id}.png"

        try:
            with Image.open(giant_path) as image:
                bounds = crop_box(center_x, center_y, window, image.width, image.height)
                crop = image.crop(bounds)
                crop.save(crop_path)
        except Exception:
            # A giant PNG that fails to open (e.g. a truncated async write whose
            # size happened to settle for one poll interval) must not stay
            # cached, or every later view_map re-opens the same broken file.
            # Drop it so the next crop takes a fresh capture.
            self._giant_path = None
            raise

        x0, y0, x1, y1 = bounds
        age = time.time() - self._captured_at if self._captured_at is not None else None
        result = {
            "ok": True,
            "action": "view_map",
            "tile": tile,
            "window": window,
            "map_log2": self.map_log2,
            "bounds": {"x0": x0, "y0": y0, "x1": x1, "y1": y1},
            "px": {"w": x1 - x0, "h": y1 - y0},
            "center_px": {"x": center_x, "y": center_y},
            "capture_id": self._capture_id,
            "captured_at_unix": self._captured_at,
            "age_seconds": round(age, 1) if age is not None else None,
            "mutations_since_capture": self._mutations_since_capture,
            "path": str(crop_path),
            "mutates_game": False,
        }
        result.update(tile_frame_for_crop(bounds, self.map_log2))
        return result

    def close(self) -> None:
        """Close the lazy admin client, if it was opened."""
        client = self._client
        self._client = None
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001, S110 -- best-effort close of a dead admin socket
                pass
