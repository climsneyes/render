from datetime import datetime
from fastapi import APIRouter, Form, File, UploadFile
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from app.services.email_service import send_error_report
from app.config import settings

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
    result = send_error_report(req.subject, req.body)
    return {"success": result.get("success", False)}

@router.post("/report-error")
async def api_report_error(
    content: str = Form(...),
    file: Optional[UploadFile] = File(None)
):
    attachment_bytes = None
    attachment_filename = None
    if file:
        attachment_bytes = await file.read()
        attachment_filename = file.filename

    lines = [line.strip() for line in content.split('\n') if line.strip()]
    first_line = lines[0][:40] if lines else "사용자 오류 제보"
    subject = f"{first_line}"
    body = (
        f"수신처: lsh4676@korea.kr\n"
        f"제보 일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"----------------------------------------\n"
        f"[오류 내용 / 불편 사항]:\n{content}\n"
        f"----------------------------------------\n"
    )
    if attachment_filename:
        body += f"첨부 파일명: {attachment_filename}\n"

    result = send_error_report(
        subject=subject,
        body=body,
        attachment_bytes=attachment_bytes,
        attachment_filename=attachment_filename,
        recipient="lsh4676@korea.kr"
    )
    return result

