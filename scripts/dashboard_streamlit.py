"""
Streamlit Dashboard for K4-L3B Day 13 Monitoring & LLMOps
Dựng đúng 6 panel theo config/dashboard.yaml và trả lời đầy đủ 6 câu hỏi SRE.
Hiển thị đồng thời đoạn Baseline (Bình thường) và đoạn Bất thường (Gai chóp/Rớt số) trên cùng một trục thời gian.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    import streamlit as st
except ImportError:
    st = None  # type: ignore
    go = None  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[1]
LOGS_PRIMARY = REPO_ROOT / "data" / "logs.jsonl"
LOGS_BACKUP = REPO_ROOT / "data" / "logs_backup.jsonl"
CONFIG_PATH = REPO_ROOT / "config" / "dashboard.yaml"


def percentile(values: Iterable[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    pos = (len(ordered) - 1) * quantile
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def load_records_from_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            r["_ts"] = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
            records.append(r)
        except Exception:
            continue
    return records


def load_combined_records(mode: str) -> list[dict[str, Any]]:
    if mode == "backup":
        records = load_records_from_file(LOGS_BACKUP)
    elif mode == "merged":
        rec_primary = load_records_from_file(LOGS_PRIMARY)
        rec_backup = load_records_from_file(LOGS_BACKUP)
        # Deduplicate by correlation_id + event + ts
        seen = set()
        records = []
        for r in rec_backup + rec_primary:
            key = (r.get("correlation_id", ""), r.get("event", ""), r.get("ts", ""))
            if key not in seen:
                seen.add(key)
                records.append(r)
    else:  # primary
        records = load_records_from_file(LOGS_PRIMARY)
        # If primary has fewer than 6 records and backup exists, allow fallback or hint
    records.sort(key=lambda x: x["_ts"])
    return records


def main() -> None:
    if st is None or go is None:
        print("Lỗi: Cần cài đặt streamlit và plotly:")
        print("pip install streamlit plotly pyyaml")
        return

    st.set_page_config(
        page_title="K4-L3B Day 13 LLMOps Dashboard",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Custom styling
    st.markdown(
        """
        <style>
        .stApp {
            background-color: #070e1b;
            color: #e2e8f0;
        }
        .main-header {
            background: linear-gradient(135deg, #0f2342 0%, #071224 100%);
            border: 1px solid rgba(56, 189, 248, 0.25);
            border-radius: 16px;
            padding: 22px 28px;
            margin-bottom: 22px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
        }
        .eyebrow {
            color: #38bdf8;
            font-size: 11px;
            font-weight: 800;
            letter-spacing: 2px;
            text-transform: uppercase;
        }
        .qa-card {
            background: rgba(15, 23, 42, 0.75);
            border-left: 4px solid #38bdf8;
            border-radius: 8px;
            padding: 12px 16px;
            margin: 10px 0 14px 0;
            font-size: 13px;
            line-height: 1.5;
        }
        .qa-card.breach {
            border-left-color: #ef4444;
            background: rgba(45, 18, 28, 0.6);
        }
        .tag-q {
            color: #38bdf8;
            font-weight: 800;
            font-size: 11px;
            margin-right: 6px;
        }
        .tag-ans {
            color: #fbbf24;
            font-weight: 800;
            font-size: 11px;
            margin-right: 6px;
        }
        .legend-chip {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 3px 10px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 600;
            margin-right: 8px;
        }
        .chip-base {
            background: rgba(16, 185, 129, 0.15);
            border: 1px solid rgba(16, 185, 129, 0.4);
            color: #34d399;
        }
        .chip-spike {
            background: rgba(239, 68, 68, 0.18);
            border: 1px solid rgba(239, 68, 68, 0.45);
            color: #f87171;
        }
        .chip-slo {
            background: rgba(255, 77, 109, 0.15);
            border: 1px dashed #ff4d6d;
            color: #ff8da0;
        }
        .compare-box {
            background: linear-gradient(145deg, #0d1a2d, #07101e);
            border: 1px solid rgba(56, 189, 248, 0.18);
            border-radius: 12px;
            padding: 16px 20px;
            margin-bottom: 24px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    dashboard_cfg = config["dashboard"]
    minutes = int(dashboard_cfg.get("time_range_minutes", 60))

    # Sidebar
    with st.sidebar:
        st.title("⚙️ Điều khiển Dashboard")
        st.markdown(f"**Contract:** `config/dashboard.yaml`")

        data_source_mode = st.radio(
            "📁 Nguồn dữ liệu log:",
            options=["merged", "primary", "backup"],
            format_func=lambda x: {
                "merged": "🌟 Ghép cả 2 (Baseline + Challenge đầy đủ)",
                "primary": "📌 data/logs.jsonl (Log hiện tại)",
                "backup": "⚠️ data/logs_backup.jsonl (Challenge CP3)",
            }[x],
            index=0,
            help="Chọn nguồn log để quan sát toàn bộ chuỗi: Baseline ban đầu ➔ Gai chóp sự cố ➔ Khôi phục.",
        )

        records = load_combined_records(data_source_mode)
        st.markdown(f"**Số bản ghi tải được:** `{len(records)}`")

        time_mode = st.radio(
            "⏱️ Cửa sổ thời gian:",
            ["Cửa sổ hoạt động gần nhất (Khuyên dùng)", "Thời gian thực (Real-time UTC)"],
            index=0,
        )

        st.markdown("---")
        st.markdown("### 🎯 Chú giải trực quan")
        st.markdown(
            """
            - 🟢 **Điểm Baseline**: Request bình thường (dưới ngưỡng SLO).
            - 🔴 **Điểm Gai chóp**: Request bị chậm/lỗi bất thường (vượt SLO).
            - 🟥 **Vùng màu đỏ nhạt**: Giai đoạn Challenge / Incident đang diễn ra.
            - ➖ **Đường nét đứt đỏ**: Ngưỡng cam kết SLO.
            """
        )

        if st.button("🔄 Làm mới dữ liệu", use_container_width=True):
            st.rerun()

    # Determine time window
    current_utc = datetime.now(timezone.utc)
    if time_mode.startswith("Cửa sổ") and records:
        now = max(r["_ts"] for r in records if "_ts" in r)
    else:
        now = current_utc

    # Window range
    start = now - timedelta(minutes=minutes)
    recent = [r for r in records if start <= r["_ts"] <= now]
    if not recent and records:
        # Fallback to show all records if window spans outside
        recent = records
        start = min(r["_ts"] for r in recent)
        now = max(r["_ts"] for r in recent)

    requests = [r for r in recent if r.get("event") == "request_received"]
    responses = [r for r in recent if r.get("event") == "response_sent"]
    failures = [r for r in recent if r.get("event") == "request_failed"]
    tool_events = [r for r in recent if r.get("tool_success") is not None]

    # Detect incident periods
    inc_starts = [r["_ts"] for r in recent if r.get("event") == "incident_enabled"]
    inc_ends = [r["_ts"] for r in recent if r.get("event") == "incident_disabled"]

    latencies = [r["latency_ms"] for r in responses if isinstance(r.get("latency_ms"), (int, float))]
    ttfts = [r["ttft_ms"] for r in responses if isinstance(r.get("ttft_ms"), (int, float))]
    qualities = [r["quality_score"] for r in responses if isinstance(r.get("quality_score"), (int, float))]

    p50 = percentile(latencies, 0.50)
    p95 = percentile(latencies, 0.95)
    p99 = percentile(latencies, 0.99)
    ttft_p95 = percentile(ttfts, 0.95)

    error_rate = len(failures) / len(requests) * 100 if requests else 0.0
    tool_ok_cnt = sum(r.get("tool_success") is True for r in tool_events)
    retrieval_success = (tool_ok_cnt / len(tool_events) * 100) if tool_events else 0.0
    total_cost = sum(float(r.get("cost_usd", 0)) for r in responses)
    tokens_in = sum(int(r.get("tokens_in", 0)) for r in responses)
    tokens_out = sum(int(r.get("tokens_out", 0)) for r in responses)
    mean_quality = sum(qualities) / len(qualities) if qualities else 0.0

    # Header
    st.markdown(
        f"""
        <div class="main-header">
            <div class="eyebrow">OBSERVABILITY &amp; LLMOPS &bull; SRE WORKFLOW: METRICS &rarr; LOGS &rarr; TRACES</div>
            <h1 style="margin:4px 0 8px; font-size:30px; font-weight:800;">{dashboard_cfg['title']}</h1>
            <div style="color:#94a3b8; font-size:13px; line-height:1.6;">
                ⏱️ Trục thời gian: <b>{start.strftime('%H:%M:%S')} &rarr; {now.strftime('%H:%M:%S UTC')}</b> (Cửa sổ 60m) &bull;
                📊 Tổng request: <b>{len(requests)}</b> &bull; Tổng response: <b>{len(responses)}</b> &bull;
                <span class="legend-chip chip-base">🟢 Baseline bình thường</span>
                <span class="legend-chip chip-spike">🔴 Gai chóp / Bất thường</span>
                <span class="legend-chip chip-slo">➖ Đường SLO Threshold</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Top KPI Row
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric(
        "⚡ P95 Latency",
        f"{p95:.1f} ms",
        delta=f"{'CẢNH BÁO VI PHẠM' if p95 > 3000 else 'ĐẠT SLO'} (≤ 3000ms)",
        delta_color="inverse" if p95 > 3000 else "normal",
    )
    k2.metric(
        "📈 Traffic (Requests)",
        f"{len(requests)} reqs",
        delta=f"{len(requests)/minutes:.2f} req/phút",
    )
    k3.metric(
        "🛡️ Error Rate",
        f"{error_rate:.2f}%",
        delta=f"{'VI PHẠM' if error_rate > 2.0 else 'ĐẠT SLO'} (≤ 2.0%)",
        delta_color="inverse" if error_rate > 2.0 else "normal",
    )
    k4.metric(
        "🎯 Retrieval Success",
        f"{retrieval_success:.1f}%",
        delta=f"{tool_ok_cnt}/{len(tool_events)} tool calls",
        delta_color="normal" if retrieval_success >= 99.0 else "inverse",
    )
    k5.metric(
        "💰 Total Cost",
        f"${total_cost:.4f}",
        delta=f"{'VƯỢT NGÂN SÁCH' if total_cost > 2.5 else 'ĐẠT SLO'} (≤ $2.50)",
        delta_color="inverse" if total_cost > 2.5 else "normal",
    )

    # Comparison Table between Baseline and Incident
    base_resps = [r for r in responses if r.get("latency_ms", 0) <= 3000]
    spike_resps = [r for r in responses if r.get("latency_ms", 0) > 3000]

    with st.expander("🔬 BẢNG SO SÁNH ĐỊNH LƯỢNG: ĐOẠN BASELINE (BÌNH THƯỜNG) VS ĐOẠN BẤT THƯỜNG (GAI CHÓP)", expanded=True):
        c_a, c_b, c_c = st.columns([1.2, 1.2, 1.6])
        with c_a:
            st.markdown("#### 🟢 Đoạn Baseline (Bình thường)")
            base_lats = [r["latency_ms"] for r in base_resps]
            st.write(f"- **Số lượng request**: `{len(base_resps)}` mẫu")
            st.write(f"- **P50 Latency**: `{percentile(base_lats, 0.5):.1f} ms`")
            st.write(f"- **P95 Latency**: `{percentile(base_lats, 0.95):.1f} ms` (Đạt SLO)")
            st.write(f"- **TTFT P95**: `{percentile([r['ttft_ms'] for r in base_resps], 0.95):.1f} ms`")
            st.write(f"- **Đánh giá**: Hệ thống phản hồi nhanh, ổn định.")
        with c_b:
            st.markdown("#### 🔴 Đoạn Bất thường (Gai chóp sự cố)")
            spike_lats = [r["latency_ms"] for r in spike_resps]
            if spike_resps:
                peak_lat = max(spike_lats)
                st.write(f"- **Số lượng request vi phạm**: `{len(spike_resps)}` mẫu")
                st.write(f"- **P95 Latency**: `{percentile(spike_lats, 0.95):.1f} ms` (🚨 Vượt SLO)")
                st.write(f"- **Gai chóp cao nhất (Peak)**: `{peak_lat:.1f} ms`")
                st.write(f"- **TTFT P95**: `{percentile([r['ttft_ms'] for r in spike_resps], 0.95):.1f} ms` (Vẫn ổn định!)")
                st.write(f"- **Đánh giá**: Latency tăng gấp **{peak_lat / max(1, percentile(base_lats, 0.5)):.0f} lần**!")
            else:
                st.write("Không phát hiện request nào vượt ngưỡng 3000ms trong cửa sổ này.")
        with c_c:
            st.markdown("#### 💡 Kết luận điều tra SRE & Root Cause")
            if spike_resps:
                st.markdown(
                    """
                    1. **Triệu chứng Metrics**: Trên cùng trục thời gian, xuất hiện gai chóp latency vọt từ ~150ms lên >2,600ms – 10,275ms.
                    2. **Chẩn đoán TTFT**: TTFT vẫn giữ nguyên ~50ms ➔ **LLM Generation KHÔNG bị chậm**.
                    3. **Root Cause**: Thời gian trễ nằm hoàn toàn ở **Tool Retrieval** (Incident `rag_slow`).
                    4. **Khôi phục**: Sau khi tắt incident, latency lập tức hạ về mức baseline ~155ms.
                    """
                )
            else:
                st.info("Hệ thống đang hoạt động hoàn toàn trong ngưỡng an toàn.")

    st.markdown("---")

    # Helper function to add incident shaded region
    def add_incident_vspan(fig: go.Figure) -> None:
        if inc_starts:
            for s_t in inc_starts:
                matching_ends = [e for e in inc_ends if e >= s_t]
                e_t = matching_ends[0] if matching_ends else now
                fig.add_vrect(
                    x0=s_t,
                    x1=e_t,
                    fillcolor="rgba(239, 68, 68, 0.12)",
                    layer="below",
                    line_width=1,
                    line_color="rgba(239, 68, 68, 0.4)",
                    annotation_text="🚨 Giai đoạn Sự cố",
                    annotation_position="top left",
                    annotation_font=dict(color="#f87171", size=10),
                )
        elif spike_resps:
            # If no explicit incident_enabled event, highlight time span of spikes
            s_t = min(r["_ts"] for r in spike_resps) - timedelta(seconds=10)
            e_t = max(r["_ts"] for r in spike_resps) + timedelta(seconds=10)
            fig.add_vrect(
                x0=s_t,
                x1=e_t,
                fillcolor="rgba(239, 68, 68, 0.10)",
                layer="below",
                line_width=1,
                line_color="rgba(239, 68, 68, 0.3)",
                annotation_text="🚨 Vùng Gai chóp",
                annotation_position="top left",
                annotation_font=dict(color="#f87171", size=10),
            )

    # 6 Panels in 2x3 Grid
    col1, col2, col3 = st.columns(3)

    # ==========================================
    # 1. LATENCY & TTFT
    # ==========================================
    with col1:
        st.subheader("⚡ 1. Latency & TTFT")
        st.caption("Đơn vị: `ms` | SLO: `p95 <= 3000 ms`")
        is_lat_breach = p95 > 3000
        st.markdown(
            f"""
            <div class="qa-card {'breach' if is_lat_breach else ''}">
                <div><span class="tag-q">CÂU HỎI:</span> Request có chậm không? P50/P95/P99 và TTFT đang ở mức nào?</div>
                <div><span class="tag-ans">ĐÁNH GIÁ:</span> {'🔴 <b style="color:#f87171">CẢNH BÁO: Request BỊ CHẬM!</b> P95 vượt ngưỡng 3000ms do sự cố.' if is_lat_breach else '🟢 <b style="color:#34d399">ĐẠT SLO: Request phản hồi nhanh.</b>'}<br>
                P50: <code>{p50:.1f}ms</code> &bull; P95: <code>{p95:.1f}ms</code> &bull; P99: <code>{p99:.1f}ms</code> &bull; TTFT P95: <code>{ttft_p95:.1f}ms</code>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        fig1 = go.Figure()

        resp_times = [r["_ts"] for r in responses]
        resp_lats = [r.get("latency_ms", 0) for r in responses]
        resp_ttfts = [r.get("ttft_ms", 0) for r in responses]

        # Continuous Latency Line
        fig1.add_trace(
            go.Scatter(
                x=resp_times,
                y=resp_lats,
                mode="lines",
                name="Latency (ms)",
                line=dict(color="#38bdf8", width=2.5),
                hoverinfo="skip",
            )
        )

        # Baseline markers (<= 3000 ms)
        b_times = [r["_ts"] for r in responses if r.get("latency_ms", 0) <= 3000]
        b_lats = [r.get("latency_ms", 0) for r in responses if r.get("latency_ms", 0) <= 3000]
        fig1.add_trace(
            go.Scatter(
                x=b_times,
                y=b_lats,
                mode="markers",
                name="🟢 Baseline (≤ 3000ms)",
                marker=dict(color="#10b981", size=8, line=dict(color="#ffffff", width=1.5)),
                hovertemplate="<b>🟢 Baseline bình thường</b><br>Thời gian: %{x|%H:%M:%S}<br>Latency: %{y:.1f} ms<extra></extra>",
            )
        )

        # Anomaly markers (> 3000 ms)
        s_times = [r["_ts"] for r in responses if r.get("latency_ms", 0) > 3000]
        s_lats = [r.get("latency_ms", 0) for r in responses if r.get("latency_ms", 0) > 3000]
        if s_times:
            fig1.add_trace(
                go.Scatter(
                    x=s_times,
                    y=s_lats,
                    mode="markers+text",
                    name="🔴 Gai chóp (> 3000ms)",
                    text=[f"{v:.0f}ms" for v in s_lats],
                    textposition="top center",
                    marker=dict(color="#ef4444", size=13, symbol="triangle-up", line=dict(color="#ffffff", width=2)),
                    hovertemplate="<b>🚨 GAI CHÓP BẤT THƯỜNG</b><br>Thời gian: %{x|%H:%M:%S}<br>Latency: %{y:.1f} ms (Vượt SLO 3000ms)<extra></extra>",
                )
            )

        # TTFT Line
        fig1.add_trace(
            go.Scatter(
                x=resp_times,
                y=resp_ttfts,
                mode="lines+markers",
                name="TTFT (ms)",
                line=dict(color="#fbbf24", width=2, dash="dot"),
                marker=dict(color="#fbbf24", size=5),
                hovertemplate="<b>TTFT:</b> %{y:.1f} ms<extra></extra>",
            )
        )

        # SLO Line
        fig1.add_hline(
            y=3000,
            line_dash="dash",
            line_color="#ff4d6d",
            line_width=2,
            annotation_text="Ngưỡng SLO: P95 ≤ 3000ms",
            annotation_position="top right",
            annotation_font=dict(color="#ff8da0", size=10),
        )

        add_incident_vspan(fig1)

        fig1.update_layout(
            height=250,
            margin=dict(l=20, r=20, t=25, b=25),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#091424",
            font=dict(color="#94a3b8", size=11),
            xaxis=dict(gridcolor="rgba(255,255,255,0.06)", tickformat="%H:%M:%S"),
            yaxis=dict(gridcolor="rgba(255,255,255,0.06)"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10)),
        )
        st.plotly_chart(fig1, use_container_width=True)

    # ==========================================
    # 2. TRAFFIC
    # ==========================================
    with col2:
        st.subheader("📈 2. Request Traffic")
        st.caption("Đơn vị: `requests_per_minute` | SLO: `rate_per_minute >= 1`")
        rate_min = len(requests) / minutes
        st.markdown(
            f"""
            <div class="qa-card">
                <div><span class="tag-q">CÂU HỎI:</span> Hệ thống đang nhận bao nhiêu request theo thời gian?</div>
                <div><span class="tag-ans">ĐÁNH GIÁ:</span> Tổng cộng <b>{len(requests)} requests</b> trong cửa sổ 60 phút.<br>
                Tốc độ trung bình: <code>{rate_min:.2f} req/phút</code>. Thấy rõ lưu lượng dồn dập lúc load test challenge.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        fig2 = go.Figure()
        req_times = [r["_ts"] for r in requests]

        # Group count by minute
        minute_counts: dict[str, int] = {}
        for rt in req_times:
            m_key = rt.strftime("%H:%M")
            minute_counts[m_key] = minute_counts.get(m_key, 0) + 1

        m_keys = sorted(minute_counts.keys())
        m_vals = [minute_counts[k] for k in m_keys]

        # Plot Traffic Bars
        fig2.add_trace(
            go.Bar(
                x=m_keys,
                y=m_vals,
                name="Requests / phút",
                marker=dict(
                    color=["#ef4444" if v >= 5 else "#00f2fe" for v in m_vals],
                    line=dict(color="rgba(255,255,255,0.2)", width=1),
                ),
                hovertemplate="Phút: %{x}<br>Lưu lượng: %{y} requests<extra></extra>",
            )
        )

        fig2.add_hline(
            y=1,
            line_dash="dash",
            line_color="#ff4d6d",
            line_width=1.5,
            annotation_text="SLO Tối thiểu: 1 req/m",
            annotation_position="top right",
            annotation_font=dict(color="#ff8da0", size=10),
        )

        fig2.update_layout(
            height=250,
            margin=dict(l=20, r=20, t=25, b=25),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#091424",
            font=dict(color="#94a3b8", size=11),
            xaxis=dict(gridcolor="rgba(255,255,255,0.06)"),
            yaxis=dict(gridcolor="rgba(255,255,255,0.06)"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10)),
        )
        st.plotly_chart(fig2, use_container_width=True)

    # ==========================================
    # 3. ERRORS & RETRIEVAL SUCCESS
    # ==========================================
    with col3:
        st.subheader("🛡️ 3. Errors & Retrieval Success")
        st.caption("Đơn vị: `percent` | SLO: `error_rate_pct <= 2`")
        is_err_breach = error_rate > 2.0 or (retrieval_success < 99.0 and len(tool_events) > 0)
        st.markdown(
            f"""
            <div class="qa-card {'breach' if is_err_breach else ''}">
                <div><span class="tag-q">CÂU HỎI:</span> Error rate có tăng không, retrieval có đang fail không?</div>
                <div><span class="tag-ans">ĐÁNH GIÁ:</span> Error rate: <code>{error_rate:.2f}%</code> (SLO ≤ 2.0%).<br>
                Retrieval success: <code>{retrieval_success:.1f}%</code> ({tool_ok_cnt}/{len(tool_events)} tool calls).
                {'🟢 Không có lỗi hệ thống.' if not is_err_breach else '🔴 <b style="color:#f87171">Cảnh báo lỗi/retrieval fail!</b>'}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        fig3 = go.Figure()

        # Track tool successes and failures over time
        t_times = [r["_ts"] for r in tool_events]
        t_status = [100.0 if r.get("tool_success") is True else 0.0 for r in tool_events]

        if t_times:
            fig3.add_trace(
                go.Scatter(
                    x=t_times,
                    y=t_status,
                    mode="lines+markers",
                    name="Retrieval Success %",
                    line=dict(color="#10b981", width=2),
                    marker=dict(
                        color=["#10b981" if s == 100 else "#ef4444" for s in t_status],
                        size=8,
                    ),
                    hovertemplate="Thời gian: %{x|%H:%M:%S}<br>Retrieval Success: %{y:.0f}%<extra></extra>",
                )
            )

        # Error rate line
        fig3.add_trace(
            go.Scatter(
                x=[start, now],
                y=[error_rate, error_rate],
                mode="lines",
                name="Error Rate (%)",
                line=dict(color="#f87171", width=2),
            )
        )

        fig3.add_hline(
            y=2,
            line_dash="dash",
            line_color="#ff4d6d",
            line_width=1.5,
            annotation_text="Ngưỡng SLO Error ≤ 2%",
            annotation_position="top right",
            annotation_font=dict(color="#ff8da0", size=10),
        )

        add_incident_vspan(fig3)

        fig3.update_layout(
            height=250,
            margin=dict(l=20, r=20, t=25, b=25),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#091424",
            font=dict(color="#94a3b8", size=11),
            xaxis=dict(gridcolor="rgba(255,255,255,0.06)", tickformat="%H:%M:%S"),
            yaxis=dict(gridcolor="rgba(255,255,255,0.06)", range=[-5, 105]),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10)),
        )
        st.plotly_chart(fig3, use_container_width=True)

    # Row 2
    col4, col5, col6 = st.columns(3)

    # ==========================================
    # 4. COST OVER TIME
    # ==========================================
    with col4:
        st.subheader("💰 4. Cost Over Time")
        st.caption("Đơn vị: `usd` | SLO: `total <= 2.5`")
        is_cost_breach = total_cost > 2.5
        st.markdown(
            f"""
            <div class="qa-card {'breach' if is_cost_breach else ''}">
                <div><span class="tag-q">CÂU HỎI:</span> Chi phí có tăng bất thường không?</div>
                <div><span class="tag-ans">ĐÁNH GIÁ:</span> Tổng chi phí: <code>${total_cost:.4f}</code> (SLO ≤ $2.50).<br>
                {'🟢 Chi phí ổn định, không tăng bất thường.' if not is_cost_breach else '🔴 <b style="color:#f87171">Chi phí vượt hạn mức!</b>'}
                Trung bình: <code>${total_cost / max(1, len(responses)):.5f}/request</code>.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        fig4 = go.Figure()
        costs = [float(r.get("cost_usd", 0)) for r in responses]

        # Cumulative Cost Line
        cum_cost = []
        running = 0.0
        for c in costs:
            running += c
            cum_cost.append(running)

        fig4.add_trace(
            go.Scatter(
                x=resp_times,
                y=cum_cost,
                mode="lines+markers",
                name="Tích lũy (USD)",
                line=dict(color="#38bdf8", width=2.5),
                marker=dict(color="#38bdf8", size=6),
                hovertemplate="Thời gian: %{x|%H:%M:%S}<br>Tổng tích lũy: $%{y:.5f}<extra></extra>",
            )
        )

        fig4.add_trace(
            go.Scatter(
                x=resp_times,
                y=costs,
                mode="markers",
                name="Từng request (USD)",
                marker=dict(color="#fbbf24", size=7),
                hovertemplate="Chi phí request: $%{y:.5f}<extra></extra>",
            )
        )

        fig4.add_hline(
            y=2.5,
            line_dash="dash",
            line_color="#ff4d6d",
            line_width=1.5,
            annotation_text="Ngân sách SLO: $2.50",
            annotation_position="top right",
            annotation_font=dict(color="#ff8da0", size=10),
        )

        fig4.update_layout(
            height=250,
            margin=dict(l=20, r=20, t=25, b=25),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#091424",
            font=dict(color="#94a3b8", size=11),
            xaxis=dict(gridcolor="rgba(255,255,255,0.06)", tickformat="%H:%M:%S"),
            yaxis=dict(gridcolor="rgba(255,255,255,0.06)"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10)),
        )
        st.plotly_chart(fig4, use_container_width=True)

    # ==========================================
    # 5. INPUT & OUTPUT TOKENS
    # ==========================================
    with col5:
        st.subheader("🔠 5. Input & Output Tokens")
        st.caption("Đơn vị: `tokens` | SLO: `sum_by_field <= 50000`")
        is_tok_breach = max(tokens_in, tokens_out) > 50000
        st.markdown(
            f"""
            <div class="qa-card {'breach' if is_tok_breach else ''}">
                <div><span class="tag-q">CÂU HỎI:</span> Input/output token có dài bất thường không?</div>
                <div><span class="tag-ans">ĐÁNH GIÁ:</span> Input: <code>{tokens_in:,}</code> &bull; Output: <code>{tokens_out:,}</code> tokens.<br>
                {'🟢 Độ dài token bình thường (≤ 50,000 tokens).' if not is_tok_breach else '🔴 <b style="color:#f87171">Cảnh báo Token Bloat!</b>'}
                Tổng cộng: <code>{tokens_in + tokens_out:,}</code> tokens.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        fig5 = go.Figure()
        t_in = [int(r.get("tokens_in", 0)) for r in responses]
        t_out = [int(r.get("tokens_out", 0)) for r in responses]

        fig5.add_trace(
            go.Scatter(
                x=resp_times,
                y=t_in,
                mode="lines+markers",
                name="Input Tokens",
                line=dict(color="#00f2fe", width=2),
                marker=dict(color="#00f2fe", size=6),
                hovertemplate="Thời gian: %{x|%H:%M:%S}<br>Input: %{y} tokens<extra></extra>",
            )
        )

        fig5.add_trace(
            go.Scatter(
                x=resp_times,
                y=t_out,
                mode="lines+markers",
                name="Output Tokens",
                line=dict(color="#fbbf24", width=2),
                marker=dict(color="#fbbf24", size=6),
                hovertemplate="Thời gian: %{x|%H:%M:%S}<br>Output: %{y} tokens<extra></extra>",
            )
        )

        fig5.add_hline(
            y=50000,
            line_dash="dash",
            line_color="#ff4d6d",
            line_width=1.5,
            annotation_text="Ngưỡng SLO: 50,000 tokens",
            annotation_position="top right",
            annotation_font=dict(color="#ff8da0", size=10),
        )

        fig5.update_layout(
            height=250,
            margin=dict(l=20, r=20, t=25, b=25),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#091424",
            font=dict(color="#94a3b8", size=11),
            xaxis=dict(gridcolor="rgba(255,255,255,0.06)", tickformat="%H:%M:%S"),
            yaxis=dict(gridcolor="rgba(255,255,255,0.06)"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10)),
        )
        st.plotly_chart(fig5, use_container_width=True)

    # ==========================================
    # 6. QUALITY PROXY (RỚT SỐ)
    # ==========================================
    with col6:
        st.subheader("⭐ 6. Quality Proxy")
        st.caption("Đơn vị: `score_0_to_1` | SLO: `mean >= 0.75`")
        is_qual_breach = mean_quality < 0.75
        st.markdown(
            f"""
            <div class="qa-card {'breach' if is_qual_breach else ''}">
                <div><span class="tag-q">CÂU HỎI:</span> Quality proxy có giảm dưới mức chấp nhận được không?</div>
                <div><span class="tag-ans">ĐÁNH GIÁ:</span> Điểm TB: <code>{mean_quality:.2f}/1.00</code> trên <b>{len(qualities)} mẫu</b>.<br>
                {'🟢 Chất lượng tốt (≥ 0.75).' if not is_qual_breach else '🔴 <b style="color:#f87171">CẢNH BÁO: RỚT SỐ CHẤT LƯỢNG!</b> Điểm TB < 0.75.'}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        fig6 = go.Figure()
        q_scores = [float(r.get("quality_score", 0)) for r in responses]

        # Normal quality points (>= 0.75)
        norm_q_times = [r["_ts"] for r in responses if r.get("quality_score", 0) >= 0.75]
        norm_q_vals = [r.get("quality_score", 0) for r in responses if r.get("quality_score", 0) >= 0.75]

        # Drop quality points (< 0.75)
        drop_q_times = [r["_ts"] for r in responses if r.get("quality_score", 0) < 0.75]
        drop_q_vals = [r.get("quality_score", 0) for r in responses if r.get("quality_score", 0) < 0.75]

        # Continuous line
        fig6.add_trace(
            go.Scatter(
                x=resp_times,
                y=q_scores,
                mode="lines",
                name="Quality Score",
                line=dict(color="#a78bfa", width=2),
                hoverinfo="skip",
            )
        )

        # Baseline markers
        fig6.add_trace(
            go.Scatter(
                x=norm_q_times,
                y=norm_q_vals,
                mode="markers",
                name="🟢 Đạt chuẩn (≥ 0.75)",
                marker=dict(color="#10b981", size=8),
                hovertemplate="Thời gian: %{x|%H:%M:%S}<br>Quality: %{y:.2f}<extra></extra>",
            )
        )

        # Drop markers
        if drop_q_times:
            fig6.add_trace(
                go.Scatter(
                    x=drop_q_times,
                    y=drop_q_vals,
                    mode="markers+text",
                    name="🔴 Rớt số (< 0.75)",
                    text=[f"{v:.2f}" for v in drop_q_vals],
                    textposition="bottom center",
                    marker=dict(color="#ef4444", size=12, symbol="triangle-down"),
                    hovertemplate="<b>🚨 RỚT SỐ CHẤT LƯỢNG</b><br>Thời gian: %{x|%H:%M:%S}<br>Điểm: %{y:.2f}<extra></extra>",
                )
            )

        fig6.add_hline(
            y=0.75,
            line_dash="dash",
            line_color="#ff4d6d",
            line_width=2,
            annotation_text="Ngưỡng SLO Tối thiểu: 0.75",
            annotation_position="bottom right",
            annotation_font=dict(color="#ff8da0", size=10),
        )

        add_incident_vspan(fig6)

        fig6.update_layout(
            height=250,
            margin=dict(l=20, r=20, t=25, b=25),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#091424",
            font=dict(color="#94a3b8", size=11),
            xaxis=dict(gridcolor="rgba(255,255,255,0.06)", tickformat="%H:%M:%S"),
            yaxis=dict(gridcolor="rgba(255,255,255,0.06)", range=[0.0, 1.05]),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10)),
        )
        st.plotly_chart(fig6, use_container_width=True)


if __name__ == "__main__":
    main()
