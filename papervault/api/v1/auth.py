from __future__ import annotations

from urllib.parse import urlencode
from flask import Blueprint, current_app, jsonify, redirect, request, session
import secrets
import requests

from ...errors import ApiError
from ...services import zhihu_oauth

bp = Blueprint("auth_v1", __name__)

def _settings(): return current_app.extensions["settings"]

def _oauth_unavailable():
    return jsonify({"error": {"code": "OAUTH_UNAVAILABLE", "message": "provider is not configured"}}), 503

def _invalid_state():
    return jsonify({"error": {"code": "INVALID_OAUTH_STATE", "message": "OAuth verification failed"}}), 400

def _zhihu_redirect_uri(s):
    return s.zhihu_redirect_uri or f"{request.host_url.rstrip('/')}/api/v1/auth/oauth/zhihu/callback"

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
    s = _settings()
    state = secrets.token_urlsafe(24)
    if provider == "github":
        if not (s.github_client_id and s.github_client_secret): return _oauth_unavailable()
        session["oauth_state"] = state
        query = {"client_id": s.github_client_id, "redirect_uri": s.github_redirect_uri, "response_type": "code", "state": state, "scope": "read:user user:email"}
        return redirect("https://github.com/login/oauth/authorize?" + urlencode(query))
    if provider == "zhihu":
        if not (s.zhihu_client_id and s.zhihu_client_secret): return _oauth_unavailable()
        session["oauth_state"] = state
        return redirect(zhihu_oauth.build_authorization_url(s, state=state, redirect_uri=_zhihu_redirect_uri(s)))
    return _oauth_unavailable()

@bp.get("/auth/oauth/<provider>/callback")
def oauth_callback(provider):
    # OAuth is a general user login. It must never grant administrator
    # privileges; admin access is reserved for the separately configured
    # administrator credentials.
    if request.args.get("error"): return jsonify({"error": request.args["error"]}), 400
    code = request.args.get("code")
    expected_state = session.pop("oauth_state", None)
    # Zhihu's authorize endpoint does not echo `state` back on the callback;
    # when the query param is absent the server-side state stands in, but a
    # returned value must always match what we issued.
    if request.args.get("state") is not None:
        if request.args["state"] != expected_state: return _invalid_state()
    elif provider != "zhihu" or not expected_state:
        return _invalid_state()
    if not code: return _invalid_state()

    if provider == "github":
        user = _github_callback_user(code)
    elif provider == "zhihu":
        user = _zhihu_callback_user(code)
    else:
        return _oauth_unavailable()
    session["papervault_user"] = user
    return redirect("/#/?login=success")

def _github_callback_user(code: str) -> dict:
    s = _settings()
    try:
        token_response = requests.post("https://github.com/login/oauth/access_token", data={"client_id": s.github_client_id, "client_secret": s.github_client_secret, "code": code, "redirect_uri": s.github_redirect_uri}, headers={"Accept": "application/json"}, timeout=15)
        token_response.raise_for_status()
    except requests.RequestException:
        raise ApiError("GitHub token exchange failed", status_code=502, code="OAUTH_FAILED")
    access_token = token_response.json().get("access_token")
    if not access_token:
        raise ApiError("GitHub token exchange failed", status_code=400, code="OAUTH_FAILED")
    profile = requests.get("https://api.github.com/user", headers={"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"}, timeout=15).json()
    return {"provider": "github", "id": str(profile.get("id", "")), "name": profile.get("login") or profile.get("name") or "GitHub user", "email": profile.get("email") or ""}

def _zhihu_callback_user(code: str) -> dict:
    s = _settings()
    try:
        exchanged = zhihu_oauth.exchange_code(s, code, redirect_uri=_zhihu_redirect_uri(s))
    except zhihu_oauth.ZhihuOAuthError as exc:
        current_app.logger.warning("zhihu oauth exchange failed: %s", exc)
        raise ApiError("Zhihu token exchange failed", status_code=502, code="OAUTH_FAILED")
    p = exchanged.profile
    return {"provider": "zhihu", "id": p.open_id, "name": p.name, "account": p.account, "headline": p.headline, "avatar_url": p.avatar_url}
