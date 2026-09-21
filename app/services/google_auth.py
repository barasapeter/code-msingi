"""Small server-side Google OpenID Connect login flow for administrators."""

import secrets
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.admin_user import AdminUser

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def normalized_email(email: str) -> str:
    return email.strip().casefold()


def is_admin_email(email: str, db: Session) -> bool:
    settings = get_settings()
    email = normalized_email(email)
    return email == settings.master_admin_email or db.scalar(
        select(AdminUser.id).where(AdminUser.email == email)
    ) is not None


def current_admin_email(request: Request, db: Session) -> str | None:
    email = request.session.get("admin_email")
    if not isinstance(email, str) or not is_admin_email(email, db):
        return None
    return normalized_email(email)


def require_admin(request: Request, db: Session) -> str:
    email = current_admin_email(request, db)
    if email is None:
        raise HTTPException(status_code=401, detail="Sign in with an authorised Google account.")
    return email


def google_redirect_uri(request: Request) -> str:
    return get_settings().google_oauth_redirect_url or str(
        request.url_for("google_auth_callback")
    )


def google_login_url(request: Request, next_url: str) -> str:
    settings = get_settings()
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        raise HTTPException(status_code=503, detail="Google administrator sign-in is not configured.")
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    request.session["google_oauth_state"] = state
    request.session["google_oauth_nonce"] = nonce
    request.session["google_oauth_next"] = next_url if next_url.startswith("/admin") else "/admin/books/new"
    return f"{GOOGLE_AUTHORIZE_URL}?{urlencode({'client_id': settings.google_oauth_client_id, 'redirect_uri': google_redirect_uri(request), 'response_type': 'code', 'scope': 'openid email', 'state': state, 'nonce': nonce, 'prompt': 'select_account'})}"


async def google_email_from_callback(request: Request) -> str:
    settings = get_settings()
    state = request.query_params.get("state", "")
    expected_state = request.session.pop("google_oauth_state", None)
    request.session.pop("google_oauth_nonce", None)
    if not expected_state or not secrets.compare_digest(state, expected_state):
        raise HTTPException(status_code=400, detail="Invalid Google sign-in state. Please try again.")
    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Google sign-in was cancelled or did not return an authorisation code.")
    async with httpx.AsyncClient(timeout=15) as client:
        token_response = await client.post(
            GOOGLE_TOKEN_URL,
            data={"code": code, "client_id": settings.google_oauth_client_id, "client_secret": settings.google_oauth_client_secret, "redirect_uri": google_redirect_uri(request), "grant_type": "authorization_code"},
        )
        if token_response.is_error:
            raise HTTPException(status_code=502, detail="Google could not complete the sign-in request.")
        access_token = token_response.json().get("access_token")
        if not isinstance(access_token, str):
            raise HTTPException(status_code=502, detail="Google did not return an access token.")
        profile_response = await client.get(
            GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
    if profile_response.is_error:
        raise HTTPException(status_code=502, detail="Google could not verify the account email.")
    profile = profile_response.json()
    email = profile.get("email")
    if not isinstance(email, str) or profile.get("email_verified") is not True:
        raise HTTPException(status_code=403, detail="A verified Google email address is required.")
    return normalized_email(email)
