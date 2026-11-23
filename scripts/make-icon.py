"""Generate the Tiktuv application icon.

Produces tiktuv.ico from the generated tiktuv-icon.png artwork for the EXE
and Desktop shortcut. Falls back to the original Hebrew tav artwork if the
PNG is unavailable.

Run from the repository root:

    .venv\\Scripts\\python.exe scripts/make-icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - guidance, not logic
    sys.exit("Pillow is required: .venv\\Scripts\\python.exe -m pip install pillow")

REPO_ROOT = Path(__file__).resolve().parent.parent
ICON_DIR = REPO_ROOT / "apps" / "desktop-gpui" / "assets"

#: Rendered large, then downsampled — far cleaner edges than drawing small.
CANVAS = 1024
BACKGROUND = (29, 102, 240, 255)   # brand-600
BACKGROUND_DEEP = (24, 68, 178, 255)  # brand-800, for the subtle vertical fade
GLYPH = (255, 255, 255, 255)
LETTER = "ת"

#: Windows uses several of these; the shell picks the closest match.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\davidbd.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\tahomabd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    raise SystemExit(
        "no Hebrew-capable TrueType font found; install one or edit _FONT_CANDIDATES"
    )


def render() -> Image.Image:
    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Vertical fade, drawn as a gradient then masked to a rounded square.
    gradient = Image.new("RGBA", (1, CANVAS))
    for y in range(CANVAS):
        t = y / (CANVAS - 1)
        gradient.putpixel(
            (0, y),
            tuple(
                round(BACKGROUND[i] * (1 - t) + BACKGROUND_DEEP[i] * t) for i in range(4)
            ),
        )
    gradient = gradient.resize((CANVAS, CANVAS))

    mask = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, CANVAS - 1, CANVAS - 1), radius=int(CANVAS * 0.22), fill=255
    )
    image.paste(gradient, (0, 0), mask)

    # The letter, optically centred rather than bounding-box centred.
    font = _load_font(int(CANVAS * 0.62))
    left, top, right, bottom = draw.textbbox((0, 0), LETTER, font=font)
    x = (CANVAS - (right - left)) / 2 - left
    y = (CANVAS - (bottom - top)) / 2 - top
    draw.text((x, y), LETTER, font=font, fill=GLYPH)

    return image


def main() -> int:
    source = ICON_DIR / "tiktuv-icon.png"
    if source.is_file():
        with Image.open(source) as artwork:
            image = artwork.convert("RGBA")
    else:
        image = render()

    ICON_DIR.mkdir(parents=True, exist_ok=True)
    ico_path = ICON_DIR / "tiktuv.ico"
    image.save(ico_path, format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    print(f"wrote {ico_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
