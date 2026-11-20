
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import auth


class AccountTests(unittest.TestCase):

    def test_two_users_persist_and_passwords_are_hashed(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = {
                "LOCALMIND_DB_PATH": str(Path(directory) / "users.db"),
                "LOCALMIND_SESSION_SECRET": "test-only-secret"
            }

            with patch.dict(os.environ, settings, clear=True):
                mahi_id, mahi_name = auth.register(
                    "Mahi", "a-long-password-1"
                )
                other_id, _ = auth.register(
                    "AnotherUser", "a-long-password-2"
                )

                self.assertNotEqual(mahi_id, other_id)
                self.assertEqual(mahi_name, "Mahi")

                saved = auth.find_user("mahi")

                self.assertEqual(saved[:2], (mahi_id, "Mahi"))
                self.assertNotIn("a-long-password-1", saved[2])
                self.assertTrue(
                    auth.verify_password("a-long-password-1", saved[2])
                )
                self.assertFalse(
                    auth.verify_password("wrong-password", saved[2])
                )

                self.assertEqual(
                    auth.find_user_by_id(other_id)[1],
                    "AnotherUser"
                )

                with self.assertRaises(auth.UsernameTaken):
                    auth.register("MAHI", "another-password")

                token = auth.create_session(mahi_id)

                self.assertEqual(
                    auth.session_user_id(token), mahi_id
                )
                self.assertIsNone(
                    auth.session_user_id(token + "tampered")
                )
                self.assertIsNone(auth.session_user_id(None))

    def test_vercel_requires_persistent_database_and_session_secret(self):
        with patch.dict(os.environ, {"VERCEL": "1"}, clear=True):
            with self.assertRaises(auth.AuthConfigurationError):
                auth.database_url()

            with self.assertRaises(auth.AuthConfigurationError):
                auth.session_secret()

    def test_signup_validation(self):
        with self.assertRaises(ValueError):
            auth.register("a!", "a-long-password")

        with self.assertRaises(ValueError):
            auth.register("ValidName", "short")

    def test_database_ready_creates_users_table(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = {
                "LOCALMIND_DB_PATH": str(Path(directory) / "users.db")
            }

            with patch.dict(os.environ, settings, clear=True):
                self.assertTrue(auth.database_ready())

                with auth.connection() as (conn, postgres):
                    self.assertFalse(postgres)

                    result = conn.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type = 'table'
                        AND name = 'localmind_users'
                        """
                    ).fetchone()

                    self.assertIsNotNone(result)

    def test_user_persists_across_new_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = {
                "LOCALMIND_DB_PATH": str(Path(directory) / "users.db"),
                "LOCALMIND_SESSION_SECRET": "test-only-secret"
            }

            with patch.dict(os.environ, settings, clear=True):
                user_id, _ = auth.register(
                    "PersistentUser",
                    "a-long-password-123"
                )

                # Open a separate database connection
                with auth.connection() as (conn, postgres):
                    self.assertFalse(postgres)

                    saved = conn.execute(
                        """
                        SELECT id, username
                        FROM localmind_users
                        WHERE username_key = ?
                        """,
                        ("persistentuser",)
                    ).fetchone()

                    self.assertEqual(
                        saved,
                        (user_id, "PersistentUser")
                    )


    def test_expired_session_is_rejected(self):
        with patch.dict(
            os.environ,
            {"LOCALMIND_SESSION_SECRET": "test-only-secret"},
            clear=True
        ):
            # Create a session at a fixed time
            with patch("auth.time.time", return_value=1000):
                token = auth.create_session(1)

            # Move time beyond the session expiry period
            expired_time = 1000 + auth.SESSION_SECONDS + 1

            with patch("auth.time.time", return_value=expired_time):
                self.assertIsNone(auth.session_user_id(token))

if __name__ == "__main__":
    unittest.main()
