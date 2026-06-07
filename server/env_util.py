#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Gemeinsames Parsen von KEY=VALUE-Env-Dateien."""

from __future__ import annotations

import os
from pathlib import Path


def env_bool(name: str, default: bool = False) -> bool:
    """Wandelt eine Umgebungsvariable in einen bool-Wert um (true/1/yes/on)."""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def parse_env_lines(lines) -> dict[str, str]:
    """Parst KEY=VALUE-Zeilen aus einem Iterable von Textzeilen."""
    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def parse_env_file(path: Path | str) -> dict[str, str]:
    """Liest KEY=VALUE aus einer Env-Datei."""
    with Path(path).open(encoding="utf-8") as handle:
        return parse_env_lines(handle)


def load_env_file(
    default_env_file: str,
    *,
    explicit_env_var: str = "MPDBACKEND_ENV_FILE",
) -> None:
    """
    Lädt Variablen aus einer Env-Datei in os.environ.

    Mit gesetztem explicit_env_var: Werte aus dieser Datei (Multi-Instanz).
    Ohne: default_env_file — bereits gesetzte Umgebungsvariablen werden
    nicht überschrieben (systemd EnvironmentFile).
    """
    explicit = os.getenv(explicit_env_var, "").strip()
    env_path = explicit or default_env_file
    if not os.path.isfile(env_path):
        return

    with open(env_path, encoding="utf-8") as handle:
        for key, value in parse_env_lines(handle).items():
            if explicit or key not in os.environ:
                os.environ[key] = value
