import os
import glob
import time
import datetime
import threading
from typing import List, Dict, Any, Optional
import google.generativeai as genai
from google.generativeai import caching
from app.config import settings
from app.api.logs import add_server_log

# 4대 자치법규 핵심 전문 도서 중 대용량 판례·사례집 3권 (총 854페이지, 약 64만 토큰)
# ※ Gemini 2.5 Flash의 1,048,576(100만) 토큰 한도를 완벽히 준수하도록 4번째 입안 길라잡이는 시스템 지침으로 융합
TARGET_PDF_FILENAMES = [
    "2026 자치법규 정비사례집.pdf",
    "자치법규 의견제시 주제별 주요 사례.pdf",
    "지방자치단체의 재의·제소 조례 모음집.pdf"
]

# 캐시 상태 전역 관리 (Thread-safe)
_cache_lock = threading.Lock()
_cache_status: str = "IDLE"  # IDLE, WARMING_UP, READY, ERROR
_cached_obj: Optional[Any] = None
_cached_expire_time: Optional[datetime.datetime] = None
_cached_api_key: Optional[str] = None
_cached_model_name: Optional[str] = None
_last_error_message: Optional[str] = None
_uploaded_files_map: Dict[str, Any] = {}


def find_target_pdf_files() -> List[str]:
    """프로젝트 내 4대 핵심 PDF 파일의 실제 절대 경로 탐색"""
    service_dir = os.path.dirname(__file__)
    backend_dir = os.path.abspath(os.path.join(service_dir, "..", ".."))
    root_dir = os.path.abspath(os.path.join(backend_dir, ".."))

    search_dirs = [
        backend_dir,
        os.path.join(backend_dir, "data"),
        root_dir,
    ]

    found_files = []
    for target_name in TARGET_PDF_FILENAMES:
        found_path = None
        for base_dir in search_dirs:
            direct_path = os.path.join(base_dir, target_name)
            if os.path.exists(direct_path):
                found_path = direct_path
                break

            prefix = target_name.split()[0].replace(".pdf", "")
            matches = glob.glob(os.path.join(base_dir, f"*{prefix}*.pdf"))
            if matches:
                found_path = matches[0]
                break

        if found_path:
            found_files.append(found_path)
            add_server_log("INFO", "CACHE", f"RAG 전문 도서 확인: {os.path.basename(found_path)} ({os.path.getsize(found_path):,} bytes)")
        else:
            add_server_log("WARNING", "CACHE", f"도서 파일 탐색 필요: {target_name}")

    return found_files


def get_cached_system_instruction() -> str:
    """4대 도서 전문 지식을 기반으로 위법성 검토 및 수정 문구를 유도하는 시스템 지침"""
    return (
        "당신은 대한민국 법제처 및 행정안전부 자치입법 심사관 출신의 대한민국 최고 자치법규(조례) 전문 변호사입니다.\n"
        "함께 제공된 4대 핵심 법률 기준 도서:\n"
        "1. [2026 자치법규 정비사례집]\n"
        "2. [자치법규 의견제시 주제별 주요 사례]\n"
        "3. [자치법규 입안 길라잡이(2026)]\n"
        "4. [지방자치단체의 재의·제소 조례 모음집]\n"
        "의 전문을 완벽히 습득하고 체계적으로 참조하여, 제출된 조례안의 위법성 및 조문상 하자를 심사하고, 바람직한 수정 문구를 조문 형식으로 명확히 제시하십시오.\n\n"
        "[핵심 심사 원칙]:\n"
        "1. 소관사무의 한계 (지방자치법 제13조): 지자체 고유사무가 아닌 국가사무(기관위임사무)를 조례로 규율하는지 검토.\n"
        "2. 법률우위의 원칙 (지방자치법 제28조 제1항 본문): 상위 법률·시행령의 명령·금지를 위반하거나 완화/강화하여 상위법의 취지를 훼손하는지 검토.\n"
        "3. 법률유보의 원칙 (지방자치법 제28조 제1항 단서): 법률의 구체적 위임 없이 주민의 권리를 제한하거나 의무를 부과하고, 과태료/벌칙 규정을 두는지 검토.\n"
        "4. 권한분립의 원칙 및 단체장의 전속권한:\n"
        "   - 지방의회가 단체장의 고유 집행권(조직편성권, 인사권, 재산관리권 등)을 사전 승인이나 위원 추천 등으로 침해하는지 검토.\n"
        "   - [행정기구 설치 조례의 전속적 제안권 (핵심)]: 행정기구를 설치·개편·증설하는 조례안의 제안권은 지방자치단체장(지방정부의 장)에게 전속됨 (지방자치법 제125조, 대법원 92추142, 2001추131, [자치법규 입안 길라잡이(2026)] p.54-55). 따라서 지방의회 의원이 의원 발의로 행정기구를 설치·신설·확대하는 조례안은 단체장의 고유 권한 침해로 명백한 [중대 위법(무효)]이며 제정 불가함. 지방의회의 수정조례안 의결 또한 단체장이 제안한 행정기구의 축소·통폐합에 한해서만 가능함.\n\n"
        "[바람직한 수정 문구(대안 조문안) 전수 제시 원칙]:\n"
        "- 위법 또는 부당한 조항에 대해서는 [자치법규 입안 길라잡이(2026)]의 표준 조례 조문 작성 기준(용어의 순화, 주어-서술어 일치, 위임 범위 내 한정)을 적용하여, 실무에서 즉시 조례 개정에 적용할 수 있는 '바람직한 수정 문구(안)'를 완전한 조문 형태로 반드시 제시하십시오.\n\n"
        "[마크다운 표 작성 엄격 금지 규칙]:\n"
        "- 마크다운 표(| ... |) 작성 시 컬럼 너비를 맞추기 위해 연속된 공백(스페이스 패딩)을 절대 입력하지 마십시오. 각 셀은 | 내용 | 형태로 간결하게 작성하십시오.\n\n"
        "[인사말 및 서두 서술 절대 금지]:\n"
        "- '존경하는 시장님', '심사 결과를 보고 드립니다' 등의 편지글 인사말이나 소개글을 일절 쓰지 마십시오. 반드시 '### 1. 비교분석 요약표(조문별)'부터 본론으로 즉시 시작하십시오."
    )


def _warmup_worker(api_key: str, model_name: str):
    """백그라운드 스레드에서 비동기로 4대 도서를 구글 캐시에 사전 등록"""
    global _cache_status, _cached_obj, _cached_expire_time, _cached_api_key, _cached_model_name, _last_error_message

    add_server_log("INFO", "CACHE", "Gemini 4대 전문 도서 사전 캐싱(Pre-warming) 백그라운드 작업 시작...")
    t0 = time.time()

    try:
        genai.configure(api_key=api_key)
        pdf_paths = find_target_pdf_files()

        if not pdf_paths:
            with _cache_lock:
                _cache_status = "ERROR"
                _last_error_message = "4대 도서 PDF 파일을 서버에서 찾을 수 없습니다."
            add_server_log("WARNING", "CACHE", _last_error_message)
            return

        # 1. 파일 업로드
        uploaded_files = []
        for path in pdf_paths:
            basename = os.path.basename(path)
            if basename in _uploaded_files_map:
                uploaded_files.append(_uploaded_files_map[basename])
            else:
                add_server_log("INFO", "CACHE", f"Gemini Cloud 파일 업로드 중: {basename}...")
                up_file = genai.upload_file(path, display_name=basename, mime_type="application/pdf")
                _uploaded_files_map[basename] = up_file
                uploaded_files.append(up_file)
                add_server_log("SUCCESS", "CACHE", f"Gemini Cloud 파일 업로드 완료: {basename}")

        # 2. 모델명 표준화
        caching_model = model_name
        if not caching_model.startswith("models/"):
            caching_model = f"models/{caching_model}"

        # 3. Context Caching 생성 (24시간 보관)
        add_server_log("INFO", "CACHE", f"구글 메모리 캐시 생성 중 ({caching_model}, 24시간 TTL)...")
        system_instruction = get_cached_system_instruction()

        cache = caching.CachedContent.create(
            model=caching_model,
            display_name="korean_ordinance_master_knowledge_cache",
            system_instruction=system_instruction,
            contents=uploaded_files,
            ttl=datetime.timedelta(hours=24)
        )

        with _cache_lock:
            _cached_obj = cache
            _cached_expire_time = cache.expire_time
            _cached_api_key = api_key
            _cached_model_name = caching_model
            _cache_status = "READY"
            _last_error_message = None

        elapsed = time.time() - t0
        add_server_log(
            "SUCCESS",
            "CACHE",
            f"🎉 4대 도서 Gemini Context Caching 사전 준비 완료 (READY)! 소요시간: {elapsed:.1f}초, 만료: {cache.expire_time}"
        )

    except Exception as e:
        with _cache_lock:
            _cache_status = "ERROR"
            _last_error_message = str(e)
        add_server_log("WARNING", "CACHE", f"Context Caching 사전 등록 실패 ({str(e)}). 고밀도 지식베이스로 자동 폴백합니다.")


def trigger_background_cache_warmup(api_key: str, model_name: str = "gemini-2.5-flash") -> Dict[str, Any]:
    """백그라운드에서 캐시 사전 준비를 비동기로 트리거 (웹 요청 블로킹 없음)"""
    global _cache_status

    if not api_key:
        return {"status": "NO_API_KEY", "message": "API 키가 제공되지 않았습니다."}

    with _cache_lock:
        now = datetime.datetime.now(datetime.timezone.utc)
        if _cache_status == "READY" and _cached_expire_time and now < _cached_expire_time - datetime.timedelta(minutes=10):
            return {
                "status": "READY",
                "cache_name": getattr(_cached_obj, "name", ""),
                "expires_at": str(_cached_expire_time),
                "message": "이미 4대 도서 캐시가 준비되어 있습니다."
            }

        if _cache_status == "WARMING_UP":
            return {
                "status": "WARMING_UP",
                "message": "현재 백그라운드에서 캐시 사전 준비가 진행 중입니다."
            }

        _cache_status = "WARMING_UP"

    # 비동기 백그라운드 스레드 시작
    thread = threading.Thread(
        target=_warmup_worker,
        args=(api_key, model_name),
        daemon=True,
        name="GeminiCacheWarmupThread"
    )
    thread.start()

    return {
        "status": "WARMING_UP",
        "message": "백그라운드 사전 캐싱 작업을 시작했습니다."
    }


def get_ready_cache(api_key: str) -> Optional[Any]:
    """준비 완료된 Context Cache 반환 (미준비 시 즉시 None 반환 및 백그라운드 준비 시작)"""
    global _cache_status, _cached_obj, _cached_expire_time

    if not api_key:
        return None

    with _cache_lock:
        now = datetime.datetime.now(datetime.timezone.utc)
        if (
            _cache_status == "READY"
            and _cached_obj is not None
            and _cached_expire_time is not None
            and now < _cached_expire_time - datetime.timedelta(minutes=5)
            and _cached_api_key == api_key
        ):
            return _cached_obj

    # 준비되어 있지 않다면 백그라운드 작업 시작 (웹 요청은 블로킹하지 않음)
    trigger_background_cache_warmup(api_key)
    return None


def get_cache_status() -> Dict[str, Any]:
    """현재 캐시 상태 조회"""
    with _cache_lock:
        return {
            "status": _cache_status,
            "cache_name": getattr(_cached_obj, "name", None),
            "expires_at": str(_cached_expire_time) if _cached_expire_time else None,
            "model_name": _cached_model_name,
            "error_message": _last_error_message
        }
