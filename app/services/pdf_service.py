import io
import re
from typing import List, Tuple, Set

try:
    import pypdf as PyPDF2
except ImportError:
    import PyPDF2


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """PDF 바이트 데이터에서 텍스트 추출"""
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
        text = ""
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                text += extracted + "\n"
        return text.strip()
    except Exception as e:
        raise RuntimeError(f"PDF 텍스트 추출 실패: {str(e)}")


def extract_superior_laws(pdf_text: str) -> Tuple[List[str], List[str]]:
    """조례안에서 상위법령 추출 함수 (추출된 법령 목록, 자동 추가된 하위 법령 목록 반환)"""
    law_check_keywords = [
        '위반', '위배', '충돌', '저촉', '준수', '적합', '불일치',
        '상위법', '상위 법령', '상위법령', '법령과의 관계', '법령과의 충돌', '법령과의 위배',
        '관계법령', '근거법령', '법적근거', '참고사항', '관련법령', '소관법령',
        '법령', '법률', '시행령', '시행규칙', '규정', '개정', '제정', '법'
    ]

    law_pattern = re.compile(r'([가-힣\w\s]*(?:법|시행령|시행규칙))\s*(?:[」]|$|[.,;:\s])', re.MULTILINE)
    upper_law_candidates: Set[str] = set()

    for line in pdf_text.split('\n'):
        if any(keyword in line for keyword in law_check_keywords):
            for match in law_pattern.finditer(line):
                law_name = match.group(1).strip()
                if law_name:
                    upper_law_candidates.add(law_name)

    quote_pattern = re.compile(r'[「『]([^」』]*(?:법|시행령|시행규칙))[」』]')
    for match in quote_pattern.finditer(pdf_text):
        law_name = match.group(1).strip()
        if law_name:
            upper_law_candidates.add(law_name)

    relation_pattern = re.compile(r'(?:관계법령|근거법령|법적근거|소관법령|관련법령)\s*[:：]\s*[「『]?([^」』\n]*(?:법|시행령|시행규칙))[」』]?')
    for match in relation_pattern.finditer(pdf_text):
        law_name = match.group(1).strip()
        if law_name:
            upper_law_candidates.add(law_name)

    invalid_law_names = {
        '자치입법', '조례', '규칙', '지침', '내규', '예규', '훈령', '적법',
        '입법', '상위법', '위법', '합법', '불법', '방법', '헌법상', '헌법적',
        '법적', '법률적', '법령상', '법률상', '법률', '법령', '법', '규정',
        '조항', '조문', '규범', '원칙', '기준', '사항', '내용', '관련법',
        '관련 법', '관련법령', '관련 법령'
    }

    def is_valid_law_name(name: str) -> bool:
        name_clean = name.strip().replace(' ', '').lower()
        for invalid in invalid_law_names:
            if name_clean == invalid.replace(' ', '').lower():
                return False
        if name_clean and name_clean[0].isdigit():
            return False
        if len(name_clean) < 3:
            return False
        return True

    valid_laws = [law for law in upper_law_candidates if is_valid_law_name(law)]

    # 시행령/시행규칙 자동 유추 추가
    additional_laws = []
    for law in valid_laws:
        if law.endswith('법') and '시행' not in law:
            base_name = law
            potential_decree = f"{base_name} 시행령"
            if potential_decree not in valid_laws and potential_decree not in additional_laws:
                additional_laws.append(potential_decree)

            potential_rule = f"{base_name} 시행규칙"
            if potential_rule not in valid_laws and potential_rule not in additional_laws:
                additional_laws.append(potential_rule)

    all_laws = list(set(valid_laws + additional_laws))
    all_laws.sort()

    return all_laws[:20], additional_laws

