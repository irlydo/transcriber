"""Draw the app icon (a speech bubble with caption lines) and save assets/icon.ico."""

import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icon.ico")


def draw(size: int = 256) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 256
    d.rounded_rectangle((8 * s, 8 * s, 248 * s, 248 * s), radius=56 * s, fill=(124, 92, 255))
    # speech bubble
    d.rounded_rectangle((44 * s, 56 * s, 212 * s, 172 * s), radius=28 * s, fill=(255, 255, 255))
    d.polygon([(76 * s, 166 * s), (76 * s, 206 * s), (116 * s, 168 * s)], fill=(255, 255, 255))
    # caption lines
    for y, x2 in ((92, 180), (126, 148)):
        d.rounded_rectangle((76 * s, (y - 8) * s, x2 * s, (y + 8) * s), radius=8 * s, fill=(124, 92, 255))
    return img


if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    draw().save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Icon saved to", OUT)
