"""Agent-swarm Prometheus exporter.

Reads from dashboard.db (JSONL transcripts) and datastore.db (controller)
and exposes Prometheus metrics. Avoids duplicating what Claude native OTEL
already provides (aggregate tokens, cost, active time).

Metrics exposed:
  From dashboard.db (all tool calls):
    - agent_swarm_tool_calls: per tool/backend/status breakdown
    - agent_swarm_tool_errors: per tool error counts
    - agent_swarm_agent_calls: per agent_type breakdown
    - agent_swarm_events_total: total event count
    - agent_swarm_sessions_total: total session count
    - agent_swarm_events_recent: events in recent time windows
  From datastore.db (controller-only, richer metadata):
    - agent_swarm_controller_duration_ms: avg duration per tool
    - agent_swarm_controller_p95_duration_ms: p95 duration per tool
    - agent_swarm_controller_summarized_total: summarization counts
    - agent_swarm_controller_summarization_savings_bytes: bytes saved
    - agent_swarm_controller_events_total: controller event count
"""

import logging
import os
import sqlite3
import time
from contextlib import contextmanager

from prometheus_client import Gauge, start_http_server

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# --- Config ---
DASHBOARD_DB = os.environ.get("DASHBOARD_DB_PATH", "/dashboard-data/dashboard.db")
DATASTORE_DB = os.environ.get("DATASTORE_PATH", "/data/datastore.db")
METRICS_PORT = int(os.environ.get("METRICS_PORT", "8098"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))
TOP_N_TOOLS = int(os.environ.get("TOP_N_TOOLS", "30"))

# --- Error normalization (mirrors dashboard/providers/sqlite.py) ---
_ERROR_PATTERNS = [
    ("Timeout waiting for serena", "Serena Timeout"),
    ("Timeout waiting for native", "Native Timeout"),
    ("Timeout waiting for", "MCP Timeout"),
    ("No active project", "No Active Project"),
    ("[BLOCKED]", "Hook Blocked"),
    ("PreToolUse:", "Hook Blocked"),
    ("No such tool available", "Tool Not Available"),
    ("unknown command", "Unknown Command"),
    ("Exit code", "Non-Zero Exit Code"),
    ("exit code", "Non-Zero Exit Code"),
    ("test session starts", "Test Failure"),
    ("FAILED", "Test Failure"),
    ("ModuleNotFoundError", "Import Error"),
    ("ImportError", "Import Error"),
    ("FileNotFoundError", "File Not Found"),
    ("PermissionError", "Permission Denied"),
    ("ConnectionRefusedError", "Connection Refused"),
    ("JSONDecodeError", "JSON Parse Error"),
    ("SyntaxError", "Syntax Error"),
    ("TypeError", "Type Error"),
    ("ValueError", "Value Error"),
    ("KeyError", "Key Error"),
    ("AttributeError", "Attribute Error"),
    ("IndexError", "Index Error"),
    ("RuntimeError", "Runtime Error"),
    ("OSError", "OS Error"),
    ("Traceback (most recent call last)", "Python Traceback"),
    ("Error:", "Generic Error"),
]


def _normalize_error_type(raw: str) -> str:
    if not raw:
        return "Unknown"
    for pattern, label in _ERROR_PATTERNS:
        if pattern.lower() in raw.lower():
            return label
    first_line = raw.split("\n")[0].strip()[:60]
    return first_line if first_line else "Unknown"


# --- Prometheus metrics: dashboard.db (JSONL transcripts) ---
TOOL_CALLS = Gauge(
    "agent_swarm_tool_calls",
    "Tool call count from JSONL transcripts",
    ["tool", "backend", "status"],
)
TOOL_ERRORS = Gauge(
    "agent_swarm_tool_errors",
    "Tool error count by error type",
    ["tool"],
)
AGENT_CALLS = Gauge(
    "agent_swarm_agent_calls",
    "Tool calls by agent type",
    ["agent_type"],
)
EVENTS_TOTAL = Gauge("agent_swarm_events_total", "Total events in dashboard DB")
SESSIONS_TOTAL = Gauge("agent_swarm_sessions_total", "Total sessions in dashboard DB")
EVENTS_RECENT = Gauge(
    "agent_swarm_events_recent",
    "Events in recent time window",
    ["window"],
)
IMPORT_SOURCE_CALLS = Gauge(
    "agent_swarm_import_source_calls",
    "Events by import source",
    ["import_source"],
)

# Token breakdown
TOKENS_BY_TOOL = Gauge("agent_swarm_tokens_by_tool", "Token count per tool by type", ["tool", "type"])
TOKENS_BY_AGENT_ROLE = Gauge("agent_swarm_tokens_by_agent_role", "Token count per agent role by type", ["agent_role", "type"])

# Subagent / concurrency
SUBAGENTS_SPAWNED = Gauge("agent_swarm_subagents_spawned_total", "Total distinct subagent IDs ever observed")
SESSIONS_WITH_SUBAGENTS = Gauge("agent_swarm_sessions_with_subagents", "Sessions that had at least one subagent")
SUBAGENT_TYPE_AGENTS = Gauge("agent_swarm_subagent_type_agents", "Distinct agent count per subagent type", ["agent_type"])
SUBAGENT_TYPE_EVENTS = Gauge("agent_swarm_subagent_type_events", "Event count per subagent type", ["agent_type"])
SUBAGENT_TYPE_TOKENS = Gauge("agent_swarm_subagent_type_tokens", "Total tokens per subagent type", ["agent_type"])

# Activity patterns
ACTIVITY_BY_HOUR = Gauge("agent_swarm_activity_by_hour", "Events per hour of day (EST/EDT)", ["hour"])
ACTIVITY_BY_DAY = Gauge("agent_swarm_activity_by_day", "Events per day of week (EST/EDT)", ["day"])

# Latency
LATENCY_BUCKET = Gauge("agent_swarm_latency_bucket", "Aggregate latency histogram bucket count", ["le"])
LATENCY_BY_TOOL_AVG = Gauge("agent_swarm_latency_by_tool_avg_ms", "Average latency per tool from dashboard DB (ms)", ["tool"])

# Normalized errors
ERRORS_BY_TYPE = Gauge("agent_swarm_errors_by_type", "Errors grouped by normalized error type", ["error_type"])

# Session averages
SESSION_AVG_EVENTS = Gauge("agent_swarm_session_avg_events", "Average events per session")
SESSION_AVG_TOKENS = Gauge("agent_swarm_session_avg_tokens", "Average tokens per session")

# --- Prometheus metrics: datastore.db (controller) ---
CTRL_DURATION_AVG = Gauge(
    "agent_swarm_controller_duration_avg_ms",
    "Average tool duration from controller (ms)",
    ["tool", "backend"],
)
CTRL_DURATION_P95 = Gauge(
    "agent_swarm_controller_duration_p95_ms",
    "P95 tool duration from controller (ms)",
    ["tool", "backend"],
)
CTRL_SUMMARIZED = Gauge(
    "agent_swarm_controller_summarized_total",
    "Summarization count from controller",
    ["was_summarized"],
)
CTRL_SAVINGS = Gauge(
    "agent_swarm_controller_summarization_savings_bytes",
    "Total bytes saved by summarization",
)
CTRL_EVENTS = Gauge(
    "agent_swarm_controller_events_total",
    "Total events in controller datastore",
)
CTRL_ERROR_RATE = Gauge(
    "agent_swarm_controller_error_rate",
    "Controller error rate (0-1)",
)
CTRL_WORKFLOW_EVENTS = Gauge(
    "agent_swarm_controller_workflow_events",
    "Controller events by workflow",
    ["workflow_id"],
)

# --- Scrape timestamp ---
LAST_SCRAPE = Gauge(
    "agent_swarm_exporter_last_scrape_timestamp",
    "Unix timestamp of last successful scrape",
)


_DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


@contextmanager
def _connect(db_path: str):
    """Open a read-only SQLite connection with WAL mode."""
    if not os.path.exists(db_path):
        yield None
        return
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def scrape_dashboard_db() -> None:
    """Read dashboard.db and update Prometheus gauges."""
    with _connect(DASHBOARD_DB) as conn:
        if conn is None:
            log.warning("dashboard.db not found at %s", DASHBOARD_DB)
            return

        # Total events and sessions
        total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        EVENTS_TOTAL.set(total)
        sessions = conn.execute("SELECT COUNT(DISTINCT session_id) FROM events").fetchone()[0]
        SESSIONS_TOTAL.set(sessions)

        # Events in recent windows
        for window, interval in [("5m", "-5 minutes"), ("1h", "-1 hour"), ("24h", "-24 hours")]:
            count = conn.execute(
                "SELECT COUNT(*) FROM events WHERE timestamp > datetime('now', ?)", (interval,)
            ).fetchone()[0]
            EVENTS_RECENT.labels(window=window).set(count)

        # Per-tool call counts (top N)
        TOOL_CALLS._metrics.clear()
        rows = conn.execute(
            "SELECT tool, backend, status, COUNT(*) as cnt FROM events "
            "GROUP BY tool, backend, status ORDER BY cnt DESC LIMIT ?",
            (TOP_N_TOOLS * 3,),
        ).fetchall()
        for r in rows:
            TOOL_CALLS.labels(tool=r["tool"], backend=r["backend"], status=r["status"]).set(r["cnt"])

        # Per-tool errors
        TOOL_ERRORS._metrics.clear()
        rows = conn.execute(
            "SELECT tool, COUNT(*) as cnt FROM events WHERE status = 'error' "
            "GROUP BY tool ORDER BY cnt DESC LIMIT ?",
            (TOP_N_TOOLS,),
        ).fetchall()
        for r in rows:
            TOOL_ERRORS.labels(tool=r["tool"]).set(r["cnt"])

        # Per agent type
        AGENT_CALLS._metrics.clear()
        rows = conn.execute(
            "SELECT agent_type, COUNT(*) as cnt FROM events "
            "WHERE agent_type != '' GROUP BY agent_type ORDER BY cnt DESC LIMIT 20"
        ).fetchall()
        for r in rows:
            AGENT_CALLS.labels(agent_type=r["agent_type"]).set(r["cnt"])

        # By import source
        IMPORT_SOURCE_CALLS._metrics.clear()
        rows = conn.execute(
            "SELECT import_source, COUNT(*) as cnt FROM events GROUP BY import_source"
        ).fetchall()
        for r in rows:
            IMPORT_SOURCE_CALLS.labels(import_source=r["import_source"] or "unknown").set(r["cnt"])

        # --- Tokens by tool ---
        TOKENS_BY_TOOL._metrics.clear()
        rows = conn.execute(
            "SELECT tool, SUM(input_tokens) as inp, SUM(output_tokens) as outp, "
            "SUM(cache_read_tokens) as cr, SUM(cache_creation_tokens) as cc "
            "FROM events GROUP BY tool "
            "ORDER BY (SUM(input_tokens) + SUM(output_tokens)) DESC LIMIT ?",
            (TOP_N_TOOLS,),
        ).fetchall()
        for r in rows:
            tool = r["tool"]
            TOKENS_BY_TOOL.labels(tool=tool, type="input").set(r["inp"] or 0)
            TOKENS_BY_TOOL.labels(tool=tool, type="output").set(r["outp"] or 0)
            TOKENS_BY_TOOL.labels(tool=tool, type="cache_read").set(r["cr"] or 0)
            TOKENS_BY_TOOL.labels(tool=tool, type="cache_creation").set(r["cc"] or 0)

        # --- Tokens by agent role ---
        TOKENS_BY_AGENT_ROLE._metrics.clear()
        rows = conn.execute(
            "SELECT CASE "
            "  WHEN agent_id = session_id THEN 'main' "
            "  WHEN agent_type != '' THEN agent_type "
            "  ELSE 'subagent (legacy)' "
            "END as agent_role, "
            "SUM(input_tokens) as inp, SUM(output_tokens) as outp, "
            "SUM(cache_read_tokens) as cr, SUM(cache_creation_tokens) as cc "
            "FROM events GROUP BY agent_role "
            "ORDER BY (SUM(input_tokens) + SUM(output_tokens)) DESC"
        ).fetchall()
        for r in rows:
            role = r["agent_role"]
            TOKENS_BY_AGENT_ROLE.labels(agent_role=role, type="input").set(r["inp"] or 0)
            TOKENS_BY_AGENT_ROLE.labels(agent_role=role, type="output").set(r["outp"] or 0)
            TOKENS_BY_AGENT_ROLE.labels(agent_role=role, type="cache_read").set(r["cr"] or 0)
            TOKENS_BY_AGENT_ROLE.labels(agent_role=role, type="cache_creation").set(r["cc"] or 0)

        # --- Subagent / concurrency stats ---
        sub_row = conn.execute(
            "SELECT COUNT(DISTINCT CASE WHEN agent_id != session_id THEN agent_id END) as total_subagents, "
            "COUNT(DISTINCT CASE WHEN agent_id != session_id THEN session_id END) as sessions_with "
            "FROM events"
        ).fetchone()
        SUBAGENTS_SPAWNED.set(sub_row["total_subagents"] or 0)
        SESSIONS_WITH_SUBAGENTS.set(sub_row["sessions_with"] or 0)

        SUBAGENT_TYPE_AGENTS._metrics.clear()
        SUBAGENT_TYPE_EVENTS._metrics.clear()
        SUBAGENT_TYPE_TOKENS._metrics.clear()
        rows = conn.execute(
            "SELECT CASE "
            "  WHEN agent_type != '' THEN agent_type "
            "  ELSE 'legacy (pre-tracking)' "
            "END as atype, "
            "COUNT(DISTINCT agent_id) as agent_count, "
            "COUNT(*) as event_count, "
            "SUM(input_tokens + output_tokens) as total_tokens "
            "FROM events WHERE agent_id != session_id "
            "GROUP BY atype ORDER BY agent_count DESC"
        ).fetchall()
        for r in rows:
            atype = r["atype"]
            SUBAGENT_TYPE_AGENTS.labels(agent_type=atype).set(r["agent_count"])
            SUBAGENT_TYPE_EVENTS.labels(agent_type=atype).set(r["event_count"])
            SUBAGENT_TYPE_TOKENS.labels(agent_type=atype).set(r["total_tokens"] or 0)

        # --- Activity by hour of day and day of week (EST = UTC-5) ---
        ACTIVITY_BY_HOUR._metrics.clear()
        rows = conn.execute(
            "SELECT CAST(strftime('%H', timestamp, '-5 hours') AS INTEGER) as hour, "
            "COUNT(*) as cnt FROM events GROUP BY hour ORDER BY hour"
        ).fetchall()
        for r in rows:
            ACTIVITY_BY_HOUR.labels(hour=str(r["hour"]).zfill(2)).set(r["cnt"])

        ACTIVITY_BY_DAY._metrics.clear()
        rows = conn.execute(
            "SELECT CAST(strftime('%w', timestamp, '-5 hours') AS INTEGER) as dow, "
            "COUNT(*) as cnt FROM events GROUP BY dow ORDER BY dow"
        ).fetchall()
        for r in rows:
            ACTIVITY_BY_DAY.labels(day=_DAY_NAMES[r["dow"]]).set(r["cnt"])

        # --- Aggregate latency histogram ---
        LATENCY_BUCKET._metrics.clear()
        bucket_row = conn.execute(
            "SELECT "
            "SUM(CASE WHEN duration_ms < 100 THEN 1 ELSE 0 END) as lt100, "
            "SUM(CASE WHEN duration_ms >= 100 AND duration_ms < 500 THEN 1 ELSE 0 END) as b100_500, "
            "SUM(CASE WHEN duration_ms >= 500 AND duration_ms < 1000 THEN 1 ELSE 0 END) as b500_1000, "
            "SUM(CASE WHEN duration_ms >= 1000 AND duration_ms < 5000 THEN 1 ELSE 0 END) as b1s_5s, "
            "SUM(CASE WHEN duration_ms >= 5000 AND duration_ms < 30000 THEN 1 ELSE 0 END) as b5s_30s, "
            "SUM(CASE WHEN duration_ms >= 30000 THEN 1 ELSE 0 END) as b30s_plus "
            "FROM events WHERE duration_ms > 0"
        ).fetchone()
        LATENCY_BUCKET.labels(le="100ms").set(bucket_row["lt100"] or 0)
        LATENCY_BUCKET.labels(le="500ms").set(bucket_row["b100_500"] or 0)
        LATENCY_BUCKET.labels(le="1s").set(bucket_row["b500_1000"] or 0)
        LATENCY_BUCKET.labels(le="5s").set(bucket_row["b1s_5s"] or 0)
        LATENCY_BUCKET.labels(le="30s").set(bucket_row["b5s_30s"] or 0)
        LATENCY_BUCKET.labels(le="+Inf").set(bucket_row["b30s_plus"] or 0)

        # --- Avg latency per tool ---
        LATENCY_BY_TOOL_AVG._metrics.clear()
        rows = conn.execute(
            "SELECT tool, AVG(duration_ms) as avg_ms, COUNT(*) as cnt "
            "FROM events WHERE duration_ms > 0 "
            "GROUP BY tool ORDER BY cnt DESC LIMIT ?",
            (TOP_N_TOOLS,),
        ).fetchall()
        for r in rows:
            LATENCY_BY_TOOL_AVG.labels(tool=r["tool"]).set(round(r["avg_ms"] or 0, 1))

        # --- Normalized error types ---
        ERRORS_BY_TYPE._metrics.clear()
        rows = conn.execute(
            "SELECT error_type, COUNT(*) as cnt FROM events "
            "WHERE status = 'error' AND error_type != '' "
            "GROUP BY error_type ORDER BY cnt DESC"
        ).fetchall()
        merged = {}
        for r in rows:
            label = _normalize_error_type(r["error_type"])
            merged[label] = merged.get(label, 0) + r["cnt"]
        for label, count in sorted(merged.items(), key=lambda x: x[1], reverse=True)[:30]:
            ERRORS_BY_TYPE.labels(error_type=label).set(count)

        # --- Session averages ---
        avg_row = conn.execute(
            "SELECT AVG(ec) as avg_events, AVG(tt) as avg_tokens "
            "FROM (SELECT session_id, COUNT(*) as ec, "
            "SUM(input_tokens + output_tokens) as tt "
            "FROM events GROUP BY session_id)"
        ).fetchone()
        SESSION_AVG_EVENTS.set(round(avg_row["avg_events"] or 0, 1))
        SESSION_AVG_TOKENS.set(round(avg_row["avg_tokens"] or 0, 0))


def scrape_datastore_db() -> None:
    """Read datastore.db and update controller-specific Prometheus gauges."""
    with _connect(DATASTORE_DB) as conn:
        if conn is None:
            log.warning("datastore.db not found at %s", DATASTORE_DB)
            return

        # Total controller events
        total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        CTRL_EVENTS.set(total)

        # Error rate
        errors = conn.execute("SELECT COUNT(*) FROM events WHERE status = 'error'").fetchone()[0]
        CTRL_ERROR_RATE.set(errors / total if total > 0 else 0)

        # Avg and P95 duration per tool (top N)
        CTRL_DURATION_AVG._metrics.clear()
        CTRL_DURATION_P95._metrics.clear()
        rows = conn.execute(
            """SELECT tool, backend, AVG(duration_ms) as avg_d, COUNT(*) as cnt
               FROM events WHERE duration_ms > 0
               GROUP BY tool, backend ORDER BY cnt DESC LIMIT ?""",
            (TOP_N_TOOLS,),
        ).fetchall()
        for r in rows:
            CTRL_DURATION_AVG.labels(tool=r["tool"], backend=r["backend"]).set(round(r["avg_d"], 1))

        # P95 via percentile query per tool
        for r in rows:
            p95_row = conn.execute(
                """SELECT duration_ms FROM events
                   WHERE tool = ? AND backend = ? AND duration_ms > 0
                   ORDER BY duration_ms
                   LIMIT 1 OFFSET (
                       SELECT CAST(COUNT(*) * 0.95 AS INTEGER)
                       FROM events WHERE tool = ? AND backend = ? AND duration_ms > 0
                   )""",
                (r["tool"], r["backend"], r["tool"], r["backend"]),
            ).fetchone()
            if p95_row:
                CTRL_DURATION_P95.labels(tool=r["tool"], backend=r["backend"]).set(p95_row[0])

        # Summarization stats
        CTRL_SUMMARIZED._metrics.clear()
        for val, label in [(1, "true"), (0, "false")]:
            count = conn.execute(
                "SELECT COUNT(*) FROM events WHERE was_summarized = ?", (val,)
            ).fetchone()[0]
            CTRL_SUMMARIZED.labels(was_summarized=label).set(count)

        # Total bytes saved
        savings = conn.execute(
            "SELECT COALESCE(SUM(original_size - COALESCE(summary_size, original_size)), 0) "
            "FROM events WHERE was_summarized = 1"
        ).fetchone()[0]
        CTRL_SAVINGS.set(savings)

        # Workflow events
        CTRL_WORKFLOW_EVENTS._metrics.clear()
        rows = conn.execute(
            "SELECT workflow_id, COUNT(*) as cnt FROM events "
            "WHERE workflow_id != '' AND workflow_id IS NOT NULL "
            "GROUP BY workflow_id ORDER BY cnt DESC LIMIT 20"
        ).fetchall()
        for r in rows:
            CTRL_WORKFLOW_EVENTS.labels(workflow_id=r["workflow_id"]).set(r["cnt"])


def run_cycle() -> None:
    """Run one scrape cycle."""
    try:
        scrape_dashboard_db()
    except Exception as e:
        log.error("Error scraping dashboard.db: %s", e)

    try:
        scrape_datastore_db()
    except Exception as e:
        log.error("Error scraping datastore.db: %s", e)

    LAST_SCRAPE.set(time.time())


def main() -> None:
    log.info("Starting agent-swarm exporter on port %d", METRICS_PORT)
    log.info("  dashboard.db: %s", DASHBOARD_DB)
    log.info("  datastore.db: %s", DATASTORE_DB)
    log.info("  poll interval: %ds", POLL_INTERVAL)

    start_http_server(METRICS_PORT)

    # Initial scrape
    run_cycle()
    log.info("Initial scrape complete")

    while True:
        time.sleep(POLL_INTERVAL)
        run_cycle()


if __name__ == "__main__":
    main()
