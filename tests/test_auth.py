from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from finance_app.auth import AuthError, AuthService
from finance_app.database import Database


class AuthServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database = Database(Path(self.temporary_directory.name) / "auth.db")
        database.migrate()
        self.database = database
        self.auth = AuthService(database)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_first_user_is_admin_and_session_is_valid(self) -> None:
        self.assertTrue(self.auth.setup_required())
        user, token = self.auth.setup_admin(
            {"name": "Administrador", "username": "Admin", "password": "senha-segura"}
        )
        self.assertEqual(user["role"], "admin")
        self.assertEqual(user["username"], "admin")
        self.assertEqual(self.auth.session_user(token)["id"], user["id"])
        self.assertFalse(self.auth.setup_required())

        with self.assertRaises(AuthError) as duplicate_setup:
            self.auth.setup_admin(
                {"name": "Outro", "username": "outro", "password": "senha-segura"}
            )
        self.assertEqual(duplicate_setup.exception.status, 409)

    def test_admin_can_create_and_update_user(self) -> None:
        admin, _ = self.auth.setup_admin(
            {"name": "Administrador", "username": "admin", "password": "senha-segura"}
        )
        created = self.auth.create_user(
            {
                "name": "Maria Silva",
                "username": "maria",
                "password": "senha-inicial",
                "role": "admin",
            }
        )
        self.assertEqual(created["role"], "user")
        authenticated, token = self.auth.authenticate("MARIA", "senha-inicial")
        self.assertEqual(authenticated["id"], created["id"])

        updated = self.auth.update_user(
            created["id"],
            {"name": "Maria Souza", "is_active": False},
            admin["id"],
        )
        self.assertEqual(updated["name"], "Maria Souza")
        self.assertFalse(updated["is_active"])
        self.assertIsNone(self.auth.session_user(token))

    def test_migration_keeps_only_initial_administrator(self) -> None:
        admin, _ = self.auth.setup_admin(
            {"name": "Administrador", "username": "admin", "password": "senha-segura"}
        )
        created = self.auth.create_user(
            {
                "name": "Outro usuário",
                "username": "outro",
                "password": "senha-inicial",
                "role": "user",
            }
        )
        with self.database.connection() as connection:
            connection.execute("UPDATE users SET role = 'admin' WHERE id = ?", (created["id"],))

        self.database.migrate()
        users = {item["id"]: item for item in self.auth.list_users()}
        self.assertEqual(users[admin["id"]]["role"], "admin")
        self.assertEqual(users[created["id"]]["role"], "user")

    def test_last_active_admin_cannot_be_removed(self) -> None:
        admin, _ = self.auth.setup_admin(
            {"name": "Administrador", "username": "admin", "password": "senha-segura"}
        )
        with self.assertRaises(AuthError):
            self.auth.update_user(
                admin["id"],
                {"name": admin["name"], "username": admin["username"], "role": "user"},
                admin["id"],
            )

    def test_invalid_password_is_rejected(self) -> None:
        self.auth.setup_admin(
            {"name": "Administrador", "username": "admin", "password": "senha-segura"}
        )
        with self.assertRaises(AuthError) as invalid:
            self.auth.authenticate("admin", "senha-errada")
        self.assertEqual(invalid.exception.status, 401)


if __name__ == "__main__":
    unittest.main()
