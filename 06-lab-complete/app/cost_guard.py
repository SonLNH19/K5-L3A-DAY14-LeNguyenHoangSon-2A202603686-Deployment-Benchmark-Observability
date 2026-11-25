"""
Cost guard (tiêu chí 8).

  • Cộng dồn chi phí LLM (tính theo token) vào Redis, theo ngày (UTC)
  • Tổng chi phí trong ngày đạt DAILY_BUDGET_USD → HTTP 402
"""
from datetime import datetime, timezone

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

# Giá USD / 1K tokens (tham khảo gpt-4o-mini)
PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006
COST_RETENTION_SECONDS = 48 * 60 * 60


def _today_key() -> str:
    day = datetime.now(timezone.utc).date().isoformat()
    return f"cost:{day}"


def estimate_tokens(text: str) -> int:
    """Ước lượng ~4 ký tự / token (mock LLM không trả usage thật)."""
    return max(1, len(text) // 4)


def check_budget() -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget → 402."""
    spent = float(r.get(_today_key()) or 0)
    if spent >= settings.daily_budget_usd:
        raise HTTPException(
            status_code=402,
            detail=f"Daily budget exhausted (${spent:.6f} / ${settings.daily_budget_usd})",
        )


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí request vào tổng ngày, trả về chi phí request."""
    cost = input_tokens / 1000 * PRICE_INPUT_PER_1K + output_tokens / 1000 * PRICE_OUTPUT_PER_1K
    key = _today_key()
    with r.pipeline(transaction=True) as transaction:
        transaction.incrbyfloat(key, cost)
        transaction.expire(key, COST_RETENTION_SECONDS)
        transaction.execute()
    return cost
