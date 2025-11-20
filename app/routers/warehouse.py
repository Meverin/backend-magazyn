# app/routers/warehouse.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from datetime import date, datetime, time, timedelta
from typing import Literal
import io

from fastapi.responses import StreamingResponse
from sqlalchemy import func

from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from ..database import get_db
from ..auth_utils import get_current_user
from .. import models

router = APIRouter(prefix="/warehouse", tags=["warehouse"])

# =====================================================
# Schemy
# =====================================================

class ProductDto(BaseModel):
    id: int
    name: str
    category: str
    index: str
    unit: str

    class Config:
        orm_mode = True


class UpdateCarStateItemDto(BaseModel):
    product_id: int
    quantity: float


class UpdateCarStateRequestDto(BaseModel):
    items: list[UpdateCarStateItemDto]


# =====================================================
# Dane produktów (dla listy we wprowadzaniu stanu)
# =====================================================

@router.get("/products", response_model=list[ProductDto])
def get_products(db: Session = Depends(get_db), user=Depends(get_current_user)):
    products = db.query(models.Product).order_by(models.Product.name).all()
    return products


# =====================================================
# WPROWADZENIE STANU — GŁÓWNY ENDPOINT
# =====================================================

@router.post("/update-car-state")
def update_car_state(
    req: UpdateCarStateRequestDto,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    """
    1. Czyści cały stan samochodu
    2. Wpisuje nowe ilości
    3. Zapisuje historię zmian
    """

    car_plate = user.car_plate

    # ---------------------------
    # 1. USUNIĘCIE obecnego stanu
    # ---------------------------
    db.query(models.CarStock).filter(
        models.CarStock.car_plate == car_plate
    ).delete()

    db.commit()

    # ---------------------------
    # 2. WPROWADZENIE NOWEGO STANU
    # ---------------------------
    for item in req.items:
        if item.quantity <= 0:
            continue

        # Sprawdzenie czy produkt istnieje
        product = db.query(models.Product).filter(models.Product.id == item.product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail=f"Produkt ID={item.product_id} nie istnieje")

        new_row = models.CarStock(
            car_plate=car_plate,
            product_id=item.product_id,
            quantity=item.quantity
        )
        db.add(new_row)

        # Historia
        log = models.StockMovement(
            user_id=user.id,
            car_plate=car_plate,
            product_id=item.product_id,
            quantity=item.quantity,     # traktujemy wprowadzony stan jako IN
            type="IN",
            place=None
        )
        db.add(log)

    db.commit()

    return {"status": "OK", "message": "Stan samochodu zaktualizowany"}


# =====================================================
# Pozostałe endpointy (PRZYJĘCIE / ROZCHÓD / HISTORIA)
# =====================================================

class ReceiveRequest(BaseModel):
    product_id: int
    quantity: float = Field(gt=0)

class IssueRequest(BaseModel):
    product_id: int
    quantity: float = Field(gt=0)
    place: str

class MovementItem(BaseModel):
    id: int
    timestamp: str
    product_id: int
    product_name: str
    index: str
    quantity: float
    type: str
    place: str | None

    class Config:
        orm_mode = True


# ==== receive_goods =============
@router.post("/receive")
def receive_goods(
    req: ReceiveRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    product = db.query(models.Product).filter(models.Product.id == req.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Produkt nie istnieje")

    stock = db.query(models.CarStock).filter(
        models.CarStock.car_plate == user.car_plate,
        models.CarStock.product_id == req.product_id
    ).first()

    if not stock:
        stock = models.CarStock(
            car_plate=user.car_plate,
            product_id=req.product_id,
            quantity=req.quantity
        )
        db.add(stock)
    else:
        stock.quantity += req.quantity

    movement = models.StockMovement(
        user_id=user.id,
        car_plate=user.car_plate,
        product_id=req.product_id,
        quantity=req.quantity,
        type="IN"
    )
    db.add(movement)

    db.commit()
    return {"status": "OK", "message": "Przyjęto towar", "quantity": stock.quantity}


# ==== issue_goods =============
@router.post("/issue")
def issue_goods(
    req: IssueRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    product = db.query(models.Product).filter(models.Product.id == req.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Produkt nie istnieje")

    stock = db.query(models.CarStock).filter(
        models.CarStock.car_plate == user.car_plate,
        models.CarStock.product_id == req.product_id
    ).first()

    if not stock:
        raise HTTPException(status_code=400, detail="Brak produktu w aucie")

    if stock.quantity < req.quantity:
        raise HTTPException(status_code=400, detail=f"Dostępne tylko: {stock.quantity}")

    stock.quantity -= req.quantity

    movement = models.StockMovement(
        user_id=user.id,
        car_plate=user.car_plate,
        product_id=req.product_id,
        quantity=-req.quantity,
        type="OUT",
        place=req.place
    )
    db.add(movement)

    db.commit()
    return {"status": "OK", "message": "Rozchodowano towar", "quantity": stock.quantity}


# ==== historia =================
@router.get("/history", response_model=list[MovementItem])
def history(
    product_id: int | None = None,
    type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):

    q = (
        db.query(models.StockMovement, models.Product)
        .join(models.Product, models.Product.id == models.StockMovement.product_id)
        .filter(models.StockMovement.car_plate == user.car_plate)
    )

    if product_id:
        q = q.filter(models.StockMovement.product_id == product_id)

    if type:
        q = q.filter(models.StockMovement.type == type)

    if date_from:
        q = q.filter(models.StockMovement.timestamp >= date_from)

    if date_to:
        q = q.filter(models.StockMovement.timestamp <= date_to)

    q = q.order_by(models.StockMovement.timestamp.desc())

    rows = q.all()
    result = []

    for movement, product in rows:
        result.append(MovementItem(
            id=movement.id,
            timestamp=str(movement.timestamp),
            product_id=product.id,
            product_name=product.name,
            index=product.index,
            quantity=movement.quantity,
            type=movement.type,
            place=movement.place
        ))

    return result
# -----------------------------
# UPDATE CAR STATE (FULL RESET)
# -----------------------------
class UpdateCarStateItemDto(BaseModel):
    product_id: int
    quantity: float = Field(ge=0)

class UpdateCarStateRequestDto(BaseModel):
    items: list[UpdateCarStateItemDto]


@router.post("/update-car-state")
def update_car_state(
    req: UpdateCarStateRequestDto,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    """
    Nadpisuje cały stan magazynowy samochodu:
    1) czyści car_stock dla danego auta
    2) wpisuje nowe rekordy
    3) tworzy logi w stock_movements
    """

    # 1. Usuń aktualny stan
    db.query(models.CarStock).filter(
        models.CarStock.car_plate == user.car_plate
    ).delete()

    # 2. Dodaj nowe pozycje
    for item in req.items:
        stock = models.CarStock(
            car_plate=user.car_plate,
            product_id=item.product_id,
            quantity=item.quantity
        )
        db.add(stock)

        # zapis do logów (IN)
        movement = models.StockMovement(
            user_id=user.id,
            car_plate=user.car_plate,
            product_id=item.product_id,
            quantity=item.quantity,
            type="IN",
            place=None
        )
        db.add(movement)

    db.commit()

    return {"status": "OK", "message": "Stan samochodu został zaktualizowany"}

class CarStockItem(BaseModel):
    product_id: int
    name: str
    category: str | None
    unit: str
    quantity: float

    class Config:
        orm_mode = True


@router.get("/car/state", response_model=list[CarStockItem])
def get_car_state(
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    rows = (
        db.query(
            models.CarStock.product_id,
            models.Product.name,
            models.Product.category,
            models.Product.unit,
            models.CarStock.quantity
        )
        .join(models.Product, models.Product.id == models.CarStock.product_id)
        .filter(models.CarStock.car_plate == user.car_plate)
        .order_by(models.Product.id)
        .all()
    )

    return [
        CarStockItem(
            product_id=r.product_id,
            name=r.name,
            category=r.category,
            unit=r.unit,
            quantity=r.quantity
        )
        for r in rows
    ]
@router.get("/car/state/export")
def export_car_state(
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Eksport aktualnego stanu magazynu auta do Excela.
    """
    # pobranie całego stanu auta
    rows = (
        db.query(
            models.Product.name,
            models.Product.category,
            models.Product.unit,
            models.CarStock.quantity
        )
        .join(models.Product, models.Product.id == models.CarStock.product_id)
        .filter(models.CarStock.car_plate == user.car_plate)
        .order_by(models.Product.id)
        .all()
    )

    if not rows:
        raise HTTPException(status_code=400, detail="Brak towaru w samochodzie")

    # generowanie XLSX
    from openpyxl import Workbook
    import io
    from fastapi.responses import StreamingResponse

    wb = Workbook()
    ws = wb.active
    ws.title = "Stan magazynu"

    ws.append(["Nazwa", "Kategoria", "Jednostka", "Ilość"])

    for name, category, unit, qty in rows:
        ws.append([name, category, unit, qty])

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)

    filename = f"stan_{user.car_plate}.xlsx"

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )


