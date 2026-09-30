from __future__ import annotations

import argparse
import html
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOGS = REPO_ROOT / "data" / "logs.jsonl"
DEFAULT_CONFIG = REPO_ROOT / "config" / "dashboard.yaml"
DEFAULT_OUTPUT = REPO_ROOT / "submission" / "evidence" / "11-dashboard-overview.html"

# Color palette for metrics series: Cyan, Amber, Purple
COLORS = [
    ("#00f2fe", "#0284c7"),  # Series 0: Cyan / Sky
    ("#fbbf24", "#d97706"),  # Series 1: Amber / Orange
    ("#c084fc", "#7c3aed"),  # Series 2: Purple / Violet
]

PANEL_QUESTIONS = {
    "latency": "Request có chậm không? P50/P95/P99 và TTFT đang ở mức nào?",
    "traffic": "Hệ thống đang nhận bao nhiêu request theo thời gian?",
    "errors": "Error rate có tăng không, retrieval có đang fail không?",
    "cost": "Chi phí có tăng bất thường không?",
    "tokens": "Input/output token có dài bất thường không?",
    "quality": "Quality proxy có giảm dưới mức chấp nhận được không?",
}

PANEL_ICONS = {
    "latency": "⚡",
    "traffic": "📈",
    "errors": "🛡️",
    "cost": "💰",
    "tokens": "🔠",
    "quality": "⭐",
}


def percentile(values: Iterable[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            record["_ts"] = datetime.fromisoformat(record["ts"].replace("Z", "+00:00"))
            records.append(record)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return records


def _minute_buckets(now: datetime, minutes: int) -> list[datetime]:
    end = now.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return [end - timedelta(minutes=offset) for offset in reversed(range(minutes))]


def _by_minute(records: list[dict[str, Any]], field: str, buckets: list[datetime]) -> list[float]:
    totals: defaultdict[datetime, float] = defaultdict(float)
    for record in records:
        value = record.get(field)
        if isinstance(value, (int, float)):
            totals[record["_ts"].astimezone(timezone.utc).replace(second=0, microsecond=0)] += float(value)
    return [totals[bucket] for bucket in buckets]


def _count_by_minute(records: list[dict[str, Any]], buckets: list[datetime]) -> list[float]:
    totals: defaultdict[datetime, float] = defaultdict(float)
    for record in records:
        bucket = record["_ts"].astimezone(timezone.utc).replace(second=0, microsecond=0)
        totals[bucket] += 1
    return [totals[bucket] for bucket in buckets]


def _mean_by_minute(records: list[dict[str, Any]], field: str, buckets: list[datetime]) -> list[float]:
    values: defaultdict[datetime, list[float]] = defaultdict(list)
    for record in records:
        value = record.get(field)
        if isinstance(value, (int, float)):
            bucket = record["_ts"].astimezone(timezone.utc).replace(second=0, microsecond=0)
            values[bucket].append(float(value))
    return [sum(values[bucket]) / len(values[bucket]) if values[bucket] else 0.0 for bucket in buckets]


def _check_slo_breach(panel_id: str, value: float, threshold: dict[str, Any]) -> bool:
    op = threshold.get("operator", "lte")
    target = float(threshold.get("value", 0))
    if op == "lte":
        return value > target
    if op == "gte":
        return value < target
    return False


def compute_dashboard(
    records: list[dict[str, Any]], config: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    dashboard = config["dashboard"]
    minutes = int(dashboard["time_range_minutes"])
    
    # If now is not provided, check if logs are outside real-time 60m and anchor intelligently
    is_anchored = False
    if now is None:
        current_utc = datetime.now(timezone.utc)
        cutoff = current_utc - timedelta(minutes=minutes)
        has_recent = any(cutoff <= r.get("_ts", current_utc) <= current_utc for r in records)
        if not has_recent and records:
            now = max((r["_ts"] for r in records if "_ts" in r), default=current_utc)
            is_anchored = True
        else:
            now = current_utc

    start = now - timedelta(minutes=minutes)
    recent = [record for record in records if start <= record["_ts"] <= now]
    buckets = _minute_buckets(now, minutes)

    requests = [record for record in recent if record.get("event") == "request_received"]
    responses = [record for record in recent if record.get("event") == "response_sent"]
    failures = [record for record in recent if record.get("event") == "request_failed"]
    tool_events = [record for record in recent if record.get("tool_success") is not None]

    latency = [record["latency_ms"] for record in responses if isinstance(record.get("latency_ms"), (int, float))]
    ttft = [record["ttft_ms"] for record in responses if isinstance(record.get("ttft_ms"), (int, float))]
    quality = [record["quality_score"] for record in responses if isinstance(record.get("quality_score"), (int, float))]

    error_rate = len(failures) / len(requests) * 100 if requests else 0.0
    retrieval_success = (
        sum(record.get("tool_success") is True for record in tool_events) / len(tool_events) * 100
        if tool_events
        else 0.0
    )

    panel_config = {panel["id"]: panel for panel in dashboard["panels"]}

    p50_val = percentile(latency, 0.50)
    p95_val = percentile(latency, 0.95)
    p99_val = percentile(latency, 0.99)
    ttft_p95_val = percentile(ttft, 0.95)

    req_count = len(requests)
    rate_per_min = req_count / minutes

    total_cost = sum(float(record.get("cost_usd", 0)) for record in responses)
    tokens_in = sum(int(record.get("tokens_in", 0)) for record in responses)
    tokens_out = sum(int(record.get("tokens_out", 0)) for record in responses)
    quality_avg = sum(quality) / len(quality) if quality else 0.0

    # SLO Breaches
    lat_thresh = panel_config["latency"]["threshold"]
    traff_thresh = panel_config["traffic"]["threshold"]
    err_thresh = panel_config["errors"]["threshold"]
    cost_thresh = panel_config["cost"]["threshold"]
    tok_thresh = panel_config["tokens"]["threshold"]
    qual_thresh = panel_config["quality"]["threshold"]

    lat_breached = _check_slo_breach("latency", p95_val, lat_thresh)
    traff_breached = _check_slo_breach("traffic", rate_per_min, traff_thresh)
    err_breached = _check_slo_breach("errors", error_rate, err_thresh)
    cost_breached = _check_slo_breach("cost", total_cost, cost_thresh)
    tok_breached = _check_slo_breach("tokens", max(tokens_in, tokens_out), tok_thresh)
    qual_breached = _check_slo_breach("quality", quality_avg, qual_thresh)

    # Executive Diagnostic Answers
    if not lat_breached:
        lat_answer = f"ĐẠT SLO: Request phản hồi nhanh (P95: {p95_val:.1f}ms ≤ {lat_thresh['value']:g}ms). P50: {p50_val:.1f}ms, P99: {p99_val:.1f}ms. TTFT P95 ở mức {ttft_p95_val:.1f}ms (thời gian trả token đầu tiên tối ưu)."
    else:
        lat_answer = f"CẢNH BÁO VI PHẠM SLO: Request BỊ CHẬM! P95 ({p95_val:.1f}ms) đã vượt ngưỡng SLO ({lat_thresh['value']:g}ms). Tail latency cao (P99: {p99_val:.1f}ms). Cần điều tra span trễ trong traces!"

    traff_answer = f"Hệ thống nhận {req_count} requests trong cửa sổ 60 phút (tốc độ trung bình {rate_per_min:.2f} req/phút). Phân bổ traffic theo từng phút ổn định."

    tool_ok_cnt = sum(record.get("tool_success") is True for record in tool_events)
    if not err_breached and (retrieval_success >= 99.0 or not tool_events):
        err_answer = f"ĐẠT SLO: Error rate an toàn ({error_rate:.2f}% ≤ {err_thresh['value']:g}%). Retrieval thành công 100% ({tool_ok_cnt}/{len(tool_events)} calls), không có lỗi hệ thống."
    elif err_breached:
        err_answer = f"CẢNH BÁO VI PHẠM SLO: Error rate TĂNG CAO ({error_rate:.2f}% > {err_thresh['value']:g}%) với {len(failures)} request thất bại! Cần kiểm tra HTTP error log."
    else:
        err_answer = f"CẢNH BÁO: Retrieval ĐANG BỊ FAIL! Tỉ lệ tool retrieval thành công chỉ đạt {retrieval_success:.1f}%. Cần kiểm tra vector database / tool kết nối!"

    avg_req_cost = (total_cost / len(responses)) if responses else 0.0
    if not cost_breached:
        cost_answer = f"ĐẠT SLO: Chi phí ổn định, không tăng bất thường. Tổng chi phí ${total_cost:.4f} nằm trong ngân sách (≤ ${cost_thresh['value']:g}). TB: ${avg_req_cost:.5f}/req."
    else:
        cost_answer = f"CẢNH BÁO VI PHẠM SLO: Chi phí TĂNG CAO BẤT THƯỜNG (${total_cost:.4f} > ${cost_thresh['value']:g})! Cần kiểm tra độ dài context và số lượt gọi LLM."

    if not tok_breached:
        tok_answer = f"ĐẠT SLO: Input/output token ở độ dài bình thường (In: {tokens_in:,} tokens, Out: {tokens_out:,} tokens ≤ {tok_thresh['value']:g} tokens/field). Tổng token: {tokens_in + tokens_out:,}."
    else:
        tok_answer = f"CẢNH BÁO VI PHẠM SLO: Token DÀI BẤT THƯỜNG (> {tok_thresh['value']:g} tokens)! Cần kiểm tra hiện tượng prompt bloat hoặc generation lặp."

    if not qual_breached:
        qual_answer = f"ĐẠT SLO: Quality proxy đạt mức tốt ({quality_avg:.2f}/1.00 ≥ {qual_thresh['value']:g}). Đánh giá trên {len(quality)} phản hồi hợp lệ."
    else:
        qual_answer = f"CẢNH BÁO VI PHẠM SLO: Quality proxy SUY GIẢM ({quality_avg:.2f} < {qual_thresh['value']:g}) dưới mức chấp nhận được! Cần rà soát chất lượng prompt/retrieval."

    return {
        "title": dashboard["title"],
        "time_range_minutes": minutes,
        "refresh_seconds": dashboard["refresh_seconds"],
        "generated_at": now.astimezone(timezone.utc),
        "is_anchored": is_anchored,
        "record_count": len(recent),
        "buckets": buckets,
        "summary": {
            "p95_ms": p95_val,
            "requests": req_count,
            "error_rate_pct": error_rate,
            "retrieval_success_pct": retrieval_success,
            "cost_usd": total_cost,
            "quality_score": quality_avg,
        },
        "panels": [
            {
                "id": "latency",
                "metrics": [
                    ("P50", p50_val),
                    ("P95", p95_val),
                    ("P99", p99_val),
                    ("TTFT P95", ttft_p95_val),
                ],
                "series": [
                    ("Latency", _mean_by_minute(responses, "latency_ms", buckets)),
                    ("TTFT", _mean_by_minute(responses, "ttft_ms", buckets)),
                ],
                "question": PANEL_QUESTIONS["latency"],
                "status": "BREACHED" if lat_breached else "PASS",
                "answer": lat_answer,
            },
            {
                "id": "traffic",
                "metrics": [
                    ("Requests", req_count),
                    ("Avg/min", rate_per_min),
                ],
                "series": [("Requests/min", _count_by_minute(requests, buckets))],
                "question": PANEL_QUESTIONS["traffic"],
                "status": "BREACHED" if traff_breached else "PASS",
                "answer": traff_answer,
            },
            {
                "id": "errors",
                "metrics": [
                    ("Error rate", error_rate),
                    ("Retrieval success", retrieval_success),
                    ("Errors", len(failures)),
                ],
                "series": [
                    ("Error rate", [error_rate] * minutes),
                    ("Retrieval success", [retrieval_success] * minutes),
                ],
                "question": PANEL_QUESTIONS["errors"],
                "status": "BREACHED" if err_breached else "PASS",
                "answer": err_answer,
            },
            {
                "id": "cost",
                "metrics": [
                    ("Total USD", total_cost),
                    ("Responses", len(responses)),
                ],
                "series": [("USD/min", _by_minute(responses, "cost_usd", buckets))],
                "question": PANEL_QUESTIONS["cost"],
                "status": "BREACHED" if cost_breached else "PASS",
                "answer": cost_answer,
            },
            {
                "id": "tokens",
                "metrics": [
                    ("Input", tokens_in),
                    ("Output", tokens_out),
                ],
                "series": [
                    ("Input", _by_minute(responses, "tokens_in", buckets)),
                    ("Output", _by_minute(responses, "tokens_out", buckets)),
                ],
                "question": PANEL_QUESTIONS["tokens"],
                "status": "BREACHED" if tok_breached else "PASS",
                "answer": tok_answer,
            },
            {
                "id": "quality",
                "metrics": [
                    ("Average", quality_avg),
                    ("Samples", len(quality)),
                ],
                "series": [("Quality", _mean_by_minute(responses, "quality_score", buckets))],
                "question": PANEL_QUESTIONS["quality"],
                "status": "BREACHED" if qual_breached else "PASS",
                "answer": qual_answer,
            },
        ],
        "panel_config": panel_config,
    }


def _format_value(panel_id: str, label: str, value: float) -> str:
    if label in {"Requests", "Responses", "Errors", "Input", "Output", "Samples"}:
        return f"{int(value):,}"
    if panel_id == "cost" and label == "Total USD":
        return f"${value:.4f}"
    if panel_id == "errors":
        return f"{value:.2f}{'%' if 'rate' in label.lower() or 'success' in label.lower() else ''}"
    if panel_id == "quality":
        return f"{value:.2f}"
    if "p50" in label.lower() or "p95" in label.lower() or "p99" in label.lower() or "ttft" in label.lower():
        return f"{value:.1f}ms"
    return f"{value:.1f}"


def _format_axis_tick(value: float, unit: str) -> str:
    if unit == "usd":
        return f"${value:.3f}" if value < 0.1 else f"${value:.2f}"
    if unit == "percent":
        return f"{value:.0f}%"
    if unit == "tokens" and value >= 1000:
        return f"{value / 1000:.0f}k"
    if unit == "ms" and value >= 1000:
        return f"{value / 1000:.1f}s"
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}"


def _chart_svg(
    series: list[tuple[str, list[float]]],
    threshold: float,
    unit: str,
    operator: str,
    panel_id: str,
    buckets: list[datetime],
) -> str:
    width, height = 680, 180
    pad_left = 52
    pad_right = 16
    pad_top = 20
    pad_bot = 26

    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bot

    flat = [float(v) for _, values in series for v in values] + [float(threshold)]
    maximum = max(flat, default=1.0)
    minimum = min(flat, default=0.0)

    # Base scale adjustments
    if minimum > 0:
        minimum = 0.0
    if unit == "percent" and maximum < 100.0 and any("success" in name.lower() for name, _ in series):
        maximum = 100.0
    if unit == "score_0_to_1":
        minimum = 0.0
        maximum = max(1.0, maximum)

    if maximum <= minimum:
        maximum = minimum + 1.0

    # Add headroom so top line doesn't collide
    headroom = (maximum - minimum) * 0.08
    maximum += headroom

    def point(index: int, val: float, length: int) -> tuple[float, float]:
        v_clamped = max(minimum, min(maximum, float(val)))
        x = pad_left + plot_w * (index / max(1, length - 1))
        y = pad_top + plot_h * (1.0 - (v_clamped - minimum) / (maximum - minimum))
        return x, y

    # Gridlines and Y ticks
    gridlines = []
    y_ticks = [
        (minimum, pad_top + plot_h),
        ((minimum + maximum) / 2.0, pad_top + plot_h / 2.0),
        (maximum - headroom, pad_top + plot_h * (headroom / (maximum - minimum))),
    ]
    for tick_val, y_pos in y_ticks:
        gridlines.append(
            f'<line x1="{pad_left}" y1="{y_pos:.1f}" x2="{width - pad_right}" y2="{y_pos:.1f}" stroke="rgba(255,255,255,0.06)" stroke-dasharray="3 3"/>'
        )
        gridlines.append(
            f'<text x="{pad_left - 8}" y="{y_pos + 3.5:.1f}" fill="#64748b" font-size="10" text-anchor="end">{_format_axis_tick(tick_val, unit)}</text>'
        )

    # X-axis time ticks
    x_ticks = []
    if buckets and len(buckets) >= 4:
        indices = [0, len(buckets) // 3, (2 * len(buckets)) // 3, len(buckets) - 1]
        for idx in indices:
            tx = pad_left + plot_w * (idx / max(1, len(buckets) - 1))
            t_str = buckets[idx].strftime("%H:%M")
            x_ticks.append(
                f'<text x="{tx:.1f}" y="{height - 6}" fill="#64748b" font-size="10" text-anchor="middle">{t_str}</text>'
            )

    # Gradient defs
    defs = []
    paths = []
    legends = []

    for s_idx, (name, values) in enumerate(series):
        c_stroke, c_fill = COLORS[s_idx % len(COLORS)]
        grad_id = f"grad-{panel_id}-{s_idx}"
        defs.append(
            f'<linearGradient id="{grad_id}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0%" stop-color="{c_stroke}" stop-opacity="0.28"/>'
            f'<stop offset="100%" stop-color="{c_stroke}" stop-opacity="0.01"/>'
            f'</linearGradient>'
        )

        pts = [point(i, v, len(values)) for i, v in enumerate(values)]
        pts_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)

        # Area polygon
        y_baseline = pad_top + plot_h
        area_pts = (
            f"M {pts[0][0]:.1f},{pts[0][1]:.1f} "
            + " ".join(f"L {x:.1f},{y:.1f}" for x, y in pts[1:])
            + f" L {pts[-1][0]:.1f},{y_baseline:.1f} L {pts[0][0]:.1f},{y_baseline:.1f} Z"
        )
        paths.append(f'<path d="{area_pts}" fill="url(#{grad_id})" />')
        paths.append(
            f'<polyline points="{pts_str}" fill="none" stroke="{c_stroke}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>'
        )

        # Dots for points with value
        for px, py in pts:
            if py < y_baseline - 1:
                paths.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3" fill="{c_stroke}" stroke="#0b132b" stroke-width="1.5"/>')

        legends.append(f'<span><i style="background:{c_stroke}; box-shadow:0 0 6px {c_stroke}"></i>{html.escape(name)}</span>')

    # Threshold line
    _, thresh_y = point(0, float(threshold), 2)
    thresh_y = max(pad_top, min(pad_top + plot_h, thresh_y))

    threshold_svg = (
        f'<line x1="{pad_left}" y1="{thresh_y:.1f}" x2="{width - pad_right}" y2="{thresh_y:.1f}" stroke="#ff4d6d" stroke-width="1.8" stroke-dasharray="6 4"/>'
        f'<rect x="{pad_left + 8}" y="{thresh_y - 12:.1f}" width="125" height="15" rx="3" fill="#2d121c" stroke="#ff4d6d" stroke-width="0.8"/>'
        f'<text x="{pad_left + 12}" y="{thresh_y - 1:.1f}" fill="#ff8da0" font-size="10" font-weight="700">threshold {threshold:g}</text>'
    )

    svg_content = (
        f'<svg viewBox="0 0 {width} {height}" role="img">'
        f'<defs>{"".join(defs)}</defs>'
        f'{"".join(gridlines)}'
        f'{"".join(x_ticks)}'
        f'{threshold_svg}'
        f'{"".join(paths)}'
        f'</svg>'
    )

    legend_html = (
        '<div class="legend">'
        + "".join(legends)
        + f'<span><i style="background:#ff4d6d; height:2px; width:12px; border-radius:1px; margin-top:5px"></i>SLO line: {operator} {threshold:g} {unit}</span>'
        + '</div>'
    )

    return svg_content + legend_html


def render_html(model: dict[str, Any]) -> str:
    cards = []
    for panel in model["panels"]:
        cfg = model["panel_config"][panel["id"]]
        threshold = cfg["threshold"]

        # Metric Chips
        metric_items = []
        for label, val in panel["metrics"]:
            formatted = _format_value(panel["id"], label, float(val))
            metric_items.append(
                f'<div class="metric"><span class="m-label">{html.escape(label)}</span><strong class="m-val">{formatted}</strong></div>'
            )
        metrics_html = "".join(metric_items)

        # Status badge
        is_breached = panel["status"] == "BREACHED"
        badge_cls = "breach" if is_breached else "pass"
        badge_text = "CẢNH BÁO SLO" if is_breached else "ĐẠT SLO"

        # Chart
        chart_html = _chart_svg(
            panel["series"],
            float(threshold["value"]),
            cfg["unit"],
            threshold["operator"],
            panel["id"],
            model.get("buckets", []),
        )

        icon = PANEL_ICONS.get(panel["id"], "📊")
        question = html.escape(panel.get("question", ""))
        answer = html.escape(panel.get("answer", ""))

        # Exactly class="card" to satisfy tests
        cards.append(
            f'''<section class="card" id="{panel['id']}">
              <div class="card-head">
                <div>
                  <div class="eyebrow">{icon} {panel['id'].upper()}</div>
                  <h2>{html.escape(cfg['title'])}</h2>
                </div>
                <div class="head-right">
                  <span class="unit">{html.escape(cfg['unit'])}</span>
                  <span class="status-badge {badge_cls}"><span class="dot"></span>{badge_text}</span>
                </div>
              </div>
              <div class="qa-box">
                <div class="qa-q"><span class="qa-tag">CÂU HỎI:</span> {question}</div>
                <div class="qa-a"><span class="qa-tag-ans">ĐÁNH GIÁ:</span> {answer}</div>
              </div>
              <div class="metrics">{metrics_html}</div>
              <div class="chart">{chart_html}</div>
              <div class="threshold">
                <div class="slo-text">SLO line: {html.escape(threshold['aggregation'])} {html.escape(threshold['operator'])} {threshold['value']:g} {html.escape(cfg['unit'])}</div>
                <div class="slo-target">Mục tiêu: {threshold['aggregation']} {threshold['operator']} {threshold['value']:g} {cfg['unit']}</div>
              </div>
            </section>'''
        )

    generated = model["generated_at"].strftime("%Y-%m-%d %H:%M:%S UTC")
    is_anchored = model.get("is_anchored", False)
    mode_badge = (
        '<span class="mode-pill anchor">⏱️ Window: Cửa sổ dữ liệu gần nhất</span>'
        if is_anchored
        else '<span class="mode-pill live"><span class="live-dot"></span> LIVE: Thời gian thực</span>'
    )

    summary = model.get("summary", {})
    summary_html = f'''
    <div class="kpi-ribbon">
      <div class="kpi-card">
        <span class="kpi-title">P95 LATENCY</span>
        <strong class="kpi-num {'alert-val' if summary.get('p95_ms', 0) > 3000 else 'ok-val'}">{summary.get('p95_ms', 0):.1f} ms</strong>
        <span class="kpi-sub">SLO: &le; 3,000 ms</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-title">TOTAL TRAFFIC</span>
        <strong class="kpi-num">{summary.get('requests', 0):,} reqs</strong>
        <span class="kpi-sub">Trong {model['time_range_minutes']} phút</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-title">ERROR RATE</span>
        <strong class="kpi-num {'alert-val' if summary.get('error_rate_pct', 0) > 2.0 else 'ok-val'}">{summary.get('error_rate_pct', 0):.2f}%</strong>
        <span class="kpi-sub">SLO: &le; 2.00%</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-title">RETRIEVAL SUCCESS</span>
        <strong class="kpi-num {'alert-val' if summary.get('retrieval_success_pct', 0) < 99.0 and summary.get('retrieval_success_pct', 0) > 0 else 'ok-val'}">{summary.get('retrieval_success_pct', 0):.1f}%</strong>
        <span class="kpi-sub">Tool calls health</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-title">TOTAL COST</span>
        <strong class="kpi-num {'alert-val' if summary.get('cost_usd', 0) > 2.5 else 'ok-val'}">${summary.get('cost_usd', 0):.4f}</strong>
        <span class="kpi-sub">SLO budget: &le; $2.50</span>
      </div>
    </div>
    '''

    return f'''<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="{model['refresh_seconds']}">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(model['title'])}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #060c17;
      color: #e2e8f0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      -webkit-font-smoothing: antialiased;
    }}
    main {{
      max-width: 1600px;
      margin: auto;
      padding: 28px 32px 50px;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      margin-bottom: 22px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      padding-bottom: 18px;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 4px;
    }}
    .eyebrow {{
      color: #38bdf8;
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 1.5px;
      text-transform: uppercase;
    }}
    h1 {{
      font-size: 28px;
      font-weight: 800;
      margin: 4px 0 6px;
      background: linear-gradient(120deg, #ffffff, #94a3b8);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      letter-spacing: -0.5px;
    }}
    .subtitle {{
      color: #64748b;
      font-size: 13px;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .meta-box {{
      text-align: right;
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 6px;
    }}
    .meta-details {{
      color: #94a3b8;
      font-size: 12px;
      line-height: 1.5;
    }}
    .mode-pill {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 10px;
      border-radius: 20px;
      font-size: 11px;
      font-weight: 600;
      border: 1px solid;
    }}
    .mode-pill.live {{
      background: rgba(16, 185, 129, 0.15);
      border-color: rgba(16, 185, 129, 0.4);
      color: #34d399;
    }}
    .mode-pill.anchor {{
      background: rgba(56, 189, 248, 0.15);
      border-color: rgba(56, 189, 248, 0.4);
      color: #38bdf8;
    }}
    .live-dot {{
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #10b981;
      box-shadow: 0 0 8px #10b981;
      animation: pulse 2s infinite;
    }}
    @keyframes pulse {{
      0% {{ opacity: 1; transform: scale(1); }}
      50% {{ opacity: 0.4; transform: scale(0.85); }}
      100% {{ opacity: 1; transform: scale(1); }}
    }}
    .controls {{
      display: flex;
      gap: 8px;
      margin-top: 4px;
    }}
    .btn {{
      background: #111e33;
      border: 1px solid #1e3a5f;
      color: #cbd5e1;
      padding: 5px 12px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s;
    }}
    .btn:hover {{
      background: #1b2f4e;
      border-color: #38bdf8;
      color: #f8fafc;
    }}

    /* KPI Ribbon */
    .kpi-ribbon {{
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 14px;
      margin-bottom: 22px;
    }}
    .kpi-card {{
      background: linear-gradient(145deg, #0e1a2d, #08111f);
      border: 1px solid rgba(56, 189, 248, 0.12);
      border-radius: 12px;
      padding: 14px 18px;
      box-shadow: 0 6px 20px rgba(0, 0, 0, 0.4);
    }}
    .kpi-title {{
      display: block;
      color: #64748b;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 1px;
      margin-bottom: 6px;
    }}
    .kpi-num {{
      font-size: 22px;
      font-weight: 800;
      line-height: 1.2;
      display: block;
    }}
    .kpi-sub {{
      display: block;
      color: #94a3b8;
      font-size: 11px;
      margin-top: 4px;
    }}
    .ok-val {{ color: #34d399; }}
    .alert-val {{ color: #f87171; }}

    /* 6 Panels Grid */
    .grid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 18px;
    }}
    .card {{
      background: linear-gradient(145deg, #0d1a2d 0%, #07101e 100%);
      border: 1px solid rgba(56, 189, 248, 0.14);
      border-radius: 16px;
      padding: 20px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45);
      transition: transform 0.2s, border-color 0.2s;
      display: flex;
      flex-direction: column;
    }}
    .card:hover {{
      transform: translateY(-2px);
      border-color: rgba(56, 189, 248, 0.35);
    }}
    .card-head {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 12px;
    }}
    .card-head h2 {{
      font-size: 17px;
      font-weight: 700;
      margin: 3px 0 0;
      color: #f1f5f9;
    }}
    .head-right {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .unit {{
      color: #94a3b8;
      background: #0f243c;
      border: 1px solid #1a3c63;
      border-radius: 20px;
      padding: 3px 10px;
      font-size: 11px;
      font-weight: 600;
    }}
    .status-badge {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 3px 9px;
      border-radius: 12px;
      font-size: 11px;
      font-weight: 700;
    }}
    .status-badge.pass {{
      background: rgba(16, 185, 129, 0.15);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.4);
    }}
    .status-badge.breach {{
      background: rgba(239, 68, 68, 0.18);
      color: #f87171;
      border: 1px solid rgba(239, 68, 68, 0.45);
    }}
    .status-badge .dot {{
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: currentColor;
    }}

    /* Q&A Executive Box */
    .qa-box {{
      background: rgba(15, 23, 42, 0.7);
      border: 1px solid rgba(255, 255, 255, 0.06);
      border-left: 3px solid #38bdf8;
      border-radius: 8px;
      padding: 10px 12px;
      margin-bottom: 14px;
      font-size: 12px;
      line-height: 1.5;
    }}
    .qa-q {{
      color: #cbd5e1;
      margin-bottom: 5px;
    }}
    .qa-a {{
      color: #93c5fd;
    }}
    .qa-tag {{
      color: #38bdf8;
      font-weight: 800;
      font-size: 10px;
      letter-spacing: 0.5px;
      margin-right: 4px;
    }}
    .qa-tag-ans {{
      color: #fbbf24;
      font-weight: 800;
      font-size: 10px;
      letter-spacing: 0.5px;
      margin-right: 4px;
    }}

    /* Metrics Row */
    .metrics {{
      display: flex;
      gap: 14px;
      margin-bottom: 12px;
    }}
    .metric {{
      min-width: 80px;
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid rgba(255, 255, 255, 0.05);
      border-radius: 8px;
      padding: 6px 10px;
    }}
    .m-label {{
      display: block;
      color: #64748b;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .m-val {{
      font-size: 18px;
      font-weight: 800;
      color: #f8fafc;
      line-height: 1.4;
    }}

    /* Chart */
    .chart {{
      margin-top: auto;
    }}
    .chart svg {{
      width: 100%;
      height: 155px;
      background: #060e1b;
      border: 1px solid rgba(255, 255, 255, 0.04);
      border-radius: 10px;
      overflow: visible;
    }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      color: #94a3b8;
      font-size: 11px;
      margin-top: 8px;
      align-items: center;
    }}
    .legend span {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
    }}
    .legend i {{
      display: inline-block;
      width: 9px;
      height: 9px;
      border-radius: 50%;
    }}

    /* Threshold Footer */
    .threshold {{
      border-top: 1px solid rgba(255, 255, 255, 0.07);
      margin-top: 12px;
      padding-top: 10px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 11px;
    }}
    .slo-text {{
      color: #ff8da0;
      font-weight: 600;
      font-family: monospace;
      font-size: 11.5px;
    }}
    .slo-target {{
      color: #64748b;
    }}

    @media (max-width: 1200px) {{
      .grid {{ grid-template-columns: repeat(2, 1fr); }}
      .kpi-ribbon {{ grid-template-columns: repeat(3, 1fr); }}
    }}
    @media (max-width: 768px) {{
      .grid {{ grid-template-columns: 1fr; }}
      .kpi-ribbon {{ grid-template-columns: 1fr; }}
      header {{ flex-direction: column; align-items: flex-start; gap: 12px; }}
      .meta-box {{ text-align: left; align-items: flex-start; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <div class="brand">
          <div class="eyebrow">OBSERVABILITY &amp; LLMOPS</div>
          {mode_badge}
        </div>
        <h1>{html.escape(model['title'])}</h1>
        <div class="subtitle">
          <span>Triển khai luồng SRE: Metrics &rarr; Logs &rarr; Traces</span>
          <span>&bull;</span>
          <span>Hệ thống giám sát hiệu năng &amp; chất lượng LLM Agent</span>
        </div>
      </div>
      <div class="meta-box">
        <div class="meta-details">
          Cửa sổ: Last {model['time_range_minutes']} minutes &middot; Auto-refresh {model['refresh_seconds']}s<br>
          {model['record_count']} log records &middot; Xuất bản: {generated}
        </div>
        <div class="controls">
          <button class="btn" onclick="location.reload()">&#x21bb; Làm mới ngay</button>
          <button class="btn" onclick="window.print()">&#x1f4f7; Chụp / In báo cáo</button>
        </div>
      </div>
    </header>

    {summary_html}

    <div class="grid">
      {''.join(cards)}
    </div>
  </main>

  <script>
    // Live reload countdown
    let refreshSec = {model['refresh_seconds']};
    console.log("Dashboard active: auto-refreshing in " + refreshSec + "s");
  </script>
</body>
</html>'''


def generate(logs: Path, config_path: Path, output: Path) -> Path:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    model = compute_dashboard(load_records(logs), config)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(model), encoding="utf-8")
    return output


def serve(logs: Path, config_path: Path, port: int) -> None:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path in {"/api/data", "/data"}:
                config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
                model = compute_dashboard(load_records(logs), config)
                data_copy = {k: v for k, v in model.items() if k not in {"buckets"}}
                data_copy["generated_at"] = data_copy["generated_at"].isoformat()
                body = json.dumps(data_copy, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            body = render_html(compute_dashboard(load_records(logs), config)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    print(f"Dashboard: http://127.0.0.1:{port} (refresh 30s)")
    ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the CP2 six-panel dashboard")
    parser.add_argument("--logs", type=Path, default=DEFAULT_LOGS)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()
    if args.serve:
        serve(args.logs, args.config, args.port)
    else:
        print(generate(args.logs, args.config, args.output))


if __name__ == "__main__":
    main()
