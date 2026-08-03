"""
users/mailerlite_service.py
============================
Syncs activated users to MailerLite, segmented into one group per
user_type (Teacher / Student / School Admin) with custom fields for
further targeting.

Never raises - a MailerLite outage or missing API key must not break
signup/activation. Callers should invoke this via the
`sync_user_to_mailerlite` Celery task (users/tasks.py) rather than
inline, so the HTTP call never blocks the request/response cycle.
"""

import logging

import requests
from django.conf import settings

from users.models import RegistrationMethod, UserTypes

logger = logging.getLogger(__name__)

MAILERLITE_API_URL = "https://connect.mailerlite.com/api/subscribers"
REQUEST_TIMEOUT_SECONDS = 10

GROUP_ID_BY_USER_TYPE = {
    UserTypes.TEACHER: "MAILERLITE_GROUP_ID_TEACHER",
    UserTypes.STUDENT: "MAILERLITE_GROUP_ID_STUDENT",
    UserTypes.SCHOOL_ADMIN: "MAILERLITE_GROUP_ID_SCHOOL_ADMIN",
}


class MailerLiteService:
    @staticmethod
    def _group_id_for(user_type):
        setting_name = GROUP_ID_BY_USER_TYPE.get(user_type)
        if not setting_name:
            return None
        return getattr(settings, setting_name, "") or None

    @staticmethod
    def _build_payload(user):
        subscription = user.get_active_subscription()
        plan_name = getattr(getattr(subscription, "plan", None), "name", None)

        fields = {
            "name": user.first_name or "",
            "last_name": user.last_name or "",
            "user_type": user.user_type,
            "registration_method": user.registration_method or RegistrationMethod.EMAIL,
            "school": user.school.name if user.school_id else "",
            "subscription_type": user.subscription_type or "",
            "plan": plan_name or "",
        }

        payload = {"email": user.email, "fields": fields}

        group_id = MailerLiteService._group_id_for(user.user_type)
        if group_id:
            payload["groups"] = [group_id]

        return payload

    @staticmethod
    def sync_user(user):
        """
        Upserts `user` as a MailerLite subscriber, tagged with a group
        matching their user_type.

        Returns True on success, False on a request failure (safe to
        retry), and None if sync was skipped outright (no API key
        configured - not worth retrying).
        """
        api_key = getattr(settings, "MAILERLITE_API_KEY", "")
        if not api_key:
            logger.info(
                "MailerLite sync skipped for %s: MAILERLITE_API_KEY not set",
                user.email,
            )
            return None

        payload = MailerLiteService._build_payload(user)

        try:
            response = requests.post(
                MAILERLITE_API_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return True
        except requests.RequestException:
            logger.error("MailerLite sync failed for %s", user.email, exc_info=True)
            return False
