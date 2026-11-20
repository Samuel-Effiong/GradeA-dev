from django.apps import AppConfig


class StudentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "students"

    def ready(self):
        import students.signals  # noqa: F401
        from AutoGrader import uploads

        uploads.warn_if_file_cap_above_request_cap()
