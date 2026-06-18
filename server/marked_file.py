#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Atomare Lese/Schreib-Zugriffe auf mark_for_delete.cfg."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]


@contextmanager
def marked_file_lock(path: str) -> Iterator[None]:
    """Exklusiver Lock für mark_for_delete.cfg (fcntl auf Unix)."""
    target_abs = os.path.abspath(path)
    parent = os.path.dirname(target_abs)
    if parent:
        os.makedirs(parent, exist_ok=True)

    lock_path = target_abs + ".lock"
    lock_handle = open(lock_path, "a+", encoding="utf-8")
    try:
        if fcntl is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()
        try:
            os.remove(lock_path)
        except OSError:
            pass


def read_marked_lines(path: str) -> list[str]:
    """Liest nicht-leere Zeilen aus der Markierdatei."""
    target_abs = os.path.abspath(path)
    if not os.path.isfile(target_abs):
        return []

    with marked_file_lock(target_abs):
        with open(target_abs, encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.strip()]


def append_marked_line(path: str, track_file: str) -> bool:
    """
    Hängt track_file an, wenn noch nicht vorhanden.

    Returns True wenn angehängt, False wenn Duplikat.
    """
    target_abs = os.path.abspath(path)
    parent = os.path.dirname(target_abs)
    if parent:
        os.makedirs(parent, exist_ok=True)

    with marked_file_lock(target_abs):
        lines: list[str] = []
        if os.path.isfile(target_abs):
            with open(target_abs, encoding="utf-8") as handle:
                lines = [line.strip() for line in handle if line.strip()]
        if track_file in lines:
            return False

        with open(target_abs, "a", encoding="utf-8") as handle:
            handle.write(track_file)
            handle.write("\n")
    return True


def clear_marked_file(path: str) -> str:
    """Leert die Markierdatei; liefert absoluten Pfad."""
    target_abs = os.path.abspath(path)
    parent = os.path.dirname(target_abs)
    if parent:
        os.makedirs(parent, exist_ok=True)

    with marked_file_lock(target_abs):
        with open(target_abs, "w", encoding="utf-8") as handle:
            handle.write("")
    return target_abs
