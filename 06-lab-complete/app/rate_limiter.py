"""
Rate limiting (tiêu chí 7).

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây (sliding window)
  • Vượt giới hạn → HTTP 429 + header Retry-After
  • State lưu trong Redis (sorted set) → mọi worker / instance dùng chung, còn nguyên sau khi restart agent
"""
import hashlib
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60

# Thực hiện kiểm tra và ghi nhận cùng lúc giữa các worker.
SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
if redis.call('ZCARD', key) >= tonumber(ARGV[3]) then
    return 0
end
redis.call('ZADD', key, now, ARGV[4])
redis.call('EXPIRE', key, window)
return 1
"""


def check_rate_limit(key: str) -> None:
    # Băm key để không lưu API key dạng rõ trong Redis
    redis_key = "ratelimit:" + hashlib.sha256(key.encode()).hexdigest()[:16]
    now = time.time()

    allowed = r.eval(
        SLIDING_WINDOW_SCRIPT, 1, redis_key, now, WINDOW_SECONDS,
        settings.rate_limit_per_minute, f"{now}:{uuid.uuid4().hex}",
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {settings.rate_limit_per_minute} requests / {WINDOW_SECONDS}s",
            headers={"Retry-After": str(WINDOW_SECONDS)},
        )

