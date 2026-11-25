"""
Production AI Agent — Part 6 Final Project

Chạy: uvicorn app.main:app --workers 2
Kiểm tra: python check_production_ready.py
"""
import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import redis_healthy
from utils.mock_llm import ask as llm_ask


# ── Structured logging: mỗi dòng log của app là 1 JSON object ──
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "lvl": record.levelname,
            "event": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)  # stack trace chỉ nằm trong log, không trả cho client
        return json.dumps(entry, ensure_ascii=False)


logger = logging.getLogger("agent")
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(JsonFormatter())
logger.handlers = [_handler]
logger.setLevel(settings.log_level)
logger.propagate = False  # không để root logger in thêm một dòng không phải JSON


def log(event: str, level: int = logging.INFO, **fields):
    logger.log(level, event, extra={"fields": fields})


# ── Graceful shutdown ──
# uvicorn bắt SIGTERM: ngừng nhận kết nối mới, chờ request đang chạy xong, rồi chạy phần sau `yield`.
# Không tự đăng ký signal handler để khỏi chặn mất handler của uvicorn.
@asynccontextmanager
async def lifespan(app: FastAPI):
    log("startup", environment=settings.environment, port=settings.port,
        rate_limit_per_minute=settings.rate_limit_per_minute, daily_budget_usd=settings.daily_budget_usd)
    try:
        yield
    finally:
        log("shutdown", note="SIGTERM received, in-flight requests finished")


app = FastAPI(title="Production AI Agent", lifespan=lifespan)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started_at = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    log("request", method=request.method, path=request.url.path,
        status=response.status_code, ms=round(elapsed_ms, 1))
    return response


@app.exception_handler(redis.RedisError)
async def redis_error_handler(request: Request, exc: redis.RedisError):
    # Redis mất kết nối giữa chừng → báo 503 rõ ràng thay vì 500 chung chung
    log("redis_error", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Storage unavailable, please retry"})


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=100)


@app.get("/health")
def health():
    """Liveness: process còn chạy → 200, không phụ thuộc Redis."""
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Readiness: chỉ nhận traffic khi Redis kết nối được."""
    if not redis_healthy():
        raise HTTPException(status_code=503, detail="Redis not reachable")
    return {"status": "ready"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    check_rate_limit(api_key)   # 429
    check_budget()              # 402
    answer = llm_ask(body.question)
    input_tokens = estimate_tokens(body.question)
    output_tokens = estimate_tokens(answer)
    request_cost = record_cost(input_tokens, output_tokens)
    return {
        "question": body.question,
        "answer": answer,
        "cost_usd": round(request_cost, 8),
    }
