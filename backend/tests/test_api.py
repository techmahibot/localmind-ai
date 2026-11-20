import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

import main
from llm import ModelResponseError


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = patch.dict(os.environ, {
            "LOCALMIND_DB_PATH": str(Path(self.directory.name) / "users.db"),
            "LOCALMIND_SESSION_SECRET": "test-session-secret",
            "LLM_PROVIDER": "ollama",
        }, clear=True)
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        )
        self.addAsyncCleanup(self.client.aclose)

    async def signup(self):
        return await self.client.post("/api/auth/signup", json={
            "username": "Mahi", "password": "a-long-password-123"
        })

    async def test_auth_and_protected_routes(self):
        self.assertEqual((await self.client.post("/api/chat", json={"message": "Hi"})).status_code, 401)
        self.assertEqual((await self.signup()).status_code, 201)
        self.assertEqual((await self.client.get("/api/auth/me")).json()["username"], "Mahi")
        self.assertEqual((await self.signup()).status_code, 409)
        self.assertEqual((await self.client.post("/api/auth/logout")).status_code, 200)
        self.assertEqual((await self.client.get("/api/auth/me")).status_code, 401)
        self.assertEqual((await self.client.post("/api/auth/login", json={
            "username": "mahi", "password": "wrong"
        })).status_code, 401)
        self.assertEqual((await self.client.post("/api/auth/login", json={
            "username": "mahi", "password": "a-long-password-123"
        })).status_code, 200)

    async def test_chat_validation_model_failure_and_stream(self):
        await self.signup()
        for endpoint in ("/api/chat", "/api/chat/stream"):
            self.assertEqual((await self.client.post(endpoint, json={"message": "   "})).status_code, 422)
        with patch.object(main, "ask", new_callable=AsyncMock, return_value="Hello"):
            response = await self.client.post("/api/chat", json={"message": "Hi"})
            self.assertEqual(response.json(), {"reply": "Hello"})
        with patch.object(main, "ask", new_callable=AsyncMock, side_effect=httpx.ConnectError("offline")):
            response = await self.client.post("/api/chat", json={"message": "Hi"})
            self.assertEqual(response.status_code, 503)
        with patch.object(main, "ask", new_callable=AsyncMock, side_effect=httpx.ReadTimeout("slow")):
            response = await self.client.post("/api/chat", json={"message": "Hi"})
            self.assertEqual(response.status_code, 504)
        with patch.object(main, "ask", new_callable=AsyncMock,
                          side_effect=ModelResponseError("The model returned an unexpected response.")):
            response = await self.client.post("/api/chat", json={"message": "Hi"})
            self.assertEqual(response.status_code, 502)

        async def open_fake_stream(_):
            client = httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, text='{"message":{"content":"Hello"},"done":false}\n'
                              '{"message":{"content":" world"},"done":true}\n'
                )))
            response = await client.send(client.build_request("GET", "http://fake"), stream=True)
            return client, response
        with patch.object(main, "open_stream", side_effect=open_fake_stream):
            response = await self.client.post("/api/chat/stream", json={"message": "Hi"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.text, "Hello world")

        async def interrupted_stream(_):
            client = httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, text='{"message":{"content":"Partial"},"done":false}\n'
                )))
            response = await client.send(client.build_request("GET", "http://fake"), stream=True)
            return client, response
        with patch.object(main, "open_stream", side_effect=interrupted_stream):
            response = await self.client.post("/api/chat/stream", json={"message": "Hi"})
            self.assertIn("Partial", response.text)
            self.assertIn("ended unexpectedly", response.text)

    async def test_summaries_uploads_and_grammar(self):
        await self.signup()
        self.assertEqual((await self.client.post("/api/summarize", json={"text": "  "})).status_code, 422)
        self.assertEqual((await self.client.post("/api/improve-text", json={
            "text": "hello", "task": "unknown"
        })).status_code, 422)
        self.assertEqual((await self.client.post("/api/improve-text", json={
            "text": "  "
        })).status_code, 422)
        with patch.object(main, "summarize_document", new_callable=AsyncMock,
                          return_value={"summary": "Summary", "chunks": 1, "truncated": False}):
            response = await self.client.post("/api/summarize", json={"text": "A document."})
            self.assertEqual(response.json()["summary"], "Summary")
            response = await self.client.post("/api/summarize/file", files={
                "file": ("notes.txt", b"A document.", "text/plain")
            })
            self.assertEqual(response.json()["characters"], 11)
        for name, data, status in [
            ("bad.csv", b"abc", 415), ("empty.txt", b"  ", 422),
            ("binary.txt", b"\xff", 422), ("broken.pdf", b"not a pdf", 422),
            ("large.txt", b"x" * (main.MAX_UPLOAD_BYTES + 1), 413),
        ]:
            response = await self.client.post("/api/summarize/file", files={
                "file": (name, data, "application/octet-stream")
            })
            self.assertEqual(response.status_code, status)
        with patch.object(main, "improve_text", new_callable=AsyncMock, return_value="Hello."):
            response = await self.client.post("/api/improve-text", json={
                "text": "hello", "task": "grammar"
            })
            self.assertEqual(response.json(), {"improved_text": "Hello."})

    async def test_health_reflects_database_and_model(self):
        with patch.object(main, "reachable", new_callable=AsyncMock, return_value=False):
            response = await self.client.get("/api/health")
        self.assertEqual(response.json()["account_storage"], "reachable")
        self.assertTrue(response.json()["login_configured"])
        self.assertEqual(response.json()["model_server"], "unavailable")
        self.assertEqual(response.json()["deployment"], "local")
        with patch.object(main, "database_ready", side_effect=sqlite3.OperationalError("offline")), \
             patch.object(main, "reachable", new_callable=AsyncMock, return_value=True):
            response = await self.client.get("/api/health")
        self.assertEqual(response.json()["account_storage"], "unavailable")
        self.assertFalse(response.json()["login_configured"])
        self.assertEqual(response.json()["model_server"], "reachable")
        with patch.dict(os.environ, {"VERCEL": "1"}), \
             patch.object(main, "reachable", new_callable=AsyncMock, return_value=False):
            response = await self.client.get("/api/health")
        self.assertEqual(response.json()["deployment"], "hosted")

    async def test_account_storage_failure_has_readable_error(self):
        with patch.object(main, "register", side_effect=sqlite3.OperationalError("offline")):
            response = await self.signup()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Account storage is temporarily unavailable.")


if __name__ == "__main__":
    unittest.main()
