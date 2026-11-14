from __future__ import annotations
from functools import wraps
from flask import Blueprint, current_app, jsonify, request, session

bp = Blueprint("admin_v1", __name__)

def require_admin(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        user = session.get("papervault_user") or {}
        allowed = {item.strip().lower() for item in current_app.extensions["settings"].admin_users.split(",") if item.strip()}
        is_whitelisted = str(user.get("id", "")).lower() in allowed or str(user.get("email", "")).lower() in allowed or str(user.get("name", "")).lower() in allowed
        if not session.get("papervault_admin") and not is_whitelisted:
            return jsonify({"error": {"code": "UNAUTHORIZED", "message": "admin login required"}}), 401
        return fn(*args, **kwargs)
    return wrapped

@bp.get("/admin/config")
@require_admin
def config():
    s = current_app.extensions["settings"]
    return jsonify({"adminUsername": s.admin_username, "llm": {"provider": s.suggest_provider, "baseUrlConfigured": bool(s.llm_api_url or s.deepseek_base_url), "apiKeyConfigured": bool(s.llm_api_key)}, "oauth": {"github": bool(s.github_client_id and s.github_client_secret), "zhihu": bool(s.zhihu_client_id and s.zhihu_client_secret)}})
