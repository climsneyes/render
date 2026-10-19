import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from typing import Optional
from app.config import settings


def send_error_report(
    subject: str,
    body: str,
    attachment_bytes: Optional[bytes] = None,
    attachment_filename: Optional[str] = None
) -> bool:
    """오류 보고서 이메일 전송"""
    if not settings.EMAIL_SENDER or not settings.EMAIL_PASSWORD or not settings.EMAIL_RECEIVER:
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = settings.EMAIL_SENDER
        msg['To'] = settings.EMAIL_RECEIVER
        msg['Subject'] = f"[조례 분석 서비스 오류 보고] {subject}"

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

        server = smtplib.SMTP(settings.EMAIL_SMTP_SERVER, settings.EMAIL_SMTP_PORT)
        server.starttls()
        server.login(settings.EMAIL_SENDER, settings.EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception:
        return False

