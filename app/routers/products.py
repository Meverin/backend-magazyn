# app/routers/products.py

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from ..database import get_db
from .. import models

router = APIRouter(
    prefix="/products",
    tags=["products"]
)


# ---------- Schemy (Pydantic) ----------

class ProductCreate(BaseModel):
    name: str
    index: str
    unit: str
    description: Optional[str] = None


class ProductResponse(BaseModel):
    id: int
    name: str
    index: str
    unit: str
    description: Optional[str] = None

    class Config:
        orm_mode = True


# ---------- Endpointy ----------

@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(
    product: ProductCreate,
    db: Session = Depends(get_db),
):
    # sprawdzamy czy indeks jest unikalny
    existing = (
        db.query(models.Product)
        .filter(models.Product.index == product.index)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Produkt o podanym indeksie już istnieje.",
        )

    new_product = models.Product(
        name=product.name,
        index=product.index,
        unit=product.unit,
        description=product.description,
    )

    db.add(new_product)
    db.commit()
    db.refresh(new_product)

    return new_product


@router.get("", response_model=List[ProductResponse])
def list_products(
    db: Session = Depends(get_db),
):
    products = db.query(models.Product).order_by(models.Product.name).all()
    return products
