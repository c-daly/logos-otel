"""Tool Sequence Mining Service.

Reads tool_result events from Loki, computes transition pairs and n-gram
sequences, and exposes them as Prometheus metrics for Sankey visualization.
"""

import json
import logging
import os
import time
from collections import Counter

import requests
from prometheus_client import Gauge, start_http_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("tool-sequence-miner")

LOKI_URL = os.environ.get("LOKI_URL", "http://loki:3100")
METRICS_PORT = int(os.environ.get("METRICS_PORT", "8099"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "300"))  # 5 min
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "168"))  # 7 days
MAX_TRANSITIONS = 400
MAX_SEQUENCES = 50
NGRAM_MIN = 2
NGRAM_MAX = 6

# --- Prometheus metrics ---
transition_gauge = Gauge(
    "tool_transition_count",
    "Tool-to-tool transition frequency",
    ["source", "target"],
)
sequence_gauge = Gauge(
    "tool_sequence_count",
    "Recurring multi-tool sequence frequency",
    ["pattern", "length"],
)
last_run_ts = Gauge(
    "tool_sequence_miner_last_run_timestamp",
    "Unix timestamp of last successful mining run",
)
sessions_processed = Gauge(
    "tool_sequence_miner_sessions_processed",
    "Number of sessions processed in last run",
)


def wait_for_loki(max_retries: int = 10) -> None:
    """Wait for Loki to become ready with exponential backoff."""
    delay = 2
    for attempt in range(max_retries):
        try:
            r = requests.get(f"{LOKI_URL}/ready", timeout=5)
            if r.status_code == 200:
                log.info("Loki is ready")
                return
        except requests.ConnectionError:
            pass
        log.info("Waiting for Loki (attempt %d/%d, next in %ds)", attempt + 1, max_retries, delay)
        time.sleep(delay)
        delay = min(delay * 2, 60)
    raise RuntimeError("Loki did not become ready")


def query_loki_events() -> list[dict]:
    """Query Loki for tool_result events over the lookback window."""
    end_ns = int(time.time() * 1e9)
    start_ns = end_ns - int(LOOKBACK_HOURS * 3600 * 1e9)

    params = {
        "query": '{service_name="claude-code"} | event_name = "tool_result"',
        "start": str(start_ns),
        "end": str(end_ns),
        "limit": "5000",
        "direction": "forward",
    }

    try:
        r = requests.get(f"{LOKI_URL}/loki/api/v1/query_range", params=params, timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        log.error("Loki query failed: %s", e)
        return []

    data = r.json()
    events = []

    for stream in data.get("data", {}).get("result", []):
        stream_labels = stream.get("stream", {})
        for ts_ns, line in stream.get("values", []):
            event = _parse_event(stream_labels, line, ts_ns)
            if event:
                events.append(event)

    log.info("Fetched %d tool_result events from Loki", len(events))
    return events


def _parse_event(labels: dict, line: str, ts_ns: str) -> dict | None:
    """Extract tool_name, session_id, and timestamp from a log entry.

    Tries stream labels first, falls back to parsing JSON from the log line.
    """
    tool_name = labels.get("tool_name")
    session_id = labels.get("session_id")
    timestamp = int(ts_ns)

    if not tool_name or not session_id:
        try:
            body = json.loads(line)
            tool_name = tool_name or body.get("tool_name")
            session_id = session_id or body.get("session_id")
        except (json.JSONDecodeError, TypeError):
            pass

    if not tool_name or not session_id:
        return None

    return {"tool_name": tool_name, "session_id": session_id, "timestamp": timestamp}


def group_by_session(events: list[dict]) -> dict[str, list[str]]:
    """Group events by session, sort by timestamp, return tool sequences.

    Collapses consecutive identical tools (e.g., Read,Read,Read -> Read).
    """
    sessions: dict[str, list[dict]] = {}
    for ev in events:
        sessions.setdefault(ev["session_id"], []).append(ev)

    result = {}
    for sid, evts in sessions.items():
        evts.sort(key=lambda e: e["timestamp"])
        # Collapse consecutive identical tools
        collapsed = []
        for ev in evts:
            if not collapsed or collapsed[-1] != ev["tool_name"]:
                collapsed.append(ev["tool_name"])
        if len(collapsed) >= 2:
            result[sid] = collapsed

    return result


def extract_transitions(sessions: dict[str, list[str]]) -> Counter:
    """Extract source->target transition pairs across all sessions."""
    counts: Counter = Counter()
    for tools in sessions.values():
        for i in range(len(tools) - 1):
            counts[(tools[i], tools[i + 1])] += 1
    return counts


def extract_ngrams(sessions: dict[str, list[str]]) -> Counter:
    """Extract n-grams of length NGRAM_MIN..NGRAM_MAX across all sessions."""
    counts: Counter = Counter()
    for tools in sessions.values():
        for n in range(NGRAM_MIN, NGRAM_MAX + 1):
            for i in range(len(tools) - n + 1):
                gram = tuple(tools[i : i + n])
                counts[gram] += 1
    return counts


def update_metrics(transitions: Counter, ngrams: Counter, num_sessions: int) -> None:
    """Push computed data into Prometheus gauges."""
    # Clear old metric values
    transition_gauge._metrics.clear()
    sequence_gauge._metrics.clear()

    # Top transitions by count
    for (src, tgt), count in transitions.most_common(MAX_TRANSITIONS):
        transition_gauge.labels(source=src, target=tgt).set(count)

    # Top n-gram sequences
    for gram, count in ngrams.most_common(MAX_SEQUENCES):
        pattern = " -> ".join(gram)
        sequence_gauge.labels(pattern=pattern, length=str(len(gram))).set(count)

    last_run_ts.set(time.time())
    sessions_processed.set(num_sessions)


def run_cycle() -> None:
    """Execute one mining cycle."""
    events = query_loki_events()
    if not events:
        log.warning("No events found, skipping cycle")
        last_run_ts.set(time.time())
        return

    sessions = group_by_session(events)
    transitions = extract_transitions(sessions)
    ngrams = extract_ngrams(sessions)
    update_metrics(transitions, ngrams, len(sessions))
    log.info(
        "Mining complete: %d sessions, %d transition types, %d sequence types",
        len(sessions),
        len(transitions),
        len(ngrams),
    )


def main() -> None:
    log.info("Starting tool-sequence-miner on port %d", METRICS_PORT)
    start_http_server(METRICS_PORT)
    wait_for_loki()

    while True:
        try:
            run_cycle()
        except Exception:
            log.exception("Error in mining cycle")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
