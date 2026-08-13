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


_EVENTS_SCHEMA = """
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT, session_id TEXT, agent_id TEXT DEFAULT '',
    tool TEXT, backend TEXT, duration_ms INTEGER DEFAULT 0, status TEXT,
    input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0, cache_creation_tokens INTEGER DEFAULT 0,
    agent_type TEXT DEFAULT '', workflow_id TEXT DEFAULT '', error_type TEXT DEFAULT '',
    was_summarized INTEGER DEFAULT 0, original_size INTEGER DEFAULT 0, summary_size INTEGER,
    tool_use_id TEXT DEFAULT '', import_source TEXT DEFAULT '', phase TEXT DEFAULT '', target TEXT DEFAULT ''
)
"""

# (session_id, agent_id, tool, backend, status, was_summarized, original_size, summary_size)
_ROWS = [
    ("s1", "s1",   "Bash", "native", "success", 0, 100,  None),   # main agent
    ("s1", "sub1", "Read", "native", "success", 1, 5000, 200),    # subagent
    ("s2", "s2",   "Bash", "native", "error",   0, 50,   None),   # main agent
]


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute(_EVENTS_SCHEMA)
    now = datetime.now(timezone.utc).isoformat()
    for sid, aid, tool, backend, status, ws, orig, summ in _ROWS:
        conn.execute(
            "INSERT INTO events (timestamp, session_id, agent_id, tool, backend, status, "
            "was_summarized, original_size, summary_size) VALUES (?,?,?,?,?,?,?,?,?)",
            (now, sid, aid, tool, backend, status, ws, orig, summ),
        )
    conn.commit()
    conn.close()


class TestCumulativeCounters:
    """The *_total metrics are Counters with correct DB-derived values (C5 #6)."""

    def _metrics_text(self, tmp_path, monkeypatch):
        from prometheus_client import CollectorRegistry, generate_latest
        dash = tmp_path / "dashboard.db"
        data = tmp_path / "datastore.db"
        _make_db(dash)
        _make_db(data)
        monkeypatch.setattr(exporter, "DASHBOARD_DB", str(dash))
        monkeypatch.setattr(exporter, "DATASTORE_DB", str(data))
        reg = CollectorRegistry()
        reg.register(exporter.CumulativeCountersCollector())
        return generate_latest(reg).decode()

    def test_totals_are_counters_with_correct_values(self, tmp_path, monkeypatch):
        text = self._metrics_text(tmp_path, monkeypatch)
        # Counter TYPE lines
        assert "# TYPE agent_swarm_events_total counter" in text
        assert "# TYPE agent_swarm_sessions_total counter" in text
        assert "# TYPE agent_swarm_controller_response_bytes_total counter" in text
        # Values (names keep the _total suffix)
        assert "agent_swarm_events_total 3.0" in text
        assert "agent_swarm_sessions_total 2.0" in text
        assert "agent_swarm_subagents_spawned_total 1.0" in text
        assert 'agent_swarm_controller_summarized_total{was_summarized="true"} 1.0' in text
        assert 'agent_swarm_controller_response_bytes_total{kind="original"} 5150.0' in text
        assert 'agent_swarm_controller_response_bytes_total{kind="saved"} 4800.0' in text


class TestScrapesRunAfterRemoval:
    """The scrape functions still run after the *_total gauges were removed
    (guards against a dangling reference left by the gauge removal)."""

    def test_scrapes_do_not_raise(self, tmp_path, monkeypatch):
        dash = tmp_path / "dashboard.db"
        data = tmp_path / "datastore.db"
        _make_db(dash)
        _make_db(data)
        monkeypatch.setattr(exporter, "DASHBOARD_DB", str(dash))
        monkeypatch.setattr(exporter, "DATASTORE_DB", str(data))
        exporter.scrape_dashboard_db()
        exporter.scrape_datastore_db()
