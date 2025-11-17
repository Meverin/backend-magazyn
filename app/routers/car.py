# app/routers/car.py

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth_utils import get_current_user
from .. import models

from pydantic import BaseModel


router = APIRouter(
    prefix="/car",
    tags=["car"]
)


# ---------- Schemy ----------
class CarStockItem(BaseModel):
    product_id: int
    name: str
    index: str
    unit: str
    quantity: float

    class Config:
        orm_mode = True


# ---------- Endpointy ----------

@router.get("/stock", response_model=List[CarStockItem])
def get_car_stock(
    db: Session = Depends(get_db),
    user = Depends(get_current_user),
):
    """
    Zwraca aktualny stan magazynowy samochodu zalogowanego użytkownika.
    """

    # pobieramy rekordy dla auta użytkownika
    items = (
        db.query(models.CarStock, models.Product)
        .join(models.Product, models.Product.id == models.CarStock.product_id)
        .filter(models.CarStock.car_plate == user.car_plate)
        .all()
    )

    # mapujemy wynik
    result = []

    for stock_item, product in items:
        result.append(CarStockItem(
            product_id=product.id,
            name=product.name,
            index=product.index,
            unit=product.unit,
            quantity=stock_item.quantity
        ))

    return result
