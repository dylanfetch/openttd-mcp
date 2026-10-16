"""Reusable OpenTTD giant-screenshot capture and PNG validation."""

from __future__ import annotations

import struct
import time
from pathlib import Path
from typing import Any, Protocol

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SCREENSHOT_BLITTER = "8bpp-optimized"
DEFAULT_CAPTURE_TIMEOUT_SEC = 30.0
REVIEW_MAX_EDGE_PX = 2048
VISION_EXTRA_INSTALL_COMMAND = "uv sync --extra vision"


class RenderDependencyError(RuntimeError):
    """Raised when replay rendering lacks its optional image dependency."""


def _load_pillow_image() -> Any:
    """Import Pillow at the point where an image operation is requested."""
    from PIL import Image

    return Image


def require_pillow_for_rendering() -> None:
    """Fail before a render run when the reviewer-image dependency is absent."""
    try:
        _load_pillow_image()
    except ImportError as exc:
        raise RenderDependencyError(
            "rendered evidence requires the optional vision extra; install it with "
            f"`{VISION_EXTRA_INSTALL_COMMAND}`"
        ) from exc


class RconClient(Protocol):
    """The administrative command surface needed for screenshot capture."""

    def rcon(self, command: str, timeout: float = 30.0) -> list[str]: ...


def capture_giant_screenshot(
    client: RconClient,
    screenshot_dir: Path,
    basename: str,
    *,
    timeout: float = DEFAULT_CAPTURE_TIMEOUT_SEC,
    poll_interval: float = 0.1,
) -> Path:
    """Request a fresh giant screenshot and return its settled PNG path.

    The fixed target is deleted before the command, so a file accepted after
    the request cannot be stale evidence from an earlier capture.
    """
    screenshot_dir = Path(screenshot_dir)
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    path = screenshot_dir / f"{basename}.png"
    path.unlink(missing_ok=True)

    command = f"screenshot giant {basename}"
    client.rcon(command, timeout=timeout)

    deadline = time.monotonic() + timeout
    last_size = -1
    while time.monotonic() < deadline:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            size = 0
        if size > 0 and size == last_size:
            return path
        last_size = size
        time.sleep(poll_interval)

    raise RuntimeError(
        f"timed out waiting for screenshot file {path} to appear and settle after "
        f"rcon {command!r}; missing signal: a non-empty, size-stable screenshot file"
    )


def read_png_dimensions(path: Path) -> tuple[int, int]:
    """Validate a PNG signature and IHDR chunk, returning width and height."""
    with Path(path).open("rb") as image:
        header = image.read(24)
    if len(header) < 24 or header[:8] != PNG_SIGNATURE:
        raise ValueError(f"captured screenshot is not a PNG image: {path}")
    ihdr_length = struct.unpack(">I", header[8:12])[0]
    if header[12:16] != b"IHDR" or ihdr_length != 13:
        raise ValueError(f"captured PNG has no valid IHDR chunk: {path}")
    width, height = struct.unpack(">II", header[16:24])
    if width <= 0 or height <= 0:
        raise ValueError(
            f"captured PNG has invalid dimensions {width}x{height}: {path}"
        )
    return width, height


def write_review_png(
    archival_path: Path,
    review_path: Path,
    *,
    max_edge_px: int = REVIEW_MAX_EDGE_PX,
) -> tuple[int, int, float]:
    """Write a reviewer-sized PNG derivative and return its dimensions and scale.

    Pillow stays a lazy dependency so non-render verifier invocations retain the
    base package's dependency-free import path.
    """
    if max_edge_px <= 0:
        raise ValueError(f"max_edge_px must be positive, got {max_edge_px}")
    Image = _load_pillow_image()

    archival_path = Path(archival_path)
    review_path = Path(review_path)
    with Image.open(archival_path) as source:
        source.load()
        source_width, source_height = source.size
        scale = min(1.0, max_edge_px / max(source_width, source_height))
        review_width = max(1, round(source_width * scale))
        review_height = max(1, round(source_height * scale))
        if (review_width, review_height) == source.size:
            review = source.copy()
        else:
            review = source.resize(
                (review_width, review_height), Image.Resampling.LANCZOS
            )
        try:
            review_path.parent.mkdir(parents=True, exist_ok=True)
            review.save(review_path, format="PNG")
        finally:
            review.close()
    return review_width, review_height, review_width / source_width
