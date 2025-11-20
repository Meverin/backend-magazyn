# app/routers/auth.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from jose import jwt, JWTError
from datetime import datetime, timedelta
from pydantic import BaseModel, EmailStr

from ..database import get_db
from .. import models

router = APIRouter(prefix="/auth", tags=["auth"])

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

SECRET_KEY = "supersekretnyklucz"        # później przeniesiemy do .env
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60


# ======================================================
# Helpers
# ======================================================

def verify_domain(email: str) -> bool:
    return email.endswith("@promax.media.pl") or email.endswith("@promaxnet.pl")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict):
    """Tworzy token JWT"""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})

    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


# ======================================================
# Schemas
# ======================================================

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str
    car_plate: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    token: str
    role: str
    name: str
    user_id: int


# ======================================================
# Dependency – pobranie aktualnego usera
# ======================================================

def get_current_user(token: str, db: Session):
    user_id = decode_token(token)

    if not user_id:
        raise HTTPException(status_code=401, detail="Niepoprawny token")

    user = db.query(models.User).filter(models.User.id == int(user_id)).first()

    if not user:
        raise HTTPException(status_code=404, detail="Użytkownik nie istnieje")

    return user


# ======================================================
# REGISTER
# ======================================================

@router.post("/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):

    if not verify_domain(req.email):
        raise HTTPException(
            status_code=400,
            detail="Rejestracja możliwa tylko dla domen @promax.media.pl oraz @promaxnet.pl"
        )

    existing = db.query(models.User).filter(models.User.email == req.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Użytkownik o tym emailu już istnieje")

    user = models.User(
        email=req.email,
        password_hash=hash_password(req.password),
        name=req.name,
        car_plate=req.car_plate,
        role="user",
        is_active=False   # admin musi aktywować
    )

    db.add(user)
    db.commit()

    return {"message": "Rejestracja przebiegła pomyślnie. Konto oczekuje na aktywację przez administratora."}


# ======================================================
# LOGIN
# ======================================================

@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):

    user = db.query(models.User).filter(models.User.email == req.email).first()

    if not user:
        raise HTTPException(status_code=400, detail="Niepoprawny email lub hasło")

    if not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Niepoprawny email lub hasło")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Konto nieaktywne. Skontaktuj się z administratorem.")

    token = create_access_token({"sub": str(user.id)})

    return LoginResponse(
        token=token,
        role=user.role,
        name=user.name,
        user_id=user.id
    )


# ======================================================
# ACTIVATE USER
# ======================================================

@router.post("/activate/{user_id}")
def activate_user(user_id: int, db: Session = Depends(get_db)):

    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Użytkownik nie istnieje")

    user.is_active = True
    db.commit()

    return {"message": f"Konto użytkownika {user.email} zostało aktywowane"}
