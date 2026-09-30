from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from scripts.generate_dashboard import compute_dashboard, load_records, render_html

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_dashboard_renders_six_panels_from_logs(tmp_path: Path) -> None:
    now = datetime(2026, 9, 30, 3, 0, tzinfo=timezone.utc)
    log_path = tmp_path / "logs.jsonl"
    log_path.write_text(
        "\n".join(
            [
                '{"ts":"2026-09-30T02:59:00Z","event":"request_received"}',
                '{"ts":"2026-09-30T02:59:01Z","event":"response_sent","latency_ms":150,"ttft_ms":50,"cost_usd":0.002,"tokens_in":30,"tokens_out":100,"quality_score":0.9,"tool_success":true}',
            ]
        ),
        encoding="utf-8",
    )
    config = yaml.safe_load((REPO_ROOT / "config" / "dashboard.yaml").read_text(encoding="utf-8"))

    model = compute_dashboard(load_records(log_path), config, now=now)
    output = render_html(model)

    assert {panel["id"] for panel in model["panels"]} == {"latency", "traffic", "errors", "cost", "tokens", "quality"}
    assert model["record_count"] == 2
    assert output.count('class="card"') == 6
    assert "Retrieval success" in output
    assert "TTFT P95" in output
    assert "SLO line" in output
    assert "refresh 30s" in output
