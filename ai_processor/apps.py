from django.apps import AppConfig


class AiProcessorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ai_processor"

    def ready(self):
        from .services import OCRService

        OCRService()
