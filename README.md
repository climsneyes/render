# 광역지자체 조례 비교 및 AI 분석 API (Render 배포용)

대한민국 17개 광역지자체 조례 검색, 상위법령 대조, 판례/가이드라인 RAG 검색 및 Gemini LLM 기반 심층 조례 분석 백엔드 서비스입니다.

---

## 🚀 Render 클라우드 배포 설정 안내

Render(https://render.com)에서 **New Web Service**를 생성한 후 아래와 같이 설정하세요:

| 설정 항목 | 설정값 |
| :--- | :--- |
| **Language / Environment** | `Python 3` |
| **Root Directory** | *(비워둠 - 저장소 루트)* |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| **Health Check Path** | `/healthz` |

---

## 🔑 환경 변수(Environment Variables) 설정

Render 대시보드의 **Environment Variables** 탭에서 아래 변수들을 등록하세요:

1. **`GEMINI_API_KEY`** (필수)
   - Google AI Studio(https://aistudio.google.com/)에서 발급받은 Gemini API 키
2. **`LAW_API_OC`** (필수)
   - 국가법령정보센터 공동이용 오픈API 서비스 인증키 (기본값: `climsneys85`)
3. **`DEFAULT_GEMINI_MODEL`** (선택)
   - 기본값: `gemini-3.8-flash`
