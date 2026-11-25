# Deployment Information

## Public URL

https://day14-agent-production-29a54820.up.railway.app

## Platform

Railway — deploy thư mục `06-lab-complete/` bằng `Dockerfile` (cấu hình trong `06-lab-complete/railway.toml`), kèm một Redis trên Railway.

## Test Commands

```bash
read -rp 'Public URL của service Railway: ' BASE
BASE=${BASE%/}
: "${AGENT_API_KEY:?Cần đặt AGENT_API_KEY trong shell trước khi kiểm tra}"

curl -sS -o /dev/null -w 'GET /health  %{http_code}\n' "$BASE/health"
curl -sS -o /dev/null -w 'GET /ready   %{http_code}\n' "$BASE/ready"
curl -sS -o /dev/null -w 'POST /ask (không key)  %{http_code}\n' "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'
curl -sS -o /dev/null -w 'POST /ask (có key)     %{http_code}\n' "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'
```

Kết quả thực tế (chạy ngày 2026-11-25, bằng `curl.exe` trong PowerShell, key lấy từ biến môi trường của shell nên không in ra màn hình):

```text
GET  /health          200
GET  /ready           200
POST /ask (khong key) 401
POST /ask (co key)    200
```

`/ready` trả 200 nghĩa là agent trên Railway kết nối được tới Redis; `/ask` không key trả 401 và có key đúng trả 200.

### API Test (with authentication)

```bash
curl -X POST "$BASE/ask" \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "Hello"}'
```

## Environment Variables Set (trên Railway, không ghi trong code)

- `AGENT_API_KEY` — secret, sinh ngẫu nhiên bằng `secrets.token_urlsafe(24)` và nạp qua `stdin` của `railway variable set`, không xuất hiện trong code hay lịch sử lệnh
- `REDIS_URL` — tham chiếu `${{Redis.REDIS_URL}}` tới Redis service của Railway (mật khẩu Redis không nằm trong repo)
- `ENVIRONMENT=production`
- `RATE_LIMIT_PER_MINUTE=10`
- `DAILY_BUDGET_USD=1.0`
- `PORT` — Railway tự cấp

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
