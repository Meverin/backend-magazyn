from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from jose import jwt
from datetime import datetime, timedelta

from ..database import get_db
from .. import models
from pydantic import BaseModel, EmailStr

router = APIRouter(prefix="/auth", tags=["auth"])

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

SECRET_KEY = "supersekretnyklucz"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60


# -----------------------------
# Schemas
# -----------------------------

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


# -----------------------------
# Helpers
# -----------------------------

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# -----------------------------
# REGISTER
# -----------------------------

@router.post("/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):

    # check domain
    if not (req.email.endswith("@promax.media.pl") or req.email.endswith("@promaxnet.pl")):
        raise HTTPException(
            status_code=400,
            detail="Rejestracja możliwa tylko dla domen @promax.media.pl oraz @promaxnet.pl"
        )

    # check existing
    existing = db.query(models.User).filter(models.User.email == req.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Użytkownik o tym emailu już istnieje")

    user = models.User(
        email=req.email,
        password_hash=hash_password(req.password),
        name=req.name,
        car_plate=req.car_plate,
        is_active=False,
        role="user"  # <-- domyślna rola
    )

    db.add(user)
    db.commit()

    return {"message": "Rejestracja przebiegła pomyślnie. Konto oczekuje na aktywację."}


# -----------------------------
# LOGIN
# -----------------------------

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
        name=user.name
    )

