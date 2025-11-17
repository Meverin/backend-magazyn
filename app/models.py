from sqlalchemy import Column, Integer, String, Boolean, Text
from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    name = Column(String, nullable=False)
    car_plate = Column(String, nullable=False)
    is_active = Column(Boolean, default=False)


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)            # nazwa
    index = Column(String, unique=True, index=True, nullable=False)  # indeks
    unit = Column(String, nullable=False)            # jednostka (szt, m, kg...)
    description = Column(Text, nullable=True)        # opis (opcjonalny)

class CarStock(Base):
    __tablename__ = "car_stock"

    id = Column(Integer, primary_key=True, index=True)
    car_plate = Column(String, nullable=False)         # numer auta
    product_id = Column(Integer, nullable=False)       # ID produktu
    quantity = Column(Integer, nullable=False, default=0)

from sqlalchemy import Column, Integer, String, Boolean, Text, Float, DateTime, func

class StockMovement(Base):
    __tablename__ = "stock_movements"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, server_default=func.now())
    user_id = Column(Integer, nullable=False)
    car_plate = Column(String, nullable=False)
    product_id = Column(Integer, nullable=False)
    quantity = Column(Float, nullable=False)  # dodatnie dla IN, ujemne dla OUT
    type = Column(String, nullable=False)  # 'IN' lub 'OUT'
    place = Column(String, nullable=True)  # tylko OUT
