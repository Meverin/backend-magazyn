# app/routers/warehouse.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import List, Optional
import io
from datetime import datetime, date

from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

from ..database import get_db
from ..auth_utils import get_current_user
from .. import models

router = APIRouter(prefix="/warehouse", tags=["warehouse"])


# =====================================================================
# DTO – PRODUKTY
# =====================================================================

class ProductDto(BaseModel):
    id: int
    name: str
    category: Optional[str] = None
    index: str
    unit: str

    class Config:
        orm_mode = True


# =====================================================================
# DTO – STARE /receive (zachowujemy kompatybilność)
# =====================================================================

class ReceiveRequest(BaseModel):
    product_id: int
    quantity: float = Field(gt=0)


class IssueRequest(BaseModel):
    product_id: int
    quantity: float = Field(gt=0)
    place: str


# =====================================================================
# DTO – NOWE DOKUMENTY POBRANIA
# =====================================================================

class GoodsReceiptItemDto(BaseModel):
    product_id: int
    quantity: float = Field(gt=0)


class GoodsReceiptRequestDto(BaseModel):
    document_date: str       # YYYY-MM-DD
    taker_name: str          # osoba pobierająca
    giver_name: str          # osoba wydająca
    items: List[GoodsReceiptItemDto]


# =====================================================================
# DTO – HISTORIA RUCHÓW
# =====================================================================

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


# =====================================================================
# DTO – RESET STANU POJAZDU
# =====================================================================

class UpdateCarStateItemDto(BaseModel):
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


# =====================================================================
# LISTA PRODUKTÓW
# =====================================================================

@router.get("/products", response_model=List[ProductDto])
def get_products(
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    return db.query(models.Product).order_by(models.Product.name).all()


# =====================================================================
# NOWE POBRANIE TOWARU – DOKUMENT
# =====================================================================

@router.post("/receive-document")
def create_goods_receipt(
    req: GoodsReceiptRequestDto,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    """
    Tworzy jeden dokument pobrania:
    - nagłówek
    - pozycje
    - uzupełnia car_stock
    - zapisuje ruchy magazynowe IN
    """

    try:
        doc_date = date.fromisoformat(req.document_date)
    except Exception:
        raise HTTPException(400, "Nieprawidłowy format daty (YYYY-MM-DD)")

    # --- Nagłówek ---
    header = models.StockReceiveHeader(
        document_date=doc_date,
        taker_name=req.taker_name.strip().title(),
        giver_name=req.giver_name.strip().title(),
        car_plate=user.car_plate,
        user_id=user.id
    )

    db.add(header)
    db.commit()
    db.refresh(header)

    # --- Pozycje ---
    for item in req.items:
        product = db.query(models.Product).filter(
            models.Product.id == item.product_id
        ).first()

        if not product:
            raise HTTPException(404, f"Produkt ID={item.product_id} nie istnieje")

        # zapis pozycji
        row = models.StockReceiveItem(
            header_id=header.id,
            product_id=item.product_id,
            quantity=item.quantity
        )
        db.add(row)

        # aktualizacja stanu auta
        stock = (
            db.query(models.CarStock)
            .filter(
                models.CarStock.car_plate == user.car_plate,
                models.CarStock.product_id == item.product_id
            )
            .first()
        )

        if not stock:
            stock = models.CarStock(
                car_plate=user.car_plate,
                product_id=item.product_id,
                quantity=item.quantity
            )
            db.add(stock)
        else:
            stock.quantity += item.quantity

        # ruch magazynowy
        move = models.StockMovement(
            user_id=user.id,
            car_plate=user.car_plate,
            product_id=item.product_id,
            quantity=item.quantity,
            type="IN",
            place=None
        )
        db.add(move)

    db.commit()

    return {"status": "OK", "receipt_id": header.id}


# =====================================================================
# LISTA DOKUMENTÓW POBRANIA
# =====================================================================

class GoodsReceiptListItemDto(BaseModel):
    id: int
    document_date: str
    taker_name: str
    giver_name: str
    items_count: int

    class Config:
        orm_mode = True


@router.get("/receipts", response_model=List[GoodsReceiptListItemDto])
def list_receipts(
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    headers = (
        db.query(models.StockReceiveHeader)
        .filter(models.StockReceiveHeader.car_plate == user.car_plate)
        .order_by(models.StockReceiveHeader.document_date.desc())
        .all()
    )

    return [
        GoodsReceiptListItemDto(
            id=h.id,
            document_date=str(h.document_date),
            taker_name=h.taker_name,
            giver_name=h.giver_name,
            items_count=len(h.items)
        )
        for h in headers
    ]


# =====================================================================
# SZCZEGÓŁY KONKRETNEGO DOKUMENTU
# =====================================================================

class GoodsReceiptDetailsItemDto(BaseModel):
    product_id: int
    name: str
    index: str
    unit: str
    quantity: float


class GoodsReceiptDetailsDto(BaseModel):
    id: int
    document_date: str
    taker_name: str
    giver_name: str
    items: List[GoodsReceiptDetailsItemDto]


@router.get("/receipt/{receipt_id}", response_model=GoodsReceiptDetailsDto)
def get_receipt(
    receipt_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    header = db.query(models.StockReceiveHeader).filter_by(id=receipt_id).first()

    if not header:
        raise HTTPException(404, "Dokument pobrania nie istnieje")

    if header.car_plate != user.car_plate:
        raise HTTPException(403, "Brak dostępu")

    items = []
    for row in header.items:
        p = row.product
        items.append(
            GoodsReceiptDetailsItemDto(
                product_id=p.id,
                name=p.name,
                index=p.index,
                unit=p.unit,
                quantity=row.quantity
            )
        )

    return GoodsReceiptDetailsDto(
        id=header.id,
        document_date=str(header.document_date),
        taker_name=header.taker_name,
        giver_name=header.giver_name,
        items=items
    )


# =====================================================================
# EKSPORT DOKUMENTU → EXCEL
# =====================================================================

@router.get("/receipt/{receipt_id}/export/excel")
def export_receipt_excel(
    receipt_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    header = db.query(models.StockReceiveHeader).filter_by(id=receipt_id).first()

    if not header:
        raise HTTPException(404, "Dokument nie istnieje")

    if header.car_plate != user.car_plate:
        raise HTTPException(403, "Brak dostępu")

    wb = Workbook()
    ws = wb.active
    ws.title = "Pobranie"

    ws.append(["Data", str(header.document_date)])
    ws.append(["Pobierający", header.taker_name])
    ws.append(["Wydający", header.giver_name])
    ws.append([])
    ws.append(["Produkt", "Indeks", "Jednostka", "Ilość"])

    for item in header.items:
        ws.append([
            item.product.name,
            item.product.index,
            item.product.unit,
            item.quantity
        ])

    data = io.BytesIO()
    wb.save(data)
    data.seek(0)

    filename = f"pobranie_{receipt_id}.xlsx"

    return StreamingResponse(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename=\"{filename}\"'}
    )


# =====================================================================
# EKSPORT DOKUMENTU → PDF
# =====================================================================

@router.get("/receipt/{receipt_id}/export/pdf")
def export_receipt_pdf(
    receipt_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):

    header = db.query(models.StockReceiveHeader).filter_by(id=receipt_id).first()

    if not header:
        raise HTTPException(404, "Dokument nie istnieje")

    if header.car_plate != user.car_plate:
        raise HTTPException(403, "Brak dostępu")

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    x = 40
    y = 800

    c.setFont("Helvetica-Bold", 16)
    c.drawString(x, y, f"DOKUMENT POBRANIA #{header.id}")
    y -= 30

    c.setFont("Helvetica", 12)
    c.drawString(x, y, f"Data: {header.document_date}")
    y -= 20

    c.drawString(x, y, f"Pobierający: {header.taker_name}")
    y -= 20

    c.drawString(x, y, f"Wydający: {header.giver_name}")
    y -= 30

    c.setFont("Helvetica-Bold", 12)
    c.drawString(x, y, "Produkt / Indeks / Jednostka / Ilość")
    y -= 20

    c.setFont("Helvetica", 12)

    for item in header.items:
        p = item.product
        line = f"{p.name} | {p.index} | {p.unit} | {item.quantity}"
        c.drawString(x, y, line)
        y -= 20

        if y < 40:
            c.showPage()
            y = 800

    c.save()
    buffer.seek(0)

    filename = f"pobranie_{receipt_id}.pdf"

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename=\"{filename}\"'}
    )


# =====================================================================
# STARE ENDPOINTY (POZOSTAWIAMY)
# =====================================================================

@router.post("/receive")
def receive_goods(req: ReceiveRequest,
                  db: Session = Depends(get_db),
                  user=Depends(get_current_user)):
    """
    Legacy – pobranie jednego produktu.
    """
    product = db.query(models.Product).filter_by(id=req.product_id).first()
    if not product:
        raise HTTPException(404, "Produkt nie istnieje")

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

    move = models.StockMovement(
        user_id=user.id,
        car_plate=user.car_plate,
        product_id=req.product_id,
        quantity=req.quantity,
        type="IN",
        place=None
    )
    db.add(move)

    db.commit()
    return {"status": "OK"}


@router.post("/issue")
def issue_goods(req: IssueRequest,
                db: Session = Depends(get_db),
                user=Depends(get_current_user)):
    """
    Rozchód towaru z auta (OUT).
    """
    product = db.query(models.Product).filter_by(id=req.product_id).first()
    if not product:
        raise HTTPException(404, "Produkt nie istnieje")

    stock = (
        db.query(models.CarStock)
        .filter(
            models.CarStock.car_plate == user.car_plate,
            models.CarStock.product_id == req.product_id
        )
        .first()
    )

    if not stock:
        raise HTTPException(400, "Brak produktu w aucie")

    if stock.quantity < req.quantity:
        raise HTTPException(400, f"Dostępne tylko: {stock.quantity}")

    stock.quantity -= req.quantity

    move = models.StockMovement(
        user_id=user.id,
        car_plate=user.car_plate,
        product_id=req.product_id,
        quantity=-req.quantity,
        type="OUT",
        place=req.place
    )
    db.add(move)

    db.commit()
    return {"status": "OK"}


@router.post("/update-car-state")
def update_car_state(
    req: UpdateCarStateRequestDto,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    """
    Reset + wprowadzenie nowego stanu.
    """
    db.query(models.CarStock).filter(
        models.CarStock.car_plate == user.car_plate
    ).delete()

    for item in req.items:
        if item.quantity <= 0:
            continue

        stock = models.CarStock(
            car_plate=user.car_plate,
            product_id=item.product_id,
            quantity=item.quantity
        )
        db.add(stock)

        move = models.StockMovement(
            user_id=user.id,
            car_plate=user.car_plate,
            product_id=item.product_id,
            quantity=item.quantity,
            type="IN",
            place=None
        )
        db.add(move)

    db.commit()
    return {"status": "OK"}


@router.get("/car/state", response_model=List[CarStockItem])
def get_car_state(db: Session = Depends(get_db),
                  user=Depends(get_current_user)):

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

    return [
        MovementItem(
            id=m.id,
            timestamp=str(m.timestamp),
            product_id=p.id,
            product_name=p.name,
            index=p.index,
            quantity=m.quantity,
            type=m.type,
            place=m.place
        )
        for m, p in rows
    ]
