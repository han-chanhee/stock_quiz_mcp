"""Small PNG chart renderer for quiz image hints."""

from __future__ import annotations

import struct
import zlib
from binascii import crc32

from contracts.schemas import StockSnapshot
from services.quiz_bank import chart_points_for_snapshot

Color = tuple[int, int, int]


def chart_png(snapshot: StockSnapshot) -> bytes:
    """Render an anonymous mini chart as PNG bytes."""
    width = 832
    height = 400
    margin_x = 48
    margin_y = 44
    pixels = bytearray([255, 255, 255] * width * height)

    grid = (226, 232, 240)
    text = (100, 116, 139)
    color = (22, 163, 74) if snapshot.change_pct >= 0 else (220, 38, 38)
    fill = (220, 252, 231) if snapshot.change_pct >= 0 else (254, 226, 226)

    for row in range(5):
        y = margin_y + row * (height - margin_y * 2) // 4
        _draw_line(pixels, width, height, margin_x, y, width - margin_x, y, grid, 1)

    points = chart_points_for_snapshot(snapshot)
    coords = [
        (
            margin_x + index * (width - margin_x * 2) // (len(points) - 1),
            height - margin_y - int(point * (height - margin_y * 2)),
        )
        for index, point in enumerate(points)
    ]

    baseline = height - margin_y
    for index in range(len(coords) - 1):
        x1, y1 = coords[index]
        x2, y2 = coords[index + 1]
        _fill_under_segment(pixels, width, height, x1, y1, x2, y2, baseline, fill)
    for index in range(len(coords) - 1):
        x1, y1 = coords[index]
        x2, y2 = coords[index + 1]
        _draw_line(pixels, width, height, x1, y1, x2, y2, color, 5)

    _draw_circle(pixels, width, height, coords[-1][0], coords[-1][1], 9, color)
    _draw_label_ticks(pixels, width, height, margin_x, baseline, width - margin_x, text)
    return _encode_png(width, height, pixels)


def _fill_under_segment(
    pixels: bytearray,
    width: int,
    height: int,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    baseline: int,
    color: Color,
) -> None:
    if x2 < x1:
        x1, y1, x2, y2 = x2, y2, x1, y1
    span = max(1, x2 - x1)
    for x in range(x1, x2 + 1):
        t = (x - x1) / span
        y = int(y1 + (y2 - y1) * t)
        for yy in range(max(0, y), min(height, baseline + 1)):
            _blend_pixel(pixels, width, x, yy, color, 0.55)


def _draw_label_ticks(
    pixels: bytearray,
    width: int,
    height: int,
    start_x: int,
    y: int,
    end_x: int,
    color: Color,
) -> None:
    for index in range(5):
        x = start_x + index * (end_x - start_x) // 4
        _draw_line(pixels, width, height, x, y - 8, x, y + 8, color, 1)


def _draw_line(
    pixels: bytearray,
    width: int,
    height: int,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    color: Color,
    thickness: int,
) -> None:
    dx = abs(x2 - x1)
    dy = -abs(y2 - y1)
    sx = 1 if x1 < x2 else -1
    sy = 1 if y1 < y2 else -1
    err = dx + dy
    radius = max(0, thickness // 2)
    while True:
        _draw_circle(pixels, width, height, x1, y1, radius, color)
        if x1 == x2 and y1 == y2:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x1 += sx
        if e2 <= dx:
            err += dx
            y1 += sy


def _draw_circle(
    pixels: bytearray,
    width: int,
    height: int,
    cx: int,
    cy: int,
    radius: int,
    color: Color,
) -> None:
    radius = max(1, radius)
    rr = radius * radius
    for y in range(cy - radius, cy + radius + 1):
        for x in range(cx - radius, cx + radius + 1):
            if (x - cx) * (x - cx) + (y - cy) * (y - cy) <= rr:
                _set_pixel(pixels, width, height, x, y, color)


def _blend_pixel(
    pixels: bytearray,
    width: int,
    x: int,
    y: int,
    color: Color,
    alpha: float,
) -> None:
    offset = (y * width + x) * 3
    inv = 1 - alpha
    pixels[offset] = int(pixels[offset] * inv + color[0] * alpha)
    pixels[offset + 1] = int(pixels[offset + 1] * inv + color[1] * alpha)
    pixels[offset + 2] = int(pixels[offset + 2] * inv + color[2] * alpha)


def _set_pixel(
    pixels: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    color: Color,
) -> None:
    if not (0 <= x < width and 0 <= y < height):
        return
    offset = (y * width + x) * 3
    pixels[offset : offset + 3] = bytes(color)


def _encode_png(width: int, height: int, pixels: bytearray) -> bytes:
    rows = bytearray()
    stride = width * 3
    for y in range(height):
        rows.append(0)
        start = y * stride
        rows.extend(pixels[start : start + stride])
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(rows), level=6))
        + _chunk(b"IEND", b"")
    )


def _chunk(kind: bytes, data: bytes) -> bytes:
    checksum = crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)
