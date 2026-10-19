import io
from urllib.parse import quote
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from app.services.law_service import get_all_superior_laws_content
from app.services.rag_service import search_rag_context
from app.services.ai_service import create_analysis_prompt, call_gemini_api
from app.services.doc_service import create_comparison_document
from app.config import settings

router = APIRouter(prefix="/analysis", tags=["Analysis"])

class RunAnalysisRequest(BaseModel):
    pdf_text: str
    superior_laws: Optional[List[str]] = []
    search_results: Optional[List[Dict[str, Any]]] = []
    api_key: Optional[str] = None
    model_name: Optional[str] = settings.DEFAULT_GEMINI_MODEL
    use_rag: Optional[bool] = True
    is_first_ordinance: Optional[bool] = False

class ExportAnalysisDocxRequest(BaseModel):
    pdf_text: str
    analysis_text: str
    search_results: Optional[List[Dict[str, Any]]] = []
    superior_laws_content: Optional[List[Dict[str, Any]]] = None
    relevant_guidelines: Optional[List[Dict[str, Any]]] = None

@router.post("/run")
def api_run_analysis(req: RunAnalysisRequest):
    if not req.pdf_text or not req.pdf_text.strip():
        raise HTTPException(
            status_code=400,
            detail="검토 대상 조례(PDF) 내용이 비어있습니다. PDF 업로드 탭에서 조례안을 먼저 업로드하고 조문이 정상 추출되었는지 확인해 주세요."
        )

    api_key = req.api_key.strip() if req.api_key else settings.GEMINI_API_KEY
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="Gemini API 키가 설정되지 않았습니다. 사이드바 설정에서 키를 입력하거나 환경 변수를 등록하세요."
        )

    from app.api.logs import add_server_log
    add_server_log(
        "INFO",
        "ANALYSIS",
        f"AI 분석 요청 수신 (검토 조례 원문: {len(req.pdf_text):,}자, 상위법령: {len(req.superior_laws or [])}개, 타 시도 조례: {len(req.search_results or [])}개)"
    )

    try:
        # 1. 상위법령 본문 수집
        superior_laws_content = []
        if req.superior_laws:
            superior_laws_content = get_all_superior_laws_content(req.superior_laws)

        # 2. RAG 참고자료 검색
        relevant_guidelines = []
        if req.use_rag:
            rag_query = req.pdf_text[:300]
            if req.superior_laws:
                rag_query += " " + " ".join(req.superior_laws[:3])
            relevant_guidelines = search_rag_context(rag_query, top_k=5)

        # 3. 프롬프트 생성
        prompt = create_analysis_prompt(
            pdf_text=req.pdf_text,
            search_results=req.search_results or [],
            superior_laws_content=superior_laws_content,
            relevant_guidelines=relevant_guidelines,
            is_first_ordinance=req.is_first_ordinance
        )

        # 4. LLM 호출
        analysis_result = call_gemini_api(
            prompt=prompt,
            api_key=api_key,
            model_name=req.model_name or settings.DEFAULT_GEMINI_MODEL
        )

        return {
            "success": True,
            "analysis": analysis_result,
            "superior_laws_content": superior_laws_content,
            "relevant_guidelines": relevant_guidelines,
            "prompt_length": len(prompt)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 분석 중 오류가 발생했습니다: {str(e)}")

@router.post("/export-docx")
def api_export_analysis_docx(payload: ExportAnalysisDocxRequest):
    try:
        doc = create_comparison_document(
            pdf_text=payload.pdf_text,
            search_results=payload.search_results or [],
            analysis_text=payload.analysis_text,
            superior_laws_content=payload.superior_laws_content,
            relevant_guidelines=payload.relevant_guidelines
        )

        file_stream = io.BytesIO()
        doc.save(file_stream)
        file_stream.seek(0)

        filename = "조례_종합_비교분석_보고서.docx"
        encoded_filename = quote(filename)

        return Response(
            content=file_stream.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"분석 보고서 Word 생성 오류: {str(e)}")

