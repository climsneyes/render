from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from app.services.ai_service import diagnose_gemini_api
from app.config import settings

router = APIRouter(prefix="/diagnosis", tags=["Diagnosis"])

class DiagnosisRequest(BaseModel):
    api_key: Optional[str] = None

@router.post("/check")
def check_api_diagnosis(req: DiagnosisRequest):
    api_key = req.api_key.strip() if req.api_key else settings.GEMINI_API_KEY
    result = diagnose_gemini_api(api_key)
    return result

