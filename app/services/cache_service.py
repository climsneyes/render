import os
import glob
import datetime
from typing import List, Dict, Any, Optional
import google.generativeai as genai
from google.generativeai import caching
from app.config import settings
from app.api.logs import add_server_log

# 4대 자치법규 핵심 전문 도서 파일명
TARGET_PDF_FILENAMES = [
    "2026 자치법규 정비사례집.pdf",
    "자치법규 의견제시 주제별 주요 사례.pdf",
    "자치법규 입안 길라잡이(2026).pdf",
    "지방자치단체의 재의·제소 조례 모음집.pdf"
]

_cached_content_obj: Optional[Any] = None
_cached_expire_time: Optional[datetime.datetime] = None
_cached_api_key: Optional[str] = None
_cached_model_name: Optional[str] = None
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
            # 1. 정확한 파일명 매칭
            direct_path = os.path.join(base_dir, target_name)
            if os.path.exists(direct_path):
                found_path = direct_path
                break

            # 2. 유니코드/특수문자(· 등) 정규화 대비 glob 매칭
            prefix = target_name.split()[0].replace(".pdf", "")
            matches = glob.glob(os.path.join(base_dir, f"*{prefix}*.pdf"))
            if matches:
                found_path = matches[0]
                break

        if found_path:
            found_files.append(found_path)
            add_server_log("INFO", "CACHE", f"RAG 전문 도서 발견: {os.path.basename(found_path)} ({os.path.getsize(found_path):,} bytes)")
        else:
            add_server_log("WARNING", "CACHE", f"RAG 도서 파일을 찾을 수 없음: {target_name}")

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
        "4. 권한분립의 원칙: 조례로 위원회를 설치하면서 지방의회가 위원을 추천하거나 단체장의 고유 집행권(인사권, 재산관리권)을 사전에 의회 승인을 받도록 강제하는지 검토.\n\n"
        "[바람직한 수정 문구(대안 조문안) 전수 제시 원칙]:\n"
        "- 위법 또는 부당한 조항에 대해서는 [자치법규 입안 길라잡이(2026)]의 표준 조례 조문 작성 기준(용어의 순화, 주어-서술어 일치, 위임 범위 내 한정)을 적용하여, 실무에서 즉시 조례 개정에 적용할 수 있는 '바람직한 수정 문구(안)'를 완전한 조문 형태로 반드시 제시하십시오."
    )


def get_or_create_ordinance_cache(api_key: str, model_name: str = "gemini-2.5-flash") -> Optional[Any]:
    """Gemini Context Caching (컨텍스트 캐싱) 조회 또는 자동 생성"""
    global _cached_content_obj, _cached_expire_time, _cached_api_key, _cached_model_name, _uploaded_files_map

    if not api_key:
        return None

    # 유효한 기존 캐시가 있는 경우 재사용
    now = datetime.datetime.now(datetime.timezone.utc)
    if (
        _cached_content_obj is not None
        and _cached_expire_time is not None
        and now < _cached_expire_time - datetime.timedelta(minutes=5)
        and _cached_api_key == api_key
    ):
        add_server_log("INFO", "CACHE", f"기존 Gemini Context Cache 재사용 (만료 잔여: {int((_cached_expire_time - now).total_seconds() / 60)}분)")
        return _cached_content_obj

    try:
        genai.configure(api_key=api_key)
        pdf_paths = find_target_pdf_files()

        if not pdf_paths:
            add_server_log("WARNING", "CACHE", "캐싱할 4대 PDF 도서 파일이 발견되지 않아 일반 모드로 진행합니다.")
            return None

        # 1. 파일 업로드 (동일 세션 내 중복 업로드 방지)
        uploaded_files = []
        for path in pdf_paths:
            basename = os.path.basename(path)
            if basename in _uploaded_files_map:
                uploaded_files.append(_uploaded_files_map[basename])
            else:
                add_server_log("INFO", "CACHE", f"Gemini Files API 업로드 시작: {basename}")
                up_file = genai.upload_file(path, display_name=basename, mime_type="application/pdf")
                _uploaded_files_map[basename] = up_file
                uploaded_files.append(up_file)
                add_server_log("INFO", "CACHE", f"Gemini Files API 업로드 완료: {up_file.name}")

        # 2. 지원 모델 이름 표준화 (models/ prefix 처리)
        caching_model = model_name
        if not caching_model.startswith("models/"):
            caching_model = f"models/{caching_model}"

        # 3. Context Cache 생성 (기본 2시간 보관, 75% 토큰 할인 적용)
        add_server_log("INFO", "CACHE", f"Gemini Context Caching 생성 요청 중 (대상 모델: {caching_model}, 4대 도서)")
        system_instruction = get_cached_system_instruction()

        cache = caching.CachedContent.create(
            model=caching_model,
            display_name="korean_ordinance_review_expert_knowledge",
            system_instruction=system_instruction,
            contents=uploaded_files,
            ttl=datetime.timedelta(hours=2)
        )

        _cached_content_obj = cache
        _cached_expire_time = cache.expire_time
        _cached_api_key = api_key
        _cached_model_name = caching_model

        add_server_log("INFO", "CACHE", f"Gemini Context Caching 성공! (Cache Name: {cache.name}, 만료: {cache.expire_time})")
        return cache

    except Exception as e:
        add_server_log("WARNING", "CACHE", f"Context Caching 생성 실패 (원인: {str(e)}). 일반 AI 호출로 자동 폴백합니다.")
        return None
