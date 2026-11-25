"""
API Key authentication (tiêu chí 6).

  • Client gửi header  X-API-Key: <key>
  • Key phải trùng biến môi trường AGENT_API_KEY
  • Thiếu hoặc sai key → HTTP 401
"""
import secrets

from fastapi import HTTPException, Security
from fastapi.security.api_key import APIKeyHeader

from app.config import settings

# auto_error=False: thiếu header thì FastAPI không tự trả 403/422, ta tự trả 401
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """Dependency cho FastAPI — dùng trong /ask: Depends(verify_api_key)."""
    expected = settings.agent_api_key
    # compare_digest: so sánh thời gian hằng, tránh đoán key qua timing; key rỗng trên server → luôn từ chối
    if api_key and expected:
        if secrets.compare_digest(api_key.encode("utf-8"), expected.encode("utf-8")):
            return api_key
    raise HTTPException(
        status_code=401,
        detail="Missing or invalid API key (header X-API-Key)",
    )
