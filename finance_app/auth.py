from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .database import Database


class AuthError(ValueError):
    def __init__(
        self,
        message: str,
        status: int = 422,
        fields: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.fields = fields or {}


class AuthService:
    PASSWORD_ROUNDS = 310_000
    SESSION_HOURS = 12
    USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{3,50}$")

    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @classmethod
    def _timestamp(cls, value: datetime | None = None) -> str:
        return (value or cls._now()).isoformat(timespec="seconds")

    @staticmethod
    def _public_user(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "username": row["username"],
            "role": row["role"],
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_login_at": row["last_login_at"],
        }

    @classmethod
    def _hash_password(cls, password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, cls.PASSWORD_ROUNDS
        )
        return "pbkdf2_sha256${}${}${}".format(
            cls.PASSWORD_ROUNDS,
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )

    @staticmethod
    def _verify_password(password: str, encoded: str) -> bool:
        try:
            algorithm, rounds, salt_text, digest_text = encoded.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
            expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
            calculated = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), salt, int(rounds)
            )
            return hmac.compare_digest(calculated, expected)
        except (ValueError, TypeError):
            return False

    @classmethod
    def _validated_identity(
        cls, payload: dict[str, Any], *, password_required: bool
    ) -> dict[str, Any]:
        name = re.sub(r"\s+", " ", str(payload.get("name") or "")).strip()
        username = str(payload.get("username") or "").strip().lower()
        password = str(payload.get("password") or "")
        role = str(payload.get("role") or "user").strip().lower()

        fields: dict[str, str] = {}
        if len(name) < 2:
            fields["name"] = "Informe o nome completo."
        elif len(name) > 120:
            fields["name"] = "Use no máximo 120 caracteres."
        if not cls.USERNAME_PATTERN.fullmatch(username):
            fields["username"] = "Use de 3 a 50 letras, números, ponto, hífen ou sublinhado."
        if password_required and len(password) < 8:
            fields["password"] = "A senha deve ter pelo menos 8 caracteres."
        elif password and len(password) < 8:
            fields["password"] = "A senha deve ter pelo menos 8 caracteres."
        elif len(password) > 128:
            fields["password"] = "Use no máximo 128 caracteres."
        if role not in {"admin", "user"}:
            fields["role"] = "Escolha administrador ou usuário."
        if fields:
            raise AuthError("Confira os dados do usuário.", fields=fields)

        return {"name": name, "username": username, "password": password, "role": role}

    def setup_required(self) -> bool:
        with self.database.connection() as connection:
            count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        return count == 0

    def setup_admin(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
        values = self._validated_identity({**payload, "role": "admin"}, password_required=True)
        now = self._timestamp()
        try:
            with self.database.connection() as connection:
                if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] != 0:
                    raise AuthError("O administrador inicial já foi criado.", status=409)
                cursor = connection.execute(
                    """
                    INSERT INTO users (name, username, password_hash, role, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, 'admin', 1, ?, ?)
                    """,
                    (
                        values["name"],
                        values["username"],
                        self._hash_password(values["password"]),
                        now,
                        now,
                    ),
                )
                user_id = int(cursor.lastrowid)
                row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        except sqlite3.IntegrityError as exc:
            raise AuthError("Este nome de usuário já está em uso.", status=409) from exc
        assert row is not None
        token = self.create_session(user_id)
        return self._public_user(row), token

    def authenticate(self, username: str, password: str) -> tuple[dict[str, Any], str]:
        normalized_username = str(username or "").strip().lower()
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                (normalized_username,),
            ).fetchone()
            if (
                row is None
                or not row["is_active"]
                or not self._verify_password(str(password or ""), row["password_hash"])
            ):
                raise AuthError("Usuário ou senha inválidos.", status=401)
            now = self._timestamp()
            connection.execute(
                "UPDATE users SET last_login_at = ?, updated_at = updated_at WHERE id = ?",
                (now, row["id"]),
            )
            row = connection.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
        assert row is not None
        token = self.create_session(int(row["id"]))
        return self._public_user(row), token

    def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = self._now()
        expires_at = now + timedelta(hours=self.SESSION_HOURS)
        with self.database.connection() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (self._timestamp(now),))
            connection.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (token_hash, user_id, self._timestamp(expires_at), self._timestamp(now)),
            )
        return token

    def session_user(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = self._timestamp()
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT u.*
                FROM sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ? AND s.expires_at > ? AND u.is_active = 1
                """,
                (token_hash, now),
            ).fetchone()
            if row is None:
                connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                return None
        return self._public_user(row)

    def logout(self, token: str | None) -> None:
        if not token:
            return
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.database.connection() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def list_users(self) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM users ORDER BY is_active DESC, role ASC, name COLLATE NOCASE"
            ).fetchall()
        return [self._public_user(row) for row in rows]

    def create_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        values = self._validated_identity({**payload, "role": "user"}, password_required=True)
        now = self._timestamp()
        try:
            with self.database.connection() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO users (name, username, password_hash, role, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        values["name"],
                        values["username"],
                        self._hash_password(values["password"]),
                        values["role"],
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM users WHERE id = ?", (int(cursor.lastrowid),)
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise AuthError("Este nome de usuário já está em uso.", status=409) from exc
        assert row is not None
        return self._public_user(row)

    def update_user(
        self, user_id: int, payload: dict[str, Any], current_user_id: int
    ) -> dict[str, Any] | None:
        with self.database.connection() as connection:
            current = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            owner_admin_id = connection.execute(
                "SELECT MIN(id) FROM users WHERE role = 'admin'"
            ).fetchone()[0]
        if current is None:
            return None
        requested_role = str(payload.get("role") or current["role"]).strip().lower()
        if user_id == owner_admin_id and requested_role != "admin":
            raise AuthError("Você não pode remover a sua própria permissão de administrador.")

        merged = {
            "name": payload.get("name", current["name"]),
            "username": payload.get("username", current["username"]),
            "password": payload.get("password", ""),
            "role": "admin" if user_id == owner_admin_id else "user",
        }
        values = self._validated_identity(merged, password_required=False)
        is_active = bool(payload.get("is_active", current["is_active"]))

        if user_id == current_user_id and not is_active:
            raise AuthError("Você não pode desativar o seu próprio usuário.")
        if user_id == current_user_id and values["role"] != "admin":
            raise AuthError("Você não pode remover a sua própria permissão de administrador.")

        removing_active_admin = (
            current["role"] == "admin"
            and current["is_active"]
            and (values["role"] != "admin" or not is_active)
        )
        if removing_active_admin:
            with self.database.connection() as connection:
                other_admins = connection.execute(
                    "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1 AND id != ?",
                    (user_id,),
                ).fetchone()[0]
            if other_admins == 0:
                raise AuthError("O sistema precisa manter pelo menos um administrador ativo.")

        password_changed = bool(values["password"])
        password_hash = (
            self._hash_password(values["password"])
            if password_changed
            else current["password_hash"]
        )
        try:
            with self.database.connection() as connection:
                connection.execute(
                    """
                    UPDATE users
                    SET name = ?, username = ?, password_hash = ?, role = ?, is_active = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        values["name"],
                        values["username"],
                        password_hash,
                        values["role"],
                        1 if is_active else 0,
                        self._timestamp(),
                        user_id,
                    ),
                )
                if password_changed or not is_active:
                    connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
                row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        except sqlite3.IntegrityError as exc:
            raise AuthError("Este nome de usuário já está em uso.", status=409) from exc
        assert row is not None
        return self._public_user(row)
