import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.urls import reverse

from .knowledge_base import (
    KnowledgeBaseError,
    KnowledgeChunk,
    _load_chunks,
    _select_pdf_paths,
    _tokens,
)
from .models import Conversation, Message


class KnowledgeContextTests(TestCase):
    @patch("chat.views.retrieve_context", return_value="[Source : guide.pdf]")
    @patch("chat.views.load_api_key", return_value="test-key")
    @patch("chat.views.requests.post")
    def test_send_message_includes_retrieved_context_in_system_prompt(
        self,
        post,
        load_api_key,
        retrieve_context,
    ):
        conversation = Conversation.objects.create()
        post.return_value = Mock(
            ok=True,
            json=lambda: {"message": {"content": "Réponse fondée."}},
        )

        response = self.client.post(
            reverse("chat:send_message", args=[conversation.pk]),
            data='{"message": "Quels sont les constats ?"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        retrieve_context.assert_called_once_with("Quels sont les constats ?")
        sent_messages = post.call_args.kwargs["json"]["messages"]
        self.assertEqual(sent_messages[0]["role"], "system")
        self.assertIn(
            "[Source : guide.pdf]",
            sent_messages[0]["content"],
        )
        self.assertEqual(sent_messages[1]["content"], "Quels sont les constats ?")

    @patch("chat.views.retrieve_context")
    @patch("chat.views.load_api_key", return_value="test-key")
    @patch("chat.views.requests.post")
    def test_retrieval_query_includes_recent_conversation_context(
        self,
        post,
        load_api_key,
        retrieve_context,
    ):
        conversation = Conversation.objects.create()
        Message.objects.create(
            conversation=conversation,
            role="user",
            content="Que sait-on du logement social ?",
        )
        post.return_value = Mock(
            ok=True,
            json=lambda: {"message": {"content": "Réponse."}},
        )

        self.client.post(
            reverse("chat:send_message", args=[conversation.pk]),
            data='{"message": "Et pour les familles ?"}',
            content_type="application/json",
        )

        retrieve_context.assert_called_once_with(
            "Que sait-on du logement social ? Et pour les familles ?"
        )

    def test_pdf_selection_matches_plural_and_compound_filename_words(self):
        pdfs = (
            Path("Rapport_LogementsSante.pdf"),
            Path("Rapport_Transport.pdf"),
        )

        selected = _select_pdf_paths("logement santé", pdfs)

        self.assertEqual(selected, (pdfs[0],))
        self.assertEqual(_tokens("logements sante"), ["logement", "sante"])

    def test_retrieval_query_normalizes_french_accents_and_plural(self):
        self.assertEqual(
            _tokens("Les logements salubres et la santé"),
            ["logement", "salubre", "sante"],
        )

    def test_questions_reponses_sources_is_excluded_from_context(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            knowledge_dir = Path(temporary_directory) / "Base_de_connaissance"
            knowledge_dir.mkdir()
            excluded_file = knowledge_dir / "QuestionsReponsesSources.docx"
            included_file = knowledge_dir / "autre_source.docx"
            excluded_file.touch()
            included_file.touch()

            with (
                override_settings(BASE_DIR=Path(temporary_directory)),
                patch(
                    "chat.knowledge_base._read_docx",
                    return_value=(
                        KnowledgeChunk(
                            source="autre_source.docx",
                            text="Contenu inclus",
                        ),
                    ),
                ) as read_docx,
            ):
                chunks = list(_load_chunks(()))

        read_docx.assert_called_once_with(included_file)
        self.assertEqual(
            chunks,
            [
                KnowledgeChunk(
                    source="autre_source.docx",
                    text="Contenu inclus",
                )
            ],
        )

    @patch(
        "chat.views.retrieve_context",
        side_effect=KnowledgeBaseError("Le PDF est illisible."),
    )
    @patch("chat.views.load_api_key", return_value="test-key")
    def test_send_message_reports_knowledge_base_errors(
        self,
        load_api_key,
        retrieve_context,
    ):
        conversation = Conversation.objects.create()

        response = self.client.post(
            reverse("chat:send_message", args=[conversation.pk]),
            data='{"message": "Question sur le logement"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {"error": "Le PDF est illisible."},
        )
        self.assertFalse(conversation.messages.exists())


class DeleteConversationTests(TestCase):
    def test_index_displays_delete_button_for_active_conversation(self):
        conversation = Conversation.objects.create(title="Conversation active")

        response = self.client.get(reverse("chat:index"))

        self.assertContains(
            response,
            "Supprimer cette conversation de l’historique",
        )
        self.assertContains(
            response,
            reverse(
                "chat:delete_conversation",
                args=[conversation.pk],
            ),
        )

    def test_deletes_selected_conversation_and_redirects_to_another(self):
        remaining = Conversation.objects.create(title="À conserver")
        selected = Conversation.objects.create(title="À supprimer")
        message = Message.objects.create(
            conversation=selected,
            role="user",
            content="Un message à supprimer",
        )

        response = self.client.post(
            reverse(
                "chat:delete_conversation",
                args=[selected.pk],
            )
        )

        self.assertRedirects(
            response,
            f"{reverse('chat:index')}?conversation={remaining.pk}",
        )
        self.assertFalse(
            Conversation.objects.filter(pk=selected.pk).exists()
        )
        self.assertFalse(Message.objects.filter(pk=message.pk).exists())
        self.assertTrue(Conversation.objects.filter(pk=remaining.pk).exists())

    def test_creates_empty_conversation_when_deleting_the_last_one(self):
        selected = Conversation.objects.create(title="À supprimer")
        Message.objects.create(
            conversation=selected,
            role="assistant",
            content="Réponse à supprimer",
        )

        response = self.client.post(
            reverse(
                "chat:delete_conversation",
                args=[selected.pk],
            )
        )

        replacement = Conversation.objects.get()
        self.assertNotEqual(replacement.pk, selected.pk)
        self.assertEqual(replacement.messages.count(), 0)
        self.assertRedirects(
            response,
            f"{reverse('chat:index')}?conversation={replacement.pk}",
        )

    def test_get_does_not_delete_conversation(self):
        conversation = Conversation.objects.create(title="À conserver")

        response = self.client.get(
            reverse(
                "chat:delete_conversation",
                args=[conversation.pk],
            )
        )

        self.assertEqual(response.status_code, 405)
        self.assertTrue(
            Conversation.objects.filter(pk=conversation.pk).exists()
        )