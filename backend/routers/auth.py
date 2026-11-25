import hmac
import uuid
from fastapi import APIRouter, Response, Request, HTTPException, status, Depends
from backend.config import settings
from backend.schemas import LoginRequest, LoginResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])

# In-memory session store (single-teacher lightweight session, <100 bytes)
ACTIVE_SESSIONS = set()


def _extract_bearer_token(request: Request) -> str:
    auth_hdr = request.headers.get("Authorization", "")
    if auth_hdr.startswith("Bearer "):
        return auth_hdr[7:].strip()
    return ""


def verify_teacher_auth(request: Request):
    """
    Dependency to verify single-teacher session cookie or Authorization header.
    """
    token = request.cookies.get(settings.SESSION_COOKIE_NAME) or _extract_bearer_token(request)
    if not token or token not in ACTIVE_SESSIONS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="教师身份未认证或登录已过期，请重新输入工作台密码"
        )
    return True


def verify_mcp_auth(request: Request):
    """
    Dependency to verify DSH MCP / Bridge Bearer token via constant-time compare.
    """
    token = _extract_bearer_token(request)
    expected = settings.MCP_AUTH_TOKEN or ""
    if not expected or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MCP/DSH 鉴权令牌无效"
        )
    return True


def verify_teacher_or_mcp_auth(request: Request):
    """
    Allows either authenticated teacher session or valid DSH MCP Bearer token.
    """
    cookie_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    bearer = _extract_bearer_token(request)
    if (cookie_token and cookie_token in ACTIVE_SESSIONS) or (bearer and bearer in ACTIVE_SESSIONS):
        return True
    expected = settings.MCP_AUTH_TOKEN or ""
    if expected and bearer and hmac.compare_digest(bearer, expected):
        return True
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未认证访问，请登录工作台或提供有效服务令牌"
    )

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
