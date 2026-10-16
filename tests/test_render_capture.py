"""Focused tests for reusable OpenTTD screenshot capture."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

import openttd_mcp.vision as vision_module
from openttd_mcp.render_capture import (
    PNG_SIGNATURE,
    REVIEW_MAX_EDGE_PX,
    SCREENSHOT_BLITTER,
    capture_giant_screenshot,
    read_png_dimensions,
    write_review_png,
)
from openttd_mcp.vision import ScreenshotSource


def _png_header(width: int = 17, height: int = 23) -> bytes:
    return PNG_SIGNATURE + struct.pack(">I4sII", 13, b"IHDR", width, height)


class WritingAdmin:
    def __init__(self, screenshot_dir: Path, basename: str) -> None:
        self.path = screenshot_dir / f"{basename}.png"
        self.commands: list[tuple[str, float]] = []
        self.stale_was_removed = False

    def rcon(self, command: str, timeout: float = 30.0) -> list[str]:
        self.commands.append((command, timeout))
        self.stale_was_removed = not self.path.exists()
        self.path.write_bytes(_png_header())
        return []


def test_capture_deletes_stale_file_and_returns_new_size_stable_png(
    tmp_path: Path,
) -> None:
    screenshot_dir = tmp_path / "screenshot"
    screenshot_dir.mkdir()
    stale = screenshot_dir / "overview.png"
    stale.write_bytes(b"old evidence")
    admin = WritingAdmin(screenshot_dir, "overview")

    captured = capture_giant_screenshot(
        admin, screenshot_dir, "overview", timeout=1, poll_interval=0
    )

    assert admin.stale_was_removed is True
    assert admin.commands == [("screenshot giant overview", 1)]
    assert captured == stale
    assert read_png_dimensions(captured) == (17, 23)


def test_capture_timeout_names_missing_stable_file(tmp_path: Path) -> None:
    class SilentAdmin:
        def rcon(self, command: str, timeout: float = 30.0) -> list[str]:
            return []

    with pytest.raises(RuntimeError, match="non-empty, size-stable screenshot"):
        capture_giant_screenshot(
            SilentAdmin(), tmp_path, "missing", timeout=0, poll_interval=0
        )


@pytest.mark.parametrize(
    "payload, message",
    [
        (b"not a png", "not a PNG"),
        (PNG_SIGNATURE + struct.pack(">I4sII", 12, b"IHDR", 1, 1), "IHDR"),
        (_png_header(width=0), "invalid dimensions"),
    ],
)
def test_png_validation_rejects_invalid_signature_header_and_dimensions(
    tmp_path: Path, payload: bytes, message: str
) -> None:
    path = tmp_path / "invalid.png"
    path.write_bytes(payload)

    with pytest.raises(ValueError, match=message):
        read_png_dimensions(path)


def test_review_png_downscales_a_full_width_archival_image(tmp_path: Path) -> None:
    from PIL import Image

    archival = tmp_path / "archival.png"
    review = tmp_path / "review.png"
    Image.new("RGB", (8065, 2), (0, 128, 0)).save(archival)

    width, height, scale = write_review_png(archival, review)

    assert read_png_dimensions(review) == (width, height)
    assert max(width, height) <= REVIEW_MAX_EDGE_PX
    assert 0 < scale < 1
    assert review.stat().st_size > 0


def test_vision_reexports_the_shared_screenshot_blitter() -> None:
    assert vision_module.SCREENSHOT_BLITTER == SCREENSHOT_BLITTER


def test_screenshot_source_reconnects_once_after_extracted_capture_drops_socket(
    tmp_path: Path, monkeypatch
) -> None:
    source = ScreenshotSource(object(), tmp_path / "screenshot", map_log2=7)
    clients = [object(), object()]
    calls: list[object] = []
    closes: list[bool] = []
    output = tmp_path / "screenshot" / "viewmap-giant.png"

    monkeypatch.setattr(source, "_ensure_client", lambda: clients[len(calls)])
    monkeypatch.setattr(source, "close", lambda: closes.append(True))

    def flaky_capture(client, *_args, **_kwargs):
        calls.append(client)
        if len(calls) == 1:
            raise ConnectionError("peer closed")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(_png_header())
        return output

    monkeypatch.setattr(vision_module, "capture_giant_screenshot", flaky_capture)

    source._render_giant()

    assert calls == clients
    assert closes == [True]
    assert source._giant_path == output
