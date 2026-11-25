"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

  • Đọc REDIS_URL từ config
  • Không ping lúc import → app vẫn khởi động được khi Redis chưa sẵn sàng
  • redis_healthy() dùng cho GET /ready
"""
import redis

from app.config import settings

# from_url chỉ tạo connection pool, chưa kết nối thật. Timeout ngắn để /ready trả 503 nhanh khi Redis chết.
r = redis.Redis.from_url(
    url=settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=2,
)


def redis_healthy() -> bool:
    try:
        return r.ping() is True
    except redis.RedisError:
        return False
