"""Prepare generated four-frame bamboo sprite sheets for the web game."""

from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path
from statistics import median

from PIL import Image


FRAME_COUNT = 4
FRAME_WIDTH = 600
SHEET_HEIGHT = 726


def remove_connected_black_background(image: Image.Image) -> Image.Image:
    source = image.convert("RGBA")
    width, height = source.size
    pixels = source.load()
    background = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    def enqueue(x: int, y: int) -> None:
        index = y * width + x
        if background[index]:
            return
        red, green, blue, _alpha = pixels[x, y]
        near_black = max(red, green, blue) <= 8
        pale_neutral = (
            min(red, green, blue) >= 220
            and max(red, green, blue) - min(red, green, blue) <= 14
        )
        if not near_black and not pale_neutral:
            return
        background[index] = 1
        queue.append((x, y))

    for x in range(width):
        enqueue(x, 0)
        enqueue(x, height - 1)
    for y in range(height):
        enqueue(0, y)
        enqueue(width - 1, y)

    while queue:
        x, y = queue.popleft()
        if x:
            enqueue(x - 1, y)
        if x + 1 < width:
            enqueue(x + 1, y)
        if y:
            enqueue(x, y - 1)
        if y + 1 < height:
            enqueue(x, y + 1)

    alpha = source.getchannel("A")
    alpha_pixels = alpha.load()
    for y in range(height):
        row = y * width
        for x in range(width):
            if background[row + x]:
                alpha_pixels[x, y] = 0
    source.putalpha(alpha)
    return source


def fit_to_game_sheet(image: Image.Image) -> Image.Image:
    output = Image.new(
        "RGBA",
        (FRAME_COUNT * FRAME_WIDTH, SHEET_HEIGHT),
        (0, 0, 0, 0),
    )
    source_width, source_height = image.size
    for index in range(FRAME_COUNT):
        left = round(index * source_width / FRAME_COUNT)
        right = round((index + 1) * source_width / FRAME_COUNT)
        frame = image.crop((left, 0, right, source_height))
        scale = FRAME_WIDTH / frame.width
        scaled_height = round(frame.height * scale)
        frame = frame.resize(
            (FRAME_WIDTH, scaled_height),
            Image.Resampling.NEAREST,
        )
        if scaled_height > SHEET_HEIGHT:
            crop_top = (scaled_height - SHEET_HEIGHT) // 2
            frame = frame.crop(
                (0, crop_top, FRAME_WIDTH, crop_top + SHEET_HEIGHT)
            )
            target_y = 0
        else:
            target_y = (SHEET_HEIGHT - scaled_height) // 2
        output.alpha_composite(frame, (index * FRAME_WIDTH, target_y))
    return output


def lower_body_anchor(frame: Image.Image) -> tuple[int, int]:
    """Return a stable seat anchor based on the character's lowest pixels."""
    alpha = frame.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        return frame.width // 2, frame.height

    left, top, right, bottom = bbox
    lower_top = top + round((bottom - top) * 0.68)
    xs: list[int] = []
    pixels = alpha.load()
    for y in range(lower_top, bottom):
        for x in range(left, min(right, round(frame.width * 0.72))):
            if pixels[x, y] >= 128:
                xs.append(x)
    anchor_x = round(median(xs)) if xs else (left + right) // 2
    return anchor_x, bottom


def align_frames_to_reference(
    sheet: Image.Image,
    reference: Image.Image,
) -> Image.Image:
    aligned = Image.new("RGBA", sheet.size, (0, 0, 0, 0))
    for index in range(FRAME_COUNT):
        box = (
            index * FRAME_WIDTH,
            0,
            (index + 1) * FRAME_WIDTH,
            SHEET_HEIGHT,
        )
        frame = sheet.crop(box)
        reference_frame = reference.crop(box)
        source_x, source_y = lower_body_anchor(frame)
        target_x, target_y = lower_body_anchor(reference_frame)
        aligned.alpha_composite(
            frame,
            (index * FRAME_WIDTH + target_x - source_x, target_y - source_y),
        )
    return aligned


def copy_frame(sheet: Image.Image, source: int, target: int) -> Image.Image:
    """Copy one one-based animation frame into another frame slot."""
    if not 1 <= source <= FRAME_COUNT or not 1 <= target <= FRAME_COUNT:
        raise ValueError(f"Frame numbers must be between 1 and {FRAME_COUNT}")
    output = sheet.copy()
    source_left = (source - 1) * FRAME_WIDTH
    frame = sheet.crop(
        (source_left, 0, source_left + FRAME_WIDTH, SHEET_HEIGHT)
    )
    output.paste(frame, ((target - 1) * FRAME_WIDTH, 0))
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--align-reference", type=Path)
    parser.add_argument(
        "--prepared-input",
        action="store_true",
        help="Treat input as an already prepared 2400x726 game sheet.",
    )
    parser.add_argument(
        "--copy-frame",
        metavar="SOURCE:TARGET",
        help="Replace a one-based target frame with another frame.",
    )
    args = parser.parse_args()

    if args.prepared_input:
        prepared = Image.open(args.input).convert("RGBA")
        expected_size = (FRAME_COUNT * FRAME_WIDTH, SHEET_HEIGHT)
        if prepared.size != expected_size:
            raise ValueError(
                f"Prepared sheet must be {expected_size}, got {prepared.size}"
            )
    else:
        prepared = fit_to_game_sheet(
            remove_connected_black_background(Image.open(args.input))
        )
    if args.align_reference:
        reference = Image.open(args.align_reference).convert("RGBA")
        prepared = align_frames_to_reference(prepared, reference)
    if args.copy_frame:
        source_text, target_text = args.copy_frame.split(":", 1)
        prepared = copy_frame(prepared, int(source_text), int(target_text))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    prepared.save(args.output, optimize=True)


if __name__ == "__main__":
    main()
