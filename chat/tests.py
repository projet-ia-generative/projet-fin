import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
import pymupdf

from .knowledge_base import (
    KnowledgeBaseError,
    KnowledgeChunk,
    _load_chunks,
    _select_pdf_paths,
    _tokens,
    retrieve_context,
)
from .models import Conversation, Message


class KnowledgeContextTests(TestCase):
    @patch("chat.views.retrieve_context", return_value="[Source : guide.pdf]")
    @patch("chat.views.requests.post")
    def test_send_message_includes_retrieved_context_in_system_prompt(
        self, post, retrieve_context
    ):
        conversation = Conversation.objects.create()
        post.return_value = Mock(
            ok=True,
            json=lambda: {
                "choices": [{"message": {"content": "Réponse fondée."}}]
            },
        )

        response = self.client.post(
            reverse("chat:send_message", args=[conversation.pk]),
            data='{"message": "Quels sont les constats ?"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Sources consultées :", response.json()["answer"])
        self.assertIn("[Source : guide.pdf]", response.json()["answer"])
        retrieve_context.assert_called_once_with("Quels sont les constats ?")
        sent_messages = post.call_args.kwargs["json"]["messages"]
        self.assertEqual(sent_messages[0]["role"], "system")
        self.assertIn(
            "[Source : guide.pdf]",
            sent_messages[0]["content"],
        )
        self.assertIn(
            "Sources prioritaires — base de connaissances locale",
            sent_messages[0]["content"],
        )
        self.assertIn("75 %", sent_messages[0]["content"])
        self.assertIn("25 %", sent_messages[0]["content"])
        self.assertIn("n'ajoute aucun fait", sent_messages[0]["content"])
        self.assertIn(
            "[Source : nom_du_fichier.pdf, page N]",
            sent_messages[0]["content"],
        )
        self.assertIn(
            "références bibliographiques, pas des numéros de page",
            sent_messages[0]["content"],
        )
        self.assertEqual(sent_messages[1]["content"], "Quels sont les constats ?")
        self.assertEqual(
            post.call_args.args[0],
            "http://127.0.0.1:1234/v1/chat/completions",
        )
        self.assertEqual(
            post.call_args.kwargs["json"]["model"],
            "liquid/lfm2-1.2b",
        )
        self.assertNotIn("Authorization", post.call_args.kwargs["headers"])

    @patch("chat.views.retrieve_context", return_value="")
    @patch("chat.views.requests.post")
    def test_uses_general_knowledge_when_no_local_context_is_found(
        self, post, retrieve_context
    ):
        conversation = Conversation.objects.create()
        post.return_value = Mock(
            ok=True,
            json=lambda: {
                "choices": [{"message": {"content": "Réponse générale."}}]
            },
        )

        response = self.client.post(
            reverse("chat:send_message", args=[conversation.pk]),
            data='{"message": "Question sans source locale"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "Réponse générale.")
        system_prompt = post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertIn("connaissances générales", system_prompt)
        self.assertNotIn("Web", system_prompt)
        self.assertNotIn("75 %", system_prompt)
        retrieve_context.assert_called_once()

    @patch("chat.views.retrieve_context")
    @patch("chat.views.requests.post")
    def test_retrieval_query_includes_recent_conversation_context(
        self, post, retrieve_context
    ):
        conversation = Conversation.objects.create()
        Message.objects.create(
            conversation=conversation,
            role="user",
            content="Que sait-on du logement social ?",
        )
        Message.objects.create(
            conversation=conversation,
            role="assistant",
            content="Une réponse qui parle d'automobile et de transport.",
        )
        post.return_value = Mock(
            ok=True,
            json=lambda: {"choices": [{"message": {"content": "Réponse."}}]},
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

    def test_pdf_selection_omits_titles_with_only_weak_overlap(self):
        target = Path("INSPQ_2021_LogementCOVIDStrategies.pdf")
        weak_match = Path("Logement_Mesure.pdf")

        selected = _select_pdf_paths(
            "INSPQ 2021 logement COVID strategies aide menages",
            (target, weak_match),
        )

        self.assertEqual(selected, (target,))

    def test_pdf_selection_returns_empty_when_no_title_matches(self):
        pdfs = (
            Path("Rapport_Logement.pdf"),
            Path("Rapport_Transport.pdf"),
        )

        self.assertEqual(_select_pdf_paths("bonjour", pdfs), ())

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

    def test_pdf_running_headers_do_not_rank_above_relevant_body_text(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            knowledge_dir = Path(temporary_directory) / "Base_de_connaissance"
            knowledge_dir.mkdir()
            pdf_path = knowledge_dir / "Logement_Sante_Strategies.pdf"
            document = pymupdf.open()
            bodies = (
                "Introduction sans détails. " * 12,
                "Table des matières - logement santé stratégies rénovation.",
                "Mesures de rénovation pour améliorer le logement et la santé.",
                "Références\nLogement santé stratégies rénovation.",
                "Logement santé stratégies rénovation, référence suivante.",
            )
            for body in bodies:
                page = document.new_page()
                page.insert_text((40, 30), "Logement santé stratégies")
                page.insert_text((40, 100), body)
                page.insert_text((40, 770), "Rapport - page")
            document.save(pdf_path)
            document.close()

            with override_settings(BASE_DIR=Path(temporary_directory)):
                context = retrieve_context("logement santé stratégies rénovation")

        self.assertIn("Logement_Sante_Strategies.pdf, page 3", context)
        self.assertNotIn("Logement_Sante_Strategies.pdf, page 1", context)
        self.assertNotIn("Logement_Sante_Strategies.pdf, page 2", context)
        self.assertNotIn("Logement_Sante_Strategies.pdf, page 4", context)
        self.assertNotIn("Logement_Sante_Strategies.pdf, page 5", context)
        self.assertIn("Mesures de rénovation", context)

    def test_pdf_context_does_not_repeat_overlapping_chunks_from_one_page(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            knowledge_dir = Path(temporary_directory) / "Base_de_connaissance"
            knowledge_dir.mkdir()
            pdf_path = knowledge_dir / "Logement_Sante.pdf"
            document = pymupdf.open()
            page = document.new_page()
            page.insert_textbox(
                pymupdf.Rect(40, 100, 570, 700),
                " ".join(["logement santé rénovation"] * 140),
            )
            page = document.new_page()
            page.insert_text(
                (40, 100),
                "La rénovation durable soutient le logement et améliore la santé.",
            )
            document.save(pdf_path)
            document.close()

            with override_settings(BASE_DIR=Path(temporary_directory)):
                context = retrieve_context("logement santé rénovation durable")

        self.assertEqual(context.count("Logement_Sante.pdf, page 1"), 1)
        self.assertIn("Logement_Sante.pdf, page 2", context)

    def test_recommendation_query_includes_pdf_strategy_tables(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            knowledge_dir = Path(temporary_directory) / "Base_de_connaissance"
            knowledge_dir.mkdir()
            pdf_path = knowledge_dir / "Logement_Sante.pdf"
            document = pymupdf.open()
            summary_page = document.new_page()
            summary_page.insert_textbox(
                pymupdf.Rect(40, 100, 570, 700),
                " ".join(
                    ["logement santé mesures propose rapport COVID"] * 45
                ),
            )
            table_page = document.new_page()
            table_page.insert_textbox(
                pymupdf.Rect(40, 100, 570, 700),
                "Moyens proposés Stratégies Alléger les coûts du logement "
                "et améliorer la sécurité des logements.",
            )
            document.save(pdf_path)
            document.close()

            with override_settings(BASE_DIR=Path(temporary_directory)):
                context = retrieve_context(
                    "Selon le rapport sur le logement et la santé, "
                    "quelles mesures propose-t-il?"
                )

        self.assertIn("Logement_Sante.pdf, page 2", context)
        self.assertIn("Moyens proposés", context)

    @patch(
        "chat.views.retrieve_context",
        side_effect=KnowledgeBaseError("Le PDF est illisible."),
    )
    def test_send_message_reports_knowledge_base_errors(self, retrieve_context):
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

    @patch(
        "chat.views.retrieve_context",
        side_effect=RuntimeError("Erreur imprévue"),
    )
    def test_send_message_handles_unexpected_errors(self, retrieve_context):
        conversation = Conversation.objects.create()

        response = self.client.post(
            reverse("chat:send_message", args=[conversation.pk]),
            data='{"message": "Bonjour"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {"error": "Erreur interne du serveur."},
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