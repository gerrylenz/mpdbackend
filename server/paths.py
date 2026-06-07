#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Sichere Pfadauflösung unter der Musik-Bibliothekswurzel."""

from __future__ import annotations

from pathlib import Path


def resolve_under_root(music_root: Path | str, rel_path: str) -> Path | None:
    """Baut absoluten Pfad; None wenn außerhalb der Wurzel oder ungültig."""
    rel = rel_path.strip().replace("\\", "/")
    if not rel or rel.startswith("/"):
        return None
    parts = Path(rel).parts
    if ".." in parts:
        return None

    root = Path(music_root).resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def build_full_path(rel_path: str, music_root: str) -> str | None:
    """Absoluter Audiodatei-Pfad unter music_root oder None bei ungültigem rel_path."""
    target = resolve_under_root(music_root, rel_path)
    return str(target) if target else None
