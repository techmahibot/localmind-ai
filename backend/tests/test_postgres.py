"""HTTP account flow against a real PostgreSQL service in CI."""

import os
import unittest
import uuid
from unittest.mock import patch

import httpx

import auth
import chat_storage
import documents
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
                user_id = auth.find_user(username)[0]
                pages = [(1, "The local model works without internet.")]
                document_id = documents.store_document(
                    documents.DocIndex("notes.txt", pages, user_id), pages
                )
                loaded = documents.load_document(document_id, user_id)
                self.assertEqual(loaded.chunks[0]["text"], pages[0][1])
                self.assertIsNone(documents.load_document(document_id, user_id + 1))

    async def test_chat_persistence_and_user_isolation(self):
        username = "Chat" + uuid.uuid4().hex[:12]
        other_username = "Other" + uuid.uuid4().hex[:12]

        settings = {
            "DATABASE_URL": os.environ["TEST_POSTGRES_URL"],
            "LOCALMIND_SESSION_SECRET": "test-only-postgres-session-secret",
            "VERCEL": "1",
        }

        with patch.dict(os.environ, settings):
            user_id, _ = auth.register(
                username, "a-long-password-123"
            )
            other_user_id, _ = auth.register(
                other_username, "a-long-password-123"
            )

            conversation_id = chat_storage.create_conversation(
                user_id,
                "PostgreSQL chat",
            )

            chat_storage.save_message(
                conversation_id,
                "user",
                "Does PostgreSQL preserve this message?",
            )

            sources = [
                {"n": 1, "page": 2, "snippet": "Test source"}
            ]

            chat_storage.save_message(
                conversation_id,
                "assistant",
                "Yes, the message was saved.",
                sources=sources,
            )

            # Read through fresh database connections.
            conversations = chat_storage.list_conversations(user_id)
            self.assertEqual(len(conversations), 1)
            self.assertEqual(conversations[0]["id"], conversation_id)

            messages = chat_storage.get_messages(
                user_id, conversation_id
            )
            self.assertEqual(len(messages), 2)
            self.assertEqual(
                messages[0]["content"],
                "Does PostgreSQL preserve this message?",
            )
            self.assertEqual(messages[1]["sources"], sources)

            # Another user must not see or retrieve this conversation.
            self.assertEqual(
                chat_storage.list_conversations(other_user_id), []
            )
            self.assertIsNone(
                chat_storage.get_messages(
                    other_user_id, conversation_id
                )
            )

            # Another user must not be able to delete it either.
            self.assertFalse(
                chat_storage.delete_conversation(
                    other_user_id, conversation_id
                )
            )

            # The owner can delete it.
            self.assertTrue(
                chat_storage.delete_conversation(
                    user_id, conversation_id
                )
            )
            self.assertIsNone(
                chat_storage.get_messages(user_id, conversation_id)
            )


if __name__ == "__main__":
    unittest.main()
