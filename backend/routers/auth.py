import uuid
from fastapi import APIRouter, Response, Request, HTTPException, status, Depends
from backend.config import settings
from backend.schemas import LoginRequest, LoginResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])

# In-memory session store (single-teacher lightweight session, <100 bytes)
ACTIVE_SESSIONS = set()

def verify_teacher_auth(request: Request):
    """
    Dependency to verify single-teacher session cookie or Authorization header.
    """
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not token:
        # Also check Authorization header Bearer token
        auth_hdr = request.headers.get("Authorization")
        if auth_hdr and auth_hdr.startswith("Bearer "):
            token = auth_hdr.split(" ")[1]

    if not token or token not in ACTIVE_SESSIONS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="教师身份未认证或登录已过期，请重新输入工作台密码"
        )
    return True

@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, response: Response):
    if req.password == settings.TEACHER_ACCESS_PASSWORD:
        session_token = f"sess_{uuid.uuid4().hex}"
        ACTIVE_SESSIONS.add(session_token)

        # Set HTTP-only Cookie
        response.set_cookie(
            key=settings.SESSION_COOKIE_NAME,
            value=session_token,
            httponly=False,  # Allow JS access for mobile/API clients
            max_age=86400 * 7,  # 7 days
            samesite="lax",
            path="/"
        )
        return LoginResponse(success=True, message=session_token)
    raise HTTPException(status_code=400, detail="教师密码错误")

@router.post("/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if token and token in ACTIVE_SESSIONS:
        ACTIVE_SESSIONS.remove(token)
    response.delete_cookie(settings.SESSION_COOKIE_NAME, path="/")
    return {"success": True, "message": "已退出登录"}

@router.get("/check")
async def check_auth(request: Request):
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if token and token in ACTIVE_SESSIONS:
        return {"authenticated": True}
    return {"authenticated": False}
