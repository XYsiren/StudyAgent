"""OIDC authorization-code + PKCE. Issuer endpoints come only from admin configuration."""
import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select

from app.api.dependencies import DB
from app.models import OIDCFlow, OIDCIdentity, User, new_id, utcnow
from app.services.admission import consume

router = APIRouter(prefix="/api/auth/oidc")


def discovery(settings):
    if not settings.oidc_issuer:
        raise HTTPException(503, "企业单点登录尚未配置")
    try:
        response = httpx.get(settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=10,
                             follow_redirects=False)
        response.raise_for_status()
        metadata = response.json()
        if metadata["issuer"].rstrip("/") != settings.oidc_issuer.rstrip("/"):
            raise ValueError("issuer mismatch")
        for key in ["authorization_endpoint", "token_endpoint", "jwks_uri"]:
            if not metadata[key].startswith("https://"):
                raise ValueError("HTTPS required")
        return metadata
    except Exception as exc:
        raise HTTPException(503, "无法读取企业身份服务配置") from exc


@router.get("/config")
def oidc_config(request: Request):
    return {"enabled": bool(request.app.state.settings.oidc_issuer)}


@router.get("/start")
def start(request: Request, db: DB):
    settings = request.app.state.settings
    consume(request.app.state.database, f"oidc:{request.client.host}", settings.auth_rate_per_minute)
    metadata = discovery(settings)
    state, verifier, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(64), secrets.token_urlsafe(32)
    db.execute(delete(OIDCFlow).where(OIDCFlow.expires_at < utcnow()))
    db.add(OIDCFlow(state=state, verifier=verifier, nonce=nonce, expires_at=utcnow() + timedelta(minutes=5)))
    db.commit()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    response = RedirectResponse(metadata["authorization_endpoint"] + "?" + urlencode({
        "client_id": settings.oidc_client_id, "redirect_uri": settings.oidc_redirect_uri,
        "response_type": "code", "scope": "openid email profile", "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256"}))
    response.set_cookie("rag_oidc_state", state, httponly=True, samesite="lax", path="/api/auth/oidc",
        max_age=300, secure=settings.cookie_secure or settings.public_base_url.startswith("https://"))
    return response


@router.get("/callback")
def callback(request: Request, db: DB, state: str = "", code: str = ""):
    settings = request.app.state.settings
    consume(request.app.state.database, f"oidc:{request.client.host}", settings.auth_rate_per_minute)
    flow = db.get(OIDCFlow, state) if len(state) <= 64 else None
    if not flow or flow.expires_at <= utcnow() or not secrets.compare_digest(request.cookies.get("rag_oidc_state", ""), state):
        raise HTTPException(400, "登录状态无效或已过期")
    verifier, nonce = flow.verifier, flow.nonce
    # Consume state atomically before exchanging the code, preventing replay across replicas.
    changed = db.execute(delete(OIDCFlow).where(OIDCFlow.state == state))
    db.commit()
    if not changed.rowcount:
        raise HTTPException(400, "登录状态已被使用")
    metadata = discovery(settings)
    try:
        tokens = httpx.post(metadata["token_endpoint"], data={"grant_type": "authorization_code", "code": code,
            "redirect_uri": settings.oidc_redirect_uri, "client_id": settings.oidc_client_id,
            "client_secret": settings.oidc_client_secret, "code_verifier": verifier}, timeout=15,
            follow_redirects=False)
        tokens.raise_for_status()
        encoded = tokens.json()["id_token"]
        key = jwt.PyJWKClient(metadata["jwks_uri"], timeout=10).get_signing_key_from_jwt(encoded)
        claims = jwt.decode(encoded, key.key, algorithms=["RS256", "ES256"],
            audience=settings.oidc_client_id, issuer=metadata["issuer"],
            options={"require": ["exp", "iat", "sub", "iss", "aud", "nonce"]})
        if not secrets.compare_digest(str(claims["nonce"]), nonce):
            raise ValueError("nonce mismatch")
        if isinstance(claims["aud"], list) and len(claims["aud"]) > 1 and claims.get("azp") != settings.oidc_client_id:
            raise ValueError("azp mismatch")
        if not claims.get("email") or claims.get("email_verified") is not True:
            raise ValueError("verified email required")
        if len(claims["sub"]) > 255 or len(claims["email"]) > 254:
            raise ValueError("identity too long")
    except Exception as exc:
        raise HTTPException(401, "企业身份验证失败") from exc
    identity = db.get(OIDCIdentity, (metadata["issuer"], claims["sub"]))
    if identity:
        user = db.get(User, identity.user_id)
    else:
        # Never silently link an existing password account by email alone.
        if db.scalar(select(User.id).where(User.email == claims["email"].lower())):
            raise HTTPException(409, "同邮箱账户已存在，请使用原登录方式；身份绑定需管理员核验")
        from app.security import hash_password
        user = User(id=new_id(), email=claims["email"].lower(), name=str(claims.get("name", claims["email"]))[:100],
                    password_hash=hash_password(secrets.token_urlsafe(48)))
        db.add(user)
        db.flush()
        db.add(OIDCIdentity(issuer=metadata["issuer"], subject=claims["sub"], user_id=user.id))
    if user.disabled:
        raise HTTPException(401, "账户已停用")
    from app.api.routes import auth_payload
    response = RedirectResponse(settings.public_base_url, status_code=303)
    auth_payload(user, request, db, response)
    response.delete_cookie("rag_oidc_state", path="/api/auth/oidc")
    return response
