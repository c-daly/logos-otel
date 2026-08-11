#!/usr/bin/env python3
"""Generate source-aware Grafana dashboards.

The dashboards are generated from compact Python structures so panel queries stay
reviewable. They assume datasource UIDs from grafana/provisioning/datasources.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = ROOT / "grafana" / "dashboards"
PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
GRAFANA = {"type": "grafana", "uid": "-- Grafana --"}


def target(expr: str, ref_id: str = "A", legend: str = "", *, query_range: bool = False) -> dict[str, Any]:
    return {
        "datasource": PROM,
        "editorMode": "code",
        "expr": expr,
        "instant": not query_range,
        "legendFormat": legend,
        "range": query_range,
        "refId": ref_id,
    }


def loki_target(expr: str, ref_id: str = "A") -> dict[str, Any]:
    return {"datasource": LOKI, "expr": expr, "queryType": "range", "refId": ref_id}


def grid(x: int, y: int, w: int, h: int) -> dict[str, int]:
    return {"x": x, "y": y, "w": w, "h": h}


def panel(
    title: str,
    panel_type: str,
    x: int,
    y: int,
    w: int,
    h: int,
    exprs: list[tuple[str, str]] | None = None,
    *,
    unit: str = "short",
    description: str = "",
    content: str = "",
    datasource: dict[str, str] | None = None,
    thresholds: list[dict[str, Any]] | None = None,
    logs: bool = False,
) -> dict[str, Any]:
    defaults: dict[str, Any] = {"unit": unit}
    if thresholds:
        defaults["thresholds"] = {"mode": "absolute", "steps": thresholds}
        defaults["color"] = {"mode": "thresholds"}

    p: dict[str, Any] = {
        "datasource": datasource or PROM,
        "description": description,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "gridPos": grid(x, y, w, h),
        "options": {},
        "targets": [],
        "title": title,
        "type": panel_type,
    }

    if panel_type == "text":
        p["datasource"] = GRAFANA
        p["options"] = {"mode": "markdown", "content": content}
    elif logs:
        p["datasource"] = LOKI
        p["targets"] = [loki_target(expr, chr(ord("A") + i)) for i, (expr, _legend) in enumerate(exprs or [])]
    elif exprs:
        query_range = panel_type == "timeseries"
        p["targets"] = [
            target(expr, chr(ord("A") + i), legend, query_range=query_range)
            for i, (expr, legend) in enumerate(exprs)
        ]

    if panel_type == "stat":
        p["options"] = {
            "colorMode": "value",
            "graphMode": "none",
            "justifyMode": "auto",
            "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "auto",
        }
    elif panel_type == "timeseries":
        p["options"] = {"legend": {"displayMode": "table", "placement": "bottom"}, "tooltip": {"mode": "multi"}}
    elif panel_type == "barchart":
        p["options"] = {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "single"}}
    elif panel_type == "table":
        p["options"] = {"showHeader": True}
    elif panel_type == "logs":
        p["options"] = {"dedupStrategy": "none", "enableLogDetails": True, "showCommonLabels": False, "showLabels": False}

    return p


def variable(name: str, query: str, label: str | None = None) -> dict[str, Any]:
    return {
        "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
        "datasource": PROM,
        "definition": query,
        "hide": 0,
        "includeAll": True,
        "label": label or name,
        "multi": True,
        "name": name,
        "query": {"query": query, "refId": f"{name}-query"},
        "refresh": 2,
        "sort": 1,
        "type": "query",
    }


def dashboard(uid: str, title: str, tags: list[str], panels: list[dict[str, Any]], variables: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "annotations": {"list": []},
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "id": None,
        "links": [],
        "liveNow": False,
        "panels": panels,
        "refresh": "30s",
        "schemaVersion": 39,
        "style": "dark",
        "tags": tags,
        "templating": {"list": variables or []},
        "time": {"from": "now-24h", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "title": title,
        "uid": uid,
        "version": 1,
        "weekStart": "",
    }


def write(name: str, data: dict[str, Any]) -> None:
    path = DASHBOARD_DIR / name
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(path)


def pipeline_health() -> dict[str, Any]:
    return dashboard(
        "telemetry-pipeline-health",
        "Telemetry Pipeline Health",
        ["source-aware", "infra", "otel"],
        [
            panel("Purpose", "text", 0, 0, 24, 3, content=(
                "Use this first when data looks suspicious. It answers whether the telemetry pipeline is alive: "
                "Prometheus scrape health, exporter freshness, sequence miner status, and visible sources. "
                "If this dashboard is broken, application dashboards cannot be trusted."
            )),
            panel("Scrape Targets Up", "stat", 0, 3, 4, 4, [("sum(up)", "")]),
            panel("Agent-Swarm Exporter Age", "stat", 4, 3, 4, 4, [("time() - agent_swarm_exporter_last_scrape_timestamp", "")], unit="s",
                  thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 90}, {"color": "red", "value": 300}]),
            panel("Transcript Events", "stat", 8, 3, 4, 4, [("agent_swarm_events_total", "")]),
            panel("Controller Events", "stat", 12, 3, 4, 4, [("agent_swarm_controller_events_total", "")]),
            panel("Sequence Miner Sessions", "stat", 16, 3, 4, 4, [("tool_sequence_miner_sessions_processed", "")]),
            panel("Cloud Export Status", "stat", 20, 3, 4, 4, [("up{job=\"otel-collector\"}", "collector")],
                  description="Local collector health only. Grafana Cloud auth errors are visible in collector logs until credentials are configured."),
            panel("Scrape Duration", "timeseries", 0, 7, 12, 7, [("scrape_duration_seconds", "{{job}} {{instance}}")], unit="s"),
            panel("Scrape Samples", "timeseries", 12, 7, 12, 7, [("scrape_samples_scraped", "{{job}} {{instance}}")]),
            panel("Targets", "table", 0, 14, 12, 8, [("up", "{{job}} {{instance}}")]),
            panel("Source Counts", "table", 12, 14, 12, 8, [
                ("agent_swarm_import_source_calls", "{{import_source}}"),
                ("agent_swarm_controller_events_total", "controller_db"),
                ("tool_sequence_miner_sessions_processed", "loki_sequence_miner"),
            ]),
        ],
    )


def telemetry_summary() -> dict[str, Any]:
    return dashboard(
        "telemetry-summary",
        "Telemetry Summary",
        ["source-aware", "summary", "home"],
        [
            panel("Read This First", "text", 0, 0, 24, 3, content=(
                "One-screen status for the current telemetry estate. This separates high-confidence "
                "agent-swarm controller facts from approximate transcript-derived usage and currently "
                "missing sources. Use this as the home dashboard; drill into the other dashboards only "
                "when one of these numbers looks wrong."
            )),
            panel("Pipeline Targets Up", "stat", 0, 3, 4, 4, [("sum(up)", "")]),
            panel("Exporter Freshness", "stat", 4, 3, 4, 4, [("time() - agent_swarm_exporter_last_scrape_timestamp", "")], unit="s",
                  thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 90}, {"color": "red", "value": 300}]),
            panel("Summarized Responses", "stat", 8, 3, 4, 4, [("agent_swarm_controller_summarized_total{was_summarized=\"true\"}", "")]),
            panel("Full Data Requests", "stat", 12, 3, 4, 4, [("agent_swarm_controller_full_content_requests_total", "")]),
            panel("get_full Rate", "stat", 16, 3, 4, 4, [("agent_swarm_controller_get_full_rate", "")], unit="percentunit",
                  thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 0.25}, {"color": "red", "value": 0.5}]),
            panel("Controller Error Rate", "stat", 20, 3, 4, 4, [("agent_swarm_controller_error_rate", "")], unit="percentunit",
                  thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 0.05}, {"color": "red", "value": 0.1}]),
            panel("Agent-Swarm Control Summary", "table", 0, 7, 8, 8, [
                ("agent_swarm_controller_events_total", "controller events"),
                ("agent_swarm_controller_error_rate", "controller error rate"),
                ("agent_swarm_controller_summarized_total{was_summarized=\"true\"}", "summarized responses"),
                ("agent_swarm_controller_summarized_total{was_summarized=\"false\"}", "not summarized responses"),
                ("agent_swarm_controller_summarization_savings_bytes", "bytes saved"),
            ]),
            panel("Summary Follow-Through", "table", 8, 7, 8, 8, [
                ("agent_swarm_controller_summarized_total{was_summarized=\"true\"}", "summaries produced"),
                ("agent_swarm_controller_full_content_requests_total", "full data requests"),
                ("agent_swarm_controller_summaries_since_get_full_total", "summaries since first get_full"),
                ("agent_swarm_controller_get_full_rate", "get_full rate"),
                ("agent_swarm_controller_effective_summarized_total", "summaries not followed by get_full"),
            ]),
            panel("Usage Summary", "table", 16, 7, 8, 8, [
                ("agent_swarm_events_total", "transcript events"),
                ("agent_swarm_sessions_total", "transcript sessions"),
                ("agent_swarm_subagents_spawned_total", "subagents"),
                ("sum(agent_swarm_tokens_by_agent_role{type=~\"input|output\"})", "approx input+output tokens"),
                ("sum(agent_swarm_tokens_by_agent_role{type=~\"input|output|cache_creation\"})", "fresh/generated tokens"),
                ("sum(agent_swarm_tokens_by_agent_role{type=\"cache_read\"})", "cache read tokens"),
                ("sum(agent_swarm_tokens_by_agent_role{type=\"cache_creation\"})", "cache creation tokens"),
                ("sum(agent_swarm_tokens_by_agent_role{type=\"cache_read\"}) / sum(agent_swarm_tokens_by_agent_role)", "cache share"),
                ("sum(agent_swarm_tokens_by_agent_role{type=\"cache_read\"}) / clamp_min(sum(agent_swarm_tokens_by_agent_role{type=\"cache_creation\"}), 1)", "cache read/create ratio"),
            ]),
            panel("Missing Or Empty Sources", "table", 0, 15, 8, 8, [
                ("claude_code_session_count or vector(0)", "claude native sessions"),
                ("claude_code_token_usage or vector(0)", "claude native tokens"),
                ("tool_sequence_miner_sessions_processed", "loki tool sequences"),
                ("count(count by (service_name) ({service_name=~\".+\"})) or vector(0)", "otel services with service_name"),
            ]),
            panel("Summary Outcome", "barchart", 8, 15, 8, 8, [
                ("agent_swarm_controller_summarized_total", "{{was_summarized}}"),
                ("agent_swarm_controller_full_content_requests_total", "full data requests"),
            ]),
            panel("Top Summary Savings", "barchart", 16, 15, 8, 8, [
                ("topk(10, agent_swarm_controller_summarization_savings_by_tool_bytes)", "{{backend}} {{tool}}")
            ], unit="bytes"),
            panel("Token Mix", "barchart", 0, 23, 8, 8, [
                ("sum by (type) (agent_swarm_tokens_by_agent_role)", "{{type}}")
            ]),
            panel("Fresh Vs Cached Tokens", "barchart", 8, 23, 8, 8, [
                ("sum(agent_swarm_tokens_by_agent_role{type=~\"input|output|cache_creation\"})", "fresh/generated"),
                ("sum(agent_swarm_tokens_by_agent_role{type=\"cache_read\"})", "retrieved from cache"),
            ]),
            panel("Cache Efficiency", "table", 16, 23, 8, 8, [
                ("sum(agent_swarm_tokens_by_agent_role{type=\"cache_read\"}) / sum(agent_swarm_tokens_by_agent_role)", "cache share"),
                ("sum(agent_swarm_tokens_by_agent_role{type=\"cache_read\"}) / clamp_min(sum(agent_swarm_tokens_by_agent_role{type=\"cache_creation\"}), 1)", "read/create ratio"),
                ("sum(agent_swarm_tokens_by_agent_role{type=\"cache_read\"}) / clamp_min(sum(agent_swarm_tokens_by_agent_role{type=~\"input|cache_creation\"}), 1)", "cache leverage vs prompt+creation"),
            ]),
            panel("Transcript Tokens By Day", "barchart", 0, 31, 12, 8, [
                ("sum by (day) (agent_swarm_tokens_by_day{type=~\"input|output\"})", "{{day}}")
            ]),
            panel("Cache Tokens By Day", "barchart", 12, 31, 12, 8, [
                ("sum by (day) (agent_swarm_tokens_by_day{type=~\"cache_read|cache_creation\"})", "{{day}}")
            ]),
            panel("Top Controller Latency", "barchart", 0, 39, 12, 8, [
                ("topk(10, agent_swarm_controller_duration_p95_ms)", "{{backend}} {{tool}}")
            ], unit="ms"),
            panel("Top Transcript Tool Errors", "barchart", 12, 39, 12, 8, [
                ("topk(10, agent_swarm_tool_errors)", "{{tool}}")
            ]),
            panel("Data Confidence", "text", 0, 47, 24, 4, content=(
                "**Authoritative now:** agent-swarm controller metrics from `datastore.db`.\n\n"
                "**Summary follow-through:** aggregate `router__get_full` requests compared with summarized responses. Historical data does not link each `content_id` back to its original summarized event, so this is an aggregate rate rather than exact per-summary attribution.\n\n"
                "**Useful but approximate:** transcript/session/token/cache data from `dashboard.db`; per-tool tokens are estimated from Claude transcript usage blocks. Cache read and cache creation are shown separately because they dominate apparent token volume.\n\n"
                "**Expected to be empty until new sessions/apps emit:** `claude_code_*`, Loki tool sequences, and LOGOS service-level OTEL metrics."
            )),
        ],
    )


def fleet_overview() -> dict[str, Any]:
    return dashboard(
        "telemetry-fleet-overview",
        "Telemetry Fleet Overview",
        ["source-aware", "fleet", "logos"],
        [
            panel("Purpose", "text", 0, 0, 24, 3, content=(
                "This is the cross-application landing page. It shows what is currently visible to the telemetry stack. "
                "LOGOS service panels will populate once those apps emit standard OTEL metrics with service labels; "
                "agent-swarm works today because it has a router-backed exporter."
            )),
            panel("Telemetry Targets Up", "stat", 0, 3, 4, 4, [("sum(up)", "")]),
            panel("Agent-Swarm Sessions", "stat", 4, 3, 4, 4, [("agent_swarm_sessions_total", "")]),
            panel("Agent-Swarm Tool Errors", "stat", 8, 3, 4, 4, [("sum(agent_swarm_tool_errors)", "")]),
            panel("Controller Error Rate", "stat", 12, 3, 4, 4, [("agent_swarm_controller_error_rate", "")], unit="percentunit"),
            panel("Claude Sessions", "stat", 16, 3, 4, 4, [("claude_code_session_count or vector(0)", "")]),
            panel("Claude Cost", "stat", 20, 3, 4, 4, [("claude_code_cost_usage or vector(0)", "")], unit="currencyUSD"),
            panel("Observed Targets", "table", 0, 7, 8, 8, [("target_info", "{{job}} {{instance}}")]),
            panel("Events By Source", "barchart", 8, 7, 8, 8, [("agent_swarm_import_source_calls", "{{import_source}}")]),
            panel("Events By Backend", "barchart", 16, 7, 8, 8, [("sum by (backend) (agent_swarm_tool_calls)", "{{backend}}")]),
            panel("Agent-Swarm Activity Windows", "timeseries", 0, 15, 12, 7, [("agent_swarm_events_recent", "{{window}}")]),
            panel("Claude Native Tokens", "timeseries", 12, 15, 12, 7, [("claude_code_token_usage or vector(0)", "{{type}}")]),
        ],
    )


def control_plane() -> dict[str, Any]:
    return dashboard(
        "agent-swarm-control-plane",
        "Agent-Swarm Control Plane",
        ["source-aware", "agent-swarm", "controller"],
        [
            panel("Trust Boundary", "text", 0, 0, 24, 3, content=(
                "Authoritative for plugin behavior. These panels read controller events from `data/datastore.db` via "
                "`agent-swarm-exporter`: router/native/tool latency, errors, workflow events, and summarization. "
                "Do not use this dashboard for token accounting; those fields are currently zero in the controller store."
            )),
            panel("Controller Events", "stat", 0, 3, 4, 4, [("agent_swarm_controller_events_total", "")]),
            panel("Error Rate", "stat", 4, 3, 4, 4, [("agent_swarm_controller_error_rate", "")], unit="percentunit",
                  thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 0.05}, {"color": "red", "value": 0.1}]),
            panel("Summarized", "stat", 8, 3, 4, 4, [("agent_swarm_controller_summarized_total{was_summarized=\"true\"}", "")]),
            panel("Full Data Requests", "stat", 12, 3, 4, 4, [("agent_swarm_controller_full_content_requests_total", "")]),
            panel("get_full Rate", "stat", 16, 3, 4, 4, [("agent_swarm_controller_get_full_rate", "")], unit="percentunit"),
            panel("Bytes Saved", "stat", 20, 3, 4, 4, [("agent_swarm_controller_summarization_savings_bytes", "")], unit="bytes"),
            panel("Average Duration By Tool", "barchart", 0, 7, 12, 8, [("topk(20, agent_swarm_controller_duration_avg_ms{backend=~\"$backend\"})", "{{backend}} {{tool}}")], unit="ms"),
            panel("P95 Duration By Tool", "barchart", 12, 7, 12, 8, [("topk(20, agent_swarm_controller_duration_p95_ms{backend=~\"$backend\"})", "{{backend}} {{tool}}")], unit="ms"),
            panel("Summary Follow-Through", "table", 0, 15, 8, 7, [
                ("agent_swarm_controller_summarized_total{was_summarized=\"true\"}", "summaries produced"),
                ("agent_swarm_controller_full_content_requests_total", "full data requests"),
                ("agent_swarm_controller_summaries_since_get_full_total", "summaries since first get_full"),
                ("agent_swarm_controller_get_full_rate", "get_full rate"),
                ("agent_swarm_controller_effective_summarized_total", "effective summaries"),
            ]),
            panel("Summarization Split", "barchart", 8, 15, 8, 7, [("agent_swarm_controller_summarized_total", "{{was_summarized}}")]),
            panel("Summary Savings By Tool", "barchart", 16, 15, 8, 7, [("topk(20, agent_swarm_controller_summarization_savings_by_tool_bytes{backend=~\"$backend\"})", "{{backend}} {{tool}}")], unit="bytes"),
            panel("Average Response Size", "barchart", 0, 22, 12, 7, [("topk(20, agent_swarm_controller_response_size_avg_bytes{backend=~\"$backend\"})", "{{kind}} {{backend}} {{tool}}")], unit="bytes"),
            panel("Workflow Events", "barchart", 12, 22, 6, 7, [("agent_swarm_controller_workflow_events", "{{workflow_id}}")]),
            panel("Duration Table", "table", 18, 22, 6, 7, [("agent_swarm_controller_duration_avg_ms{backend=~\"$backend\"}", "{{backend}} {{tool}}")], unit="ms"),
        ],
        [variable("backend", "label_values(agent_swarm_controller_duration_avg_ms, backend)", "Backend")],
    )


def usage_provenance() -> dict[str, Any]:
    return dashboard(
        "agent-swarm-usage-provenance",
        "Agent-Swarm Usage And Provenance",
        ["source-aware", "agent-swarm", "usage"],
        [
            panel("Trust Boundary", "text", 0, 0, 24, 3, content=(
                "Approximate usage analytics from transcript imports in `dashboard.db`. Session shape and tool counts are useful. "
                "Per-tool token attribution is approximate because importer divides assistant-message usage across tool calls. "
                "Use native `claude_code_*` metrics for authoritative aggregate usage once new Claude sessions emit OTEL."
            )),
            panel("Transcript Events", "stat", 0, 3, 4, 4, [("agent_swarm_events_total", "")]),
            panel("Sessions", "stat", 4, 3, 4, 4, [("agent_swarm_sessions_total", "")]),
            panel("Distinct Subagents Observed", "stat", 8, 3, 4, 4, [("agent_swarm_subagents_spawned_total", "")]),
            panel("Sessions With Subagents", "stat", 12, 3, 4, 4, [("agent_swarm_sessions_with_subagents", "")]),
            panel("Avg Events/Session", "stat", 16, 3, 4, 4, [("agent_swarm_session_avg_events", "")]),
            panel("Avg Tokens/Session", "stat", 20, 3, 4, 4, [("agent_swarm_session_avg_tokens", "")]),
            panel("Cache Read Tokens", "stat", 0, 7, 4, 4, [("sum(agent_swarm_tokens_by_agent_role{type=\"cache_read\"})", "")]),
            panel("Cache Creation Tokens", "stat", 4, 7, 4, 4, [("sum(agent_swarm_tokens_by_agent_role{type=\"cache_creation\"})", "")]),
            panel("Fresh/Generated Tokens", "stat", 8, 7, 4, 4, [("sum(agent_swarm_tokens_by_agent_role{type=~\"input|output|cache_creation\"})", "")]),
            panel("Cache Share", "stat", 12, 7, 4, 4, [
                ("sum(agent_swarm_tokens_by_agent_role{type=~\"cache_read|cache_creation\"}) / sum(agent_swarm_tokens_by_agent_role)", "")
            ], unit="percentunit"),
            panel("Read/Create Ratio", "stat", 16, 7, 4, 4, [
                ("sum(agent_swarm_tokens_by_agent_role{type=\"cache_read\"}) / clamp_min(sum(agent_swarm_tokens_by_agent_role{type=\"cache_creation\"}), 1)", "")
            ]),
            panel("Cache Leverage", "stat", 20, 7, 4, 4, [
                ("sum(agent_swarm_tokens_by_agent_role{type=\"cache_read\"}) / clamp_min(sum(agent_swarm_tokens_by_agent_role{type=~\"input|cache_creation\"}), 1)", "")
            ]),
            panel("Fresh Vs Cached Tokens", "barchart", 0, 11, 8, 8, [
                ("sum(agent_swarm_tokens_by_agent_role{type=~\"input|output|cache_creation\"})", "fresh/generated"),
                ("sum(agent_swarm_tokens_by_agent_role{type=\"cache_read\"})", "retrieved from cache"),
            ]),
            panel("Token Mix", "barchart", 8, 11, 8, 8, [("sum by (type) (agent_swarm_tokens_by_agent_role)", "{{type}}")]),
            panel("Native Claude Tokens", "barchart", 16, 11, 8, 8, [
                ("sum by (type) (claude_code_token_usage) or vector(0)", "{{type}}")
            ]),
            panel("Tokens By Agent Role", "barchart", 0, 19, 8, 8, [("sum by (agent_role) (agent_swarm_tokens_by_agent_role{type=~\"$token_type\"})", "{{agent_role}}")]),
            panel("Tokens By Tool", "barchart", 8, 19, 8, 8, [("topk(20, sum by (tool) (agent_swarm_tokens_by_tool{type=~\"$token_type\"}))", "{{tool}}")]),
            panel("Cache By Tool", "barchart", 16, 19, 8, 8, [("topk(20, sum by (tool) (agent_swarm_tokens_by_tool{type=~\"cache_read|cache_creation\"}))", "{{tool}}")]),
            panel("Transcript Tokens By Day", "barchart", 0, 27, 12, 7, [("sum by (day) (agent_swarm_tokens_by_day{type=~\"input|output\"})", "{{day}}")]),
            panel("Cache Tokens By Day", "barchart", 12, 27, 12, 7, [("sum by (day) (agent_swarm_tokens_by_day{type=~\"cache_read|cache_creation\"})", "{{day}}")]),
            panel("Top Successful Tools", "barchart", 0, 34, 8, 7, [("topk(20, agent_swarm_tool_calls{status=\"success\", backend=~\"$backend\"})", "{{backend}} {{tool}}")]),
            panel("Tool Errors", "barchart", 8, 34, 8, 7, [("topk(20, agent_swarm_tool_errors)", "{{tool}}")]),
            panel("Normalized Error Types", "barchart", 16, 34, 8, 7, [("topk(20, agent_swarm_errors_by_type)", "{{error_type}}")]),
            panel("Activity By Hour", "barchart", 0, 41, 12, 7, [("agent_swarm_activity_by_hour", "{{hour}}")]),
            panel("Activity By Day", "barchart", 12, 41, 12, 7, [("agent_swarm_activity_by_day", "{{day}}")]),
            panel("Import Sources", "table", 0, 48, 8, 6, [("agent_swarm_import_source_calls", "{{import_source}}")]),
            panel("Agent Type Coverage", "table", 8, 48, 8, 6, [
                ("agent_swarm_agent_calls or vector(0)", "{{agent_type}}"),
                ("agent_swarm_subagent_type_events or vector(0)", "{{agent_type}}"),
            ]),
            panel("Latency Buckets", "barchart", 16, 48, 8, 6, [("agent_swarm_latency_bucket", "{{le}}")]),
        ],
        [
            variable("backend", "label_values(agent_swarm_tool_calls, backend)", "Backend"),
            variable("token_type", "label_values(agent_swarm_tokens_by_tool, type)", "Token Type"),
        ],
    )


def main() -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    write("telemetry-summary.json", telemetry_summary())
    write("telemetry-pipeline-health.json", pipeline_health())
    write("telemetry-fleet-overview.json", fleet_overview())
    write("agent-swarm-control-plane.json", control_plane())
    write("agent-swarm-usage-provenance.json", usage_provenance())


if __name__ == "__main__":
    main()
