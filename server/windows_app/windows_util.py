"""Windows-Hilfsfunktionen: Icon, Autostart, WebView2."""

from __future__ import annotations

import os
import sys
import winreg
from pathlib import Path
from urllib.parse import urlsplit

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


def configure_webview2_for_http(base_url: str) -> None:
    """
    Edge/WebView2 versucht HTTP oft automatisch auf HTTPS hochzustufen.
    mpdbackend spricht nur HTTP — ohne diese Flags erscheint kurz die
    SSL-Fehlerseite, danach lädt die Seite erst nach manuellem Wegklicken.
    """
    parts = urlsplit(base_url.strip())
    if (parts.scheme or "http").lower() != "http" or not parts.netloc:
        return

    origin = f"http://{parts.netloc}"
    host = (parts.hostname or "").lower()
    origins = [origin]
    port_suffix = f":{parts.port}" if parts.port else ""
    if host == "127.0.0.1":
        origins.append(f"http://localhost{port_suffix}")
    elif host == "localhost":
        origins.append(f"http://127.0.0.1{port_suffix}")

    allowlist = ",".join(dict.fromkeys(origins))
    flags = " ".join(
        [
            "--https-upgrades-enabled=false",
            "--disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable",
            f"--unsafely-treat-insecure-origin-as-secure={allowlist}",
        ]
    )
    existing = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "").strip()
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        f"{existing} {flags}".strip() if existing else flags
    )
