"""Разделить листы рыбака на тело и видимую часть удилища.

Скрипт сохраняет исходные листы без изменений. Маска проходит только по
открытой части удилища: участок, закрытый кистями, остаётся в слое рыбака.
При наложении двух новых слоёв результат должен побитово совпадать с исходником.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps


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
        ((560, 165), (389, 357), 20),
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
    bamboo_path = ASSET_DIR / f"fisher-{state}-rod-bamboo.png"
    body.save(body_path, optimize=True)
    rod.save(rod_path, optimize=True)
    make_bamboo_rod(rod, state).save(bamboo_path, optimize=True)

    composite = Image.alpha_composite(body, rod)
    difference = ImageChops.difference(source, composite)
    if difference.getbbox() is not None:
        raise RuntimeError(f"Слои {state} не совпадают с исходным листом")


def make_bamboo_rod(rod: Image.Image, state: str) -> Image.Image:
    """Перенести генеративный дизайн бамбука на исходную траекторию."""
    palette = (
        (36, (48, 48, 10)),
        (64, (74, 72, 12)),
        (92, (108, 103, 14)),
        (122, (148, 136, 20)),
        (154, (190, 169, 31)),
        (190, (230, 202, 55)),
        (256, (255, 231, 108)),
    )
    bamboo = Image.new("RGBA", rod.size)
    source_pixels = rod.load()
    bamboo_pixels = bamboo.load()
    source_alpha = rod.getchannel("A")
    # Бамбуковый стебель немного толще деревянного удилища, но центр и
    # точки крепления остаются на исходной траектории.
    bamboo_alpha = source_alpha.filter(ImageFilter.MaxFilter(3))
    bamboo_alpha_pixels = bamboo_alpha.load()

    for y in range(rod.height):
        for x in range(rod.width):
            alpha = bamboo_alpha_pixels[x, y]
            if not alpha:
                continue
            red, green, blue, source_pixel_alpha = source_pixels[x, y]
            if source_pixel_alpha:
                luminance = (
                    red * 299 + green * 587 + blue * 114
                ) // 1000
                color = next(
                    color for limit, color in palette if luminance < limit
                )
            else:
                color = (45, 45, 9)
            bamboo_pixels[x, y] = (*color, alpha)

    node_mask = Image.new("L", rod.size)
    node_draw = ImageDraw.Draw(node_mask)
    for frame_index, (tip, hand, _width) in enumerate(SHEETS[state]):
        dx = hand[0] - tip[0]
        dy = hand[1] - tip[1]
        length = max(1.0, (dx * dx + dy * dy) ** 0.5)
        perpendicular_x = -dy / length
        perpendicular_y = dx / length
        offset = frame_index * FRAME_WIDTH
        for position in (0.24, 0.48, 0.72):
            center_x = tip[0] + dx * position + offset
            center_y = tip[1] + dy * position
            radius = 10
            node_draw.line(
                (
                    (
                        center_x - perpendicular_x * radius,
                        center_y - perpendicular_y * radius,
                    ),
                    (
                        center_x + perpendicular_x * radius,
                        center_y + perpendicular_y * radius,
                    ),
                ),
                fill=255,
                width=5,
            )

    # Коленца слегка выступают за основной стебель, как в сгенерированном
    # дизайн-референсе, но не меняют его ось.
    bamboo_alpha = ImageChops.lighter(
        bamboo.getchannel("A"),
        node_mask,
    )
    bamboo.putalpha(bamboo_alpha)
    nodes = ImageChops.multiply(node_mask, bamboo_alpha)
    node_pixels = nodes.load()
    for y in range(bamboo.height):
        for x in range(bamboo.width):
            if node_pixels[x, y]:
                alpha = bamboo_pixels[x, y][3]
                bamboo_pixels[x, y] = (67, 77, 14, alpha)
    return bamboo


def main() -> None:
    for state in SHEETS:
        split_sheet(state)
    print("Слои рыбака и удочки созданы; композиция совпадает с исходниками.")


if __name__ == "__main__":
    main()
