import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.db import init_db
from backend.services.kb_retriever import load_kb_vectors
from backend.routers import auth, students, exams, ocr_upload, sheets, kb, mcp_server

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure directories exist
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    os.makedirs(settings.CROPPED_DIR, exist_ok=True)
    os.makedirs(settings.PDFS_DIR, exist_ok=True)
    os.makedirs(settings.BATCHES_DIR, exist_ok=True)

    # 1. Initialize SQLite Database (WAL mode & seeding)
    init_db()

    # 2. In-memory KB vector index loading (NumPy memory ~0.5MB)
    try:
        load_kb_vectors()
    except Exception as e:
        print(f"Warning: KB vector index deferred or failed: {e}")

    # Notice: Playwright browser is STRICTLY NOT started here to save memory (250-350MB limit)!
    yield

app = FastAPI(
    title="小学六年级数学‘保及格’教师端工作台",
    version="1.0.0",
    lifespan=lifespan
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API Routers
app.include_router(auth.router)
app.include_router(students.router)
app.include_router(exams.router)
app.include_router(ocr_upload.router)
app.include_router(ocr_upload.batches_router)
app.include_router(sheets.router)
app.include_router(kb.router)
app.include_router(mcp_server.router)

# Mount Static & Data Storage Directories
frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
static_dir = os.path.join(frontend_dir, "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
if os.path.exists(data_dir):
    app.mount("/data", StaticFiles(directory=data_dir), name="data")

# Frontend HTML Page Routes
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse("<h1>小学六年级数学‘保及格’教师端工作台已启动</h1>")

@app.get("/login.html", response_class=HTMLResponse)
async def login_page():
    page_path = os.path.join(frontend_dir, "login.html")
    if os.path.exists(page_path):
        return FileResponse(page_path)
    return HTMLResponse("<h1>登录页面</h1>")

@app.get("/upload.html", response_class=HTMLResponse)
async def upload_page():
    page_path = os.path.join(frontend_dir, "upload.html")
    if os.path.exists(page_path):
        return FileResponse(page_path)
    return HTMLResponse("<h1>手机拍照直传页</h1>")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=settings.APP_HOST, port=settings.APP_PORT, reload=True)
