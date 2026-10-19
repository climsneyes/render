import io
from urllib.parse import quote
from fastapi import APIRouter, Query, HTTPException, Response
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from app.services.law_service import search_ordinances, get_ordinance_detail
from app.services.doc_service import create_word_document

router = APIRouter(prefix="/ordinances", tags=["Ordinances"])

class WordExportRequest(BaseModel):
    query: str
    ordinances: List[Dict[str, Any]]

@router.get("/search")
def api_search_ordinances(query: str = Query(..., min_length=1, description="검색할 조례명 키워드")):
    try:
        results, total_count = search_ordinances(query)
        return {
            "query": query,
            "total_count": total_count,
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"조례 검색 중 오류 발생: {str(e)}")

@router.get("/detail")
def api_get_ordinance_detail(id: str = Query(..., description="자치법규ID")):
    try:
        articles = get_ordinance_detail(id)
        return {"id": id, "articles": articles}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"조례 상세 조회 오류: {str(e)}")

@router.post("/export-docx")
def api_export_ordinances_docx(payload: WordExportRequest):
    try:
        doc = create_word_document(payload.query, payload.ordinances)
        file_stream = io.BytesIO()
        doc.save(file_stream)
        file_stream.seek(0)
        
        encoded_filename = quote(f"조례_비교표_{payload.query}.docx")
        
        return Response(
            content=file_stream.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Word 문서 생성 오류: {str(e)}")

