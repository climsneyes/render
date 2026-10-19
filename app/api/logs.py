from datetime import datetime
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from app.services.email_service import send_error_report

router = APIRouter(prefix="/logs", tags=["Logs"])

# 인메모리 로그 저장소
_logs: List[Dict[str, Any]] = []

class LogEntry(BaseModel):
    level: str
    stage: str
    message: str
    detail: Optional[str] = ""

class ErrorReportRequest(BaseModel):
    subject: str
    body: str

def add_server_log(level: str, stage: str, message: str, detail: str = ""):
    global _logs
    timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
    _logs.append({
        'time': timestamp,
        'level': level,
        'stage': stage,
        'message': message,
        'detail': detail
    })
    if len(_logs) > 300:
        _logs = _logs[-300:]

@router.get("")
def get_logs():
    return {"logs": _logs}

@router.post("")
def create_log(entry: LogEntry):
    add_server_log(entry.level, entry.stage, entry.message, entry.detail or "")
    return {"success": True}

@router.delete("")
def clear_logs():
    global _logs
    _logs.clear()
    return {"success": True}

@router.post("/send-error")
def send_error(req: ErrorReportRequest):
    success = send_error_report(req.subject, req.body)
    return {"success": success}

