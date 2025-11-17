# app/auth_utils.py

from fastapi import Depends, HTTPException, status

from jose import jwt, JWTError
from sqlalchemy.orm import Session
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .database import get_db
from . import models

SECRET_KEY = "supersekretnyklucz"
ALGORITHM = "HS256"

security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    token = credentials.credentials

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")

        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")

        user = db.query(models.User).filter(models.User.id == user_id).first()

        if user is None:
            raise HTTPException(status_code=401, detail="User not found")

        if not user.is_active:
            raise HTTPException(status_code=403, detail="User inactive")

        return user

    except JWTError:
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token"
        )
