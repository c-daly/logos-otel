#!/usr/bin/env python3
"""Tests for the agent-swarm Prometheus exporter."""

import importlib.util
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_EXP = Path(__file__).parent / "exporter.py"
_spec = importlib.util.spec_from_file_location("exporter", _EXP)
exporter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exporter)


@pytest.fixture
def events_conn(tmp_path):
    conn = sqlite3.connect(tmp_path / "dash.db")
    conn.execute("CREATE TABLE events (timestamp TEXT)")
    yield conn
    conn.close()


def _insert(conn, *timestamps):
    conn.executemany("INSERT INTO events (timestamp) VALUES (?)", [(t,) for t in timestamps])
    conn.commit()


class TestRecentEventCount:
    """recent_event_count respects the actual time window (C5 #6 'T' > ' ' bug)."""

    def test_windows_are_distinct(self, events_conn):
        now = datetime.now(timezone.utc)
        _insert(
            events_conn,
            (now - timedelta(minutes=30)).isoformat(),   # inside 1h and 24h
            (now - timedelta(hours=2)).isoformat(),       # outside 1h, inside 24h
            (now - timedelta(hours=30)).isoformat(),      # outside both
        )
        # The bug (raw string compare) counted every same-day event, collapsing
        # the windows: 1h -> 2 and 24h -> 3. Correct is 1 and 2.
        assert exporter.recent_event_count(events_conn, "-1 hour") == 1
        assert exporter.recent_event_count(events_conn, "-24 hours") == 2

    def test_five_minute_window_excludes_older(self, events_conn):
        now = datetime.now(timezone.utc)
        _insert(
            events_conn,
            (now - timedelta(minutes=1)).isoformat(),
            (now - timedelta(minutes=10)).isoformat(),
        )
        assert exporter.recent_event_count(events_conn, "-5 minutes") == 1
