from django.contrib import admin

from .models import ChatMessage, ChatSession


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "course", "created_at", "updated_at")
    list_filter = ("created_at", "updated_at", "course")
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("course",)


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("session", "role", "timestamp")
    list_filter = ("role", "timestamp")
    search_fields = ("content",)
    readonly_fields = ("id", "timestamp")
    raw_id_fields = ("session",)
