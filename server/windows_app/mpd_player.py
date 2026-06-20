#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Windows-Desktop-Client für den mpdbackend-Web-Player.

Lädt die bestehende Web-UI (HTML/CSS/JS) aus dem mpdbackend-Server in
einem nativen Fenster (Microsoft Edge WebView2).

Beispiel:
  python mpd_player.py
  python mpd_player.py --url http://192.168.1.10:4533 --password geheim
  python mpd_player.py --settings
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

try:
    import webview
except ImportError:
    print("Abhängigkeit fehlt. Bitte installieren:", file=sys.stderr)
    print("  pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

try:
    import pystray
except ImportError:
    pystray = None  # type: ignore[assignment]

from native_mpv import NativeMpvPlayer
from webview_api import WebPlayerApi
from windows_util import (
    app_install_dir,
    autostart_enabled,
    configure_webview2_for_http,
    is_private_host,
    load_tray_image,
    set_autostart,
)

APP_NAME = "mpdbackend-player"
DEFAULT_URL = "http://127.0.0.1:4533"
WINDOW_TITLE = "MPD Player"
WINDOW_WIDTH = 480
WINDOW_HEIGHT = 860
MIN_WIDTH = 360
MIN_HEIGHT = 640

DEFAULT_CONFIG: dict[str, Any] = {
    "url": DEFAULT_URL,
    "password": "",
    "minimize_to_tray": True,
    "autostart": False,
}


class PlayerApp:
    """Fenster, Taskleiste und Konfiguration."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.window: webview.Window | None = None
        self.tray_icon: pystray.Icon | None = None
        self.quitting = False
        self._mpv = NativeMpvPlayer()

    def native_start_stream(self, url: str) -> None:
        self._mpv.start(url)

    def native_stop_stream(self) -> None:
        self._mpv.stop()

    def native_stream_playing(self) -> bool:
        return self._mpv.playing

    def config_bool(self, key: str) -> bool:
        value = self.config.get(key, DEFAULT_CONFIG[key])
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def show_window(self) -> None:
        if self.window is None:
            return
        self.config = load_config()
        self.reload_player()
        self.window.show()
        try:
            self.window.restore()
        except Exception:
            pass

    def hide_window(self) -> None:
        if self.window is not None:
            self.window.hide()

    def reload_player(self) -> None:
        if self.window is None:
            return
        self._mpv.stop()
        self.window.load_url(
            player_url(str(self.config["url"]), str(self.config.get("password", "")))
        )

    def apply_settings(self, edited: dict[str, Any]) -> None:
        self.config.update(edited)
        save_config(self.config)
        if self.config_bool("autostart"):
            set_autostart(True)
        else:
            set_autostart(False)
        self.reload_player()

    def open_settings(self) -> None:
        # Tkinter muss auf dem GUI-Hauptthread laufen. Tray-Menü-Callbacks laufen
        # dagegen in einem Hintergrundthread, während webview.start() den Hauptthread
        # blockiert — Eingabefelder reagieren dann oft nicht auf Klicks.
        if self.window is not None:
            proc = subprocess.Popen(settings_command())
            threading.Thread(
                target=self._after_settings_process,
                args=(proc,),
                daemon=True,
            ).start()
            return
        edited = edit_settings_dialog(self.config)
        if edited is None:
            return
        self.apply_settings(edited)

    def _after_settings_process(self, proc: subprocess.Popen[Any]) -> None:
        proc.wait()
        self.config = load_config()
        self.reload_player()

    def quit_app(self) -> None:
        self.quitting = True
        self._mpv.stop()
        if self.tray_icon is not None:
            self.tray_icon.stop()
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass

    def on_window_closing(self) -> bool:
        if self.quitting:
            return True
        if self.config_bool("minimize_to_tray") and pystray is not None:
            self.hide_window()
            return False
        return True

    def toggle_autostart(self) -> None:
        enabled = not autostart_enabled()
        set_autostart(enabled)
        self.config["autostart"] = enabled
        save_config(self.config)

    def build_tray_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem("Anzeigen", lambda _icon, _item: self.show_window(), default=True),
            pystray.MenuItem("Einstellungen", lambda _icon, _item: self.open_settings()),
            pystray.MenuItem(
                "Autostart",
                lambda _icon, _item: self.toggle_autostart(),
                checked=lambda _item: autostart_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Beenden", lambda _icon, _item: self.quit_app()),
        )

    def start_tray(self) -> None:
        if pystray is None:
            return
        self.tray_icon = pystray.Icon(
            APP_NAME,
            load_tray_image(),
            WINDOW_TITLE,
            self.build_tray_menu(),
        )
        thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        thread.start()


def config_path() -> Path:
    return app_install_dir() / "config.json"


def legacy_config_path() -> Path:
    base = os.environ.get("APPDATA", "").strip() or str(Path.home())
    return Path(base) / APP_NAME / "config.json"


def resolve_config_path() -> Path:
    path = config_path()
    if path.is_file():
        return path
    legacy = legacy_config_path()
    if legacy.is_file():
        return legacy
    return path


def load_config() -> dict[str, Any]:
    path = resolve_config_path()
    config = dict(DEFAULT_CONFIG)
    if not path.is_file():
        return config
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return config
    if not isinstance(data, dict):
        return config
    if str(data.get("url") or "").strip():
        config["url"] = normalize_base_url(str(data["url"]))
    config["password"] = str(data.get("password") or "")
    if "minimize_to_tray" in data:
        config["minimize_to_tray"] = bool(data["minimize_to_tray"]) if isinstance(
            data["minimize_to_tray"], bool
        ) else str(data["minimize_to_tray"]).lower() in ("1", "true", "yes", "on")
    if "autostart" in data:
        config["autostart"] = bool(data["autostart"]) if isinstance(
            data["autostart"], bool
        ) else str(data["autostart"]).lower() in ("1", "true", "yes", "on")
    return config


def save_config(config: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": normalize_base_url(str(config.get("url") or DEFAULT_URL)),
        "password": str(config.get("password") or ""),
        "minimize_to_tray": bool(config.get("minimize_to_tray", True)),
        "autostart": bool(config.get("autostart", False)),
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def normalize_base_url(url: str) -> str:
    """Basis-URL für mpdbackend (Standard: plain HTTP)."""
    raw = url.strip().rstrip("/")
    if not raw:
        return DEFAULT_URL
    if "://" not in raw:
        raw = f"http://{raw}"
    parts = urlsplit(raw)
    if not parts.netloc:
        return DEFAULT_URL
    scheme = (parts.scheme or "http").lower()
    host = (parts.hostname or "").lower()
    if scheme == "https" and is_private_host(host):
        scheme = "http"
    normalized = urlunsplit((scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
    return normalized.rstrip("/") or DEFAULT_URL


def player_url(base_url: str, password: str) -> str:
    base = normalize_base_url(base_url)
    if not password.strip():
        return f"{base}/"
    return f"{base}/?password={quote(password.strip(), safe='')}"


def settings_command() -> list[str]:
    """Kommandozeile für einen separaten Einstellungs-Dialog."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--settings"]
    script = Path(__file__).resolve()
    return [sys.executable, str(script), "--settings"]


def edit_settings_dialog(config: dict[str, Any]) -> dict[str, Any] | None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title(f"{WINDOW_TITLE} – Einstellungen")
    root.resizable(False, False)
    root.geometry("+200+200")

    frame = ttk.Frame(root, padding=16)
    frame.grid(row=0, column=0, sticky="nsew")

    ttk.Label(frame, text="mpdbackend URL:").grid(row=0, column=0, sticky="w")
    url_var = tk.StringVar(value=str(config.get("url", DEFAULT_URL)))
    url_entry = ttk.Entry(frame, textvariable=url_var, width=42)
    url_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 4))
    ttk.Label(
        frame,
        text="Standard: http:// (nicht https://), z. B. http://192.168.1.10:4533",
        foreground="#666666",
        wraplength=360,
    ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 12))

    ttk.Label(frame, text="Web-Passwort (optional):").grid(row=3, column=0, sticky="w")
    pwd_var = tk.StringVar(value=str(config.get("password", "")))
    ttk.Entry(frame, textvariable=pwd_var, width=42, show="*").grid(
        row=4, column=0, columnspan=2, sticky="ew", pady=(4, 12)
    )

    tray_var = tk.BooleanVar(value=bool(config.get("minimize_to_tray", True)))
    ttk.Checkbutton(
        frame,
        text="Beim Schließen in Taskleiste minimieren",
        variable=tray_var,
    ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 4))

    autostart_var = tk.BooleanVar(value=bool(config.get("autostart", False)))
    ttk.Checkbutton(
        frame,
        text="Mit Windows starten",
        variable=autostart_var,
    ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(0, 12))

    result: dict[str, Any | None] = {"value": None}

    def on_ok() -> None:
        url = url_var.get().strip()
        if not url:
            messagebox.showerror("Fehler", "Bitte eine URL angeben.", parent=root)
            return
        normalized = normalize_base_url(url)
        if not urlsplit(normalized).netloc:
            messagebox.showerror("Fehler", "Ungültige URL.", parent=root)
            return
        if url.strip().lower().startswith("https://") and normalized.startswith("http://"):
            messagebox.showinfo(
                "Hinweis",
                "Für lokale/LAN-Adressen wird http:// verwendet (mpdbackend ohne HTTPS).",
                parent=root,
            )
        result["value"] = {
            "url": normalized,
            "password": pwd_var.get(),
            "minimize_to_tray": tray_var.get(),
            "autostart": autostart_var.get(),
        }
        root.destroy()

    def on_cancel() -> None:
        root.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=7, column=0, columnspan=2, sticky="e")
    ttk.Button(buttons, text="Abbrechen", command=on_cancel).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(buttons, text="Speichern", command=on_ok).grid(row=0, column=1)

    def focus_dialog() -> None:
        root.update_idletasks()
        root.lift()
        root.attributes("-topmost", True)
        root.after(50, lambda: root.attributes("-topmost", False))
        root.focus_force()
        url_entry.focus_set()
        url_entry.icursor(tk.END)

    root.protocol("WM_DELETE_WINDOW", on_cancel)
    root.after(50, focus_dialog)
    root.mainloop()

    value = result["value"]
    return value if isinstance(value, dict) else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="mpdbackend Web-Player für Windows")
    parser.add_argument("--url", help=f"mpdbackend Basis-URL (Standard: {DEFAULT_URL})")
    parser.add_argument("--password", help="Web-Passwort für MPD-Steuerung")
    parser.add_argument("--settings", action="store_true", help="Einstellungen bearbeiten")
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Taskleiste deaktivieren (Fenster schließen beendet die App)",
    )
    return parser.parse_args()


def maybe_fix_saved_url(config: dict[str, Any]) -> None:
    """Speichert korrigierte http://-URL, falls in config.json noch https:// stand."""
    path = resolve_config_path()
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    raw = str(data.get("url") or "").strip().rstrip("/")
    fixed = normalize_base_url(raw) if raw else normalize_base_url(str(config.get("url", DEFAULT_URL)))
    if raw and fixed != raw:
        save_config(config)


def main() -> int:
    args = parse_args()
    config = load_config()

    if args.url:
        config["url"] = normalize_base_url(args.url)
    if args.password is not None:
        config["password"] = args.password
    if args.no_tray:
        config["minimize_to_tray"] = False

    if args.settings:
        edited = edit_settings_dialog(config)
        if edited is None:
            return 1
        PlayerApp(config).apply_settings(edited)
        print(f"Gespeichert: {config_path()}")
        return 0

    if config.get("autostart") and not autostart_enabled():
        set_autostart(True)

    maybe_fix_saved_url(config)
    config["url"] = normalize_base_url(str(config["url"]))

    app = PlayerApp(config)
    start_url = player_url(str(config["url"]), str(config.get("password", "")))

    configure_webview2_for_http(str(config["url"]))
    # Selbstsignierte Zertifikate (Reverse-Proxy) tolerieren; muss vor create_window gesetzt werden.
    webview.settings["IGNORE_SSL_ERRORS"] = True

    web_api = WebPlayerApi(app)
    app.window = webview.create_window(
        WINDOW_TITLE,
        start_url,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=(MIN_WIDTH, MIN_HEIGHT),
        text_select=True,
        js_api=web_api,
    )
    app.window.events.closing += app.on_window_closing

    if pystray is not None and app.config_bool("minimize_to_tray"):
        app.start_tray()

    webview.start(debug=False)
    app.quitting = True
    app._mpv.stop()
    if app.tray_icon is not None:
        try:
            app.tray_icon.stop()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
