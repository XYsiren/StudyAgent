"""Explicit platform administration and database-backed runtime quotas."""
from fastapi import HTTPException

from app.models import OperationalPolicy

LIMITS = ("user_storage_mb", "max_pending_jobs", "max_active_chats", "global_active_chats", "user_daily_model_requests")


def is_platform_admin(user, settings):
    return user.email.lower() in {email.strip().lower() for email in settings.platform_admin_emails}


def require_platform_admin(user, settings):
    if not is_platform_admin(user, settings):
        raise HTTPException(403, "需要平台管理员权限")


def effective_settings(db, settings, user_id=None):
    values = {}
    for scope in ["global"] + (["user:" + user_id] if user_id else []):
        policy = db.get(OperationalPolicy, scope)
        if policy:
            values.update({key: value for key, value in policy.limits.items() if key in LIMITS
                           and (scope == "global" or key != "global_active_chats")})
    return settings.model_copy(update=values)
