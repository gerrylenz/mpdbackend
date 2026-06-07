"""Windows-Hilfsfunktionen: Icon, Autostart."""

from __future__ import annotations

import os
import sys
import winreg
from pathlib import Path

from PIL import Image, ImageDraw

APP_REG_NAME = "MPDPlayer"
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def app_root() -> Path:
    """Verzeichnis mit assets/ (PyInstaller: _MEIPASS)."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def icon_path() -> Path:
    return app_root() / "assets" / "icon.ico"


def load_tray_image() -> Image.Image:
    path = icon_path()
    if path.is_file():
        return Image.open(path).convert("RGBA")
    return _draw_fallback_icon(64)


def _draw_fallback_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = max(2, size // 16)
    draw.ellipse(
        (margin, margin, size - margin - 1, size - margin - 1),
        fill=(108, 140, 255, 255),
    )
    return img


def autostart_command() -> str:
    """Kommandozeile für den Windows-Autostart-Eintrag."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    script = (app_root() / "mpd_player.py").resolve()
    executable = Path(sys.executable).resolve()
    return f'"{executable}" "{script}"'


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_READ
        ) as key:
            winreg.QueryValueEx(key, APP_REG_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(key, APP_REG_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, APP_REG_NAME)
            except OSError:
                pass
