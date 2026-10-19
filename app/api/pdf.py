from fastapi import APIRouter, UploadFile, File, HTTPException
from app.services.pdf_service import extract_pdf_text, extract_superior_laws

router = APIRouter(prefix="/pdf", tags=["PDF"])

@router.post("/parse")
async def api_parse_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="PDF 형식의 파일만 업로드할 수 있습니다.")
    
    try:
        content = await file.read()
        pdf_text = extract_pdf_text(content)
        
        if not pdf_text.strip():
            raise HTTPException(status_code=400, detail="PDF에서 텍스트를 추출할 수 없습니다. (스캔된 이미지 PDF일 수 있습니다)")
            
        superior_laws, additional_laws = extract_superior_laws(pdf_text)
        
        return {
            "filename": file.filename,
            "char_count": len(pdf_text),
            "text": pdf_text,
            "superior_laws": superior_laws,
            "additional_laws": additional_laws
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF 처리 실패: {str(e)}")

