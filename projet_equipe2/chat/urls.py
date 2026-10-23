
from django.urls import path
from . import views

app_name = "chat"

urlpatterns = [
    path("", views.index, name="index"),
    path(
        "nouvelle-conversation/",
        views.new_conversation,
        name="new_conversation",
    ),
    path(
        "conversation/<int:conversation_id>/supprimer/",
        views.delete_conversation,
        name="delete_conversation",
    ),
    path(
        "api/chat/<int:conversation_id>/",
        views.send_message,
        name="send_message",
    ),
]
