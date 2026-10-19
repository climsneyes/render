import os
import re
import pickle
from typing import List, Dict, Any, Optional

_loaded_vectorstores: Optional[Dict[str, Any]] = None

def is_quality_content(text: str) -> bool:
    """RAG 청크의 유용한 내용 여부 판단"""
    if len(text) < 100:
        return False

    toc_patterns = [
        r'^제\d+장\s+',
        r'^제\d+절\s+',
        r'^\d+\.\s+\w+\s*$',
        r'^[가-힣]+\s+\d+$',
        r'^\s*목\s*차\s*$',
        r'^\s*차\s*례\s*$',
    ]

    for pattern in toc_patterns:
        if re.search(pattern, text.strip(), re.MULTILINE):
            if len(text) > 300:
                return True
            return False

    sentence_count = text.count('.') + text.count('다.') + text.count('함.')
    if sentence_count < 2:
        return False

    useful_keywords = ['판단', '해석', '따라서', '경우', '규정', '위반', '적법', '위법', '검토', '사례', '판례']
    has_useful_content = any(kw in text for kw in useful_keywords)

    return has_useful_content or len(text) > 500


def load_rag_vectorstores() -> Dict[str, Any]:
    """로컬 PKL 파일에서 RAG 벡터스토어 로드 (캐시)"""
    global _loaded_vectorstores
    if _loaded_vectorstores is not None:
        return _loaded_vectorstores

    vectorstores = {}
    service_dir = os.path.dirname(__file__)
    backend_dir = os.path.abspath(os.path.join(service_dir, "..", ".."))
    root_dir = os.path.abspath(os.path.join(backend_dir, ".."))

    # 1. 자치법규 매뉴얼
    manual_candidates = [
        "enhanced_vectorstore_20250914_101739.pkl",
        os.path.join(backend_dir, "enhanced_vectorstore_20250914_101739.pkl"),
        os.path.join(root_dir, "enhanced_vectorstore_20250914_101739.pkl"),
        os.path.join(root_dir, "backend", "enhanced_vectorstore_20250914_101739.pkl"),
    ]
    for p in manual_candidates:
        if os.path.exists(p):
            try:
                with open(p, 'rb') as f:
                    vectorstores['manual'] = pickle.load(f)
                break
            except Exception:
                pass

    # 2. 재의·제소 판례 모음집
    cases_filename = "3. 지방자치단체의 재의·제소 조례 모음집(Ⅸ) (1)_new_vectorstore.pkl"
    cases_candidates = [
        cases_filename,
        os.path.join(backend_dir, cases_filename),
        os.path.join(root_dir, cases_filename),
        os.path.join(root_dir, "backend", cases_filename),
    ]
    for p in cases_candidates:
        if os.path.exists(p):
            try:
                with open(p, 'rb') as f:
                    vectorstores['cases'] = pickle.load(f)
                break
            except Exception:
                pass

    _loaded_vectorstores = vectorstores
    return vectorstores


def search_rag_context(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """RAG 벡터스토어에서 관련 문서 검색"""
    vectorstores = load_rag_vectorstores()
    results = []

    for store_name, store_data in vectorstores.items():
        try:
            if isinstance(store_data, dict) and 'chunks' in store_data:
                chunks = store_data['chunks']
                query_keywords = [kw.lower() for kw in query.split() if len(kw) > 1]
                scored_chunks = []

                for chunk in chunks:
                    if isinstance(chunk, dict) and 'text' in chunk:
                        text = chunk['text']
                    elif isinstance(chunk, str):
                        text = chunk
                    else:
                        continue

                    if not is_quality_content(text):
                        continue

                    text_lower = text.lower()
                    keyword_score = sum(1 for kw in query_keywords if kw in text_lower)
                    length_bonus = min(len(text) / 500, 3.0)
                    analysis_keywords = ['판단', '검토', '위법', '적법', '사례', '판례', '해석', '기준']
                    analysis_bonus = sum(0.5 for kw in analysis_keywords if kw in text)
                    total_score = keyword_score + length_bonus + analysis_bonus

                    if keyword_score > 0:
                        scored_chunks.append({
                            'text': text,
                            'score': total_score,
                            'source_store': '자치법규 매뉴얼' if store_name == 'manual' else '재의·제소 판례 모음집',
                            'similarity': min(total_score / 10.0, 1.0)
                        })

                scored_chunks.sort(key=lambda x: x['score'], reverse=True)
                results.extend(scored_chunks[:top_k])
        except Exception:
            continue

    results.sort(key=lambda x: x.get('score', 0), reverse=True)
    return results[:top_k]

