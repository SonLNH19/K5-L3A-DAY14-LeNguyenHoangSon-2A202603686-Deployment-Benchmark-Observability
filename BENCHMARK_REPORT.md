# Benchmark & Observability Report — Day 14

> Copy file này thành `BENCHMARK_REPORT.md` trong repo của bạn và điền đầy đủ.
> Mọi con số phải lấy từ report JSON / CSV / screenshot thật, không ước lượng.

**Họ tên:** Lê Nguyễn Hoàng Sơn 
**Ngày chạy:** 2026-11-25
**Môi trường:** Windows 11, WSL2 Ubuntu (Python 3.12.16 trong venv), Docker Desktop 4.99.0 (Docker 29.9.3); WSL2 thấy 20 CPU logic và khoảng 7,6 GB RAM. Load test và eval chạy trực tiếp trên WSL, agent v1/v2 chạy cùng máy với bộ sinh tải.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | Thời gian trả lời bình thường của agent v2 chỉ khoảng 190–230 ms (p50 và p95 ở mức concurrency 1–50), nên 500 ms còn dư hơn gấp đôi cho dao động mà vẫn đủ chặt để bắt được sự cố: đo thực tế, mức 100 luồng đẩy p95 lên 1798.8 ms và vi phạm ngưỡng này |
| Error rate `/ask` | < 2 % | Mock LLM có lỗi nền 1% (`MOCK_ERROR_RATE=0.01`) và các lần đo cho 0.67%–1.67%; ngưỡng 2% chừa chỗ cho nhiễu thống kê của mẫu 300 request nhưng vẫn phát hiện được khi lỗi tăng gấp đôi |
| Accuracy trên golden dataset | ≥ 85 % | Cùng ngưỡng với job CI `quality-gate`. Agent v2 ban đầu đạt 91.3% nên còn cách ngưỡng 6 điểm %, đủ để một lần sửa prompt hay retrieval làm giảm chút chất lượng không bị chặn oan, nhưng chặn được sự suy giảm lớn (agent v1 chỉ 4.3%) |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.82 | 189.8 | 216.9 | 1665.7 | 1.67% | ✅ PASS |
| 10 | 36.96 | 189.8 | 221.6 | 1707.9 | 1.67% | ✅ PASS |
| 50 | 104.89 | 192.5 | 228.6 | 1718.2 | 1.00% | ✅ PASS |
| 100 | 81.90 | 274.1 | 1798.8 | 1959.5 | 1.33% | ❌ FAIL (p95 > 500 ms) |

Mỗi mức gửi 300 request tới agent v2 (`load_test.py --requests 300`), số liệu lấy từ `reports/load_v2_c1.json`, `load_v2_c10.json`, `load_v2_c50.json`, `load_v2_c100.json`.

**Nhận xét:** RPS không tăng tuyến tính. Từ 1 lên 10 luồng, RPS tăng 7,7 lần (4.82 → 36.96), gần tuyến tính vì mỗi request chủ yếu chờ mock LLM ngủ (async), CPU gần như rảnh. Từ 10 lên 50, RPS chỉ tăng 2,8 lần (36.96 → 104.89). Lên 100 luồng, RPS lại **giảm** (104.89 → 81.90) và p95 nhảy từ 228.6 ms lên 1798.8 ms: tail latency lan sang cả p90 (1692.4 ms) và kéo avg từ 242.6 ms lên 644.1 ms. Với một process uvicorn, event loop vừa nhận kết nối, vừa chạy middleware ghi log JSON và cập nhật metric, vừa phải đồng thời phục vụ cả bộ sinh tải chạy cùng máy; vượt khoảng 50 luồng thì hàng đợi trong process hình thành và hệ thống bước vào vùng bão hòa. Mức 100 là điểm vi phạm SLO p95, không phải mức 1–50.

Một điểm dễ hiểu sai: ở mọi mức, p99 đều quanh 1,7 giây trong khi p50 chỉ khoảng 190 ms. Khoảng cách này không đến từ tải mà đến từ `MOCK_SLOW_RATE=0.03` (3% request cố ý bị cộng thêm 1,5 giây), nên nó xuất hiện ngay cả khi chỉ có 1 luồng. Chạy một request đơn lẻ vài lần thường cho cảm giác "agent nhanh", nhưng p99 mới là thứ người dùng xui xẻo gặp.

### 2.2 Tail latency

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 187.2 | 215.3 | 222.7 |
| 0.03 (mặc định) | 233.5 | 218.4 | 1709.0 |
| 0.1 | 345.1 | 1688.0 | 1714.5 |

Mỗi hàng chạy `load_test.py --requests 300 --concurrency 20` sau khi tắt và bật lại agent v2 với giá trị `MOCK_SLOW_RATE` tương ứng; số liệu lấy từ `reports/load_v2_slow_0.json`, `load_v2_slow_0.03.json`, `load_v2_slow_0.1.json`. Hai mức 0.03 và 0.1 vẫn có lỗi 503 ngẫu nhiên (error rate từ 0.67% đến 1.67%) vì `MOCK_ERROR_RATE=0.01` không đổi, nên lỗi không phải là nguyên nhân của khác biệt giữa các hàng.

**Nhận xét:** Chỉ số nhạy nhất là **p99** ở mức 0.03 và **p95** ở mức 0.1; avg là chỉ số phản ứng chậm nhất.
- 0 → 0.03: avg chỉ tăng từ 187.2 lên 233.5 ms (+46 ms, +25%), p95 gần như đứng yên (215.3 → 218.4 ms), nhưng p99 nhảy gần 8 lần (222.7 → 1709.0 ms). Khoảng 3% request bị chậm là quá ít để chạm tới p95 nhưng đủ để chiếm hẳn vị trí p99.
- 0.03 → 0.1: lúc này hơn 5% request chậm nên chúng chiếm luôn vị trí p95 (218.4 → 1688.0 ms) và SLO p95 ≤ 500 ms chuyển từ PASS sang **FAIL**, trong khi avg mới là 345.1 ms, thấp hơn hẳn ngưỡng 500 ms.
- Vì sao SLO không nên đặt theo average: ở mức 0.1, nếu chỉ nhìn avg = 345 ms thì có vẻ "dưới 500 ms, ổn", nhưng thực tế cứ khoảng 10 người dùng có 1 người chờ gần 1,7 giây. Average là trung bình cộng nên các request chậm bị pha loãng bởi 90% request nhanh. Percentile trả lời đúng câu hỏi "người dùng xui xẻo nhất chờ bao lâu". Phân biệt này cũng chính là lý do cần đặt SLO theo p95/p99 khi viết alert.

Một tình huống thực tế: với p95 = 218 ms và error rate 0.67% ở mức 0.03, dashboard trông hoàn toàn xanh, trong khi 3 trong 100 người dùng vẫn chờ khoảng 1,7 giây. Nếu chỉ giám sát average hay p95 thì sự cố này hầu như vô hình; chỉ p99 mới lộ ra.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | 100.0% (23/23) |
| Avg tokens / câu | 56.7 | 108.7 | 117.8 |
| Tổng cost (USD) | 0.000399 | 0.000756 | 0.000826 |
| Cost / câu đúng (USD) | 0.00039855 | 0.00003599 | 0.00003593 |
| Quality gate | ❌ FAIL (accuracy ≥ 80%) | ✅ PASS (accuracy ≥ 80%, không regression so với v1) | ✅ PASS (accuracy ≥ 100%, không regression so với v2) |

Số liệu lấy từ `reports/eval_v1.json`, `reports/eval_v2.json`, `reports/eval_v2_fixed.json`. Ngưỡng của lần chạy cuối là `--min-accuracy 1.0`, chặt hơn ngưỡng mặc định 80%.

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `security-02` ("JWT khác gì API key trong xác thực?") | `token`, `hết hạn`, `chữ ký` | Thêm vào cuối `KNOWLEDGE_BASE` một entry kích hoạt bằng từ khoá `jwt`, nội dung nêu JWT là token có chữ ký và có thời điểm hết hạn (claim `exp`), so với API key là chuỗi bí mật do server tra cứu và quản lý |
| `scaling-03` ("Kubernetes HPA scale dựa trên những chỉ số nào?") | `cpu`, `memory`, `custom metrics` | Thêm entry kích hoạt bằng `kubernetes` và `hpa`, nội dung nêu HPA điều chỉnh số replica theo cpu, memory hoặc custom metrics (số request mỗi giây, độ dài hàng đợi) |

Nguyên nhân hai câu sai là agent v2 không tìm thấy tài liệu nào khớp (không từ khoá nào của `KNOWLEDGE_BASE` xuất hiện trong câu hỏi) nên rơi về câu trả lời chung chung và mất cả 3/3 keyword. Từ khoá kích hoạt chỉ được chọn trong số các từ khoá chỉ xuất hiện ở hai câu này (`jwt`, `kubernetes`, `hpa`), vì vậy 21 câu đang đúng không bị đổi sang tài liệu khác: lần chạy cuối ghi nhận 2 câu cải thiện và **0 regression**. Chỉ sửa `KNOWLEDGE_BASE`, không sửa `golden_dataset.jsonl`.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**
- **Chất lượng:** v2 tăng từ 4.3% lên 91.3% (100% sau khi sửa KB), đổi lại phải thêm bước retrieval và nhồi tài liệu vào prompt.
- **Chi phí:** v2 tốn nhiều token hơn khoảng 1,9 lần mỗi câu (108.7 so với 56.7), tổng cost tăng 1,9 lần (0.000756 so với 0.000399). Nhưng v1 gần như không có câu đúng nên chi phí thực sự cho mỗi câu trả lời hữu ích là 0.00039855, còn v2 chỉ 0.00003599: **rẻ hơn khoảng 11 lần**. Chỉ số "cost / câu đúng" phản ánh đúng giá trị hơn "cost / câu", vì rẻ mà sai thì vẫn là tiền bỏ đi.
- **Latency:** p95 của eval v1 là 1494.3 ms và v2 là 221.1 ms, nhưng chênh lệch này **không nên đọc như v1 chậm hơn v2**. Eval chỉ có 23 câu nên chỉ 2 câu chậm bất thường của v1 (probe-01 1638.9 ms, security-02 1668.2 ms) là đủ kéo p95 lên. Mẫu quá nhỏ để kết luận về latency; latency phải đo bằng load test (mục 2). Về lý thuyết, v2 có thêm bước retrieval khoảng 20 ms nên sẽ chậm hơn v1 một chút, không nhanh hơn.
- **Quality gate trong CI:** file `.github/workflows/quality-gate.yml` chạy trên mỗi pull request: cài gói, bật agent v2 với `MOCK_ERROR_RATE=0` (tắt lỗi ngẫu nhiên để eval không đổi kết quả giữa các lần chạy), rồi chạy `python eval/run_eval.py --min-accuracy 0.85`. `run_eval.py` kết thúc với **exit code 1** khi accuracy dưới ngưỡng hoặc có regression so với baseline, nên bước đó thất bại, job đỏ và PR bị chặn merge nếu bật "Require status checks to pass" cho job `quality-gate` trong Branch protection. Cơ chế y hệt lần chạy cục bộ ở trên: lần eval cuối kết thúc `Quality gate (accuracy ≥ 100%, không regression): ✅ PASS`, còn lần eval của v1 thì `❌ FAIL`.
- **Kết luận:** với agent trả lời kiến thức kỹ thuật, v2 đáng giá vì chi phí tăng gấp đôi nhưng giá trị thu về tăng hơn 20 lần. Điều đáng lưu ý là sửa hai câu cuối chỉ tốn thêm khoảng 9 token mỗi câu và 0.00007 USD tổng, không đáng kể so với lợi ích 8.7 điểm phần trăm accuracy.

---

## 4. Observability stack (production)

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals): `screenshots/grafana-dashboard.png` (3 instance, Traffic 61.8 req/s, Error rate 1.09%, p95 293.14 ms)
- [x] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian): `screenshots/jaeger-slow-trace.png` (trace `e25f8db`, span `chat mock-llm-small`)
- [x] Một trace lỗi trong Jaeger (có event `exception`): `screenshots/jaeger-error-trace.png` (trace `d8dd0ae`, `Upstream LLM error: 503 Service Unavailable (mock)`)
- [x] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**: `screenshots/prometheus-alert-firing.png` (nhóm `agent-slo` có `FIRING (2)`: `AgentHighErrorRate` firing 4m52s với giá trị 0.2122, và `AgentHighLatencyP95` firing 5m3s với giá trị 1.894 s; `AgentInstanceDown` inactive)

### Trả lời Exercise 7.4

**1. p95 và avg đang là bao nhiêu? Khoảng cách giữa chúng nói lên điều gì?** Ở đợt đo 3 instance (`benchmark-results/scale_3_stats.csv`, dòng `/ask`), trung bình là 232.5 ms trong khi p95 chỉ 220 ms và p99 là 1700 ms. Nghĩa là **avg lớn hơn cả p95**: khoảng 3% request chậm (khoảng 1,7 giây, do `MOCK_SLOW_RATE=0.03`) kéo trung bình lên, nhưng vì chúng nằm ngoài 5% cuối nên p95 không nhìn thấy chúng. Khoảng cách này là dấu hiệu của phân phối lệch phải (tail latency): phần lớn người dùng nhận trả lời trong khoảng 190–220 ms, một nhóm nhỏ chờ gấp 8 lần. Ở Grafana lúc đó p95 hiện 293.14 ms (cửa sổ 1 phút đang xen kẽ giữa các đợt tải). Vì vậy SLO nên đặt theo p95 và theo dõi cả p99, không đặt theo trung bình.

**2. Nginx có chia đều traffic cho các instance không?** Có. Panel "Requests/s theo instance" trong `grafana-dashboard.png` cho thấy khi chạy 3 instance, ba đường (`172.19.0.7`, `.8`, `.9`) gần như chồng lên nhau, mỗi instance khoảng 20 req/s trên tổng khoảng 60 req/s. Nginx dùng round-robin mặc định, và mỗi phản hồi còn có header `X-Served-By` để kiểm tra từng request do instance nào xử lý.

**3. Vì sao input tokens/s lớn hơn output tokens/s?** Prompt gửi cho LLM gồm system prompt, tài liệu retrieval (agent v2) và câu hỏi, trong khi câu trả lời ngắn. Ví dụ một request thật trong lúc kiểm tra: 81 token vào, 46 token ra (`usage` của phản hồi `/ask`). Thí nghiệm E xác nhận phần tài liệu retrieval chiếm phần lớn input: khi đổi sang v1 (không retrieval), input tokens/s giảm từ 1489.5 xuống 684.7 (giảm khoảng 54%) ở cùng mức tải.

**4. `curl http://localhost:8080/metrics` trả về gì? Vì sao?** Trả về **404** (đã chạy thật: `metrics 404`). `nginx.conf` có khối `location = /metrics` (dòng 22) chặn đường này từ bên ngoài, vì metric chứa thông tin nội bộ (số instance, phiên bản, chi phí, độ trễ) không nên công khai. Prometheus không cần đi qua Nginx: nó scrape trực tiếp `agent:8000/metrics` trong mạng Docker nội bộ nhờ `dns_sd_configs` trong `prometheus.yml`.

### 4.2 Thí nghiệm "phá hệ thống"

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (critical) | pending sau 22.2 s, **firing sau 82.6 s** | "Error rate" (Golden Signals) và "Requests/s theo status code" (đường 503 tăng) |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` (warning) | pending sau 21.4 s, **firing sau 142.1 s** (p95 đo được 1.91 s) | "Latency /ask p50/p95/p99": p95 và p99 vọt lên, p50 gần như không đổi |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` (warning) | pending sau 21.8 s, **firing sau 142.4 s** (tốc độ đốt 36.33 USD/giờ, so với khoảng 2.5 USD/giờ của model nhỏ ở trạng thái ổn định; xem ghi chú bên dưới về ngưỡng 2 USD/giờ) | "Cost burn rate (USD/giờ)" và "LLM cost (1h)" |
| D | `docker pause` 1 instance | `AgentInstanceDown` (critical) | pending sau 10.1 s, **firing sau 40.4 s** | Alert `up == 0` và "Instances up"; panel "Error rate" của agent vẫn thấp (0.70%) |
| E | `AGENT_VERSION=v1` | **Không có alert nào** | không firing sau 150 s quan sát | "Tokens/s" (input 1489.5 → 684.7), "Retrieval hit ratio" (về 0), bảng "Phiên bản đang chạy" |

Cách đo: mỗi thí nghiệm sửa `.env` rồi chạy `docker compose up -d --scale agent=2`, giữ Locust 30 người dùng (spawn rate 5), và đọc trạng thái alert từ `/api/v1/alerts` của Prometheus mỗi 5 giây. Mốc 0 là lúc chạy lệnh đổi cấu hình, nên thời gian đã gồm cả lúc agent khởi động lại. Với thí nghiệm D (không đổi `.env`) mốc 0 là lúc chạy `docker pause`. Thời gian tới firing khớp công thức "độ trễ phát hiện + `for`": A 22 + 60 ≈ 83 s (`for: 1m`), B 21 + 120 ≈ 142 s và C 22 + 120 ≈ 142 s (`for: 2m`), D 10 + 30 ≈ 40 s (`for: 30s`). Độ trễ phát hiện 10–22 s đến từ chu kỳ scrape 5 s và cửa sổ `rate()`. Không thí nghiệm nào làm alert khác firing cùng lúc (`other_firing` rỗng). Unit test của bộ luật alert (`promtool test rules`) cũng đạt `SUCCESS`.

Để có ảnh `prometheus-alert-firing.png` với nhiều alert firing cùng lúc, cuối phiên thí nghiệm đã bật đồng thời `MOCK_ERROR_RATE=0.2` và `MOCK_SLOW_RATE=0.3` (tức A + B): `AgentHighErrorRate` firing sau 87.3 s và `AgentHighLatencyP95` firing sau 132.6 s kể từ lúc đổi cấu hình. Khi chụp, hai alert này đã firing lần lượt 4m52s và 5m3s với giá trị 21,2% lỗi và p95 1.894 s (xem ảnh). Lúc đó `LLMCostBurnRateHigh` cũng đang firing (xem ghi chú về ngưỡng ở dưới).

Hai chi tiết đáng ghi nhận:
- Ở thí nghiệm B, p95 đo được 1.91 s (vượt SLO 1 s ngay từ đầu) nhưng alert chỉ firing sau 2 phút vì `for: 2m` được thiết kế để tránh báo động giả khi chỉ có một spike ngắn: đánh đổi giữa phát hiện nhanh và ít báo nhầm.
- Ở thí nghiệm C, tốc độ đốt tiền đo được là 36.33 USD/giờ. So với baseline ban đầu (1.57 USD/giờ) thì tăng khoảng 23 lần, cao hơn tỉ lệ giá của hai model (giá input lẫn output đều gấp khoảng 16,7 lần), nhưng baseline đó bị đo thấp (xem chi tiết ở gạch đầu dòng ngay bên dưới). Với baseline đo lại ở trạng thái ổn định (khoảng 2.53 USD/giờ) thì mức tăng chỉ khoảng 14 lần, thấp hơn tỉ lệ giá 16,7 lần. Chênh lệch đó hợp lý với giả thuyết (suy luận, chưa đo): model lớn chậm hơn 2,5 lần nên mỗi người dùng ảo gửi được ít request hơn mỗi giây, trong khi giá mỗi request vẫn tăng khoảng 16,7 lần.
- **Ngưỡng chi phí gần sát mức tiêu thụ bình thường, nên thí nghiệm C bị nhiễu.** Sau khi đo xong và trả cấu hình về mặc định (model `mock-llm-small`, v2), Prometheus vẫn ghi `LLMCostBurnRateHigh` ở trạng thái firing: ở khoảng 18.9 req/s, tốc độ đốt tiền đo được là 2.528 USD/giờ (cửa sổ 5 phút) và 2.583 USD/giờ (cửa sổ 1 phút), cao hơn ngưỡng 2 USD/giờ. Con số baseline 1.57 USD/giờ ghi ở lượt đo đầu thấp hơn thực tế vì cửa sổ `[2m]` đo ngay sau khi khởi động lại agent, lẫn cả đoạn không có traffic. Vậy kết quả C vẫn hợp lệ ở khía cạnh độ lớn (36.33 so với khoảng 2.5 USD/giờ, tức khoảng 14 lần) nhưng không chứng minh được "đổi model là nguyên nhân duy nhất làm alert firing", vì ở tải 30 người dùng alert này có thể firing ngay cả với model nhỏ. Bài học thực tế: ngưỡng alert phải được hiệu chỉnh theo mức tiêu thụ bình thường đã đo (hoặc theo ngân sách), nếu không alert sẽ firing liên tục và đội vận hành sẽ quen với việc bỏ qua nó (alert fatigue).
- **Tác dụng phụ của phương pháp:** vì mỗi lần đổi cấu hình tạo lại cả hai container agent cùng lúc, Nginx tạm thời không có upstream và trả 502 trong khoảng 10 giây (log Nginx có 1474 lỗi 502 trong toàn bộ quá trình, thành từng đợt trùng với các lần tạo lại; con số "10 giây" là ước tính từ tốc độ khoảng 22 request mỗi giây, chưa đo trực tiếp). Các lỗi 502 này không tính vào metric của agent vì agent chưa chạy lúc đó.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?** Khi đổi sang v1, agent vẫn trả 200 nhanh (thậm chí rẻ hơn: input token/giây giảm khoảng 54%), nên mọi tín hiệu hạ tầng (error rate, p95, instance up, cost) đều xanh, trong khi chất lượng thực tế sụp từ 91.3% xuống 4.3% theo eval (`reports/eval_v2.json` và `reports/eval_v1.json`). Lỗi chất lượng không làm metric hạ tầng đỏ, nên cần giám sát riêng về chất lượng: (1) chạy eval trên golden dataset định kỳ hoặc trước mỗi lần deploy và chặn bằng quality gate (đúng với job CI ở mục 3); (2) chấm một mẫu traffic thật bằng LLM-as-judge hoặc so sánh ngữ nghĩa, có alert khi accuracy giảm; (3) theo dõi chỉ số gián tiếp như "Retrieval hit ratio" (ở thí nghiệm này về 0 khi dùng v1) hoặc độ dài câu trả lời; (4) thu phản hồi người dùng (like/dislike, báo lỗi). Phải thiết kế các tín hiệu đó từ trước, vì lúc người dùng phàn nàn thì sự cố đã xảy ra.

**Thí nghiệm D (`docker pause`): vì sao lỗi 504 không hiện trên dashboard của agent?** Trong lúc pause, log Nginx ghi 27 lỗi 504 (phút 10:18–10:19 UTC) nhưng tỉ lệ lỗi 5xx trong metric của chính agent chỉ là 0.70%, ngang mức lỗi nền. Lỗi 504 xảy ra ở Nginx khi chờ agent quá `proxy_read_timeout 10s` (`nginx.conf` dòng 34), request chưa bao giờ được agent xử lý xong nên agent không đếm nó. Metric chỉ phản ánh những gì chính service đó nhìn thấy, vì vậy production cần thêm giám sát ở tầng load balancer (metric của Nginx) hoặc synthetic check từ bên ngoài. Lý do dùng `docker pause` thay vì `docker stop`: container bị stop biến mất khỏi DNS nên Prometheus coi như target không còn tồn tại và không có alert, còn container bị pause vẫn còn trong DNS nhưng không trả lời nên `up == 0`.

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được:

- `trace_id`: `e25f8db04cf663963fc00c512da5fa6c` (ảnh: `screenshots/jaeger-slow-trace.png`)
- Tổng thời gian: 1721.7 ms (log `http_request`; Jaeger hiển thị `1.7s`)
- Span chiếm nhiều thời gian nhất: `chat mock-llm-small` (1642.1 ms theo `llm_ms` trong log, Jaeger hiển thị `1.6s`; chiếm khoảng 95,4% tổng thời gian). Span còn lại: `agent.retrieve_context` 78.3 ms (khoảng 4,5%); hai span cha `POST /ask` và `agent.run` là tổng của các span con nên không tính.
- Dòng log tương ứng (grep theo `trace_id`, hai dòng của cùng request, khớp `span_id` với span `POST /ask` trong Jaeger):

```json
{"ts": "2026-11-25T10:01:45.415191+00:00", "level": "INFO", "service": "observable-agent", "instance": "b98e33df8436", "event": "llm_call", "trace_id": "e25f8db04cf663963fc00c512da5fa6c", "span_id": "a50fddf7ea0ce1ca", "request_id": "0b6cf2006f79", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 72, "output_tokens": 40, "cost_usd": 3.5e-05, "llm_ms": 1642.1}
{"ts": "2026-11-25T10:01:45.415461+00:00", "level": "INFO", "service": "observable-agent", "instance": "b98e33df8436", "event": "http_request", "trace_id": "e25f8db04cf663963fc00c512da5fa6c", "span_id": "a50fddf7ea0ce1ca", "request_id": "0b6cf2006f79", "method": "POST", "route": "/ask", "status": 200, "ms": 1721.7}
```

Nhận xét: trace bắt đầu lúc 17:01:43.693 (GMT+7) và log `http_request` ghi lúc 10:01:45.415 (UTC) tức 17:01:45.415 (GMT+7), cách nhau đúng khoảng 1,72 giây nên log và trace thật sự là cùng một request. Phần lớn thời gian nằm ở lời gọi LLM upstream chứ không phải code của agent; 1642.1 ms + 78.3 ms = 1720.4 ms, chỉ lệch khoảng 1,3 ms so với tổng, tức overhead của chính agent gần như bằng không. Span `agent.retrieve_context` mất 78.3 ms trong khi cấu hình mặc định chỉ khoảng 20 ms (`MOCK_RETRIEVAL_MS=20`): trace này nằm trong đợt Locust đang tạo tải 100 người dùng nên event loop phải xen kẽ nhiều request; đây là giải thích suy ra từ bối cảnh, chưa đo trực tiếp.

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 59.79 | 190 | 230 | 1700 | 75 / 7111 (1.05%) |
| 3 | 60.08 | 190 | 220 | 1700 | 71 / 7144 (0.99%) |

Số liệu lấy từ dòng `POST /ask` trong `benchmark-results/scale_1_stats.csv` và `scale_3_stats.csv` (Locust headless: `-u 100 -r 10 -t 2m`). Dòng tổng hợp (gồm cả `/health`) cho 65.76 req/s ở 1 instance và 66.20 req/s ở 3 instance. Toàn bộ lỗi là `503 Service Unavailable` từ `/ask`, tức lỗi upstream do mock LLM sinh ra với `MOCK_ERROR_RATE=0.01`, không phải do thiếu tài nguyên. Trước mỗi lần đo đều kiểm tra bằng `docker ps` và danh sách target của Prometheus rằng số agent đang chạy đúng bằng số instance ghi trong bảng.

**Giải thích kết quả:** Scale từ 1 lên 3 instance **không** cải thiện RPS (59.79 → 60.08) và p95 chỉ đổi từ 230 ms xuống 220 ms, nằm trong dao động đo, còn p50 và p99 không đổi. Nút thắt không nằm ở agent mà ở hai chỗ khác:

1. **Bộ tạo tải tự giới hạn tốc độ.** Mỗi người dùng ảo trong `locustfile.py` nghỉ ngẫu nhiên 0.5–2 giây giữa hai lần gọi (`wait_time = between(0.5, 2)`, trung bình khoảng 1,25 s). Với 100 người dùng, mỗi vòng mất khoảng 1,25 s nghỉ + khoảng 0,2 s chờ trả lời ≈ 1,45 s, tức khoảng 69 vòng mỗi giây, trong đó `/ask` chiếm 10/11 số task, nghĩa là khoảng 63 req/s. Con số tính ra khớp với 60 req/s đo được. Đây là mô hình tải "vòng kín": lưu lượng bị quyết định bởi số người dùng và thời gian nghỉ, không phải bởi năng lực của server, nên thêm server không làm lưu lượng tăng. Một instance đã đủ sức phục vụ mức 60 req/s này với p95 khoảng 230 ms (gần bằng độ trễ nền của mock LLM), tức chưa bão hòa. Muốn thấy lợi ích của scale cần tăng số người dùng hoặc giảm thời gian nghỉ cho đến khi 1 instance bão hòa.
2. **Độ trễ đuôi do LLM upstream, không do agent.** p99 ≈ 1700 ms ở cả hai lần vì 3% request bị mock LLM cố ý cộng thêm 1,5 giây (`MOCK_SLOW_RATE=0.03`). Thêm instance agent không làm LLM nhanh hơn, nên p99 không đổi. Trên Grafana, panel "LLM latency p95" cho thấy đúng bước này là bước chậm.

Một tình huống thực tế trong lúc đo: lần chạy `scale_1` đầu tiên thực ra chạy trên **2 instance**. File compose khai báo `deploy.replicas: 2`, và lệnh `docker compose run --rm locust ...` khi chạy sẽ đảm bảo các service phụ thuộc đang chạy theo khai báo đó chứ không theo `--scale agent=1` của lần `up` trước, nên Compose tự bật thêm `agent-2`. Panel "Requests/s theo instance" có hai đường và `Instances up = 2` là dấu hiệu phát hiện ra. Cách khắc phục: ép về đúng 1 container bằng `up -d --no-recreate --scale agent=1`, rồi chạy Locust với cờ `--no-deps` và đo lại; số liệu trong bảng là của lần đo lại này.

---

## 6. Kết luận

- 3 điều học được về benchmark & observability:
  1. **Trung bình che mất người dùng xui xẻo; percentile mới nói thật.** Ở mức `MOCK_SLOW_RATE=0.03`, avg chỉ tăng 46 ms (187.2 → 233.5 ms), p95 gần như đứng yên (218.4 ms) nhưng p99 nhảy gần 8 lần (222.7 → 1709.0 ms). Ở mức 0.1, avg mới 345.1 ms (dưới ngưỡng 500 ms) trong khi p95 đã là 1688.0 ms và SLO p95 FAIL. Một dashboard chỉ có "độ trễ trung bình" sẽ xanh trong cả hai tình huống đó.
  2. **Con số benchmark chỉ đáng tin khi đã kiểm tra điều kiện đo.** Lần đo `scale_1` đầu tiên thực ra chạy trên 2 instance vì `deploy.replicas: 2` trong compose cộng với `docker compose run` tự bật thêm replica; chính panel "Requests/s theo instance" (hai đường) và `Instances up = 2` trên Grafana lộ ra điều này. Nếu không nhìn dashboard thì bảng so sánh 1 và 3 instance sẽ sai mà không ai hay. Kết quả sau khi đo lại cũng không như kỳ vọng: scale từ 1 lên 3 instance không tăng RPS (59.79 → 60.08) vì bộ tạo tải của Locust tự giới hạn bằng thời gian nghỉ của người dùng ảo (`wait_time = between(0.5, 2)`), không phải vì agent quá tải. Nút thắt có thể nằm ở chính công cụ đo.
  3. **Ba trụ cột bổ sung cho nhau, không thay thế nhau.** Metrics (Grafana) báo p95 và p99 tăng; Jaeger chỉ ra bước nào chậm (span `chat mock-llm-small` chiếm 1642.1 ms trong tổng 1721.7 ms, khoảng 95%, nên lỗi nằm ở LLM upstream chứ không phải code agent); log JSON có `trace_id` nối hai thứ đó lại với nhau đúng một request. Thiếu một trụ thì chỉ biết "có vấn đề" hoặc chỉ biết "ở đâu" mà không biết "chuyện gì xảy ra".
- Nếu đưa agent này lên production thật, sẽ thêm các metric / alert sau:
  - **Chất lượng câu trả lời:** chạy eval định kỳ trên một mẫu traffic thật hoặc golden dataset, gắn alert khi accuracy tụt (thí nghiệm E cho thấy đổi sang v1 làm accuracy từ 91.3% xuống 4.3% mà không alert hạ tầng nào firing).
  - **Giám sát từ bên ngoài (synthetic check)** gọi `/ask` qua đường công khai, vì lỗi 504 ở Nginx không xuất hiện trong metric của agent (thí nghiệm D).
  - **Ngân sách chi phí theo ngày và tỉ lệ hết ngân sách (HTTP 402)** thay vì chỉ tốc độ đốt tiền theo giờ, vì alert hiện tại tính trên cửa sổ 5 phút nên phản ứng chậm với đợt tăng chi phí đột ngột.
  - **Retrieval hit ratio** có alert khi tụt (agent v2 mất retrieval là mất chất lượng mà hạ tầng vẫn xanh) và **p99** bên cạnh p95, vì tail latency nằm đúng ở p99.
  - **Đo thật CPU/RAM của container** để biết khi nào một instance bão hòa, vì load test này chưa đẩy agent tới giới hạn.

---

## Phụ lục: Exercise 7.1 (đọc metrics của agent)

**1. Ví dụ cho mỗi loại metric** (từ `develop/app.py` và đầu ra `/metrics`):
- **Counter** (chỉ tăng, dùng với `rate()`): `http_requests_total{method="POST",route="/ask",status="200"}`, `llm_tokens_total{type="input"}`, `llm_cost_usd_total`.
- **Gauge** (lên xuống được): `agent_in_flight_requests` (số request đang xử lý), `agent_info` (luôn bằng 1, mang nhãn `version` và `model`).
- **Histogram** (chia giá trị vào bucket để tính percentile): `http_request_duration_seconds_bucket{le="0.2",...}` và `llm_request_duration_seconds_bucket`.

**2. Nếu dùng `request.url.path` làm label `route` thay vì `route.path`:** mỗi URL khác nhau tạo ra một giá trị label mới, nên mỗi URL lạ mà bot quét (như `/wp-admin`, `/.env`, `/a1b2c3`) sinh thêm một bộ time series riêng (cho mọi tổ hợp `method` và `status`, và với histogram nhân thêm số bucket cộng 2). Vài nghìn URL lạ cho ra hàng chục nghìn series, làm Prometheus tốn RAM và đĩa, truy vấn chậm đi, và kẻ tấn công có thể cố ý gây ra điều đó. Dùng `route.path` (mẫu như `/ask`, và gán `unmatched` cho URL không khớp route nào) giữ số giá trị label ở mức vài chục.

**Nhiệm vụ: thêm metric `agent_question_length_chars`.** Đã thêm `QUESTION_LENGTH = Histogram("agent_question_length_chars", ..., buckets=(20, 50, 100, 200, 500, 1000, 2000))` vào `develop/app.py` và gọi `QUESTION_LENGTH.observe(len(body.question))` trong endpoint `ask()`. Dùng Histogram với bucket cố định thay vì một label `length=...`, vì độ dài có vô số giá trị nên nếu làm label thì cardinality sẽ bùng nổ. Kiểm chứng bằng cách chạy agent và gửi 3 câu hỏi dài 7, 70 và 300 ký tự:

```text
agent_question_length_chars_bucket{le="20.0"} 1.0
agent_question_length_chars_bucket{le="50.0"} 1.0
agent_question_length_chars_bucket{le="100.0"} 2.0
agent_question_length_chars_bucket{le="200.0"} 2.0
agent_question_length_chars_bucket{le="500.0"} 3.0
agent_question_length_chars_bucket{le="1000.0"} 3.0
agent_question_length_chars_bucket{le="2000.0"} 3.0
agent_question_length_chars_bucket{le="+Inf"} 3.0
agent_question_length_chars_count 3.0
agent_question_length_chars_sum 377.0
```

Các bucket tích lũy: câu 7 ký tự rơi vào `le="20"` (và mọi bucket lớn hơn), câu 70 ký tự vào `le="100"`, câu 300 ký tự vào `le="500"`; `count = 3` và `sum = 377 = 7 + 70 + 300`. Các file `reports/*.json` đo trước khi thêm metric này; thêm một Histogram không ảnh hưởng đường xử lý của `/ask` nên không làm thay đổi kết quả của `load_test.py` và `run_eval.py`.
