# app/database.py
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

# Railway zwraca URL zaczynający się od "postgres://"
# SQLAlchemy wymaga "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Silnik bazy
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,     # automatycznie odświeża zerwane połączenia
    echo=False
)

# sesja DB
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# baza modeli
Base = declarative_base()

# dependency injection dla FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
