"""注册 / 登录 / 验证码 / 当前用户（设计方案 §4.2 P1、P2）。

接口清单::

    POST /api/auth/captcha           出题（返回 captcha_id + SVG data-uri）
    GET  /api/auth/check-username    注册页实时查重（失焦触发）
    POST /api/auth/register          注册（账号/密码/验证码）
    POST /api/auth/login             登录 → JWT
    GET  /api/auth/me                当前用户（需 Bearer token）

安全约定：
* 账号不存在与密码错误返回**同一句**"账号或密码错误"，避免枚举账号；
* 账号不存在时也做一次 bcrypt 哑校验（耗时对齐）；
* 连续失败 ``login_fail_limit`` 次（默认 5）锁定 ``login_fail_window_s`` 秒（Redis 计数）；
* 验证码**一次性**，校验即删除。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import captcha as captcha_svc
from app.core.errors import AuthError, ConflictError, RateLimited
from app.core.logger import get_logger
from app.core.security import (
    CurrentUser,
    create_access_token,
    dummy_verify,
    get_current_user,
    hash_password,
    validate_password,
    validate_username,
    verify_password,
)
from app.db.mysql import get_db
from config import Settings, get_settings

logger = get_logger("lawanswer.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

_FAIL_PREFIX = "login:fail:"


# ---------------------------------------------------------------------------
# 模型
# ---------------------------------------------------------------------------
class CaptchaResponse(BaseModel):
    captcha_id: str
    image: str = Field(description="可直接用于 <img src> 的 SVG data URI")
    expires_in: int


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=64)
    captcha_id: str = Field(min_length=4, max_length=64)
    captcha_code: str = Field(min_length=1, max_length=8)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=64)
    captcha_id: str = Field(min_length=4, max_length=64)
    captcha_code: str = Field(min_length=1, max_length=8)


class UserOut(BaseModel):
    id: int
    username: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


# ---------------------------------------------------------------------------
# 数据访问（单测可 monkeypatch 这几个函数）
# ---------------------------------------------------------------------------
def find_user_by_username(db: Session, username: str) -> dict | None:
    row = db.execute(
        text("SELECT id, username, password_hash, status FROM users WHERE username = :u"),
        {"u": username},
    ).mappings().first()
    return dict(row) if row else None


def find_user_by_id(db: Session, user_id: int) -> dict | None:
    row = db.execute(
        text("SELECT id, username, status FROM users WHERE id = :id"),
        {"id": user_id},
    ).mappings().first()
    return dict(row) if row else None


def insert_user(db: Session, username: str, password_hash: str) -> int:
    result = db.execute(
        text("INSERT INTO users (username, password_hash) VALUES (:u, :p)"),
        {"u": username, "p": password_hash},
    )
    return int(result.lastrowid or 0)


def touch_login(db: Session, user_id: int) -> None:
    db.execute(text("UPDATE users SET last_login_at = CURRENT_TIMESTAMP(3) WHERE id = :id"), {"id": user_id})


# ---------------------------------------------------------------------------
# 登录失败计数（Redis，故障时降级不锁）
# ---------------------------------------------------------------------------
def _fail_counter(username: str, settings: Settings):
    from app.db.redis_client import get_redis

    try:
        return get_redis(settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("登录失败计数降级（Redis 不可用）：%s", str(exc)[:120])
        return None


def _check_locked(username: str, settings: Settings) -> None:
    r = _fail_counter(username, settings)
    if r is None:
        return
    try:
        raw = r.get(_FAIL_PREFIX + username)
        count = int(raw or 0)
    except Exception:  # noqa: BLE001
        return
    if count >= settings.login_fail_limit:
        raise RateLimited(f"登录失败次数过多，请 {settings.login_fail_window_s} 秒后再试")


def _record_failure(username: str, settings: Settings) -> None:
    r = _fail_counter(username, settings)
    if r is None:
        return
    try:
        key = _FAIL_PREFIX + username
        count = r.incr(key)
        if int(count) == 1:
            r.expire(key, settings.login_fail_window_s)
    except Exception:  # noqa: BLE001
        return


def _clear_failures(username: str, settings: Settings) -> None:
    r = _fail_counter(username, settings)
    if r is None:
        return
    try:
        r.delete(_FAIL_PREFIX + username)
    except Exception:  # noqa: BLE001
        return


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@router.post("/captcha", response_model=CaptchaResponse, summary="获取图形验证码")
def get_captcha() -> CaptchaResponse:
    s = get_settings()
    captcha_id, image = captcha_svc.new_captcha(settings=s)
    return CaptchaResponse(captcha_id=captcha_id, image=image, expires_in=s.captcha_ttl_s)


@router.get("/check-username", summary="账号是否可用（注册页实时查重）")
def check_username(username: str = Query(min_length=1, max_length=32), db: Session = Depends(get_db)) -> dict:
    u = (username or "").strip()
    import re

    from app.core.security import USERNAME_PATTERN

    if not re.match(USERNAME_PATTERN, u):
        return {"available": False, "reason": "账号需为 3–32 位字母、数字或下划线"}
    if find_user_by_username(db, u) is not None:
        return {"available": False, "reason": "该账号已被占用"}
    return {"available": True, "reason": None}


@router.post("/register", status_code=201, response_model=UserOut, summary="注册")
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)) -> UserOut:
    s = get_settings()
    username = validate_username(payload.username)
    validate_password(payload.password)
    if not captcha_svc.verify_captcha(payload.captcha_id, payload.captcha_code, settings=s):
        raise AuthError("验证码错误或已过期，请刷新后重试", code="captcha_invalid", status_code=422)
    if find_user_by_username(db, username) is not None:
        raise ConflictError("该账号已被占用")
    user_id = insert_user(db, username, hash_password(payload.password))
    db.commit()
    logger.info("注册成功 username=%s id=%s ip=%s", username, user_id, _client_ip(request))
    return UserOut(id=user_id, username=username)


@router.post("/login", response_model=LoginResponse, summary="登录")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    s = get_settings()
    username = (payload.username or "").strip()

    _check_locked(username, s)
    if not captcha_svc.verify_captcha(payload.captcha_id, payload.captcha_code, settings=s):
        raise AuthError("验证码错误或已过期，请刷新后重试", code="captcha_invalid", status_code=422)

    user = find_user_by_username(db, username)
    if user is None:
        dummy_verify()  # 耗时对齐，防响应时间枚举账号
        _record_failure(username, s)
        raise AuthError("账号或密码错误")
    if not verify_password(payload.password, user["password_hash"]):
        _record_failure(username, s)
        raise AuthError("账号或密码错误")
    if int(user["status"]) != 1:
        raise AuthError("账号已被禁用，请联系管理员", code="forbidden", status_code=403)

    _clear_failures(username, s)
    touch_login(db, int(user["id"]))
    db.commit()
    token = create_access_token(int(user["id"]), user["username"], settings=s)
    logger.info("登录成功 username=%s id=%s ip=%s", username, user["id"], _client_ip(request))
    return LoginResponse(
        access_token=token,
        expires_in=s.jwt_expire_hours * 3600,
        user=UserOut(id=int(user["id"]), username=user["username"]),
    )


@router.get("/me", response_model=UserOut, summary="当前登录用户")
def me(user: CurrentUser = Depends(get_current_user)) -> UserOut:
    return UserOut(id=user.id, username=user.username)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "-"
