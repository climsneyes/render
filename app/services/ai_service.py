import time
import re
import traceback
from typing import Dict, List, Any, Optional
import google.generativeai as genai
from app.config import settings
from app.api.logs import add_server_log
from app.services.legal_knowledge_base import LEGAL_KNOWLEDGE_BASE_4_BOOKS
from app.services.cache_service import get_ready_cache


_cached_available_models: List[str] = []

def get_available_models(api_key: str) -> List[str]:
    """API 키로 사용 가능한 최신 모델 목록을 동적으로 조회 (캐싱 적용)"""
    global _cached_available_models
    if _cached_available_models:
        return _cached_available_models

    fallback_modern = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
    if not api_key:
        return fallback_modern

    try:
        genai.configure(api_key=api_key)
        discovered = []
        for m in genai.list_models():
            methods = getattr(m, 'supported_generation_methods', []) or []
            if 'generateContent' in methods:
                name_clean = m.name.replace('models/', '')
                # 종료/제외 모델 (3.8-flash, 2.5-pro, 1.x, 2.0 제외)
                if not any(excluded in name_clean for excluded in ['3.8', 'pro', 'gemini-1.0', 'gemini-1.5', 'gemini-2.0']):
                    discovered.append(name_clean)

        if discovered:
            def sort_key(name: str):
                if '2.5-flash-lite' in name:
                    return 1
                if '2.5-flash' in name:
                    return 0
                return 2
            discovered.sort(key=sort_key)
            _cached_available_models = discovered
            add_server_log("INFO", "API", f"사용 가능한 Gemini 모델 자동 감지 완료: {discovered}")
            return discovered
    except Exception as e:
        add_server_log("WARNING", "API", f"모델 목록 조회 중 예외({e}), 기본 최신 모델 목록 적용")

    return fallback_modern


def diagnose_gemini_api(api_key: str) -> Dict[str, Any]:
    """Gemini API 키 유효성, 모델 목록, 테스트 호출 종합 진단"""
    results = {
        'api_key_set': bool(api_key),
        'api_key_masked': f"{api_key[:6]}...{api_key[-4:]}" if api_key and len(api_key) > 10 else "미설정",
        'models_available': [],
        'test_success': False,
        'test_model_used': None,
        'test_response': None,
        'error': None
    }

    if not api_key:
        results['error'] = "Gemini API 키가 설정되지 않았습니다."
        return results

    try:
        genai.configure(api_key=api_key)

        available_models = get_available_models(api_key)
        results['models_available'] = available_models

        candidate_models = []
        if settings.DEFAULT_GEMINI_MODEL:
            candidate_models.append(settings.DEFAULT_GEMINI_MODEL)
        for m in available_models:
            if m not in candidate_models:
                candidate_models.append(m)
        for m in ["gemini-2.5-flash", "gemini-2.5-flash-lite"]:
            if m not in candidate_models:
                candidate_models.append(m)

        test_prompt = "대한민국 헌법 제1조 제1항의 전문(全文)을 한 줄로 답해줘."

        for m_name in candidate_models:
            try:
                m = genai.GenerativeModel(m_name)
                resp = m.generate_content(test_prompt)
                resp_text = ""
                if hasattr(resp, 'text') and resp.text:
                    resp_text = resp.text
                elif resp.candidates and resp.candidates[0].content and resp.candidates[0].content.parts:
                    resp_text = "".join(
                        p.text for p in resp.candidates[0].content.parts if hasattr(p, 'text') and p.text
                    )

                if resp_text and resp_text.strip():
                    results['test_success'] = True
                    results['test_model_used'] = m_name
                    results['test_response'] = resp_text.strip()
                    break
            except Exception:
                continue

        if not results['test_success']:
            results['error'] = "테스트 모델 호출에 실패했습니다. API 키 권한 또는 할당량(Quota)을 확인해주세요."

    except Exception as e:
        results['error'] = f"{type(e).__name__}: {str(e)}"

    return results


def clean_and_repair_markdown_report(text: str) -> str:
    """반복 루프 제거 및 마크다운 구조 자동 보정"""
    if not text:
        return ""

    lines = text.split('\n')
    cleaned_lines = []
    repeat_count = 0
    last_line = None

    for line in lines:
        stripped = line.strip()
        if stripped and stripped == last_line:
            repeat_count += 1
            if repeat_count <= 2:
                cleaned_lines.append(line)
        else:
            repeat_count = 0
            last_line = stripped
            cleaned_lines.append(line)

    result = '\n'.join(cleaned_lines)
    if len(result) > 50000:
        result = result[:50000] + "\n\n... [반복 내용 자동 정리됨] ...\n"

    recent_lines = result.split('\n')[-6:]
    has_pipe_recently = any('|' in ln for ln in recent_lines)
    if has_pipe_recently:
        result += "\n\n"

    return result


def optimize_prompt_length(prompt: str, max_chars: int = 80000) -> str:
    """프롬프트가 너무 길 경우 필수 섹션을 보존하면서 지능적으로 자동 축약
    
    보존 우선순위:
    1. 검토 대상 조례 원문 (내 조례 본문 전체 - 최우선 절대 보존)
    2. 최종 분석 지시사항 및 작성 양식 (표 컬럼, 분석 항목 전체)
    3. 서두 및 법리 가이드라인 (소관사무, 법률유보, 법령우위 등 원칙)
    축소 대상:
    - 중간 상위법령 본문 및 타 시도 조례 내용, RAG 참고자료 청크
    """
    original_len = len(prompt)
    if original_len <= max_chars:
        return prompt

    add_server_log("WARNING", "PROMPT", f"프롬프트 길이 초과 ({original_len:,}자 > {max_chars:,}자). 지능형 자동 축약을 실행합니다.")

    try:
        start_marker = "📄 **[검토 대상 조례 원문 시작]**"
        end_marker = "📄 **[검토 대상 조례 원문 종료]**"
        ref_start_marker = "📚 **[참고자료:"
        ref_end_marker = "📚 **[참고자료 종료]**"
        instruction_marker = "아래 기준에 따라 분석해줘"

        ordinance_start = prompt.find(start_marker)
        ordinance_end = prompt.find(end_marker)
        reference_start = prompt.find(ref_start_marker)
        reference_end = prompt.find(ref_end_marker)
        analysis_instruction_start = prompt.find(instruction_marker)

        # 1. 서두 법리 가이드라인 (최대 3,000자로 제어)
        if ordinance_start != -1:
            guideline_section = prompt[:ordinance_start].strip()
        else:
            guideline_section = prompt[:2000].strip()
        if len(guideline_section) > 3000:
            guideline_section = guideline_section[:3000].strip()

        # 2. 검토 대상 조례 원문 (핵심 필수 보존 - 절대 중간 절단 금지)
        if ordinance_start != -1 and ordinance_end != -1:
            ordinance_section = prompt[ordinance_start : ordinance_end + len(end_marker)].strip()
        else:
            ordinance_section = ""

        # 조례 본문이 비정상적으로 거대한 경우(예: 35,000자 초과)에만 조문 중간 안전 축약
        max_ordinance_len = 35000
        if len(ordinance_section) > max_ordinance_len:
            head_len = 25000
            tail_len = 9000
            ordinance_section = (
                ordinance_section[:head_len]
                + "\n\n... [검토 대상 조례 본문 일부 축약] ...\n\n"
                + ordinance_section[-tail_len:]
            )

        # 3. 분석 지시사항 및 출력 포맷 (완전 100% 보존, 최대 4,000자)
        if analysis_instruction_start != -1:
            instruction_section = prompt[analysis_instruction_start:].strip()
        else:
            instruction_section = prompt[-3000:].strip()
        if len(instruction_section) > 4000:
            instruction_section = instruction_section[:4000].strip()

        # 4. 참고자료 요약 (RAG 자료는 최대 3,000자까지만 허용)
        reference_section = ""
        if reference_start != -1 and reference_end != -1:
            ref_content = prompt[reference_start : reference_end + len(ref_end_marker)]
            if len(ref_content) > 3000:
                reference_section = ref_content[:3000] + "\n\n... [참고자료 일부 생략]\n📚 **[참고자료 종료]**"
            else:
                reference_section = ref_content

        # 5. 중간 섹션 (상위법령 및 타 시도 조례)
        # 상위법령/타시도 조례는 조례 원문 종료 태그 뒤 ~ 참고자료(또는 지시사항) 시작 전까지 위치함
        next_anchor = reference_start if reference_start != -1 else analysis_instruction_start
        middle_section = ""
        if ordinance_end != -1 and next_anchor != -1 and next_anchor > (ordinance_end + len(end_marker)):
            middle_section = prompt[ordinance_end + len(end_marker) : next_anchor].strip()

        # 남은 가용 용량 계산: 필수 섹션들(가이드라인, 검토조례, 참고자료, 지침)을 먼저 확보하고 남은 공간만 상위법령에 배분
        used_essential = len(guideline_section) + len(ordinance_section) + len(instruction_section) + len(reference_section) + 500
        available_for_middle = max(max_chars - used_essential, 0)

        if len(middle_section) > available_for_middle:
            if available_for_middle > 1000:
                middle_section = (
                    middle_section[:available_for_middle]
                    + "\n\n... [상위법령 및 타 시도 조례 본문 일부 자동 축약됨] ...\n"
                )
            else:
                middle_section = ""

        reduced_prompt = f"{guideline_section}\n\n{ordinance_section}\n\n{middle_section}\n\n{reference_section}\n\n{instruction_section}".strip()

        # 만약 여전히 초과하는 경우: middle_section과 reference_section을 우선 축소하여 조례 본문과 지침을 온전히 보존
        if len(reduced_prompt) > max_chars:
            reduced_prompt = f"{guideline_section}\n\n{ordinance_section}\n\n{instruction_section}".strip()
            if len(reduced_prompt) > max_chars:
                over = len(reduced_prompt) - max_chars
                guideline_section = guideline_section[:max(len(guideline_section) - over - 100, 500)]
                reduced_prompt = f"{guideline_section}\n\n{ordinance_section}\n\n{instruction_section}".strip()

        add_server_log(
            "SUCCESS",
            "PROMPT",
            f"프롬프트 지능형 자동 축약 완료: {original_len:,}자 ➔ {len(reduced_prompt):,}자 (조례 원문 {len(ordinance_section):,}자 보존)"
        )
        return reduced_prompt

    except Exception as e:
        add_server_log("WARNING", "PROMPT", f"지능형 축약 중 예외 발생({e}), 안전 슬라이싱 적용")
        return prompt[:max_chars]


def detect_agency_delegation(superior_article: Dict, ordinance_article: Dict, source_type: str) -> Dict:
    """기관위임사무 특화 판별"""
    superior_content = superior_article.get('content', '').lower()
    ordinance_content = ordinance_article.get('content', '').lower()

    national_affairs_indicators = [
        '건축허가', '개발행위허가', '환경영향평가', '도시계획',
        '산업단지', '관광단지', '택지개발', '도로개설',
        '하천점용', '산지전용', '농지전용', '산업입지',
        '국토계획', '지역계획', '광역계획'
    ]
    is_national_affair = any(indicator in superior_content for indicator in national_affairs_indicators)

    delegation_to_head_indicators = [
        '시장', '군수', '구청장', '지방자치단체의 장',
        '시장이', '군수가', '구청장이', '장이',
        '위임한다', '위탁한다'
    ]
    is_delegated_to_head = any(indicator in superior_content for indicator in delegation_to_head_indicators)

    ordinance_regulation_indicators = [
        '허가', '승인', '신고', '인가', '지정', '등록',
        '기준', '절차', '방법', '조건', '제한'
    ]
    has_ordinance_regulation = any(indicator in ordinance_content for indicator in ordinance_regulation_indicators)

    is_agency_delegation = False
    severity = "낮음"
    evidence = []
    description = ""

    if is_national_affair and is_delegated_to_head and has_ordinance_regulation:
        is_agency_delegation = True
        severity = "매우 높음"
        description = "기관위임사무에 대해 조례로 별도 규정을 두어 지방자치법 제22조 위반"
        evidence.append(f"국가사무 확인: {[ind for ind in national_affairs_indicators if ind in superior_content][:2]}")
        evidence.append(f"지자체 장 위임 확인: {[ind for ind in delegation_to_head_indicators if ind in superior_content][:2]}")
        evidence.append(f"조례 별도 규정 확인: {[ind for ind in ordinance_regulation_indicators if ind in ordinance_content][:2]}")

    elif is_national_affair and has_ordinance_regulation:
        is_agency_delegation = True
        severity = "높음"
        description = "국가사무로 추정되는 사항에 대해 조례가 별도 규정, 기관위임사무 가능성"
        evidence.append(f"국가사무 가능성: {[ind for ind in national_affairs_indicators if ind in superior_content][:2]}")
        evidence.append(f"조례 별도 규정: {[ind for ind in ordinance_regulation_indicators if ind in ordinance_content][:2]}")

    elif is_delegated_to_head and has_ordinance_regulation:
        is_agency_delegation = True
        severity = "높음"
        description = "지방자치단체 장에게 위임된 사무에 대해 조례로 별도 규정"
        evidence.append(f"지자체 장 위임: {[ind for ind in delegation_to_head_indicators if ind in superior_content][:2]}")
        evidence.append(f"조례 별도 규정: {[ind for ind in ordinance_regulation_indicators if ind in ordinance_content][:2]}")

    return {
        'is_agency_delegation': is_agency_delegation,
        'description': description,
        'evidence': evidence,
        'severity': severity,
        'national_affair': is_national_affair,
        'delegated_to_head': is_delegated_to_head,
        'has_regulation': has_ordinance_regulation
    }


def analyze_ordinance_vs_superior_laws(pdf_text: str, superior_laws_content: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """조례와 상위법령 직접 비교 분석 함수"""
    if not superior_laws_content:
        return []

    ordinance_provisions = []
    lines = pdf_text.split('\n')
    current_article = ""
    current_content = ""

    for line in lines:
        line = line.strip()
        if line.startswith('제') and '조' in line:
            if current_article:
                ordinance_provisions.append({
                    'article': current_article,
                    'content': current_content.strip()
                })
            current_article = line
            current_content = ""
        else:
            current_content += line + " "

    if current_article:
        ordinance_provisions.append({
            'article': current_article,
            'content': current_content.strip()
        })

    comparison_results = []

    for ordinance_provision in ordinance_provisions:
        if not ordinance_provision['content']:
            continue

        provision_analysis = {
            'ordinance_article': ordinance_provision['article'],
            'ordinance_content': ordinance_provision['content'],
            'superior_law_conflicts': [],
            'delegation_issues': []
        }

        for law_group in superior_laws_content:
            base_name = law_group['base_name']

            for superior_article in law_group.get('combined_articles', []):
                superior_content = superior_article['content'].lower()
                ordinance_lower = ordinance_provision['content'].lower()

                article_source = "법률"
                for law_type, law_info in law_group['laws'].items():
                    if law_info and 'articles' in law_info:
                        for article in law_info['articles']:
                            if article['content'] == superior_article['content']:
                                article_source = "법률" if law_type == 'law' else ("시행령" if law_type == 'decree' else "시행규칙")
                                break

                agency_delegation_result = detect_agency_delegation(superior_article, ordinance_provision, article_source)

                if agency_delegation_result['is_agency_delegation']:
                    provision_analysis['delegation_issues'].append({
                        'superior_law': f"{base_name} ({article_source})",
                        'superior_article': f"{superior_article['number']} {superior_article['title']}",
                        'description': agency_delegation_result['description']
                    })

                conflict_indicators = [
                    ('금지', '허용'), ('의무', '면제'), ('필수', '선택'),
                    ('강제', '임의'), ('반드시', '가능'), ('불가', '허용')
                ]

                for prohibit_word, allow_word in conflict_indicators:
                    if prohibit_word in superior_content and allow_word in ordinance_lower:
                        provision_analysis['superior_law_conflicts'].append({
                            'superior_law': f"{base_name} ({article_source})",
                            'superior_article': f"{superior_article['number']} {superior_article['title']}",
                            'conflict_type': f'{article_source} {prohibit_word} vs 조례 {allow_word}'
                        })

        if provision_analysis['delegation_issues'] or provision_analysis['superior_law_conflicts']:
            comparison_results.append(provision_analysis)

    return comparison_results


def create_analysis_prompt(
    pdf_text: str,
    search_results: List[Dict[str, Any]],
    superior_laws_content: Optional[List[Dict[str, Any]]] = None,
    relevant_guidelines: Optional[List[Dict[str, Any]]] = None,
    is_first_ordinance: bool = False
) -> str:
    """조례 위법성 및 타 지자체 비교 분석 프롬프트 생성"""
    sep_line = "=" * 80
    prompt = (
        f"{sep_line}\n"
        f"🇰🇷 **[필수 언어 규정: 100% 한국어 작성 원칙]**\n"
        f"모든 분석 보고서, 표, 조문 비교, 차별점, 법리 검토, 결론은 **반드시 100% 한국어(Korean)로만 작성**하십시오.\n"
        f"영문(English) 답변은 엄격히 금지됩니다.\n"
        f"{sep_line}\n\n"
        f"⚠️ **최우선 지시사항: 교과서적 가정법 절대 금지 & 구체적 확정 판정 필수**\n"
        f"{sep_line}\n"
        f"당신은 대한민국 법제처 수석연구관 출신의 조례 전문 변호사입니다.\n\n"
        f"**[절대 금지 사항]**:\n"
        f"- '만약 ~인 경우 위법 소지가 있습니다', '~라면 법률유보에 위배될 수 있습니다' 같은 **교과서식 일반론이나 가정법 서술을 절대 금지**합니다.\n"
        f"- 질문자가 원하는 것은 일반 법이론 강의가 아니라, **'업로드된 내 조례의 실제 조문이 적법한지 위법한지 AI가 직접 판정하는 것'**입니다.\n\n"
        f"**[핵심 임무]**:\n"
        f"1. [검토 대상 조례 원문]의 실제 조문(제1조부터 끝조까지)을 하나하나 짚으며, **'어떤 이유 때문에 적법하다', '어떤 이유 때문에 위법하다'**를 AI가 직접 판정하십시오.\n"
        f"2. 내 조례의 실제 내용이 자치사무인지 기관위임사무인지, 권리제한 규정이 있는지 없는지, 상위법과 충돌하는지 안 하는지를 조례안 문언에 근거하여 확정적으로 판정하십시오.\n"
        f"3. [컨텍스트 캐싱 도서 전수 대조 및 근거 제시]:\n"
        f"   - 제공된 4대 핵심 도서([2026 자치법규 정비사례집], [자치법규 의견제시 주제별 주요 사례], [자치법규 입안 길라잡이(2026)], [지방자치단체의 재의·제소 조례 모음집])를 전수 대조하여 대법원 판례 번호, 법제처 질의회신 및 정비권고 사유를 정확히 인용하십시오.\n"
        f"4. [바람직한 수정 문구(대안 조문안) 전수 제시 - 핵심 의무]:\n"
        f"   - 위법·부당하거나 상위법령과 불일치하는 조항, 또는 권한 한계를 일탈한 조항에 대해서는 [자치법규 입안 길라잡이(2026)]의 표준 입안 기준에 부합하도록, 실무에서 그대로 조례 개정안으로 사용할 수 있는 **'바람직한 수정 문구(안)'를 완전한 조문 형태로 반드시 제시**하십시오.\n\n"
        f"**분석 결과 형식**:\n"
        f"- 반드시 아래 모든 섹션을 포함하여 답변:\n"
        f"  1. [비교분석 요약표(조문별)]\n"
        f"  2. [내 조례의 차별점 요약]\n"
        f"  3. [원칙별 실질적 위법성 판정 결과] (소관사무, 법률유보, 법령우위, 권한분립 원칙별 직접 판정 및 관련 대법원 판례 인용)\n"
        f"  4. [조문별 구체적 위법성 검토 및 바람직한 수정 문구 대비표 (기존 조문 vs 수정 조문안)]\n"
        f"  5. [상위법령 적합성 종합 판정 및 최종 입법 조치 제안]\n\n"
        f"{sep_line}\n\n"
        f"🚨 **필독: 조례 위법 판단 법리적 가이드라인 (최우선 준수 사항)**\n\n"
        f"**1. 조례 위법 판단의 원칙**\n"
        f"- 조례는 자치입법권에 따라 폭넓은 재량을 가지며, 법률과 상충하거나 주민의 권리를 침해하지 않는 이상 위법으로 보지 않는다.\n"
        f"- 조례는 법률유보, 포괄위임금지, 절차적 위법 등 요건이 엄격할 때만 위법성이 문제된다.\n"
        f"- 단순한 표현 방식, 관행과의 차이, 타 지자체와의 비교만으로 위법 판단을 하지 않는다.\n\n"
        f"**2. 자동해산·포괄조항 등 자치단체 조례에서 흔히 쓰는 규정은 원칙적으로 허용**\n"
        f"- 특정안건형·비상설 위원회에 대한 자동 구성·자동 해산 규정은 조례가 정한 절차에 따른 것으로 보며 위법으로 판단하지 않는다.\n"
        f"- '그 밖에 시장이 필요하다고 인정하는 경우' 등 보충적 포괄조항은 권리·의무를 제한하지 않는 한 자치입법권 범위에서 허용된다.\n"
        f"- 이를 법률유보·포괄위임금지로 판단하지 않는다.\n\n"
        f"**3. 자문위원회에 대해서는 '절차권', '구성권' 개념을 적용하지 않기**\n"
        f"- 위원회는 자치단체장의 자문기구이며, 위원회 존폐는 주민의 절차권 또는 권익과 직접 관련되지 않는다.\n\n"
        f"**4. 위법 판단은 반드시 '법률 상위규범과의 명확한 충돌'이 있을 때만 함**\n"
        f"- 1) 조례가 상위법의 명령·금지를 명백히 위반하는 경우\n"
        f"- 2) 주민의 권리를 제한하거나 의무를 부과하면서 법적 근거가 명확히 없는 경우\n"
        f"- 3) 자치사무가 아닌 국가사무를 침해하는 경우\n"
        f"- 그 외에는 '위법 가능성 있음'이라고 판단하지 않는다.\n\n"
        f"{LEGAL_KNOWLEDGE_BASE_4_BOOKS}\n\n"
        f"{sep_line}\n"
        f"📄 **[검토 대상 조례 원문 시작]**\n"
        f"{sep_line}\n"
        f"{pdf_text}\n\n"
        f"{sep_line}\n"
        f"📄 **[검토 대상 조례 원문 종료]**\n"
        f"{sep_line}\n\n"
    )

    # 상위법령 본문 추가 (법령당 최대 12,000자로 제어하여 프롬프트 팽창 방지)
    if superior_laws_content:
        prompt += "\n그리고 아래는 조례안에서 언급된 상위법령들의 실제 조문 내용이야. (법률, 시행령, 시행규칙 통합 분석)\n---\n"
        for law_group in superior_laws_content:
            base_name = law_group['base_name']
            prompt += f"◆ {base_name}\n"
            if 'combined_content' in law_group and law_group['combined_content']:
                content = law_group['combined_content']
                if len(content) > 12000:
                    content = content[:12000] + "\n[... 이하 본문 축약 ...]"
                prompt += f"  본문 내용:\n{content}\n"
            else:
                for article in law_group.get('combined_articles', [])[:12]:
                    prompt += f"    {article['number']} {article['title']}\n    {article['content']}\n\n"
        prompt += "---\n"

        try:
            comparison_results = analyze_ordinance_vs_superior_laws(pdf_text, superior_laws_content)
            if comparison_results:
                prompt += "\n**중요: 조례와 상위법령 직접 비교 분석 결과**\n---\n"
                for result in comparison_results[:6]:
                    prompt += f"◆ {result['ordinance_article']}\n"
                    if result['delegation_issues']:
                        prompt += "⚠️ 기관위임사무 가능성:\n"
                        for issue in result['delegation_issues']:
                            prompt += f"  - {issue['superior_law']} {issue['superior_article']}: {issue['description']}\n"
                    if result['superior_law_conflicts']:
                        prompt += "🚨 상위법령 충돌 가능성:\n"
                        for conflict in result['superior_law_conflicts']:
                            prompt += f"  - {conflict['superior_law']} {conflict['superior_article']}: {conflict['conflict_type']}\n"
                prompt += "---\n"
        except Exception:
            pass

    # 참고자료 추가
    if relevant_guidelines:
        prompt += "\n" + "=" * 80 + "\n"
        prompt += "📚 **[참고자료: 자치법규 가이드라인 및 위법 판단 기준]**\n"
        prompt += "=" * 80 + "\n"
        for i, guideline in enumerate(relevant_guidelines[:5]):
            similarity_score = guideline.get('similarity', 1 - guideline.get('distance', 0))
            text_snippet = guideline['text'][:800]
            prompt += f"  [참고자료 {i+1}] (출처: {guideline.get('source_store', '')}, 유사도: {similarity_score:.3f})\n"
            prompt += f"  {text_snippet}\n\n"
        prompt += "📚 **[참고자료 종료]**\n" + "=" * 80 + "\n\n"

    # 타 지자체 조례 추가
    if is_first_ordinance or not search_results:
        prompt += (
            "※ 참고: 이 조례는 17개 시도 중 최초로 제정되는 조례이거나 타 시도 유사 조례가 없습니다.\n"
            "타 시도 조례가 없는 상황에서 조례의 적정성, 상위법령과의 관계, 실무적 검토 포인트를 중심으로 분석해줘.\n"
        )
    else:
        prompt += "그리고 아래는 타 시도 조례명과 주요 조문 내용이야.\n"
        for result in search_results[:4]:
            prompt += f"조례명: {result['name']} ({result.get('metro', '')})\n"
            for idx, article in enumerate(result['content'][:8]):
                prompt += f"제{idx+1}조: {article[:300]}\n"

    # 분석 작성 양식 및 상세 지침
    prompt += (
        "---\n"
        "아래 기준에 따라 분석해줘. 반드시 100% 한글로 답변하고, 가정법('~인 경우', '~라면') 대신 단정적 판정 형식으로 작성해줘.\n\n"
        "1. [비교분석 요약표(조문별)]\n"
        "- 표의 컬럼: 조문(내 조례), 주요 내용, 타 시도 유사 조항, 동일 여부, 차이 및 내 조례 특징, 추천 조문\n"
        "- 내 조례의 각 조문별로 타 시도 조례와 비교해 빠짐없이 표로 정리\n\n"
        "2. [내 조례의 차별점 요약]\n"
        "- 타 시도 조례와 비교하여 구조적으로 다른 점, 내 조례만의 고유한 규정 등을 명확히 요약\n\n"
        "3. [원칙별 실질적 위법성 판정 결과]\n"
        "※ 교과서적인 원칙 설명(예: '법률유보란 주민의 권리를 제한할 때~')은 일체 쓰지 마시오. 오직 내 조례안의 실제 조문들을 하나하나 짚으며 [적법/위법]을 직접 판정하시오.\n\n"
        "a) 소관사무의 원칙 판정:\n"
        "   - 내 조례에 규정된 사무가 '지방자치단체 고유 자치사무'인지 '국가 기관위임사무'인지 AI가 직접 판정.\n"
        "   - 판정 형식: [조문 번호] - [판정: 적법 / 위법 / 수정권고] ➔ 구체적 판정 이유 (지방자치법 제13조 자치사무 해당 여부 및 관련 상위법령 조항 명시)\n\n"
        "b) 법률유보의 원칙 판정:\n"
        "   - 내 조례 조문 중 주민의 권리를 제한하거나 의무를 부과하는 규정이 있는지 전수 점검하여 직접 판정.\n"
        "   - 판정 형식: [조문 번호] - [판정: 적법 / 위법 / 수정권고] ➔ 구체적 판정 이유 (침익적 규정인지, 단순 지원·권장 규정인지, 상위법령의 위임 근거가 충분한지 명시)\n\n"
        "c) 법령우위의 원칙 판정:\n"
        "   - 상위법령(법률, 시행령)과 정면으로 저촉되거나 상위법의 입법 취지에 반하는 조문이 있는지 직접 대조 판정.\n"
        "   - 판정 형식: [조문 번호] - [판정: 적법 / 위법 / 수정권고] ➔ 구체적 판정 이유 (상위법령 조항과 충돌 여부 명시)\n\n"
        "4. [실무적 검토 포인트 및 구체적 수정안]\n"
        "- 조례 집행 실무(예산 수반, 위원회 운영, 타 부서 협의 등)에서 예상되는 구체적 문제점과 수정 조문 예시 제시\n\n"
        "5. [상위법령 적합성 종합 판정 및 결론]\n"
        "- 조례안 전체에 대한 최종 종합 판정:\n"
        "  * 최종 법적 적합성 등급: [원안 적법 / 일부 문구 수정 권고 / 중대 위법으로 제정 불가 중 명확히 택1]\n"
        "  * 종합 법리 총평: 어떤 구체적 이유 때문에 이러한 최종 판정이 내려졌는지 3~5줄로 명확하게 결론을 내리시오.\n"
    )

    return prompt


def call_gemini_api(
    prompt: str,
    api_key: str,
    model_name: str = settings.DEFAULT_GEMINI_MODEL,
    max_chars: int = 80000
) -> str:
    """Gemini API 호출 및 응답 정제 (다중 모델 자동 Fallback 및 8만자 지능형 자동 축약 지원)"""
    if not api_key:
        add_server_log("ERROR", "API", "Gemini API 키가 설정되지 않았습니다.")
        raise ValueError("Gemini API 키가 설정되지 않았습니다.")

    genai.configure(api_key=api_key)

    # 1. 프롬프트 지능형 자동 축약 실행
    prompt = optimize_prompt_length(prompt, max_chars=max_chars)

    korean_instruction = (
        "[최우선 필수 지침: 당신은 대한민국 법제처 출신의 자치법규(조례) 검토 최고 전문 변호사이자 수석연구원입니다.\n"
        "1. [가정법·교과서적 일반론 절대 금지 - 필수 엄수]:\n"
        "   - '만약 ~인 경우 위법 소지가 있습니다', '~라면 법률유보에 위배될 수 있습니다'와 같은 교과서식 일반론 나열을 절대 금지합니다.\n"
        "   - 반드시 업로드된 [검토 대상 조례]의 실제 조문(제1조부터 마지막 조문까지)을 구체적으로 인용하며,\n"
        "     '제O조는 OOO하므로 [적법]하다', '제O조는 상위법 OO법 제O조와 충돌하므로 [위법]이다'와 같이 AI가 직접 확정적으로 판정하십시오.\n"
        "2. 모든 섹션에서 '어떤 이유 때문에 적법한지', '어떤 구체적 근거 때문에 위법한지' 실제 조례 문언과 법령 조항을 1:1로 직접 대조하여 실질적 판정을 내리십시오.\n"
        "3. 모든 보고서 내용, 조문 비교표(Table), 차별점 요약, 검토 유의사항, 법리 분석, 결론 등은 반드시 100% 한국어로만 작성하십시오.\n"
        "4. 1번 비교분석 요약표부터 5번 상위법령 적합성 종합 판정까지 모든 섹션을 누락 없이 끝까지 완성도 높게 작성하십시오.]\n\n"
    )
    full_prompt = korean_instruction + prompt

    candidate_models = []
    if model_name:
        candidate_models.append(model_name)

    live_models = get_available_models(api_key)
    for m in live_models:
        if m not in candidate_models:
            candidate_models.append(m)

    static_fallbacks = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
    for fb in static_fallbacks:
        if fb not in candidate_models:
            candidate_models.append(fb)

    generation_config = {
        "temperature": 0.2,
        "top_p": 0.95,
        "max_output_tokens": 8192,
    }

    last_exception = None

    # 사전 준비된 Gemini Context Cache 획득 시도 (비동기 완료 시 75% 토큰 할인 및 4대 도서 전수 대조)
    cached_content = None
    try:
        cached_content = get_ready_cache(api_key)
        if cached_content:
            add_server_log("SUCCESS", "CACHE", f"사전 준비된 4대 도서 Context Cache 적용 완료 ({getattr(cached_content, 'name', 'OK')})")
        else:
            add_server_log("INFO", "CACHE", "캐시가 준비 중이거나 미생성 상태입니다. 고밀도 지식베이스 모드로 초고속 심사 진행 (타임아웃 0%)")
    except Exception as e_cache:
        add_server_log("WARNING", "CACHE", f"캐시 확인 중 경미한 예외({e_cache}), 고밀도 지식베이스 모드로 진행")

    for target_model in candidate_models:
        t0 = time.time()
        add_server_log("INFO", "API", f"Gemini 모델 [{target_model}] 심층 분석 호출 시작 (프롬프트: {len(full_prompt):,}자)...")
        try:
            if cached_content:
                try:
                    model = genai.GenerativeModel.from_cached_content(
                        cached_content=cached_content,
                        generation_config=generation_config
                    )
                except Exception as e_cached:
                    add_server_log("WARNING", "CACHE", f"Cached 모델 인스턴스화 실패({e_cached}), 표준 모델로 호출")
                    model = genai.GenerativeModel(target_model, generation_config=generation_config)
            else:
                model = genai.GenerativeModel(target_model, generation_config=generation_config)

            response = model.generate_content(full_prompt)
            elapsed = time.time() - t0

            text_result = ""
            finish_reason = "UNKNOWN"

            if response:
                if hasattr(response, 'candidates') and response.candidates:
                    c = response.candidates[0]
                    finish_reason = str(getattr(c, 'finish_reason', 'UNKNOWN'))

                try:
                    if hasattr(response, 'text') and response.text:
                        text_result = response.text
                except Exception:
                    pass

                if not text_result and response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                    text_result = "".join(
                        p.text for p in response.candidates[0].content.parts if hasattr(p, 'text') and p.text
                    )

            raw_len = len(text_result) if text_result else 0

            # 비정상 반복 루프 감지 (글자수 4만자 초과 or MAX_TOKENS 도달 3만자 이상)
            is_runaway_loop = raw_len > 40000 or (
                str(finish_reason) in ['2', 'FinishReason.MAX_TOKENS', 'MAX_TOKENS'] and raw_len > 30000
            )
            if is_runaway_loop and target_model != candidate_models[-1]:
                add_server_log("WARNING", "API", f"모델 {target_model} 비정상 반복 루프 감지 ({raw_len:,}자). 다음 모델로 자동 전환합니다.")
                continue

            cleaned_text = clean_and_repair_markdown_report(text_result) if text_result else ""
            if cleaned_text and len(cleaned_text.strip()) > 50:
                add_server_log("SUCCESS", "API", f"Gemini [{target_model}] 분석 보고서 생성 성공 ({len(cleaned_text):,}자, 소요시간: {elapsed:.2f}초)")
                return cleaned_text
            else:
                add_server_log("WARNING", "API", f"모델 {target_model} 응답이 비어있거나 너무 짧음({len(cleaned_text)}자). 다음 모델로 전환합니다.")

        except Exception as e_call:
            elapsed = time.time() - t0
            last_exception = e_call
            err_msg = f"{type(e_call).__name__}: {str(e_call)}"
            add_server_log("WARNING", "API", f"모델 [{target_model}] 호출 실패 ({elapsed:.2f}초): {err_msg}")

    if last_exception:
        add_server_log("ERROR", "API", f"모든 Gemini 모델({candidate_models}) 호출 실패. 최종 예외: {last_exception}")
        raise last_exception

    raise RuntimeError("Gemini AI로부터 온전한 분석 결과를 생성하지 못했습니다. 디버그 로그와 API 키 권한을 확인해주세요.")
