"""Local accounts and revocable, server-side sessions. No external auth service."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
import unicodedata
from contextlib import closing, contextmanager
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from slide_agent.utils import request_workspace, resolve_workspace

COOKIE = "predel_session"
SESSION_AGE = 7 * 24 * 3600
router = APIRouter(prefix="/api/auth", tags=["Accounts"])


@contextmanager
def database():
    root = resolve_workspace()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "accounts.sqlite3"
    with closing(sqlite3.connect(path, timeout=15)) as db, db:
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT NOT NULL,
                username_key TEXT UNIQUE NOT NULL, position TEXT NOT NULL,
                salt TEXT NOT NULL, password_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attempts (
                key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL
            );
        """)
        if os.name != "nt":
            path.chmod(0o600)
        yield db


def password_hash(password: str, salt: str) -> str:
    return hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1
    ).hex()


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[\w.-]+$")
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return (
            unicodedata.normalize("NFKC", value).strip()
            if isinstance(value, str)
            else value
        )


class Registration(Credentials):
    position: str = Field(min_length=2, max_length=120)

    @field_validator("position", mode="before")
    @classmethod
    def clean_position(cls, value):
        return value.strip() if isinstance(value, str) else value


def public_user(row) -> dict:
    return {key: row[key] for key in ("id", "username", "position")}


def current_user(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE, "")
    if not token or len(token) > 200:
        return None
    with database() as db:
        row = db.execute(
            "SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id "
            "WHERE token_hash=? AND expires>?",
            (hashlib.sha256(token.encode()).hexdigest(), time.time()),
        ).fetchone()
    return public_user(row) if row else None


def issue_session(db, user_id: str, request: Request, response: Response):
    now = time.time()
    db.execute("DELETE FROM sessions WHERE expires<=?", (now,))
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO sessions VALUES (?, ?, ?)",
        (hashlib.sha256(token.encode()).hexdigest(), user_id, now + SESSION_AGE),
    )
    response.set_cookie(
        COOKIE,
        token,
        max_age=SESSION_AGE,
        httponly=True,
        secure=request.url.scheme == "https"
        or os.getenv("PREDEL_SECURE_COOKIES") == "1",
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


def rate_limit(request: Request, kind: str, limit: int):
    address = request.client.host if request.client else "unknown"
    key = hashlib.sha256(f"{kind}:{address}".encode()).hexdigest()
    now = time.time()
    with database() as db:
        db.execute("DELETE FROM attempts WHERE expires<=?", (now,))
        db.execute(
            "INSERT INTO attempts VALUES (?, 1, ?) ON CONFLICT(key) "
            "DO UPDATE SET count=count+1",
            (key, now + 600),
        )
        row = db.execute("SELECT count FROM attempts WHERE key=?", (key,)).fetchone()
    if row["count"] > limit:
        raise HTTPException(
            429,
            "Слишком много попыток. Повторите через 10 минут.",
            headers={"Retry-After": "600"},
        )


@router.post("/register", status_code=201)
def register(body: Registration, request: Request, response: Response):
    rate_limit(request, "register", 10)
    salt = secrets.token_hex(16)
    user_id = secrets.token_hex(16)
    hashed = password_hash(body.password, salt)
    try:
        with database() as db:
            db.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    body.username,
                    body.username.casefold(),
                    body.position,
                    salt,
                    hashed,
                ),
            )
            issue_session(db, user_id, request, response)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Это имя пользователя уже занято.") from None
    return {"id": user_id, "username": body.username, "position": body.position}


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    rate_limit(request, "login", 20)
    with database() as db:
        row = db.execute(
            "SELECT * FROM users WHERE username_key=?", (body.username.casefold(),)
        ).fetchone()
        # Derive a hash even for unknown users to reduce account timing differences.
        hashed = password_hash(body.password, row["salt"] if row else "0" * 32)
        if row is None or not hmac.compare_digest(hashed, row["password_hash"]):
            raise HTTPException(401, "Неверное имя пользователя или пароль.")
        issue_session(db, row["id"], request, response)
        return public_user(row)


@router.get("/me")
def me(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "Войдите в аккаунт.")
    return user


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE, "")
    with database() as db:
        db.execute(
            "DELETE FROM sessions WHERE token_hash=?",
            (hashlib.sha256(token.encode()).hexdigest(),),
        )
    response.delete_cookie(COOKIE, path="/")
    response.headers["Cache-Control"] = "no-store"


class AccountMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        origin = request.headers.get("origin")
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and origin
            and (
                urlsplit(origin).netloc != request.url.netloc
                or urlsplit(origin).scheme != request.url.scheme
            )
        ):
            return await JSONResponse(
                {"detail": "Недопустимый источник запроса."}, 403
            )(scope, receive, send)
        token = None
        if request.url.path.startswith("/v1/"):
            user = await run_in_threadpool(current_user, request)
            if user is None:
                return await JSONResponse({"detail": "Войдите в аккаунт."}, 401)(
                    scope, receive, send
                )
            scope.setdefault("state", {})["user"] = user
            token = request_workspace.set(resolve_workspace() / "users" / user["id"])
        try:

            async def safe_send(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.extend(
                        [
                            (b"x-content-type-options", b"nosniff"),
                            (b"referrer-policy", b"same-origin"),
                            (b"x-frame-options", b"DENY"),
                        ]
                    )
                    if request.url.path.startswith(("/v1/", "/api/auth/")):
                        headers.append((b"cache-control", b"no-store"))
                    message["headers"] = headers
                await send(message)

            await self.app(scope, receive, safe_send)
        finally:
            if token is not None:
                request_workspace.reset(token)


def install_auth(app: FastAPI):
    app.include_router(router)
    app.add_middleware(AccountMiddleware)
