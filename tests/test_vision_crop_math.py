from __future__ import annotations

import pytest

from openttd_mcp.vision import (
    crop_box,
    giant_px_to_tile_xy,
    in_bounds,
    map_side,
    tile_frame_for_crop,
    tile_to_giant_px,
    tile_to_xy,
)


def assert_valid_box(
    box: tuple[int, int, int, int], window: int, img_w: int, img_h: int
) -> None:
    x0, y0, x1, y1 = box
    assert 0 <= x0 <= x1 <= img_w
    assert 0 <= y0 <= y1 <= img_h
    assert x1 - x0 == min(window, img_w)
    assert y1 - y0 == min(window, img_h)


def test_tile_to_xy_and_giant_pixels_for_128_map_landmarks() -> None:
    map_log2 = 7
    assert map_side(map_log2) == 128

    cases = [
        (12481, (65, 97), (5056, 2710)),
        (4083, (115, 31), (1344, 2454)),
        (0, (0, 0), (4032, 118)),
    ]

    for tile, xy, px in cases:
        assert tile_to_xy(tile, map_log2) == xy
        assert tile_to_giant_px(tile, map_log2) == px


def test_giant_pixel_inverse_round_trips_every_valid_tile() -> None:
    map_log2 = 7

    for tile in range(map_side(map_log2) ** 2):
        assert giant_px_to_tile_xy(
            *tile_to_giant_px(tile, map_log2), map_log2
        ) == tile_to_xy(tile, map_log2)


def test_in_bounds_edges_for_128_map() -> None:
    map_log2 = 7
    assert in_bounds(-1, map_log2) is False
    assert in_bounds(0, map_log2) is True
    assert in_bounds(16383, map_log2) is True
    assert in_bounds(16384, map_log2) is False


@pytest.mark.parametrize(
    ("center", "window", "image_size", "expected"),
    [
        ((500, 400), 384, (1000, 900), (308, 208, 692, 592)),
        ((10, 20), 384, (1000, 900), (0, 0, 384, 384)),
        ((995, 899), 384, (1000, 900), (616, 516, 1000, 900)),
        ((50, 50), 384, (300, 200), (0, 0, 300, 200)),
        ((120, 600), 384, (300, 900), (0, 408, 300, 792)),
    ],
    ids=("centered", "top-left", "bottom-right", "small-image", "small-width"),
)
def test_crop_box_clamps_within_the_image_and_uses_the_largest_permitted_extent(
    center: tuple[int, int],
    window: int,
    image_size: tuple[int, int],
    expected: tuple[int, int, int, int],
) -> None:
    box = crop_box(*center, window, *image_size)

    assert box == expected
    assert_valid_box(box, window, *image_size)


def test_tile_frame_uses_last_included_pixels_for_odd_and_even_windows() -> None:
    for window in (64, 65):
        bounds = crop_box(500, 400, window, 1000, 900)
        x0, y0, x1, y1 = bounds
        frame = tile_frame_for_crop(bounds, 7)
        top_left_x, top_left_y = giant_px_to_tile_xy(x0, y0, 7)
        bottom_right_x, bottom_right_y = giant_px_to_tile_xy(x1 - 1, y1 - 1, 7)
        center_x, center_y = giant_px_to_tile_xy(x0 + window // 2, y0 + window // 2, 7)

        assert frame["frame_corner_tiles"]["top_left"] == {
            "x": top_left_x,
            "y": top_left_y,
            "in_map": False,
        }
        assert frame["frame_corner_tiles"]["bottom_right"] == {
            "x": bottom_right_x,
            "y": bottom_right_y,
            "in_map": False,
        }
        assert frame["rendered_center_tile"] == {
            "x": center_x,
            "y": center_y,
            "in_map": False,
        }


def test_tile_frame_reports_out_of_map_edge_corners_without_clamping() -> None:
    bounds = crop_box(*tile_to_giant_px(0, 7), 384, 8064, 2200)
    frame = tile_frame_for_crop(bounds, 7)
    corners = frame["frame_corner_tiles"]

    assert bounds == (3840, 0, 4224, 384)
    assert corners["top_left"] == {"x": -0.6875, "y": -6.6875, "in_map": False}
    assert corners["top_right"] == {"x": -6.671875, "y": -0.703125, "in_map": False}
    assert corners["bottom_right"]["in_map"] is True
    assert corners["bottom_left"]["in_map"] is True
    assert frame["visible_diamond_tile_bbox"] == {
        "min_x": -6.671875,
        "min_y": -6.6875,
        "max_x": 11.28125,
        "max_y": 11.265625,
    }
    assert frame["tile_extent"] == {"x": 17.953125, "y": 17.953125}


def test_tile_frame_reports_edge_clamped_rendered_center() -> None:
    requested_center = tile_to_giant_px(0, 7)
    bounds = crop_box(*requested_center, 384, 8064, 2200)
    frame = tile_frame_for_crop(bounds, 7)

    assert requested_center == (4032, 118)
    assert frame["rendered_center_tile"] == {
        "x": 2.3125,
        "y": 2.3125,
        "in_map": True,
        "tile": 258,
    }


def test_rendered_center_tile_id_is_present_only_for_an_in_map_center() -> None:
    in_map = tile_frame_for_crop((3840, 0, 4224, 384), 7)["rendered_center_tile"]
    out_of_map = tile_frame_for_crop((0, 0, 64, 64), 7)["rendered_center_tile"]

    assert in_map["tile"] == int(in_map["y"]) * map_side(7) + int(in_map["x"])
    assert out_of_map["in_map"] is False
    assert "tile" not in out_of_map
