import os
from typing import Dict
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", os.environ.get("GOOGLE_API_KEY", ""))
    LAW_API_OC: str = os.environ.get("LAW_API_OC", "climsneys85")
    DEFAULT_GEMINI_MODEL: str = "gemini-2.5-flash"
    
    LAW_SEARCH_URL: str = "https://www.law.go.kr/DRF/lawSearch.do"
    LAW_DETAIL_URL: str = "https://www.law.go.kr/DRF/lawService.do"
    
    # 이메일 HTTP API 설정 (Render 등 클라우드 배포 환경용)
    RESEND_API_KEY: str = os.environ.get("RESEND_API_KEY", "")
    WEB3FORMS_ACCESS_KEY: str = os.environ.get("WEB3FORMS_ACCESS_KEY", "")
    BREVO_API_KEY: str = os.environ.get("BREVO_API_KEY", "")

    # 이메일 SMTP 설정 (로컬 환경용)
    EMAIL_SMTP_SERVER: str = os.environ.get("EMAIL_SMTP_SERVER", "smtp.gmail.com")
    EMAIL_SMTP_PORT: int = int(os.environ.get("EMAIL_SMTP_PORT", 587))
    EMAIL_SENDER: str = os.environ.get("EMAIL_SENDER", "")
    EMAIL_PASSWORD: str = os.environ.get("EMAIL_PASSWORD", "")
    EMAIL_RECEIVER: str = os.environ.get("EMAIL_RECEIVER", "lsh4676@korea.kr")

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()

# 17개 광역지자체 코드 및 이름
METROPOLITAN_GOVS: Dict[str, str] = {
    '6110000': '서울특별시',
    '6260000': '부산광역시',
    '6270000': '대구광역시',
    '6280000': '인천광역시',
    '6290000': '광주광역시',
    '6300000': '대전광역시',
    '6310000': '울산광역시',
    '6510000': '세종특별자치시',
    '6410000': '경기도',
    '6420000': '강원특별자치도',
    '6430000': '충청북도',
    '6440000': '충청남도',
    '6450000': '전북특별자치도',
    '6460000': '전라남도',
    '6470000': '경상북도',
    '6480000': '경상남도',
    '6500000': '제주특별자치도'
}

