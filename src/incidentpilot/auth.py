"""Local authentication and role verification with environment-owned secrets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import json
import os
import secrets
from typing import Any

import jwt
from pwdlib import PasswordHash


class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    APPROVER = "approver"


@dataclass(frozen=True, slots=True)
class UserRecord:
    username: str
    password_hash: str
    role: Role


@dataclass(frozen=True, slots=True)
class AuthConfig:
    secret: str
    users: dict[str, UserRecord]
    token_ttl_seconds: int = 900
    issuer: str = "incidentpilot-local"
    audience: str = "incidentpilot-api"

    def __post_init__(self) -> None:
        if len(self.secret.encode("utf-8")) < 32:
            raise ValueError("Authentication signing secret must be at least 32 bytes")
        if not self.users:
            raise ValueError("At least one authenticated user is required")
        if self.token_ttl_seconds < 1:
            raise ValueError("Token lifetime must be positive")

    @classmethod
    def from_env(cls) -> AuthConfig | None:
        secret = os.environ.get("INCIDENTPILOT_AUTH_SECRET")
        users_json = os.environ.get("INCIDENTPILOT_USERS_JSON")
        if not secret or not users_json:
            return None
        try:
            raw_users: dict[str, dict[str, Any]] = json.loads(users_json)
            users = {
                username: UserRecord(
                    username=username,
                    password_hash=str(value["password_hash"]),
                    role=Role(value["role"]),
                )
                for username, value in raw_users.items()
            }
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid INCIDENTPILOT_USERS_JSON configuration") from exc
        ttl = int(os.environ.get("INCIDENTPILOT_TOKEN_TTL_SECONDS", "900"))
        return cls(secret=secret, users=users, token_ttl_seconds=ttl)


class AuthService:
    _password_hash = PasswordHash.recommended()
    _algorithm = "HS256"

    def __init__(self, config: AuthConfig | None):
        self.config = config

    @classmethod
    def from_env(cls) -> AuthService:
        return cls(AuthConfig.from_env())

    @classmethod
    def for_testing(cls, users: dict[str, tuple[str, Role]]) -> AuthService:
        records = {
            username: UserRecord(
                username=username,
                password_hash=cls._password_hash.hash(password),
                role=role,
            )
            for username, (password, role) in users.items()
        }
        return cls(AuthConfig(secret=secrets.token_urlsafe(48), users=records))

    @classmethod
    def hash_password(cls, password: str) -> str:
        if len(password) < 12:
            raise ValueError("Password must contain at least 12 characters")
        return cls._password_hash.hash(password)

    def authenticate(self, username: str, password: str) -> UserRecord | None:
        if self.config is None:
            return None
        user = self.config.users.get(username)
        if user is None:
            self._password_hash.verify(password, self._password_hash.hash(secrets.token_urlsafe(16)))
            return None
        if not self._password_hash.verify(password, user.password_hash):
            return None
        return user

    def issue_token(
        self, user: UserRecord, *, expires_delta: timedelta | None = None
    ) -> str:
        if self.config is None:
            raise RuntimeError("Authentication is not configured")
        now = datetime.now(timezone.utc)
        expires = now + (expires_delta or timedelta(seconds=self.config.token_ttl_seconds))
        return jwt.encode(
            {
                "sub": user.username,
                "role": user.role.value,
                "iat": now,
                "exp": expires,
                "iss": self.config.issuer,
                "aud": self.config.audience,
            },
            self.config.secret,
            algorithm=self._algorithm,
        )

    def verify_token(self, token: str) -> UserRecord | None:
        if self.config is None:
            return None
        try:
            payload = jwt.decode(
                token,
                self.config.secret,
                algorithms=[self._algorithm],
                issuer=self.config.issuer,
                audience=self.config.audience,
                options={"require": ["sub", "role", "iat", "exp", "iss", "aud"]},
            )
            username = payload["sub"]
            role = Role(payload["role"])
        except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
            return None
        user = self.config.users.get(username)
        if user is None or user.role != role:
            return None
        return user
