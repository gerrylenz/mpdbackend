"""Windows-Hilfsfunktionen: Icon, Autostart, WebView2."""

from __future__ import annotations

import os
import sys
import winreg
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from PIL import Image, ImageDraw

APP_REG_NAME = "MPDPlayer"
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def install_dir() -> Path:
    """Verzeichnis der EXE bzw. mpd_player.py (config.json liegt hier)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def app_root() -> Path:
    """Verzeichnis mit assets/ (PyInstaller: _MEIPASS)."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def app_install_dir() -> Path:
    """Persistentes App-Verzeichnis (EXE bzw. Skript), nicht PyInstaller-Temp."""
    if getattr(sys, "frozen", False):
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
    Edge/WebView2 stuft HTTP oft auf HTTPS hoch; mpdbackend spricht nur HTTP.

    pywebview setzt AdditionalBrowserArguments selbst — WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS
    allein reicht nicht. Deshalb werden die Flags per Patch an pywebview übergeben.
    """
    extra = webview2_http_browser_args(base_url)
    if not extra:
        return
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = extra
    patch_pywebview_for_http(extra)


def is_private_host(host: str) -> bool:
    host = host.lower().strip("[]")
    if host in ("127.0.0.1", "localhost", "::1"):
        return True
    if host.endswith(".local"):
        return True
    parts = host.split(".")
    if len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts):
        first, second = int(parts[0]), int(parts[1])
        if first == 10:
            return True
        if first == 192 and second == 168:
            return True
        if first == 172 and 16 <= second <= 31:
            return True
    return False


def http_url_if_private_https(url: str) -> str | None:
    """HTTPS auf private/LAN-Hosts zurück auf HTTP mappen."""
    parts = urlsplit(url.strip())
    if (parts.scheme or "").lower() != "https" or not parts.netloc:
        return None
    host = (parts.hostname or "").lower()
    if not is_private_host(host):
        return None
    return urlunsplit(("http", parts.netloc, parts.path, parts.query, parts.fragment))


def webview2_http_browser_args(base_url: str) -> str:
    parts = urlsplit(base_url.strip())
    if (parts.scheme or "http").lower() != "http" or not parts.netloc:
        return ""

    origin = f"http://{parts.netloc}"
    host = (parts.hostname or "").lower()
    origins = [origin]
    port_suffix = f":{parts.port}" if parts.port else ""
    if host == "127.0.0.1":
        origins.append(f"http://localhost{port_suffix}")
    elif host == "localhost":
        origins.append(f"http://127.0.0.1{port_suffix}")

    allowlist = ",".join(dict.fromkeys(origins))
    return " ".join(
        [
            "--https-upgrades-enabled=false",
            "--disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable,AutomaticHttpsDefault",
            f"--unsafely-treat-insecure-origin-as-secure={allowlist}",
        ]
    )


def patch_pywebview_for_http(extra_args: str) -> None:
    """Hängt Browser-Flags an pywebview/WebView2 an (vor webview.start())."""
    extra = extra_args.strip()
    if not extra:
        return

    from threading import Semaphore

    import webview.platforms.edgechromium as ec

    ec._mpd_http_browser_args = extra
    if getattr(ec, "_mpd_http_patch_installed", False):
        return

    original_ready = ec.EdgeChrome.on_webview_ready

    def patched_init(self, form, window, cache_dir):
        self.pywebview_window = window
        self.webview = ec.WebView2()
        props = ec.CoreWebView2CreationProperties()

        runtime_path = ec.webview_settings["WEBVIEW2_RUNTIME_PATH"]
        if runtime_path:
            if not os.path.isabs(runtime_path):
                runtime_path = os.path.join(ec.get_app_root(), runtime_path)
            if os.path.exists(runtime_path):
                props.BrowserExecutableFolder = runtime_path
                ec.logger.debug(f"Using custom WebView2 runtime: {runtime_path}")
            else:
                ec.logger.warning(
                    "Custom WebView2 runtime path does not exist: "
                    f"{runtime_path}. Using system WebView2."
                )

        props.UserDataFolder = cache_dir
        self.user_data_folder = props.UserDataFolder
        props.set_IsInPrivateModeEnabled(ec._state["private_mode"])
        props.AdditionalBrowserArguments = "--disable-features=ElasticOverscroll"

        if ec.webview_settings["ALLOW_FILE_URLS"]:
            props.AdditionalBrowserArguments += " --allow-file-access-from-files"

        if ec.webview_settings["REMOTE_DEBUGGING_PORT"] is not None:
            props.AdditionalBrowserArguments += (
                f" --remote-debugging-port={ec.webview_settings['REMOTE_DEBUGGING_PORT']}"
            )

        browser_args = getattr(ec, "_mpd_http_browser_args", "")
        if browser_args:
            props.AdditionalBrowserArguments += f" {browser_args}"

        self.webview.CreationProperties = props

        self.form = form
        form.Controls.Add(self.webview)

        self.js_results = {}
        self.js_result_semaphore = Semaphore(0)
        self.webview.Dock = ec.WinForms.DockStyle.Fill
        self.webview.BringToFront()
        self.webview.CoreWebView2InitializationCompleted += self.on_webview_ready
        self.webview.NavigationStarting += self.on_navigation_start
        self.webview.NavigationCompleted += self.on_navigation_completed
        self.webview.WebMessageReceived += self.on_script_notify
        self.syncContextTaskScheduler = ec.TaskScheduler.FromCurrentSynchronizationContext()
        self.webview.DefaultBackgroundColor = ec.Color.FromArgb(
            255,
            int(window.background_color.lstrip("#")[0:2], 16),
            int(window.background_color.lstrip("#")[2:4], 16),
            int(window.background_color.lstrip("#")[4:6], 16),
        )

        if window.transparent:
            self.webview.DefaultBackgroundColor = ec.Color.Transparent

        self.url = None
        self.ishtml = False
        self.html = ec.DEFAULT_HTML

        self.webview.EnsureCoreWebView2Async(None)

    def patched_ready(self, sender, args):
        original_ready(self, sender, args)
        if not args.IsSuccess:
            return

        def on_navigation_start(_sender, nav_args):
            try:
                fixed = http_url_if_private_https(str(nav_args.Uri))
                if fixed:
                    nav_args.Cancel = True
                    _sender.Navigate(fixed)
            except Exception:
                pass

        sender.CoreWebView2.NavigationStarting += on_navigation_start

    ec.EdgeChrome.__init__ = patched_init
    ec.EdgeChrome.on_webview_ready = patched_ready
    ec._mpd_http_patch_installed = True
