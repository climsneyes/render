import time
import re
import requests
import xml.etree.ElementTree as ET
from typing import Dict, List, Any, Optional, Tuple, Callable
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from app.config import settings, METROPOLITAN_GOVS

http_session = requests.Session()
http_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
})

retry_strategy = Retry(
    total=5,
    backoff_factor=1.5,
    status_forcelist=[429, 500, 502, 503, 504],
    raise_on_status=False
)
http_session.mount("http://", HTTPAdapter(max_retries=retry_strategy))
http_session.mount("https://", HTTPAdapter(max_retries=retry_strategy))


def request_get_with_retry(url: str, params: Optional[Dict] = None, timeout: int = 60) -> requests.Response:
    """API 요청시 ConnectionResetError 등을 방지하기 위한 재시도 및 지연 헬퍼"""
    time.sleep(0.15)
    for attempt in range(3):
        try:
            response = http_session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.HTTPError, ConnectionResetError) as e:
            if attempt == 2:
                raise e
            time.sleep((attempt + 1) * 1.5)
    raise RuntimeError("API request failed after retries")


def get_ordinance_detail(ordinance_id: str) -> List[str]:
    """조례 상세 내용 가져오기"""
    params = {
        'OC': settings.LAW_API_OC,
        'target': 'ordin',
        'ID': ordinance_id,
        'type': 'XML'
    }
    try:
        response = request_get_with_retry(settings.LAW_DETAIL_URL, params=params, timeout=60)
        root = ET.fromstring(response.text)
        articles = []
        for article in root.findall('.//조'):
            content = article.find('조내용').text if article.find('조내용') is not None else ""
            if content:
                content = content.replace('<![CDATA[', '').replace(']]>', '')
                content = content.replace('<p>', '').replace('</p>', '\n')
                content = content.replace('<br/>', '\n')
                content = content.replace('<br>', '\n')
                content = content.replace('&nbsp;', ' ')
                content = content.strip()
            if content:
                articles.append(content)
        return articles
    except Exception:
        return []


def search_ordinances(
    query: str, 
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> Tuple[List[Dict[str, Any]], int]:
    """17개 광역지자체 조례 검색 함수"""
    results = []
    total_count = 0
    total_metros = len(METROPOLITAN_GOVS)

    for idx, (org_code, metro_name) in enumerate(METROPOLITAN_GOVS.items()):
        if progress_callback:
            progress_callback(idx + 1, total_metros, f"검색 중... {metro_name} ({idx + 1}/{total_metros})")

        try:
            params = {
                'OC': settings.LAW_API_OC,
                'target': 'ordin',
                'type': 'XML',
                'query': query,
                'display': 100,
                'search': 1,
                'sort': 'ddes',
                'page': 1,
                'org': org_code
            }

            response = request_get_with_retry(settings.LAW_SEARCH_URL, params=params, timeout=60)
            response.raise_for_status()
            root = ET.fromstring(response.text)

            for law in root.findall('.//law'):
                ordinance_name = law.find('자치법규명').text if law.find('자치법규명') is not None else ""
                ordinance_id = law.find('자치법규ID').text if law.find('자치법규ID') is not None else None
                gov_name = law.find('지자체기관명').text if law.find('지자체기관명') is not None else ""

                if gov_name != metro_name:
                    continue

                # 조례만 필터링 (규칙, 훈령 등 제외)
                law_type_elem = law.find('자치법규종류')
                if law_type_elem is None:
                    law_type_elem = law.find('자치법규종류명')
                if law_type_elem is not None and law_type_elem.text:
                    law_type_text = law_type_elem.text.strip()
                    if '규칙' in law_type_text or law_type_text != '조례':
                        continue

                ordinance_name_stripped = ordinance_name.strip()
                if '시행규칙' in ordinance_name_stripped or ordinance_name_stripped.endswith('규칙') or not ordinance_name_stripped.endswith('조례'):
                    continue

                # 검색어 매칭
                search_terms = [term.lower() for term in query.split() if term.strip()]
                ordinance_name_clean = ordinance_name.replace(' ', '').lower()
                if not all(term in ordinance_name_clean for term in search_terms):
                    continue

                total_count += 1
                articles = get_ordinance_detail(ordinance_id)

                results.append({
                    'id': ordinance_id or f"{org_code}_{total_count}",
                    'name': ordinance_name,
                    'content': articles,
                    'metro': metro_name
                })

        except Exception as e:
            continue

    return results, total_count


def smart_filter_content(content: str, max_length: int = 50000) -> str:
    """조례와 관련성 높은 부분을 우선 추출"""
    lines = content.split('\n')
    priority_keywords = [
        '시장', '군수', '구청장', '지방자치단체', '조례', '시도', '시군구',
        '위임', '위탁', '권한', '사무', '신고', '허가', '승인', '지정',
        '주차', '정차', '금지', '제한', '구역', '시설', '설치', '관리'
    ]

    high_priority, medium_priority, low_priority = [], [], []

    for line in lines:
        line_lower = line.lower()
        priority_count = sum(1 for keyword in priority_keywords if keyword in line_lower)
        if priority_count >= 2:
            high_priority.append(line)
        elif priority_count >= 1:
            medium_priority.append(line)
        else:
            low_priority.append(line)

    filtered_content = []
    current_length = 0

    for line in high_priority:
        if current_length + len(line) < max_length:
            filtered_content.append(line)
            current_length += len(line)
        else:
            break

    for line in medium_priority:
        if current_length + len(line) < max_length:
            filtered_content.append(line)
            current_length += len(line)
        else:
            break

    for line in low_priority:
        if current_length + len(line) < max_length:
            filtered_content.append(line)
            current_length += len(line)
        else:
            break

    result = '\n'.join(filtered_content)
    if len(content) > len(result):
        result += "\n\n[... 조례 관련성이 높은 부분을 우선 표시하였습니다 ...]"
    return result


def get_superior_law_content_xml_fallback(law_name: str) -> Optional[Dict[str, Any]]:
    """XML 방식 폴백 (간소화 버전)"""
    try:
        search_params = {
            'OC': settings.LAW_API_OC,
            'target': 'law',
            'type': 'XML',
            'query': law_name,
            'display': 5,
            'search': 1
        }
        search_response = request_get_with_retry(settings.LAW_SEARCH_URL, params=search_params, timeout=30)
        if search_response.status_code != 200 or not search_response.text.strip():
            return None

        search_root = ET.fromstring(search_response.text)
        law_id = None
        exact_law_name = None

        for law in search_root.findall('.//law'):
            found_name = law.find('법령명').text if law.find('법령명') is not None else ""
            found_id = law.find('법령ID').text if law.find('법령ID') is not None else None
            if found_name == law_name or (law_name in found_name):
                law_id = found_id
                exact_law_name = found_name
                break

        if not law_id:
            return None

        detail_params = {
            'OC': settings.LAW_API_OC,
            'target': 'law', 
            'ID': law_id,
            'type': 'XML'
        }
        detail_response = request_get_with_retry(settings.LAW_DETAIL_URL, params=detail_params, timeout=30)
        detail_root = ET.fromstring(detail_response.text)

        articles = []
        for article in detail_root.findall('.//조'):
            article_num = article.find('조문번호').text if article.find('조문번호') is not None else ""
            article_title = article.find('조문제목').text if article.find('조문제목') is not None else ""
            article_content = article.find('조문내용').text if article.find('조문내용') is not None else ""
            if article_content:
                article_content = article_content.replace('<![CDATA[', '').replace(']]>', '')
                article_content = article_content.replace('<p>', '').replace('</p>', '\n')
                article_content = article_content.replace('<br/>', '\n').replace('<br>', '\n')
                article_content = article_content.replace('&nbsp;', ' ').strip()
                if article_content:
                    articles.append({
                        'number': article_num,
                        'title': article_title,
                        'content': article_content
                    })

        return {
            'law_name': exact_law_name,
            'law_id': law_id,
            'articles': articles
        }
    except Exception:
        return None


def get_superior_law_content_xml(law_name: str) -> Optional[Dict[str, Any]]:
    """XML API를 통해 상위법령 본문 가져오기"""
    try:
        search_query = law_name.strip()
        search_params = {
            'OC': settings.LAW_API_OC,
            'target': 'law',
            'type': 'XML',
            'query': search_query,
            'display': 10
        }
        search_response = request_get_with_retry(settings.LAW_SEARCH_URL, params=search_params, timeout=30)
        if search_response.status_code != 200:
            return get_superior_law_content_xml_fallback(law_name)

        search_root = ET.fromstring(search_response.text)
        current_laws = []
        for law in search_root.findall('.//law'):
            status = law.find('현행연혁코드')
            if status is not None and status.text == '현행':
                law_id_elem = law.find('법령ID')
                law_name_elem = law.find('법령명한글')
                if law_id_elem is not None and law_name_elem is not None:
                    current_laws.append({
                        'id': law_id_elem.text,
                        'name': law_name_elem.text
                    })

        if not current_laws:
            return get_superior_law_content_xml_fallback(law_name)

        best_law = None
        best_score = -1

        for law_info in current_laws:
            found_name = law_info['name']
            score = 0
            if found_name == law_name:
                score += 1000
            if law_name in found_name:
                score += 500
            if found_name in law_name:
                score += 300

            law_lower = law_name.lower().replace(' ', '')
            found_lower = found_name.lower().replace(' ', '')

            requested_type = ''
            if '시행규칙' in law_lower:
                requested_type = '시행규칙'
            elif '시행령' in law_lower:
                requested_type = '시행령'
            elif '법' in law_lower and '시행' not in law_lower:
                requested_type = '법'

            if requested_type:
                if requested_type in found_lower:
                    score += 300
                elif requested_type == '법' and found_lower.endswith('법') and '시행' not in found_lower:
                    score += 300
            else:
                if found_lower.endswith('법') and not ('시행령' in found_lower or '시행규칙' in found_lower):
                    score += 100
                elif '시행령' in found_lower:
                    score += 50
                elif '시행규칙' in found_lower:
                    score += 25

            if len(found_name) > 30:
                score -= 30

            if score > best_score:
                best_score = score
                best_law = law_info

        if best_law:
            law_id = best_law['id']
            exact_law_name = best_law['name']
        else:
            law_id = current_laws[0]['id']
            exact_law_name = current_laws[0]['name']

        if not law_id:
            return get_superior_law_content_xml_fallback(law_name)

        detail_params = {
            'OC': settings.LAW_API_OC,
            'target': 'law',
            'type': 'XML',
            'ID': law_id
        }
        detail_response = request_get_with_retry(settings.LAW_DETAIL_URL, params=detail_params, timeout=30)
        if detail_response.status_code != 200:
            return get_superior_law_content_xml_fallback(law_name)

        detail_root = ET.fromstring(detail_response.text)
        upper_law_text = ""

        for node in detail_root.iter():
            if node.tag == '조문내용' and node.text and node.text.strip():
                content = re.sub(r'<[^>]+>', '', node.text)
                content = content.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').strip()
                upper_law_text += content + '\n'
            elif node.tag == '항내용' and node.text and node.text.strip():
                content = re.sub(r'<[^>]+>', '', node.text)
                content = content.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').strip()
                upper_law_text += '    ' + content + '\n'
            elif node.tag == '호내용' and node.text and node.text.strip():
                content = re.sub(r'<[^>]+>', '', node.text)
                content = content.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').strip()
                upper_law_text += '        ' + content + '\n'

        if upper_law_text.strip():
            max_length = 80000
            if len(upper_law_text) > max_length:
                truncated_text = smart_filter_content(upper_law_text, max_length)
            else:
                truncated_text = upper_law_text.strip()

            return {
                'law_name': exact_law_name,
                'law_id': law_id,
                'content': truncated_text
            }
        else:
            return get_superior_law_content_xml_fallback(law_name)

    except Exception:
        return get_superior_law_content_xml_fallback(law_name)


def get_superior_law_content(law_name: str) -> Optional[Dict[str, Any]]:
    return get_superior_law_content_xml(law_name)


def normalize_law_name(law_name: str) -> Optional[str]:
    normalized = law_name.strip()
    normalized = re.sub(r'\s+', ' ', normalized)
    normalized = re.sub(r'(\w+)\s+(법|령|규칙)$', r'\1\2', normalized)
    if '폐광지' in normalized or '역개발' in normalized:
        if '특별법' in normalized:
            normalized = "폐광지역개발지원에관한특별법"
    if len(normalized) <= 3:
        return None
    invalid_patterns = [r'^한특별법$', r'^\w{1,2}특별법$']
    for pattern in invalid_patterns:
        if re.match(pattern, normalized):
            return None
    return normalized


def group_laws_by_hierarchy(superior_laws: List[str]) -> Dict[str, Dict[str, Optional[str]]]:
    law_groups = {}
    normalized_laws = set()
    for law_name in superior_laws:
        normalized = normalize_law_name(law_name)
        if normalized:
            normalized_laws.add(normalized)

    for law_name in normalized_laws:
        base_name = law_name
        law_type = 'law'
        if '시행규칙' in law_name:
            base_name = law_name.replace(' 시행규칙', '').replace('시행규칙', '')
            law_type = 'rule'
        elif '시행령' in law_name:
            base_name = law_name.replace(' 시행령', '').replace('시행령', '')
            law_type = 'decree'
        elif law_name.endswith('령') and not law_name.endswith('법령'):
            law_type = 'decree'
        elif law_name.endswith('규칙'):
            law_type = 'rule'

        if base_name not in law_groups:
            law_groups[base_name] = {'law': None, 'decree': None, 'rule': None}
        law_groups[base_name][law_type] = law_name

    return law_groups


def get_all_superior_laws_content(
    superior_laws: List[str],
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> List[Dict[str, Any]]:
    superior_laws_content = []
    if not superior_laws:
        return superior_laws_content

    law_groups = group_laws_by_hierarchy(superior_laws)
    total_laws = sum(1 for laws in law_groups.values() for law in laws.values() if law is not None)
    current_idx = 0

    for base_name, laws in law_groups.items():
        group_content = {
            'base_name': base_name,
            'laws': {},
            'combined_articles': [],
            'combined_content': ""
        }

        for law_type in ['law', 'decree', 'rule']:
            law_name = laws[law_type]
            if law_name:
                current_idx += 1
                if progress_callback:
                    progress_callback(current_idx, total_laws, f"상위법령 조회 중... {law_name} ({current_idx}/{total_laws})")

                law_content = get_superior_law_content(law_name)
                if law_content:
                    group_content['laws'][law_type] = law_content
                    if 'content' in law_content:
                        group_content['combined_content'] += law_content['content'] + '\n'
                    elif 'articles' in law_content:
                        group_content['combined_articles'].extend(law_content['articles'])

        if group_content['laws']:
            superior_laws_content.append(group_content)

    for group in superior_laws_content:
        group_chars = len(group.get('combined_content', ''))
        for article in group.get('combined_articles', []):
            group_chars += len(article.get('content', ''))
        group['text_length'] = group_chars

    return superior_laws_content

