import os
import smtplib
import base64
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from typing import Optional, Dict, Any
from app.config import settings


def _send_via_resend(
    subject: str,
    body: str,
    recipient: str,
    attachment_bytes: Optional[bytes] = None,
    attachment_filename: Optional[str] = None
) -> Dict[str, Any]:
    """Resend HTTP REST API를 통한 이메일 발송 (Render 포트 차단 우회)"""
    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {settings.RESEND_API_KEY.strip()}",
        "Content-Type": "application/json"
    }

    # 발신자 주소: 등록된 도메인이 없을 경우 기본 온보딩 주소 사용
    sender = settings.EMAIL_SENDER or "조례분석시스템 <onboarding@resend.dev>"

    payload: Dict[str, Any] = {
        "from": sender,
        "to": [recipient],
        "subject": f"[조례 분석 서비스 오류 제보] {subject}",
        "text": body,
    }

    if attachment_bytes and attachment_filename:
        encoded = base64.b64encode(attachment_bytes).decode("utf-8")
        payload["attachments"] = [
            {
                "filename": attachment_filename,
                "content": encoded
            }
        ]

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=12)
        if response.status_code in (200, 201):
            return {
                "success": True,
                "message": f"성공적으로 오류 제보가 {recipient}로 전송되었습니다. 소중한 의견 감사합니다!",
                "recipient": recipient
            }
        else:
            err_detail = response.text
            try:
                err_json = response.json()
                err_detail = err_json.get("message", response.text)
            except Exception:
                pass
            return {
                "success": False,
                "error_type": "RESEND_API_ERROR",
                "message": f"이메일 발송 API 실패 ({response.status_code}): {err_detail}",
                "recipient": recipient
            }
    except Exception as e:
        return {
            "success": False,
            "error_type": "HTTP_REQUEST_FAILED",
            "message": f"이메일 API 요청 중 네트워크 오류 발생: {str(e)}",
            "recipient": recipient
        }


def _send_via_web3forms(
    subject: str,
    body: str,
    recipient: str,
    attachment_bytes: Optional[bytes] = None,
    attachment_filename: Optional[str] = None
) -> Dict[str, Any]:
    """Web3Forms HTTP REST API를 통한 이메일 발송 (별도 도메인 인증 불필요, 가장 간편)"""
    url = "https://api.web3forms.com/submit"
    data = {
        "access_key": settings.WEB3FORMS_ACCESS_KEY.strip(),
        "subject": f"[조례 분석 서비스 오류 제보] {subject}",
        "from_name": "조례 분석 시스템 오류제보",
        "message": body,
    }

    files = None
    if attachment_bytes and attachment_filename:
        files = {
            "attachment": (attachment_filename, attachment_bytes)
        }

    try:
        response = requests.post(url, data=data, files=files, timeout=15)
        res_json = response.json()
        if res_json.get("success"):
            return {
                "success": True,
                "message": f"성공적으로 오류 제보가 {recipient}로 전송되었습니다. 소중한 의견 감사합니다!",
                "recipient": recipient
            }
        else:
            return {
                "success": False,
                "error_type": "WEB3FORMS_ERROR",
                "message": f"이메일 전송 실패: {res_json.get('message', '알 수 없는 오류')}",
                "recipient": recipient
            }
    except Exception as e:
        return {
            "success": False,
            "error_type": "HTTP_REQUEST_FAILED",
            "message": f"이메일 API 요청 중 오류 발생: {str(e)}",
            "recipient": recipient
        }


def _send_via_brevo(
    subject: str,
    body: str,
    recipient: str,
    attachment_bytes: Optional[bytes] = None,
    attachment_filename: Optional[str] = None
) -> Dict[str, Any]:
    """Brevo (Sendinblue) HTTP REST API를 통한 이메일 발송"""
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "api-key": settings.BREVO_API_KEY.strip(),
        "Content-Type": "application/json"
    }

    sender_email = settings.EMAIL_SENDER or "admin@ordinance-ai.com"
    payload: Dict[str, Any] = {
        "sender": {"email": sender_email, "name": "조례 분석 시스템"},
        "to": [{"email": recipient}],
        "subject": f"[조례 분석 서비스 오류 제보] {subject}",
        "textContent": body,
    }

    if attachment_bytes and attachment_filename:
        encoded = base64.b64encode(attachment_bytes).decode("utf-8")
        payload["attachment"] = [
            {
                "name": attachment_filename,
                "content": encoded
            }
        ]

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=12)
        if response.status_code in (200, 201, 202):
            return {
                "success": True,
                "message": f"성공적으로 오류 제보가 {recipient}로 전송되었습니다. 소중한 의견 감사합니다!",
                "recipient": recipient
            }
        else:
            return {
                "success": False,
                "error_type": "BREVO_API_ERROR",
                "message": f"Brevo API 전송 실패 ({response.status_code}): {response.text}",
                "recipient": recipient
            }
    except Exception as e:
        return {
            "success": False,
            "error_type": "HTTP_REQUEST_FAILED",
            "message": f"이메일 API 요청 중 오류 발생: {str(e)}",
            "recipient": recipient
        }


def _send_via_smtp(
    subject: str,
    body: str,
    recipient: str,
    attachment_bytes: Optional[bytes] = None,
    attachment_filename: Optional[str] = None
) -> Dict[str, Any]:
    """표준 SMTP를 통한 이메일 전송 (로컬 환경용)"""
    try:
        msg = MIMEMultipart()
        msg['From'] = settings.EMAIL_SENDER
        msg['To'] = recipient
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
            "message": f"성공적으로 오류 제보가 {recipient}로 전송되었습니다. 소중한 의견 감사합니다!",
            "recipient": recipient
        }
    except Exception as e:
        return {
            "success": False,
            "error_type": "SMTP_SEND_FAILED",
            "message": f"이메일 전송 중 오류 발생: {str(e)}",
            "recipient": recipient
        }


def send_error_report(
    subject: str,
    body: str,
    attachment_bytes: Optional[bytes] = None,
    attachment_filename: Optional[str] = None,
    recipient: Optional[str] = None
) -> Dict[str, Any]:
    """오류 보고서 이메일 전송 (우선순위: Resend HTTP API -> Brevo HTTP API -> SMTP)"""
    target_receiver = recipient or settings.EMAIL_RECEIVER or "lsh4676@korea.kr"

    # 1. Web3Forms HTTP REST API (추천: 도메인 인증 없이 수신 이메일로 즉시 발송 가능)
    if settings.WEB3FORMS_ACCESS_KEY and settings.WEB3FORMS_ACCESS_KEY.strip():
        return _send_via_web3forms(
            subject=subject,
            body=body,
            recipient=target_receiver,
            attachment_bytes=attachment_bytes,
            attachment_filename=attachment_filename
        )

    # 2. Resend HTTP REST API (추천: Render 클라우드 환경)
    if settings.RESEND_API_KEY and settings.RESEND_API_KEY.strip():
        return _send_via_resend(
            subject=subject,
            body=body,
            recipient=target_receiver,
            attachment_bytes=attachment_bytes,
            attachment_filename=attachment_filename
        )

    # 3. Brevo HTTP REST API
    if settings.BREVO_API_KEY and settings.BREVO_API_KEY.strip():
        return _send_via_brevo(
            subject=subject,
            body=body,
            recipient=target_receiver,
            attachment_bytes=attachment_bytes,
            attachment_filename=attachment_filename
        )

    # 4. 기존 SMTP (로컬 환경 또는 SMTP 허용 환경)
    if settings.EMAIL_SENDER and settings.EMAIL_PASSWORD:
        return _send_via_smtp(
            subject=subject,
            body=body,
            recipient=target_receiver,
            attachment_bytes=attachment_bytes,
            attachment_filename=attachment_filename
        )

    # 5. 아무것도 설정되지 않은 경우
    return {
        "success": False,
        "error_type": "EMAIL_NOT_CONFIGURED",
        "message": f"서버 이메일 발송 설정(WEB3FORMS_ACCESS_KEY 또는 RESEND_API_KEY 등)이 되어 있지 않습니다. 관리자({target_receiver})에게 직접 발송할 수 있도록 안내합니다.",
        "recipient": target_receiver
    }
