# app/routers/warehouse.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import List, Optional
import io

from fastapi.responses import StreamingResponse
from openpyxl import Workbook

from ..database import get_db
from ..auth_utils import get_current_user
from .. import models

router = APIRouter(prefix="/warehouse", tags=["warehouse"])

# =====================================================
# SCHEMY DTO
# =====================================================

class ProductDto(BaseModel):
    id: int
    name: str
    category: Optional[str] = None
    index: str
    unit: str

    class Config:
        orm_mode = True


class ReceiveRequest(BaseModel):
    """Pobranie towaru z magazynu zewnętrznego na samochód."""
    product_id: int
    quantity: float = Field(gt=0)


class IssueRequest(BaseModel):
    """Rozchód towaru z samochodu (zużycie w terenie)."""
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
    place: Optional[str]

    class Config:
        orm_mode = True


class UpdateCarStateItemDto(BaseModel):
    """Pojedyncza pozycja przy pełnym wprowadzeniu stanu auta."""
    product_id: int
    quantity: float = Field(ge=0)


class UpdateCarStateRequestDto(BaseModel):
    items: List[UpdateCarStateItemDto]


class CarStockItem(BaseModel):
    product_id: int
    name: str
    category: Optional[str]
    unit: str
    quantity: float

    class Config:
        orm_mode = True


# =====================================================
# LISTA PRODUKTÓW
# =====================================================

@router.get("/products", response_model=List[ProductDto])
def get_products(
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    """
    Zwraca listę wszystkich produktów – używane w wyszukiwaniu
    / podpowiedziach po stronie aplikacji.
    """
    products = (
        db.query(models.Product)
        .order_by(models.Product.name)
        .all()
    )
    return products


# =====================================================
# POBRANIE TOWARU NA SAMOCHÓD (PRZYJĘCIE)
# =====================================================

@router.post("/receive")
def receive_goods(
    req: ReceiveRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    """
    Pobranie towaru z magazynu zewnętrznego na samochód:
    - jeżeli pozycja już istnieje w car_stock → zwiększamy ilość,
    - jeżeli nie ma → tworzymy nowy rekord.
    - zapisujemy ruch w stock_movements z type="IN".
    """
    product = (
        db.query(models.Product)
        .filter(models.Product.id == req.product_id)
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Produkt nie istnieje")

    stock = (
        db.query(models.CarStock)
        .filter(
            models.CarStock.car_plate == user.car_plate,
            models.CarStock.product_id == req.product_id
        )
        .first()
    )

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
        type="IN",
        place=None
    )
    db.add(movement)

    db.commit()

    return {
        "status": "OK",
        "message": "Przyjęto towar na samochód",
        "quantity": stock.quantity
    }


# =====================================================
# ROZCHÓD TOWARU Z SAMOCHODU
# =====================================================

@router.post("/issue")
def issue_goods(
    req: IssueRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    """
    Rozchód towaru z samochodu:
    - sprawdza czy produkt istnieje i jest na stanie,
    - pilnuje, żeby nie zejść poniżej zera,
    - zapisuje ruch w stock_movements z type="OUT".
    """
    product = (
        db.query(models.Product)
        .filter(models.Product.id == req.product_id)
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Produkt nie istnieje")

    stock = (
        db.query(models.CarStock)
        .filter(
            models.CarStock.car_plate == user.car_plate,
            models.CarStock.product_id == req.product_id
        )
        .first()
    )

    if not stock:
        raise HTTPException(status_code=400, detail="Brak produktu w aucie")

    if stock.quantity < req.quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Dostępne tylko: {stock.quantity}"
        )

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

    return {
        "status": "OK",
        "message": "Rozchodowano towar",
        "quantity": stock.quantity
    }


# =====================================================
# PEŁNE WPROWADZENIE STANU SAMOCHODU (RESET)
# =====================================================

@router.post("/update-car-state")
def update_car_state(
    req: UpdateCarStateRequestDto,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    """
    Nadpisuje cały stan magazynowy samochodu:
    1) czyści car_stock dla danego auta,
    2) wpisuje nowe rekordy z req.items,
    3) tworzy logi w stock_movements (IN) dla każdej pozycji.
    """

    # 1. Usuń aktualny stan
    db.query(models.CarStock).filter(
        models.CarStock.car_plate == user.car_plate
    ).delete()

    # 2. Dodaj nowe pozycje
    for item in req.items:
        if item.quantity < 0:
            raise HTTPException(
                status_code=400,
                detail=f"Ilość nie może być ujemna (product_id={item.product_id})"
            )

        if item.quantity == 0:
            # zero pomijamy – nie ma sensu tworzyć rekordu
            continue

        stock = models.CarStock(
            car_plate=user.car_plate,
            product_id=item.product_id,
            quantity=item.quantity
        )
        db.add(stock)

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


# =====================================================
# AKTUALNY STAN SAMOCHODU
# =====================================================

@router.get("/car/state", response_model=List[CarStockItem])
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


# =====================================================
# HISTORIA RUCHÓW
# =====================================================

@router.get("/history", response_model=List[MovementItem])
def history(
    product_id: Optional[int] = None,
    type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    q = (
        db.query(models.StockMovement, models.Product)
        .join(models.Product, models.Product.id == models.StockMovement.product_id)
        .filter(models.StockMovement.car_plate == user.car_plate)
    )

    if product_id is not None:
        q = q.filter(models.StockMovement.product_id == product_id)

    if type is not None:
        q = q.filter(models.StockMovement.type == type)

    if date_from is not None:
        q = q.filter(models.StockMovement.timestamp >= date_from)

    if date_to is not None:
        q = q.filter(models.StockMovement.timestamp <= date_to)

    q = q.order_by(models.StockMovement.timestamp.desc())

    rows = q.all()
    result: List[MovementItem] = []

    for movement, product in rows:
        result.append(
            MovementItem(
                id=movement.id,
                timestamp=str(movement.timestamp),
                product_id=product.id,
                product_name=product.name,
                index=product.index,
                quantity=movement.quantity,
                type=movement.type,
                place=movement.place,
            )
        )

    return result


# =====================================================
# EKSPORT STANU SAMOCHODU DO EXCELA
# =====================================================

@router.get("/car/state/export")
def export_car_state(
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    """
    Eksport aktualnego stanu magazynu auta do pliku XLSX.
    """
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
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )
