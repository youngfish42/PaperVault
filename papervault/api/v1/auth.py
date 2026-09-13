from __future__ import annotations

from functools import wraps
from urllib.parse import urlencode
from flask import Blueprint, current_app, jsonify, redirect, request, session
import secrets
import requests

bp = Blueprint("auth_v1", __name__)

def _settings(): return current_app.extensions["settings"]

@bp.get("/auth/config")
def auth_config():
    s = _settings()
    return jsonify({"github": bool(s.github_client_id and s.github_client_secret), "zhihu": bool(s.zhihu_client_id and s.zhihu_client_secret), "adminConfigured": bool(s.admin_password or s.admin_users)})

@bp.post("/auth/login")
def login():
    s = _settings(); data = request.get_json(silent=True) or {}
    if not s.admin_password or data.get("username") != s.admin_username or data.get("password") != s.admin_password:
        return jsonify({"error": {"code": "UNAUTHORIZED", "message": "invalid credentials"}}), 401
    session["papervault_admin"] = True
    return jsonify({"authenticated": True, "username": s.admin_username})

@bp.post("/auth/logout")
def logout():
    session.pop("papervault_admin", None); return jsonify({"authenticated": False})

@bp.get("/auth/me")
def me():
    is_admin = bool(session.get("papervault_admin"))
    user = session.get("papervault_user")
    return jsonify({"authenticated": is_admin or bool(user), "isAdmin": is_admin, "username": _settings().admin_username if is_admin else (user.get("name") if isinstance(user, dict) else None), "user": user})

@bp.get("/auth/oauth/<provider>")
def oauth_start(provider):
    s = _settings(); configs = {"github": (s.github_client_id, "https://github.com/login/oauth/authorize", s.github_redirect_uri), "zhihu": (s.zhihu_client_id, "https://www.zhihu.com/oauth/authorize", s.zhihu_redirect_uri)}
    if provider not in configs or not configs[provider][0]: return jsonify({"error": {"code": "OAUTH_UNAVAILABLE", "message": "provider is not configured"}}), 503
    client_id, endpoint, redirect_uri = configs[provider]; state = secrets.token_urlsafe(24); session["oauth_state"] = state
    return redirect(endpoint + "?" + urlencode({"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "state": state, "scope": "read:user user:email" if provider == "github" else ""}))

@bp.get("/auth/oauth/<provider>/callback")
def oauth_callback(provider):
    # Provider token exchange is intentionally delegated to the deployment's
    # identity gateway; this endpoint preserves a safe callback contract.
    if request.args.get("error"): return jsonify({"error": request.args["error"]}), 400
    if not request.args.get("code") or request.args.get("state") != session.pop("oauth_state", None):
        return jsonify({"error": {"code": "INVALID_OAUTH_STATE", "message": "OAuth verification failed"}}), 400
    # OAuth is a general user login. It must never grant administrator
    # privileges; admin access is reserved for the separately configured
    # administrator credentials.
    user = {"provider": provider, "name": provider.title() + " user"}
    if provider == "github":
        s = _settings()
        try:
            token_response = requests.post("https://github.com/login/oauth/access_token", data={"client_id": s.github_client_id, "client_secret": s.github_client_secret, "code": request.args["code"], "redirect_uri": s.github_redirect_uri}, headers={"Accept": "application/json"}, timeout=15)
            token_response.raise_for_status()
        except requests.RequestException:
            return jsonify({"error": {"code": "OAUTH_FAILED", "message": "GitHub token exchange failed"}}), 502
        access_token = token_response.json().get("access_token")
        if not access_token: return jsonify({"error": {"code": "OAUTH_FAILED", "message": "GitHub token exchange failed"}}), 400
        profile = requests.get("https://api.github.com/user", headers={"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"}, timeout=15).json()
        user = {"provider": provider, "id": str(profile.get("id", "")), "name": profile.get("login") or profile.get("name") or "GitHub user", "email": profile.get("email") or ""}
    session["papervault_user"] = user
    return redirect("/#/?login=success")
