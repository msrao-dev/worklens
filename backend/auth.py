import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
import hmac
import hashlib
import json
import base64
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from backend.db import get_db_connection

logger = logging.getLogger(__name__)

JWT_SECRET = os.getenv("JWT_SECRET", "").strip()
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRY_HOURS = 24

security = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    role: Optional[str] = "EMPLOYEE"


class ResetPasswordRequest(BaseModel):
    new_password: str
    retype_new_password: str


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _base64url_decode(data: str) -> bytes:
    padding = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET is not configured.")

    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(hours=TOKEN_EXPIRY_HOURS))
    to_encode.update({"exp": int(expire.timestamp())})

    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    encoded_header = _base64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_payload = _base64url_encode(json.dumps(to_encode, separators=(",", ":")).encode("utf-8"))

    signing_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")
    signature = hmac.new(JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    encoded_signature = _base64url_encode(signature)

    return f"{encoded_header}.{encoded_payload}.{encoded_signature}"


def decode_access_token(token: str) -> dict:
    if not JWT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT_SECRET is not configured on the backend.",
        )

    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token format.")

    encoded_header, encoded_payload, encoded_signature = parts
    signing_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")
    expected_sig = hmac.new(JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    actual_sig = _base64url_decode(encoded_signature)

    if not hmac.compare_digest(expected_sig, actual_sig):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token signature.")

    try:
        payload = json.loads(_base64url_decode(encoded_payload).decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token payload.")

    exp = payload.get("exp")
    if not exp or datetime.now(timezone.utc).timestamp() > exp:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired.")

    return payload


async def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> dict:
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(credentials.credentials)
    email = payload.get("sub")
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token identity.")

    conn = get_db_connection()
    try:
        cursor = conn.execute(
            "SELECT id, email, role, is_active FROM users WHERE email = ?",
            (email,),
        )
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")
        if not user["is_active"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is deactivated.")
        return dict(user)
    finally:
        conn.close()


async def require_hr_user(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "HR":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access restricted: HR privileges required.",
        )
    return current_user