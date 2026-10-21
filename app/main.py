from typing import Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.diagnosis import router as diagnosis_router
from app.api.ordinances import router as ordinances_router
from app.api.pdf import router as pdf_router
from app.api.analysis import router as analysis_router
from app.api.logs import router as logs_router
from app.services.cache_service import trigger_background_cache_warmup, get_cache_status
from app.config import settings

app = FastAPI(
    title="광역지자체 조례 검색·비교·AI 분석 API",
    version="2.0.0",
    description="국가법령정보센터 조례 검색 및 Gemini LLM 기반 상위법령/타지자체 비교분석 백엔드"
)

# CORS 설정: 프론트엔드 통신 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(diagnosis_router, prefix="/api")
app.include_router(ordinances_router, prefix="/api")
app.include_router(pdf_router, prefix="/api")
app.include_router(analysis_router, prefix="/api")
app.include_router(logs_router, prefix="/api")

@app.on_event("startup")
def on_startup():
    # 서버 기동 시 백그라운드에서 4대 도서 Context Caching 사전 준비
    if settings.GEMINI_API_KEY:
        trigger_background_cache_warmup(settings.GEMINI_API_KEY)

@app.get("/api/cache/status")
def api_cache_status():
    return get_cache_status()

@app.post("/api/cache/warmup")
def api_cache_warmup(api_key: Optional[str] = None):
    key = api_key or settings.GEMINI_API_KEY
    return trigger_background_cache_warmup(key)

@app.get("/")
def root():
    return {
        "status": "online",
        "service": "Ordinance Analysis API",
        "version": "2.0.0"
    }

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

