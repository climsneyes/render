import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from typing import Optional, Dict, Any
from app.config import settings


def send_error_report(
    subject: str,
    body: str,
    attachment_bytes: Optional[bytes] = None,
    attachment_filename: Optional[str] = None,
    recipient: Optional[str] = None
) -> Dict[str, Any]:
    """오류 보고서 이메일 전송 (수신자: lsh4676@korea.kr 기본값)"""
    target_receiver = recipient or settings.EMAIL_RECEIVER or "lsh4676@korea.kr"

    if not settings.EMAIL_SENDER or not settings.EMAIL_PASSWORD:
        return {
            "success": False,
            "error_type": "SMTP_NOT_CONFIGURED",
            "message": f"서버 발신용 이메일(SMTP) 계정이 설정되지 않았습니다. 관리자({target_receiver})에게 직접 발송할 수 있도록 안내합니다.",
            "recipient": target_receiver
        }

    try:
        msg = MIMEMultipart()
        msg['From'] = settings.EMAIL_SENDER
        msg['To'] = target_receiver
        msg['Subject'] = f"[조례 분석 서비스 오류 제보] {subject}"

        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        if attachment_bytes and attachment_filename:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(attachment_bytes)
            encoders.encode_base64(part)
            part.add_header(
                'Content-Disposition',
                f'attachment; filename="{attachment_filename}"'
            )
            msg.attach(part)

        if settings.EMAIL_SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(settings.EMAIL_SMTP_SERVER, settings.EMAIL_SMTP_PORT, timeout=10)
        else:
            server = smtplib.SMTP(settings.EMAIL_SMTP_SERVER, settings.EMAIL_SMTP_PORT, timeout=10)
            server.starttls()
        server.login(settings.EMAIL_SENDER, settings.EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        return {
            "success": True,
            "message": f"성공적으로 오류 제보가 {target_receiver}로 전송되었습니다. 소중한 의견 감사합니다!",
            "recipient": target_receiver
        }
    except Exception as e:
        return {
            "success": False,
            "error_type": "SMTP_SEND_FAILED",
            "message": f"이메일 전송 중 오류 발생: {str(e)}",
            "recipient": target_receiver
        }

