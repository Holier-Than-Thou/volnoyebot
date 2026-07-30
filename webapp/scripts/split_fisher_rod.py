"""Разделить листы рыбака на тело и видимую часть удилища.

Скрипт сохраняет исходные листы без изменений. Маска проходит только по
открытой части удилища: участок, закрытый кистями, остаётся в слое рыбака.
При наложении двух новых слоёв результат должен побитово совпадать с исходником.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "public" / "assets" / "fishing"
FRAME_WIDTH = 600

# Координаты заданы внутри каждого кадра: от кончика удилища до точки рядом
# с кистью. Ширина включает тёмную пиксельную обводку.
SHEETS = {
    "idle": (
        ((560, 201), (384, 397), 22),
        ((558, 201), (384, 397), 22),
        ((560, 201), (384, 398), 22),
        ((560, 201), (384, 397), 22),
    ),
    "cast": (
        ((58, 159), (177, 310), 20),
        ((5, 145), (128, 207), 20),
        ((570, 181), (402, 374), 20),
        ((560, 165), (260, 352), 20),
    ),
}


def split_sheet(state: str) -> None:
    source_path = ASSET_DIR / f"fisher-{state}.png"
    source = Image.open(source_path).convert("RGBA")
    mask = Image.new("L", source.size, 0)
    draw = ImageDraw.Draw(mask)

    for frame_index, (tip, hand, width) in enumerate(SHEETS[state]):
        offset = frame_index * FRAME_WIDTH
        draw.line(
            (
                (tip[0] + offset, tip[1]),
                (hand[0] + offset, hand[1]),
            ),
            fill=255,
            width=width,
        )

    source_alpha = source.getchannel("A")
    rod = source.copy()
    rod.putalpha(ImageChops.multiply(source_alpha, mask))
    body = source.copy()
    body.putalpha(
        ImageChops.multiply(source_alpha, ImageOps.invert(mask))
    )

    body_path = ASSET_DIR / f"fisher-{state}-body.png"
    rod_path = ASSET_DIR / f"fisher-{state}-rod.png"
    body.save(body_path, optimize=True)
    rod.save(rod_path, optimize=True)

    composite = Image.alpha_composite(body, rod)
    difference = ImageChops.difference(source, composite)
    if difference.getbbox() is not None:
        raise RuntimeError(f"Слои {state} не совпадают с исходным листом")


def main() -> None:
    for state in SHEETS:
        split_sheet(state)
    print("Слои рыбака и удочки созданы; композиция совпадает с исходниками.")


if __name__ == "__main__":
    main()
