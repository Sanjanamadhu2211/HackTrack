from fastapi import APIRouter, Depends, Request

from app.auth import require_user_dependency
from app.errors import body_bytes
from app.modules.capture.service import capture_data

router = APIRouter()


@router.post("/capture")
async def capture(request: Request, user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    data = await body_bytes()(request)
    return capture_data(user_id, data.get("url", ""), data.get("text", ""), bool(data.get("use_ai")))
