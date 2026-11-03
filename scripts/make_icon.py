from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ICONSET = ASSETS / "Dafeiyu.iconset"

BG = (10, 18, 32, 255)
BODY = (255, 179, 71, 255)
BODY_LIGHT = (255, 214, 130, 255)
FIN = (255, 140, 66, 255)
DARK = (20, 24, 35, 255)
BLUE = (77, 171, 247, 255)


def draw_icon(size: int) -> Image.Image:
    scale = size / 1024
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = int(220 * scale)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=BG)

    # bubbles
    for cx, cy, r in ((790, 260, 34), (850, 190, 18), (748, 190, 12)):
        draw.ellipse(((cx - r) * scale, (cy - r) * scale, (cx + r) * scale, (cy + r) * scale), fill=(*BLUE[:3], 180))

    # tail
    draw.polygon(
        [
            (720 * scale, 500 * scale),
            (900 * scale, 380 * scale),
            (900 * scale, 620 * scale),
        ],
        fill=FIN,
    )
    # body
    draw.ellipse((200 * scale, 320 * scale, 780 * scale, 700 * scale), fill=BODY)
    # belly
    draw.ellipse((270 * scale, 470 * scale, 700 * scale, 690 * scale), fill=BODY_LIGHT)
    # top fin
    draw.polygon(
        [
            (470 * scale, 335 * scale),
            (560 * scale, 250 * scale),
            (620 * scale, 350 * scale),
        ],
        fill=FIN,
    )
    # side fin
    draw.ellipse((470 * scale, 500 * scale, 610 * scale, 610 * scale), fill=FIN)
    # eye
    draw.ellipse((310 * scale, 430 * scale, 420 * scale, 540 * scale), fill=(255, 255, 255, 255))
    draw.ellipse((345 * scale, 465 * scale, 400 * scale, 520 * scale), fill=DARK)
    draw.ellipse((365 * scale, 478 * scale, 382 * scale, 495 * scale), fill=(255, 255, 255, 255))
    # mouth
    draw.arc((270 * scale, 520 * scale, 350 * scale, 590 * scale), start=20, end=110, fill=DARK, width=max(2, int(10 * scale)))
    # cheek
    draw.ellipse((260 * scale, 560 * scale, 320 * scale, 600 * scale), fill=(255, 130, 130, 120))
    return image


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    ICONSET.mkdir(exist_ok=True)
    sizes = [16, 32, 64, 128, 256, 512, 1024]
    for size in sizes:
        image = draw_icon(size)
        image.save(ICONSET / f"icon_{size}x{size}.png")
        if size <= 512:
            image.save(ICONSET / f"icon_{size}x{size}@2x.png")
    base = draw_icon(1024)
    base.save(ASSETS / "icon.png")
    print("PNG icons written")


if __name__ == "__main__":
    main()
