from sqlalchemy import Column, Integer, String, Boolean, Text, Float, DateTime, func, ForeignKey
from sqlalchemy.orm import relationship
from .database import Base


# -----------------------------
# USER
# -----------------------------
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    name = Column(String, nullable=False)
    car_plate = Column(String, nullable=False)

    # rola: user / admin
    role = Column(String, nullable=False, default="user")

    is_active = Column(Boolean, default=False)

    # relacja do historii ruchów
    movements = relationship("StockMovement", back_populates="user")


# -----------------------------
# PRODUCT
# -----------------------------
class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)            
    category = Column(String, nullable=False)
    index = Column(String, unique=True, index=True, nullable=False)  
    unit = Column(String, nullable=False)            
    description = Column(Text, nullable=True)

    # relacja do ruchów
    movements = relationship("StockMovement", back_populates="product")


# -----------------------------
# STOCK IN CAR
# -----------------------------
class CarStock(Base):
    __tablename__ = "car_stock"

    id = Column(Integer, primary_key=True, index=True)
    car_plate = Column(String, nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Float, nullable=False, default=0)

    product = relationship("Product")


# -----------------------------
# STOCK MOVEMENTS LOG
# -----------------------------
class StockMovement(Base):
    __tablename__ = "stock_movements"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)  
    car_plate = Column(String, nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)

    quantity = Column(Float, nullable=False)  
    type = Column(String, nullable=False)    # IN / OUT / RESET
    place = Column(String, nullable=True)    

    # relacje
    user = relationship("User", back_populates="movements")
    product = relationship("Product", back_populates="movements")
