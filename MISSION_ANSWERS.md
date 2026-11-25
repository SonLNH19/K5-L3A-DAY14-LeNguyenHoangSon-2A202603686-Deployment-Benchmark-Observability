# Day 14 Lab — Mission Answers

> Môi trường: Windows 11 + WSL2 Ubuntu (Python 3.12 trong venv), Docker Desktop (tích hợp WSL).

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns trong `01-localhost-vs-production/develop/app.py`

1. **Secret hardcode (dòng 17–18):** `OPENAI_API_KEY = "sk-..."` và `DATABASE_URL` chứa mật khẩu `password123` nằm thẳng trong code. Push lên GitHub là lộ ngay; xoá file sau đó cũng không đủ vì secret còn trong lịch sử git.
2. **Log lộ secret (dòng 34):** `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` in key ra stdout → key nằm trong log của platform, ai đọc được log đều thấy.
3. **Dùng `print()` thay vì logging có cấu trúc (dòng 33–38):** không có level, timestamp, không parse/lọc được bằng công cụ log (Loki, Datadog...).
4. **Config cứng, không đọc env (dòng 21–22):** `DEBUG = True`, `MAX_TOKENS = 500`. Muốn đổi giữa dev/staging/production phải sửa code và build lại.
5. **Không có health check (dòng 42–43):** platform không có cách hỏi "process còn sống không", nên không tự restart khi app treo.
6. **Bind `localhost` (dòng 51):** chỉ nhận kết nối từ chính máy đó; trong container, request từ ngoài (port mapping) sẽ không vào được. Production cần `0.0.0.0`.
7. **Port cố định 8000 (dòng 52):** Railway/Render cấp port qua biến `PORT`; port cứng làm app không nhận được traffic.
8. **`reload=True` (dòng 53):** chế độ dev, theo dõi file và tự restart → tốn tài nguyên, có thể restart giữa lúc đang xử lý request.
9. **Không xử lý SIGTERM / graceful shutdown:** file không có `signal` hay `lifespan`; khi platform tắt container, request đang chạy bị cắt ngang.

### Exercise 1.2: Chạy bản develop

```bash
cd 01-localhost-vs-production/develop
pip install -r requirements.txt
python app.py
curl -X POST "http://localhost:8000/ask?question=Hello"
```

Kết quả (bản develop nhận `question` qua query, gửi JSON sẽ bị 422):

```json
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

Log của server sau lệnh curl trên:

```text
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Đây là câu trả lời từ AI agent (mock). ...
INFO:     127.0.0.1:55006 - "POST /ask?question=Hello HTTP/1.1" 200 OK
```

Quan sát: app chạy được và trả lời đúng, nhưng chỉ sau một request đầu tiên, key đã nằm nguyên văn trong log (anti-pattern 2). Trên Railway hay Render, log hiển thị cho bất kỳ ai có quyền xem dự án, nên đây là cách lộ secret rất dễ xảy ra mà không cần ai tấn công. Khi tắt bằng Ctrl+C, log chỉ có các dòng `Shutting down` của uvicorn, không có dòng nào từ app, vì bản develop không có logic shutdown riêng.

### Exercise 1.3: So sánh develop và production

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode (`DEBUG = True`, key trong code) | Đọc env trong `config.py` dòng 17–35 (`os.getenv`) | Cùng một code chạy được ở mọi môi trường, đổi cấu hình không cần sửa code; secret không vào git |
| Secrets | `sk-...` trong code, còn in ra log | `os.getenv("OPENAI_API_KEY")`, `AGENT_API_KEY`; fail fast nếu thiếu key ở production (`validate()` dòng 42–43) | Lộ key = mất tiền / bị lạm dụng; secret phải đặt trên platform hoặc `.env` ngoài git |
| Health check | Không có | `GET /health` (liveness, dòng 135) và `GET /ready` (readiness, 503 khi chưa sẵn sàng, dòng 152) | Platform restart container chết (`/health`) và load balancer ngừng gửi traffic vào instance chưa sẵn sàng (`/ready`) |
| Logging | `print()`, lộ secret | Log JSON (`json.dumps`), chỉ log `question_length`, không log nội dung/secret (dòng 111–122) | Log có cấu trúc thì tìm, lọc, đếm được; không lộ dữ liệu nhạy cảm |
| Shutdown | Đột ngột | `lifespan` (dòng 39–67): tắt cờ `is_ready`, dọn dẹp sau khi uvicorn xử lý xong request | Không mất request đang chạy khi deploy/scale |
| Host / Port | `localhost:8000` cứng | `host=0.0.0.0`, `port` từ `PORT` (dòng 194–198) | Chạy được trong container và trên cloud (platform cấp `PORT`) |
| Reload | Luôn bật | Chỉ bật khi `DEBUG=true` | Không có watcher/restart tự động trên production |

Hai điểm bản production vẫn chưa hoàn hảo (rút ra để áp dụng ở Part 6):
- `logging.basicConfig(format='{"time":..., "msg":"%(message)s"}')` khi `message` đã là chuỗi JSON thì dấu `"` lồng nhau làm dòng log **không còn là JSON hợp lệ**. Part 6 xử lý bằng `JsonFormatter` gọi `json.dumps` cho cả dòng log (kiểm chứng: script chấm báo mọi dòng log của app đều là JSON hợp lệ).
- `handle_sigterm` (dòng 180–189) tự đăng ký handler SIGTERM mà không gọi lại handler cũ, có nguy cơ chặn cơ chế tắt của uvicorn. Part 6 không đăng ký handler riêng mà dùng `lifespan`, để uvicorn tự xử lý SIGTERM (kiểm chứng: `docker stop` tắt trong 0.6–0.7 giây với exit code 0).

---

## Part 2: Docker

### Exercise 2.1: `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11` — bản Python đầy đủ (Debian + build tools), rất nặng.
2. **Working directory:** `/app`.
3. **Vì sao COPY `requirements.txt` trước:** Docker cache theo từng layer. Copy riêng `requirements.txt` rồi `pip install` thì khi chỉ sửa code, layer cài dependencies vẫn dùng cache, build lại chỉ mất vài giây. Nếu `COPY . .` trước thì mọi thay đổi code đều làm pip install chạy lại.
4. **CMD vs ENTRYPOINT:** `CMD` là lệnh mặc định, bị ghi đè khi gõ thêm lệnh sau `docker run image <lệnh>`. `ENTRYPOINT` là chương trình cố định của container; tham số sau `docker run` (hoặc `CMD`) được nối thêm vào nó, muốn đổi phải dùng `--entrypoint`.

### Exercise 2.2: Build và chạy bản develop

```bash
docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
docker run -p 8000:8000 my-agent:develop
curl -X POST "http://localhost:8000/ask?question=What%20is%20Docker"
```

Kết quả:
```json
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
```

### Exercise 2.3: Multi-stage build và so sánh kích thước

- **Stage 1 (builder):** `python:3.11-slim` + cài `gcc`, `libpq-dev`, rồi `pip install --user` vào `/root/.local`. Stage này có build tools, không dùng để deploy.
- **Stage 2 (runtime):** `python:3.11-slim` sạch, chỉ copy `/root/.local` (packages đã cài) và code, tạo user non-root `appuser`, có `HEALTHCHECK`.
- **Vì sao nhỏ hơn:** image cuối không chứa gcc, header, apt cache, pip cache; base `slim` thay vì bản đầy đủ.

Đo bằng `docker images` (Docker 29 hiển thị 2 cột; so sánh trên cùng cột **DISK USAGE**):

| Image | DISK USAGE | CONTENT SIZE |
|---|---|---|
| `my-agent:develop` | 1.67 GB | 426 MB |
| `my-agent:advanced` | 240 MB | 57.4 MB |

Output `docker images | grep my-agent`:

```text
my-agent:advanced   1d855948630c        240MB         57.4MB
my-agent:develop    88cc1288ee1a       1.67GB          426MB   U
```

Chênh lệch `(develop − advanced) / develop × 100%` (quy 1.67 GB ≈ 1670 MB):
- DISK USAGE: (1670 − 240) / 1670 ≈ **85.6%**
- CONTENT SIZE: (426 − 57.4) / 426 ≈ **86.5%**

Nhận xét: mức giảm lớn hơn khoảng 50–70% thường thấy vì bản develop dùng `python:3.11` đầy đủ (cả bộ công cụ build), còn bản production dùng `python:3.11-slim`. Trong log build, riêng bước cài `gcc` và `libpq-dev` ở stage builder mất 37.5 giây, nhưng nằm hoàn toàn ngoài image cuối: stage runtime chỉ nhận `/root/.local` (các package đã cài) qua `COPY --from=builder`. Tức là thời gian build cao nhưng image chạy production lại rất nhỏ.

### Exercise 2.4: Docker Compose stack

```
                    ┌──────────────────────── network "internal" ────────────────────────┐
Client ──:80──▶ Nginx (reverse proxy, rate limit 10 r/s/IP) ──▶ agent:8000 (FastAPI)
                                                                   │          │
                                                                   ▼          ▼
                                                             redis:6379   qdrant:6333
                                                          (session, rate   (vector DB
                                                           limit cache)      cho RAG)
```

- Chỉ Nginx mở port ra ngoài (80/443); agent, Redis, Qdrant chỉ nằm trong network `internal`, gọi nhau bằng tên service (DNS của Docker).
- `depends_on ... condition: service_healthy`: agent chỉ start khi Redis và Qdrant đã healthy.

Bật stack (sau `touch .env.local` trong `02-docker/production/`):

```bash
docker compose -p k5-day14-docker up -d --build
```

Lần chạy đầu, 3 service lên bình thường nhưng Nginx lỗi:

```text
✔ Container k5-day14-docker-redis-1  Healthy
✔ Container k5-day14-docker-qdrant-1 Healthy
✔ Container k5-day14-docker-agent-1  Started
✘ Container k5-day14-docker-nginx-1  Error response from daemon: ports are not available: exposing port TCP 0.0.0.0:80 ...
```

Nguyên nhân: cổng 80 trên máy Windows đang bị `PID 4` (tiến trình `System`, tức driver `http.sys` của nhân Windows) giữ; `netsh interface ipv4 show excludedportrange protocol=tcp` cũng liệt kê 80 trong các cổng bị loại trừ. Dừng một thành phần của hệ điều hành chỉ để chạy bài lab là không đáng, nên Nginx được đổi sang cổng 8081 bằng một file ghi đè riêng (`02-docker/production/docker-compose.port8081.yml`, nội dung `ports: !override ["8081:80"]`), không sửa file compose mẫu:

```bash
docker compose -p k5-day14-docker -f docker-compose.yml -f docker-compose.port8081.yml up -d
```

```text
k5-day14-docker-agent-1    Up (healthy)    8000/tcp
k5-day14-docker-nginx-1    Up              0.0.0.0:8081->80/tcp, [::]:8081->80/tcp
k5-day14-docker-qdrant-1   Up (healthy)    6333-6334/tcp
k5-day14-docker-redis-1    Up (healthy)    6379/tcp
```

Kiểm tra qua Nginx (cổng 8081 thay cho cổng 80 trong đề; `/ask` đọc trường `question` từ JSON body):

```text
$ curl -i http://localhost:8081/health
HTTP/1.1 200 OK
Server: nginx
Content-Type: application/json
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"status":"ok","uptime_seconds":114.9,"version":"2.0.0","timestamp":"2026-11-25T08:58:35.528265"}

$ curl -i -X POST http://localhost:8081/ask -H 'Content-Type: application/json' -d '{"question":"Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx

{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

Quan sát:
- Header `Server: nginx` cùng các header bảo mật (`X-Frame-Options: DENY`...) do `nginx.conf` thêm vào cho thấy request đi qua Nginx chứ không gọi thẳng agent. Agent chỉ có cổng `8000/tcp` nội bộ, Nginx là cửa vào duy nhất.
- Thông báo lỗi của Docker nói về cổng 80 nên lần đầu dễ nghĩ là lỗi cấu hình compose, trong khi nguyên nhân thật nằm ở máy chủ. Đọc kỹ thông báo rồi kiểm tra tiến trình đang giữ cổng giúp khoanh vùng nhanh hơn việc sửa compose theo đoán mò.

---

## Part 3: Cloud Deployment

### Exercise 3.1: Deploy (agent Part 6, Railway)

- URL: https://day14-agent-production-29a54820.up.railway.app (chi tiết trong `DEPLOYMENT.md`)
- Screenshot: [dashboard](screenshots/dashboard.png), [running](screenshots/running.png), [test](screenshots/test.png)

Triển khai bằng Railway CLI từ thư mục `06-lab-complete/` (builder `DOCKERFILE` theo `railway.toml`), thêm một service Redis trong cùng project, `REDIS_URL` tham chiếu tới Redis, `AGENT_API_KEY` sinh ngẫu nhiên và nạp qua `stdin`. Output 4 lệnh tự kiểm:

```text
GET  /health          200
GET  /ready           200
POST /ask (khong key) 401
POST /ask (co key)    200
```

Ghi chú từ quá trình làm:
- `railway add --database redis` tự liên kết thư mục với service Redis; nếu chạy `railway up` ngay thì mã agent bị đẩy nhầm vào Redis. Cần tạo service `day14-agent` (`railway add --service`) và chọn lại bằng `railway service` trước khi deploy.
- npm bản mới không tự chạy script `postinstall` của gói, nên cài Railway CLI cần `--allow-scripts=@railway/cli`.
- Railway cấp `PORT=8080` (log ghi `Uvicorn running on http://0.0.0.0:8080`), CMD trong Dockerfile đã đọc `${PORT:-8000}` nên không cần đặt `PORT` thủ công.
- CLI cảnh báo `railway.toml` (Config as Code) bị đánh dấu deprecated, vẫn hoạt động đến 2026-12-01; bài vẫn dùng `railway.toml` vì đề và script chấm yêu cầu file này.
- Các lệnh `variable list --kv` hoặc `--json` in giá trị thật, nên việc kiểm tra biến chỉ in tên khoá, không in giá trị.

### Exercise 3.2: So sánh `railway.toml` và `render.yaml`

| Khía cạnh | `railway.toml` | `render.yaml` |
|---|---|---|
| Định dạng / phạm vi | TOML, cấu hình **một service** (build + deploy) | YAML Blueprint, khai báo **nhiều service** cùng lúc (web + Redis/Key Value) — Infrastructure as Code |
| Build | `builder = "DOCKERFILE"` (bản mẫu `03-cloud-deployment` dùng `NIXPACKS` + `startCommand`) | `runtime: docker` + `dockerfilePath`, `rootDir` (bản mẫu dùng `runtime: python` + `buildCommand`/`startCommand`) |
| Health check | `healthcheckPath = "/health"` | `healthCheckPath: /health` |
| Biến môi trường / secret | Không khai báo trong file; đặt bằng `railway variables` hoặc dashboard | Khai báo trong file; secret dùng `sync: false` (nhập tay) hoặc `generateValue: true` (Render tự sinh) |
| Redis | Thêm Redis riêng trên dashboard, rồi trỏ `REDIS_URL` | Khai báo service Redis/Key Value ngay trong blueprint, nối bằng `fromService` |
| Restart | `restartPolicyType = "ON_FAILURE"` | Render tự quản lý |
| Deploy | `railway up` từ CLI (upload thư mục) | Kết nối repo GitHub, Render đọc blueprint và deploy khi push (`autoDeploy`) |

Vì sao khác: Railway thiên về CLI và cấu hình từng service, môi trường đặt ngoài file; Render coi blueprint là nguồn sự thật cho toàn bộ hạ tầng nên file mô tả cả service phụ và cách cấp secret.

### Exercise 3.3: Pipeline Cloud Run (`cloudbuild.yaml`, `service.yaml`)

Khi push lên `main`, Cloud Build chạy 4 bước nối tiếp (`waitFor`): (1) **test** — cài requirements và chạy `pytest` trong `python:3.11-slim`, fail thì dừng pipeline; (2) **build** image Docker, gắn tag `$COMMIT_SHA` và `latest`, dùng `--cache-from latest` để tận dụng layer cache; (3) **push** image lên Container Registry; (4) **deploy** lên Cloud Run region `asia-southeast1` bằng image đúng commit, `min-instances=1` (tránh cold start), `max-instances=10`, 512Mi/1 CPU, secret lấy từ Secret Manager (`--set-secrets`). `service.yaml` mô tả cùng service theo dạng IaC: autoscale 1–10, `containerConcurrency: 80`, liveness probe `/health`, startup probe `/ready`, secret qua `secretKeyRef`.

---

## Part 4: API Security

### Exercise 4.1: API Key (`04-api-gateway/develop/app.py`)

- **Key được check ở đâu:** dependency `verify_api_key` (dòng 39–54), gắn vào `/ask` bằng `Depends(verify_api_key)` (dòng 70). Header đọc bằng `APIKeyHeader(name="X-API-Key", auto_error=False)`.
- **Sai key:** thiếu key → **401**; key sai → **403** "Invalid API key". (Part 6 theo hợp đồng trả 401 cho cả hai trường hợp.)
- **Rotate key:** key đọc từ env `AGENT_API_KEY` (dòng 35) → đổi giá trị biến môi trường trên platform rồi restart, không cần sửa code. Để không làm gián đoạn client, có thể cho phép tạm thời cả key cũ và mới, báo client đổi, rồi gỡ key cũ.

Chạy với `AGENT_API_KEY=secret-key-123 python app.py`:

```text
# Không key
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

# Có key đúng
HTTP/1.1 200 OK
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

```text
# Key sai (X-API-Key: wrong)
HTTP/1.1 403 Forbidden
content-length: 29
content-type: application/json
```

Ba trường hợp cho ba mã khác nhau: thiếu key → 401, key sai → 403, key đúng → 200. Bản develop phân biệt "chưa xác thực" với "bị từ chối". Hợp đồng của Part 6 gộp cả hai thành 401 để client không phân biệt được "key không tồn tại" với "key sai".

Ghi chú: khi body JSON bị hỏng (do quote sai trong PowerShell), app trả **500** vì `await request.json()` (dòng 73) không bắt lỗi. Part 6 dùng Pydantic model (`AskRequest`) nên body sai trả **422** rõ ràng, không lộ stack trace (đã kiểm tra: thiếu trường, `question` quá dài và body không phải JSON đều trả 422 và không có `Traceback`).

### Exercise 4.2: JWT (`04-api-gateway/production/auth.py`)

Flow: client `POST /auth/token` với username/password → server kiểm tra `DEMO_USERS`, tạo JWT ký HS256 bằng `JWT_SECRET`, payload gồm `sub`, `role`, `exp` (hết hạn sau 60 phút) → client gửi `Authorization: Bearer <token>` → `verify_token` giải mã, kiểm chữ ký và hạn, lấy `role` mà không cần tra database.

Lấy token (tài khoản `student` / `demo123`, token đã được che):

```text
$ curl -i -X POST localhost:8000/auth/token -H 'Content-Type: application/json' -d '{"username":"student","password":"demo123"}'
HTTP/1.1 200 OK
content-type: application/json
x-content-type-options: nosniff
x-frame-options: DENY
x-xss-protection: 1; mode=block
referrer-policy: strict-origin-when-cross-origin

{"access_token":"eyJ...[đã che]","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer ..."}
```

Quan sát: phản hồi còn có các header bảo mật (`x-frame-options: DENY`, `x-content-type-options: nosniff`...) do middleware `security_headers` của bản production thêm vào; bản develop không có. Token hết hạn sau 60 phút, khớp với `ACCESS_TOKEN_EXPIRE_MINUTES = 60` trong `auth.py`.

Gọi `/ask` bằng token (`Authorization: Bearer <token>`):

```text
$ curl -i -X POST localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"question":"Explain JWT"}'
HTTP/1.1 200 OK
content-length: 184
content-type: application/json
x-content-type-options: nosniff
x-frame-options: DENY
x-xss-protection: 1; mode=block
referrer-policy: strict-origin-when-cross-origin
```

Quan sát: không cần tra database hay so key cố định, server chỉ kiểm chữ ký và hạn của token. Phản hồi 200 không kèm header `x-ratelimit-*`.

### Exercise 4.3: Rate limiting (`04-api-gateway/production/rate_limiter.py`)

- **Algorithm:** sliding window — mỗi user giữ một `deque` các timestamp, bỏ timestamp cũ hơn 60 giây rồi đếm (đúng ra là *sliding window log*).
- **Limit:** user thường 10 request/phút; admin 100 request/phút.
- **Bypass cho admin:** `app.py` dòng 141 chọn `rate_limiter_admin` khi `role == "admin"` (tài khoản `teacher`), nên admin có hạn mức cao hơn.
- **Hạn chế:** state nằm trong RAM của process → nhiều instance/worker thì mỗi cái đếm riêng, restart là mất. Part 6 chuyển sang Redis sorted set.

Vòng lặp 15 request liên tiếp với token của `student` (chỉ in mã HTTP):

```text
1: 200   2: 200   3: 200   4: 200   5: 200
6: 200   7: 200   8: 200   9: 200   10: 200
11: 429  12: 429  13: 429  14: 429  15: 429
```

Kết quả: đúng 10 request đầu qua, request thứ 11 trở đi bị chặn, khớp giới hạn 10 request/phút của `rate_limiter_user`. Cả 5 request bị chặn đều nằm trong cùng cửa sổ 60 giây nên chưa có lượt nào được giải phóng.

Chi tiết một request bị chặn (`curl -i`, token đã che):

```text
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795596610
retry-after: 36
content-type: application/json

{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":36}}
```

`retry-after: 36` cho client biết phải đợi 36 giây nữa mới được gọi lại: đó là thời gian còn lại cho đến khi request cũ nhất trong cửa sổ 60 giây trượt ra ngoài (`oldest + window − now`, `rate_limiter.py`). Tức là cửa sổ trượt thật sự, không phải đếm lại từ đầu mỗi phút.

### Exercise 4.4: Cost guard

Cách làm trong Part 6 (`06-lab-complete/app/cost_guard.py`):
- Sau mỗi lần gọi LLM, ước lượng token (~4 ký tự/token) cho câu hỏi và câu trả lời, tính `cost = input/1000 × 0.00015 + output/1000 × 0.0006` (giá gpt-4o-mini), cộng dồn vào Redis bằng `INCRBYFLOAT` ở key theo ngày `cost:YYYY-MM-DD`, đặt `EXPIRE` 2 ngày để key cũ tự xoá (tự reset mỗi ngày).
- Trước khi gọi LLM, đọc tổng chi phí trong ngày; nếu ≥ `DAILY_BUDGET_USD` thì trả **402 Payment Required**.
- Lưu trong Redis (không phải RAM) để mọi worker/instance dùng chung một con số và không mất khi restart. Bản mẫu `04-api-gateway/production/cost_guard.py` lưu in-memory nên mỗi instance tính riêng.

Rate limit bảo vệ **tốc độ** (chống spam, chống một client chiếm tài nguyên trong thời gian ngắn); cost guard bảo vệ **tiền** (tổng chi trong ngày). Một client gọi đều dưới rate limit cả ngày vẫn có thể làm cháy budget, nên cần cả hai.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop/app.py`)

- `/health` = **liveness**: process còn sống không (trả uptime, % memory). Fail → platform restart container.
- `/ready` = **readiness**: có nên nhận traffic không. Trả 503 khi `_is_ready = False` (đang khởi động hoặc đang shutdown) → load balancer ngừng gửi request mới nhưng không restart.
- Trong Part 6: `/health` luôn 200 dù Redis chết (process vẫn sống, restart cũng không sửa được Redis); `/ready` trả 503 khi không ping được Redis.

### Exercise 5.2: Graceful shutdown

`lifespan` của bản develop: khi shutdown đặt `_is_ready = False` (để `/ready` trả 503), rồi chờ `_in_flight_requests` về 0 (tối đa 30 giây) trước khi thoát. Middleware `track_requests` tăng/giảm bộ đếm cho mỗi request. Signal handler chỉ ghi log, việc tắt do uvicorn đảm nhận.

Test: chạy `python app.py` trong WSL, gửi 2 request `/ask` chạy nền, sau 0,5 giây gửi `kill -TERM <PID>` (PID lấy từ dòng `Started server process [5076]`).

Kết quả ở terminal gửi lệnh:

```text
req1: 200
req2: 000
```

Log của server, theo đúng thứ tự:

```text
INFO:     Shutting down
INFO:     127.0.0.1:58478 - "POST /ask?question=hello HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-25 15:50:53,403 INFO 🔄 Graceful shutdown initiated...
2026-11-25 15:50:53,403 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [5076]
2026-11-25 15:50:53,403 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

Nhận xét:
- Dòng `POST /ask ... 200 OK` nằm **sau** `Shutting down`: request đang chạy khi SIGTERM đến vẫn được xử lý xong rồi process mới thoát. Đó là hành vi graceful shutdown cần chứng minh.
- `req2` trả `000` (curl không nhận được phản hồi HTTP) và không có dòng log nào của request này trên server: kết nối đến sau khi uvicorn nhận SIGTERM và đã đóng cổng nghe nên bị từ chối. Đây là race giữa lúc spawn `curl` và lúc gửi tín hiệu, không phải request bị cắt ngang. Hai request cần cùng vào server trước tín hiệu thì mới thấy cả hai được hoàn tất.
- Lần thử đầu tiên bị lỗi `syntax error` do gõ nguyên văn `<PID>` vào lệnh `kill` (dấu `<` `>` là toán tử của bash), nên SIGTERM không được gửi; hai request lúc đó trả 200 nhưng không chứng minh được gì. Chỉ lần thử có PID thật mới có giá trị làm bằng chứng.
- `handle_sigterm` trong app chỉ ghi log (`Received signal 15`); việc dừng nhận kết nối mới và chờ request đang chạy do uvicorn đảm nhận (`timeout_graceful_shutdown=30`).

### Exercise 5.3: Stateless design

`05-scaling-reliability/production/app.py` lưu session/history vào Redis (`session:{id}`, `setex` TTL 1 giờ) thay vì dict trong RAM. Khi chạy nhiều instance sau load balancer, request kế tiếp của cùng user có thể đến instance khác; nếu history nằm trong RAM của instance trước thì instance sau không thấy. Đặt trong Redis thì instance nào cũng đọc được.

### Exercise 5.4 + 5.5: Load balancing và test stateless

```bash
cd 05-scaling-reliability/production
touch .env.local
docker compose -p k5-day14-scaling up -d --build --scale agent=3
python test_stateless.py
docker compose -p k5-day14-scaling down
```

Trạng thái stack sau khi bật (`docker compose -p k5-day14-scaling ps`, sau khoảng 35 giây):

```text
k5-day14-scaling-agent-1   Up 35 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-2   Up 35 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-3   Up 35 seconds (healthy)   8000/tcp
k5-day14-scaling-nginx-1   Up 35 seconds             0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 46 seconds (healthy)   6379/tcp
```

Output `python test_stateless.py` (rút gọn phần câu trả lời):

```text
Session ID: 3bc7cf2d-a5ff-4bce-a8d8-d17c8c73265f

Request 1: [instance-fcce9a]   Q: What is Docker?
Request 2: [instance-6f3575]   Q: Why do we need containers?
Request 3: [instance-36c146]   Q: What is Kubernetes?
Request 4: [instance-fcce9a]   Q: How does load balancing work?
Request 5: [instance-6f3575]   Q: What is Redis used for?

Total requests: 5
Instances used: {'instance-fcce9a', 'instance-36c146', 'instance-6f3575'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user] / [assistant] xen kẽ, đủ 5 lượt hỏi đáp
✅ Session history preserved across all instances via Redis!
```

Giải thích: 5 request cùng một `session_id` đi qua 3 instance khác nhau theo kiểu round-robin của Nginx (`fcce9a → 6f3575 → 36c146 → fcce9a → 6f3575`), nhưng khi đọc lại, lịch sử vẫn đủ **10 tin nhắn** (5 câu hỏi + 5 câu trả lời), đúng thứ tự. Mỗi instance có RAM riêng, nên nếu history nằm trong RAM thì instance xử lý request 2 sẽ không thấy câu hỏi ở request 1. Chỉ khi lưu trong Redis dùng chung (`session:{id}`) thì kết quả mới đủ như trên. Dòng "✅" cuối của script luôn được in kể cả khi số tin nhắn bị thiếu, nên con số đáng tin để đối chiếu là `Total messages: 10`, không phải biểu tượng ✅. Lưu ý: script không tắt instance nào, nên kết quả này chứng minh state được chia sẻ giữa các instance chứ chưa chứng minh việc tồn tại khi một instance chết.

---

## Part 6: Final Project — output `check_production_ready.py`

Chạy trong WSL2 (`os.name == posix`), có Docker:

```bash
cd 06-lab-complete
python check_production_ready.py
```

Kết quả (chạy đầy đủ, không `--skip-docker` / `--static-only`):

```text
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Một số con số đáng chú ý trong output: image 273 MB (ngưỡng 500 MB), `docker stop` mất 0.7 giây với exit code 0, rate limit trả 429 sau đúng 5 request thành công (khớp `RATE_LIMIT_PER_MINUTE=5` do script đặt), restart agent vẫn bị 429 (state nằm trong Redis chứ không nằm trong RAM của process).

Nhận xét: tiêu chí Graceful shutdown chỉ đạt khi chạy Python trong WSL2. Trên Windows, script không gửi được SIGTERM nên mục này luôn ở trạng thái ⚠️. Vì vậy phần cài WSL2 Ubuntu ở bước setup, dù mất nhiều thời gian, là điều kiện để lấy được `PASS 12/12`.

---

## AI log

Công cụ: Claude Code trong VS Code.

| # | Phần | Hỏi gì | Nhận gì | Tự sửa / kiểm tra thế nào |
|---|---|---|---|---|
| 1 | Setup (CP0) | Cách xử lý lỗi Python 3.14 không build được `pydantic-core==2.23.2` và Docker trong WSL báo lỗi `npipe` | Gợi ý tạo venv Python 3.12 bằng `uv`, bật WSL Integration trong Docker Desktop | Chỉnh môi trường rồi chạy lại: `os.name` trả `posix`, Python 3.12.16, `docker run hello-world` chạy được. Phần setup khá mất thời gian; kiểm tra phiên bản và môi trường từ đầu giúp khoanh vùng lỗi dễ hơn. |
| 2 | Part 1, 2, 4 | Nhờ giải thích lỗi `curl` trong PowerShell, lỗi URL có dấu cách, 422 khi gửi JSON cho bản develop, 500 khi body JSON bị vỡ do quote | Giải thích bản develop nhận `question` qua query, dùng `curl.exe` / WSL, cách quote JSON đúng; lỗi 500 do `await request.json()` không bắt lỗi | Chỉnh lệnh gọi, chạy lại và đối chiếu phản hồi (200 / 401) với yêu cầu từng bài. Dễ nhầm lỗi gửi request với lỗi logic app, nên cần xem cả endpoint lẫn dữ liệu gửi đi. |
| 3 | Part 6 | Viết bản tham khảo cho các TODO trong `06-lab-complete/` (`app/*.py`, `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml`) | Bản tham khảo do AI viết (chạy script chấm đạt PASS 12/12), kèm giải thích từng file và danh sách câu hỏi để tự kiểm tra | Gỡ bản của AI ra khỏi repo, rồi tự viết lại từng file, có đối chiếu với bản tham khảo. Các chỗ tự thay đổi so với bản AI: rate limiter dùng Lua script để đếm và ghi trong Redis là một thao tác nguyên tử (tránh 2 worker cùng lọt qua giới hạn); so sánh API key trên bytes (tránh lỗi 500 khi header có ký tự ngoài ASCII); `pipeline(transaction=True)` cho cost guard; `try/finally` trong `lifespan`; đồng bộ `PORT` giữa map cổng và biến môi trường trong compose. Nhờ AI review bản đã viết lại, rồi tự chạy `python check_production_ready.py` trong WSL → PASS 12/12. |
| 4 | Part 1–5, Part 3 | Soạn nháp câu trả lời lý thuyết (anti-pattern, bảng so sánh, câu hỏi Dockerfile, so sánh `railway.toml`/`render.yaml`, pipeline Cloud Run, JWT, rate limit, cost guard, stateless) và khung `DEPLOYMENT.md` | Bản nháp dựa trên code mẫu của từng phần | Đối chiếu từng ý với code và output tự chạy; mọi output, số đo, URL deploy và ảnh chụp trong bài do tự chạy, không lấy từ AI. Sửa lại khối lệnh test trong `DEPLOYMENT.md`. |
| 5 | Part 7 | Viết job CI cho quality gate (`.github/workflows/quality-gate.yml`) | File workflow chạy agent v2 rồi `run_eval.py --min-accuracy 0.85` | Đọc lại để hiểu cơ chế: `run_eval.py` trả exit code 1 khi không đạt nên job fail và PR bị chặn merge. |
| 6 | Part 7 (CP7) | Hướng dẫn chạy load test 4 mức concurrency, 3 mức `MOCK_SLOW_RATE`, eval v1/v2; soạn `BENCHMARK_REPORT.md` mục 1, 2, 3 từ số liệu đã chạy | Lệnh chạy từng bước và bản nháp báo cáo | Tự chạy mọi lệnh và tự thêm hai entry `KNOWLEDGE_BASE` (`jwt`; `kubernetes`/`hpa`) để v2 đạt 23/23; đối chiếu các số trong báo cáo với file JSON trong `reports/`. AI review hai entry và kiểm tra offline 23/23 câu trước khi chạy eval thật. |
| 7 | Part 3, Part 6 (CP6) | Hướng dẫn deploy Railway từng bước, kiểm tra cú pháp CLI, điền `DEPLOYMENT.md` và mục 3.1 | Chuỗi lệnh `railway`, cách nạp `AGENT_API_KEY` qua `stdin` để không in ra màn hình | Tự đăng nhập, tạo project, chạy các lệnh, chụp 3 ảnh và tự chạy 4 lệnh kiểm tra (200, 200, 401, 200). Key chỉ nằm trong biến shell và trên Railway, không có trong repo hay ảnh chụp. |
| 8 | Part 2.4 | Chẩn đoán lỗi cổng 80 khi bật Nginx, viết `02-docker/production/docker-compose.port8081.yml` | Phát hiện `PID 4` (`http.sys`) giữ cổng 80; file ghi đè `ports: !override ["8081:80"]` | Tự chạy lại stack với cổng 8081, kiểm tra `curl` qua Nginx. File compose mẫu của đề giữ nguyên. |
| 9 | Part 7 (CP8) | Chẩn đoán và sửa lỗi stack: `ModuleNotFoundError: uvicorn` (thiếu `PYTHONUSERBASE` trong Dockerfile), lỗi credential helper của Docker (`error getting credentials`), phát hiện `deploy.replicas: 2` làm số instance sai khi chạy `docker compose run` | Giải thích nguyên nhân, gợi ý thêm `ENV PYTHONUSERBASE=/home/agent/.local`, dùng `DOCKER_CONFIG` rỗng, dùng `--no-deps` | Tự sửa Dockerfile, tự chạy `scale_1` và `scale_3` (có đối chiếu bằng `docker ps` và danh sách target Prometheus), tự chụp ảnh Grafana và Jaeger. |
| 10 | Part 7 (CP8) | Chạy trực tiếp trên máy theo yêu cầu: script nền chạy thí nghiệm A–E (sửa `.env`, `docker compose up`, bơm tải bằng Locust API, đo thời gian alert bằng API Prometheus), unit test alert bằng `promtool`, soạn `BENCHMARK_REPORT.md` mục 4 và 6 | Các số đo thời gian alert, dòng log khớp trace, bản nháp phân tích | Số liệu thí nghiệm được tạo bởi lệnh thật trên máy này và lưu trong output script; tự chụp ảnh Prometheus Alerts và trace lỗi. Lưu ý trung thực: phần thí nghiệm này do AI chạy theo yêu cầu, không phải tự gõ từng lệnh. |
| 11 | Part 7 (Exercise 7.1) | Thêm metric `agent_question_length_chars` vào `develop/app.py` và trả lời hai câu hỏi về loại metric và cardinality | Đoạn code thêm `Histogram` và `observe()` theo hướng dẫn của `CODE_LAB.md`, bản trả lời trong `BENCHMARK_REPORT.md` (phụ lục) | AI viết đoạn code; đã chạy agent thật, gửi 3 câu hỏi dài 7/70/300 ký tự và đối chiếu bucket, `count`, `sum` trên `/metrics`. Các số trong `reports/` đo trước khi thêm metric và không bị ảnh hưởng. |
