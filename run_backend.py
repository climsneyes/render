import uvicorn
import sys
import os

# backend 디렉토리를 파이썬 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Starting FastAPI backend server on port {port}...")
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)

