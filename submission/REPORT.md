# Báo cáo cá nhân — K4-L3B Day 13 Monitoring & LLMOps

> Mỗi học viên hoàn thiện một file duy nhất này. Khi dẫn evidence, dùng đường dẫn tương đối, ví dụ `evidence/07-trace-waterfall.png`.

## 1. Thông tin học viên

- **Họ và tên:Nguyễn Gia Khánh**
- **MSSV:2A202602851**
- **Lớp:** K4-L3B
- **Repository URL:https://github.com/Khanh-AI-Developer/K4-L3B-Day13-NguyenGiaKhanh-2A202602851-Monitoring-LLMOps**
- **Commit SHA cuối:**
- **Challenge ID:** `day13-k4-l3b-monitoring-llmops-v1`
- **Tên project Langfuse cá nhân:** `day13-k4-l3b-2A202602851`

## 2. Evidence index

Điền đúng đường dẫn tới evidence thực tế. Có thể đổi tên hoặc dùng nhiều ảnh nếu cần.

| Evidence | Đường dẫn |
|---|---|
| Pytest cuối | `evidence/01-pytest.png` |
| Log validator | `evidence/02-log-validator.png` |
| Dashboard validator | `evidence/03-dashboard-validator.png` |
| Structured log | `evidence/04-structured-log.png` |
| PII redaction | `evidence/05-pii-redaction.png` |
| Trace list | `evidence/06-trace-list.png` |
| Trace waterfall | `evidence/07-trace-waterfall.png` |
| Trace metadata | `evidence/08-trace-metadata.png` |
| Prompt versions | `evidence/09-prompt-versions.png` |
| Prompt rollback | `evidence/10-prompt-rollback.png` |
| Dashboard runtime | `evidence/11-dashboard-overview.png` |
| Incident metric | `evidence/12-incident-metric.png` |
| Incident log | `evidence/13-incident-log.png` |
| Incident trace | `evidence/14-incident-trace.png` |

## 3. Kết quả kỹ thuật

| Nội dung | Baseline | Kết quả cuối | Nhận xét |
|---|---|---|---|
| `validate_logs.py` | 30/100 | 100/100 | Đạt CP1; 0 lỗi schema/enrichment, 11 correlation ID duy nhất, 0 PII leak |
| `validate_dashboard.py` | Chưa đo | 6/6 panel hợp lệ | Dashboard runtime đọc `data/logs.jsonl`, có 60 phút, refresh 30 giây, unit và threshold |
| `pytest` | | 27 passed | Toàn bộ public test và test CP1/CP2 đều đạt |
| Số traces hợp lệ | 0 | 15 | Thuộc project cá nhân; mỗi trace có agent, retrieval và generation |
| Số PII leak | 0 | 0 | Kiểm tra thêm email, điện thoại VN, CCCD và thẻ trong request thử nghiệm |
| Latency P95 / TTFT P95 | | 2142,75 ms / 51,75 ms | P95 latency gồm cold-start fetch managed prompt nhưng vẫn dưới SLO 3000 ms |
| Retrieval success rate | | 100% | Tính trên mọi event có `tool_success` trong workload CP2 |

## 4. Logging và PII

- **Cách tạo/nhận và truyền correlation ID:** Middleware xóa context cũ ở đầu mỗi request, chỉ nhận `x-request-id` đúng format `req-<8-hex>`, nếu thiếu hoặc sai thì sinh ID mới từ UUID. ID được bind vào structlog context, lưu trong `request.state`, truyền vào agent và trả lại qua response header cùng `x-response-time-ms`.
- **Các metadata được ghi vào structured log:** `correlation_id`, SHA-256 rút gọn của `user_id` trong `user_id_hash`, `session_id`, `feature`, `model` và `env` được bind trước event `request_received` và tiếp tục xuất hiện ở event `response_sent`.
- **Cách bảo đảm PII được scrub trước khi ghi:** `scrub_event` duyệt đệ quy các chuỗi trong event dictionary và chạy trước `JsonlFileProcessor`/`JSONRenderer`; do đó email, số điện thoại Việt Nam, CCCD và số thẻ được thay bằng nhãn `[REDACTED_*]` trước khi serialize hoặc ghi file.
- **Cách kiểm chứng kết quả:** Lưu baseline CP0 ra `../logs-cp0-baseline.jsonl`, restart API, chạy workload 10 request và một request chứa đủ bốn loại PII. `python scripts/validate_logs.py` đạt 100/100 với 26 log records, 11 correlation IDs, 0 thiếu metadata và 0 PII leak; response thử nghiệm trả `x-request-id=req-a1b2c3d4` và `x-response-time-ms=154.62`. `python -m pytest -q` đạt 27 tests.

## 5. Tracing và prompt versioning

- **Cách xác nhận traces do chính tôi tạo trong project cá nhân:** Xác thực API bằng key trong `.env` với project `day13-k4-l3b-2A202602851`, chạy workload của repo rồi truy vấn `GET /api/public/v2/observations`. Kết quả có 15 trace mới với session riêng của workload CP2; các trace ID đại diện được ghi trực tiếp bên dưới.
- **Cấu trúc root/retrieval/generation observations:** Trace `day13-agent-request` chứa `lab-agent-run` loại AGENT, child `retrieval` loại RETRIEVER và child `generation` loại GENERATION. Hai child tắt capture input/output; generation chỉ ghi model, token usage, cost, TTFT metadata và liên kết managed prompt.
- **Cách nối trace với log:** `correlation_id` được bind ở middleware, ghi vào structured log và truyền vào trace metadata. Ví dụ trace candidate `8e3e0de6e10460a3e92d8a31e60753d3` nối với log bằng `req-c2e998db`.
- **Prompt name:** `day13-chat`, loại text, giữ đủ `{{feature}}`, `{{docs}}`, `{{message}}`.
- **Version/label baseline:** v1; trạng thái cuối có label `baseline` và `production`.
- **Version/label candidate:** v2; trạng thái cuối có label `candidate` và `latest`; thay đổi nhỏ là yêu cầu trả lời ngắn gọn.
- **Trace ID của mỗi version:** baseline v1 `8b01cae4fd9949fe444f5c6ede3bb262`; candidate v2 `8e3e0de6e10460a3e92d8a31e60753d3`.
- **Cách promote và rollback `production`:** Dùng endpoint chính thức `PATCH /api/public/v2/prompts/{name}/versions/{version}`. Promote chuyển `production` sang v2 và được xác nhận bởi trace `b0a1f2e1236e80f773f3f116a64d1778`; rollback chuyển `production` về v1 và được xác nhận bởi trace `86e150a7ce5d4ada090ae25b695d76c7`. Trạng thái cuối là production ở v1.

## 6. Dashboard, SLO và alerts

- **Dashboard và sáu panel:** `scripts/generate_dashboard.py` đọc log trong 60 phút gần nhất và dựng Latency/TTFT, Traffic, Errors/Retrieval success, Cost, Tokens và Quality. Dashboard refresh 30 giây khi serve, hiển thị unit và SLO/threshold line cho từng panel; validator đạt 6/6.
- **SLO và lý do chọn:** 99,5% request trong cửa sổ 28 ngày phải có `response_sent` và `latency_ms <= 3000`. Baseline thường khoảng 150–155 ms; ngưỡng 3000 ms chừa headroom cho network/cold-start managed prompt nhưng vẫn phát hiện tail latency hoặc retrieval chậm.
- **Cách tính error budget:** Error budget là `100% - 99,5% = 0,5%`. Với 10.000 request trong 28 ngày, số request được phép lỗi hoặc chậm quá 3000 ms là `10.000 × 0,005 = 50`.
- **Ba alert và runbook tương ứng:** `HighLatencyP95` (`p95 > 3000 ms` trong 5m), `ErrorOrRetrievalDegradation` (error rate >2% hoặc retrieval success <90% trong 5m), và `LowAnswerQuality` (quality trung bình <0,75 trong 10m). Cả ba gửi Slack `#k4-l3b-alerts`, owner `student-2A202602851`, có runbook Metrics → Logs → Traces và mitigation trong `docs/alerts.md`.

> Ví dụ cách viết error budget: "SLO 99.5% trong 28 ngày nghĩa là error budget 0.5%. Nếu workload có 10,000 request thì tối đa 50 request được phép lỗi hoặc chậm hơn ngưỡng SLO."

## 7. Điều tra challenge

- **Challenge ID:** `day13-k4-l3b-monitoring-llmops-v1` (cohort K4, affected feature `monitoring`, threshold 2000 ms).
- **Khoảng thời gian điều tra:** `2026-09-30T04:15:31Z`–`2026-09-30T04:16:06Z`; năm response challenge hoàn tất từ `04:15:41Z` đến `04:16:06Z`.
- **Triệu chứng từ metrics:** 5/5 request vượt 2000 ms; latency P50 = 7677 ms, P95/P99 = 10275 ms, trong khi TTFT P95 = 50 ms, error breakdown rỗng, quality trung bình = 0,84 và tổng cost = $0,0096. Các request đầu còn bị cộng thêm managed-prompt network timeout, vì vậy request sạch gần cuối được chọn để khoanh vùng incident chính thức.
- **Log line và correlation ID liên quan:** Event `response_sent` lúc `2026-09-30T04:16:06.037787Z`, `correlation_id=req-789202b6`, session `k4-l3b-challenge-s04`, feature `monitoring`, `latency_ms=2652`, `ttft_ms=50`, `tool_name=retrieval`, `tool_success=true`, cost `$0.001641`, quality `0.9`.
- **Trace ID và span gây ảnh hưởng:** Trace `d4110226d50f8a948a4a034f536443a6` có root `lab-agent-run` 2653 ms; child `retrieval` 2501 ms và child `generation` chỉ 151 ms. Trace metadata chứa cùng `correlation_id=req-789202b6`, prompt `day13-chat` production v1.
- **Root cause:** Incident `rag_slow` chèn khoảng 2,5 giây delay trong retrieval. Bằng chứng định lượng là retrieval chiếm khoảng 94,3% thời gian root span, trong khi generation/TTFT, token, cost, quality và error rate không cho thấy LLM là nguồn chậm.
- **Fix action:** Tắt `rag_slow` qua control endpoint và xác nhận toàn bộ incident flags về false. Chạy lại cùng loại request sau mitigation cho kết quả app latency 155 ms (`correlation_id=req-284276db`), giảm khoảng 94,2% so với request điều tra 2652 ms.
- **Preventive measure:** Duy trì alert `HighLatencyP95` trên 3000 ms trong 5 phút và alert retrieval success; runbook bắt buộc đi Metrics → log/correlation ID → trace waterfall. Bổ sung timeout/circuit breaker và latency budget riêng cho retrieval, theo dõi P95 span retrieval, cache/fallback context an toàn, đồng thời tách cảnh báo prompt-fetch timeout để tránh nhiễu khi điều tra latency.

> Gợi ý cách viết ngắn, không thay cho evidence thực tế: "Metric cho thấy `[latency/error/cost/quality]` bất thường trong `[khoảng thời gian]`. Log line `[event]` có `correlation_id=[...]` đại diện cho request bị ảnh hưởng. Trace cùng `correlation_id` cho thấy span `[retrieval/generation/prompt/tool]` có dấu hiệu `[chậm/lỗi/token tăng]`. Root cause là `[nguyên nhân suy ra từ evidence]`. Fix action là `[hành động khôi phục]`; preventive measure là `[alert/runbook/test/guardrail để ngăn tái diễn]`."

## 8. Giải thích và tự đánh giá

- **Một quyết định kỹ thuật quan trọng và lý do:** Dùng `@observe` trực tiếp trên `retrieve` và `FakeLLM.generate` để SDK tự duy trì quan hệ parent-child, đồng thời đặt `capture_input=False` và `capture_output=False` để không đưa câu hỏi có PII hoặc prompt đã compile lên trace.
- **Một lỗi/blocker đã gặp:** `.env` ban đầu trỏ tới US region nên Langfuse trả 401; một lần exporter trace rollback cũng bị timeout.
- **Cách tìm nguyên nhân và xử lý:** Kiểm tra host/prefix key mà không in secret, xác thực thành công với EU/default endpoint rồi sửa `LANGFUSE_BASE_URL`. Với exporter timeout, chạy lại observation và gọi `flush()` đồng bộ trước khi thoát, sau đó xác minh bằng Observations API v2.
- **Cách hiểu luồng Metrics → Logs → Traces:** Metrics cho biết triệu chứng và khoảng thời gian; log tìm request cụ thể cùng `correlation_id`; trace cùng ID chỉ ra retrieval hay generation gây chậm/lỗi.
- **Vai trò của prompt version, token/cost, SLO hoặc rollback trong vận hành LLM:** Prompt version/label cho phép promote và rollback không cần deploy code; token/cost giúp phát hiện output tăng bất thường; SLO và error budget biến chất lượng vận hành thành ngưỡng đo được và cơ sở phát alert.
- **Điều quan trọng nhất đã học:**
- **Hạn chế hoặc phần chưa hoàn thành, nếu có:** Evidence trace/prompt được dựng từ dữ liệu xác thực của Langfuse Observations API v2 thay vì ảnh chụp UI; không chứa secret hoặc raw input/output.

## 9. Checklist trước khi nộp

- [X] Kết quả và evidence thuộc commit SHA cuối.
- [X] Tất cả ảnh/output mở được bằng đường dẫn tương đối.
- [X] Incident evidence nối đúng metric → log → trace.
- [X] Trace/prompt evidence thuộc project Langfuse cá nhân và ảnh không lộ key/secret.
- [X] Repository chạy lại được theo README.
- [X] Không có secret, API key, PII thô hoặc evidence của người khác/lớp khác.
- [X] URL repo và commit SHA cuối đã được nộp trên LMS/Codelabs.
