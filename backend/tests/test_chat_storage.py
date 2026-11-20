
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import auth
import chat_storage


class ChatStorageTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

        self.settings = {
            "LOCALMIND_DB_PATH": str(
                Path(self.temp_dir.name) / "chat_test.db"
            ),
            "LOCALMIND_SESSION_SECRET": "test-only-secret",
        }

        self.env = patch.dict(os.environ, self.settings, clear=True)
        self.env.start()

        self.user_id, _ = auth.register(
            "TestUser", "test-password-123"
        )

        self.other_user_id, _ = auth.register(
            "OtherUser", "other-password-123"
        )

        chat_storage.initialize_chat_storage()

    def tearDown(self):
        self.env.stop()
        self.temp_dir.cleanup()

    def test_create_and_list_conversation(self):
        conversation_id = chat_storage.create_conversation(
            self.user_id,
            "My first chat"
        )

        conversations = chat_storage.list_conversations(self.user_id)

        self.assertEqual(len(conversations), 1)
        self.assertEqual(conversations[0]["id"], conversation_id)
        self.assertEqual(conversations[0]["title"], "My first chat")
        self.assertEqual(conversations[0]["type"], "chat")

    def test_save_and_retrieve_messages(self):
        conversation_id = chat_storage.create_conversation(
            self.user_id,
            "Python discussion"
        )

        chat_storage.save_message(
            conversation_id, "user", "What is Python?"
        )

        chat_storage.save_message(
            conversation_id, "assistant", "Python is a programming language."
        )

        messages = chat_storage.get_messages(
            self.user_id, conversation_id
        )

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["content"], "What is Python?")
        self.assertEqual(messages[1]["role"], "assistant")

    def test_document_conversation_and_sources(self):
        conversation_id = chat_storage.create_conversation(
            self.user_id,
            "Research paper",
            conversation_type="document",
            document_name="research.pdf"
        )

        sources = [
            {"n": 1, "page": 2, "snippet": "Example source"}
        ]

        chat_storage.save_message(
            conversation_id,
            "assistant",
            "The document says this.",
            sources=sources
        )

        messages = chat_storage.get_messages(
            self.user_id, conversation_id
        )

        self.assertEqual(messages[0]["sources"], sources)

        conversations = chat_storage.list_conversations(
            self.user_id, conversation_type="document"
        )

        self.assertEqual(len(conversations), 1)
        self.assertEqual(
            conversations[0]["document_name"], "research.pdf"
        )

    def test_users_cannot_access_each_others_conversations(self):
        conversation_id = chat_storage.create_conversation(
            self.user_id, "Private chat"
        )

        self.assertIsNone(
            chat_storage.get_messages(
                self.other_user_id, conversation_id
            )
        )

        other_conversations = chat_storage.list_conversations(
            self.other_user_id
        )

        self.assertEqual(other_conversations, [])

    def test_delete_conversation(self):
        conversation_id = chat_storage.create_conversation(
            self.user_id, "Delete me"
        )

        chat_storage.save_message(
            conversation_id, "user", "Hello"
        )

        self.assertTrue(
            chat_storage.delete_conversation(
                self.user_id, conversation_id
            )
        )

        self.assertIsNone(
            chat_storage.get_messages(
                self.user_id, conversation_id
            )
        )

        self.assertFalse(
            chat_storage.delete_conversation(
                self.user_id, conversation_id
            )
        )

    def test_invalid_conversation_type_rejected(self):
        with self.assertRaises(ValueError):
            chat_storage.create_conversation(
                self.user_id,
                "Invalid",
                conversation_type="something_else"
            )


if __name__ == "__main__":
    unittest.main()
