from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.models import AuthSession, User, utcnow
from app.security import token_claims

bearer = HTTPBearer(auto_error=False)


def get_db(request: Request):
    with request.app.state.database.session() as session:
        yield session


DB = Annotated[Session, Depends(get_db)]


def current_user(request: Request, db: DB,
                 credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    token = credentials.credentials if credentials else request.cookies.get("rag_session")
    claims = token_claims(token, request.app.state.settings) if token else None
    subject = claims["sub"] if claims else None
    user = db.get(User, subject) if subject else None
    session = db.get(AuthSession, claims.get("nonce", "")) if claims else None
    if user is None or user.disabled or not session or session.user_id != subject or session.revoked or session.expires_at <= utcnow():
        raise HTTPException(401, "请先登录，或重新登录以刷新已过期的凭证", headers={"WWW-Authenticate": "Bearer"})
    request.state.session_id = session.id
    return user


CurrentUser = Annotated[User, Depends(current_user)]
