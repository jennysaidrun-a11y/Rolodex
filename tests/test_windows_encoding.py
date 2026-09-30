"""The analysis commands must print supplier text on a Windows (cp1252) console."""

import io
import sys

from rolodex import tasks


def test_tasks_print_survives_cp1252_console(monkeypatch):
    raw = io.BytesIO()
    cp1252 = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", cp1252)
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    tasks.utf8_console()
    tasks._out({"name": "Émulsifiant ✓ 食品 → 500 g"})
    cp1252.flush()
    assert "食品".encode() in raw.getvalue()


def test_cp1252_console_really_breaks_without_it(monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    try:
        tasks._out({"name": "食品"})
    except UnicodeEncodeError:
        return
    raise AssertionError("expected the cp1252 crash this guards against")
