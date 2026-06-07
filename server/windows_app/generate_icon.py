#!/usr/bin/env python3
"""Erzeugt assets/icon.ico für MPD Player (Taskleiste + EXE)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent / "assets"


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = max(2, size // 16)
    draw.ellipse(
        (margin, margin, size - margin - 1, size - margin - 1),
        fill=(108, 140, 255, 255),
    )
    play_w = size // 3
    play_h = int(play_w * 1.15)
    cx = size // 2 - play_w // 6
    cy = size // 2
    draw.polygon(
        [
            (cx - play_w // 2, cy - play_h // 2),
            (cx - play_w // 2, cy + play_h // 2),
            (cx + play_w // 2, cy),
        ],
        fill=(255, 255, 255, 255),
    )
    return img


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    master = draw_icon(256)
    ico_path = ASSETS / "icon.ico"
    master.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    png_path = ASSETS / "icon.png"
    draw_icon(256).save(png_path, format="PNG")
    print(f"Written {ico_path}")
    print(f"Written {png_path}")


if __name__ == "__main__":
    main()
