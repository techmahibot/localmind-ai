"""HTTP account flow against a real PostgreSQL service in CI."""

import os
import unittest
import uuid
from unittest.mock import patch

import httpx

import auth
import main


@unittest.skipUnless(os.environ.get("TEST_POSTGRES_URL"), "PostgreSQL test service not configured")
class PostgresAccountTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_survives_new_connections_and_login(self):
        username = "Test" + uuid.uuid4().hex[:12]
        settings = {
            "DATABASE_URL": os.environ["TEST_POSTGRES_URL"],
            "LOCALMIND_SESSION_SECRET": "test-only-postgres-session-secret",
            "VERCEL": "1",
        }
        with patch.dict(os.environ, settings):
            self.assertTrue(auth.database_ready())
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app),
                base_url="https://test.local",
            ) as client:
                created = await client.post("/api/auth/signup", json={
                    "username": username, "password": "a-long-password-123"
                })
                self.assertEqual(created.status_code, 201, created.text)
                self.assertEqual((await client.get("/api/auth/me")).json()["username"], username)
                self.assertEqual(auth.find_user(username.lower())[1], username)
                self.assertEqual((await client.post("/api/auth/logout")).status_code, 200)
                self.assertEqual((await client.get("/api/auth/me")).status_code, 401)
                logged_in = await client.post("/api/auth/login", json={
                    "username": username.lower(), "password": "a-long-password-123"
                })
                self.assertEqual(logged_in.status_code, 200, logged_in.text)
                self.assertEqual((await client.get("/api/auth/me")).json()["username"], username)
                duplicate = await client.post("/api/auth/signup", json={
                    "username": username.upper(), "password": "another-long-password"
                })
                self.assertEqual(duplicate.status_code, 409)


if __name__ == "__main__":
    unittest.main()
