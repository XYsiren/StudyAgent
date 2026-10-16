from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.models import User
from app.security import token_subject

bearer = HTTPBearer(auto_error=False)


def get_db(request: Request):
    with request.app.state.database.session() as session:
        yield session


DB = Annotated[Session, Depends(get_db)]


def current_user(request: Request, db: DB,
                 credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    subject = token_subject(credentials.credentials, request.app.state.settings) if credentials else None
    user = db.get(User, subject) if subject else None
    if user is None:
        raise HTTPException(401, "请先登录，或重新登录以刷新已过期的凭证", headers={"WWW-Authenticate": "Bearer"})
    return user


CurrentUser = Annotated[User, Depends(current_user)]
