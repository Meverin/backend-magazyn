# app/routers/warehouse.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import List, Optional
import io
from datetime import datetime

from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

from ..database import get_db
from ..auth_utils import get_current_user
from .. import models

router = APIRouter(prefix="/warehouse", tags=["warehouse"])

# =====================================================
# DTO – PRODUKTY
# =====================================================

class ProductDto(BaseModel):
    id: int
    name: str
    category: Optional[str] = None
    index: str
    unit: str

    class Config:
        orm_mode = True


# =====================================================
# DTO – PROSTE POBRANIE (legacy /receive)
# =====================================================

class ReceiveRequest(BaseModel):
    product_id: int
    quantity: float = Field(gt=0)


class IssueRequest(BaseModel):
    product_id: int
    quantity: float = Field(gt=0)
    place: str


# =====================================================
# DTO – NOWY DOKUMENT POBRANIA
# =====================================================

class GoodsReceiptItemDto(BaseModel):
    product_id: int
    quantity: float = Field(gt=0)


class GoodsReceiptRequestDto(BaseModel):
    date: str                     # ISO string YYYY-MM-DD lub pełna data
    received_by: str              # osoba pobierająca
    issued_by: str                # osoba wydająca
    items: List[GoodsReceiptItemDto]


# =====================================================
# DTO – HISTORIA RUCHÓW
# =====================================================

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


# =====================================================
# DTO – WPROWADZENIE STANU (RESET)
# =====================================================

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


# =====================================================
# LISTA PRODUKTÓW
# =====================================================

@router.get("/products", response_model=List[ProductDto])
def get_products(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return db.query(models.Product).order_by(models.Product.name).all()


# =====================================================
# NOWE POBRANIE TOWARU (DOKUMENT)
# =====================================================

@router.post("/receive-document")
def create_goods_receipt(
    req: GoodsReceiptRequestDto,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    """
    Nowe, pełne pobranie:
    - nagłówek dokumentu (date, received_by, issued_by),
    - wiele pozycji,
    - ruchy w stock_movements (IN),
    - aktualizacja car_stock.
    """

    # 1) utworzenie nagłówka
    try:
        parsed_date = datetime.fromisoformat(req.date)
    except:
        raise HTTPException(400, "Nieprawidłowy format daty (wymagane ISO 8601)")

    receipt = models.GoodsReceipt(
        date=parsed_date,
        car_plate=user.car_plate,
        received_by=req.received_by.strip().title(),
        issued_by=req.issued_by.strip().title(),
    )
    db.add(receipt)
    db.commit()
    db.refresh(receipt)

    # 2) zapis pozycji i aktualizacja stanów
    for item in req.items:
        product = db.query(models.Product).filter(models.Product.id == item.product_id).first()
        if not product:
            raise HTTPException(404, f"Produkt ID={item.product_id} nie istnieje")

        row = models.GoodsReceiptItem(
            receipt_id=receipt.id,
            product_id=item.product_id,
            quantity=item.quantity
        )
        db.add(row)

        # aktualizacja car_stock
        stock = db.query(models.CarStock).filter(
            models.CarStock.car_plate == user.car_plate,
            models.CarStock.product_id == item.product_id
        ).first()

        if not stock:
            stock = models.CarStock(
                car_plate=user.car_plate,
                product_id=item.product_id,
                quantity=item.quantity
            )
            db.add(stock)
        else:
            stock.quantity += item.quantity

        # zapis ruchu
        move = models.StockMovement(
            user_id=user.id,
            car_plate=user.car_plate,
            product_id=item.product_id,
            quantity=item.quantity,
            type="IN",
            place=None,
            receipt_id=receipt.id
        )
        db.add(move)

    db.commit()

    return {"status": "OK", "receipt_id": receipt.id}


# =====================================================
# LISTA DOKUMENTÓW POBRANIA
# =====================================================

class GoodsReceiptListItemDto(BaseModel):
    id: int
    date: str
    received_by: str
    issued_by: str
    items_count: int

    class Config:
        orm_mode = True


@router.get("/receipts", response_model=List[GoodsReceiptListItemDto])
def list_receipts(
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    rows = (
        db.query(models.GoodsReceipt)
        .filter(models.GoodsReceipt.car_plate == user.car_plate)
        .order_by(models.GoodsReceipt.date.desc())
        .all()
    )

    result = []
    for r in rows:
        result.append(GoodsReceiptListItemDto(
            id=r.id,
            date=str(r.date),
            received_by=r.received_by,
            issued_by=r.issued_by,
            items_count=len(r.items)
        ))

    return result


# =====================================================
# SZCZEGÓŁY DOKUMENTU POBRANIA
# =====================================================

class GoodsReceiptDetailsItemDto(BaseModel):
    product_id: int
    name: str
    index: str
    unit: str
    quantity: float


class GoodsReceiptDetailsDto(BaseModel):
    id: int
    date: str
    received_by: str
    issued_by: str
    items: List[GoodsReceiptDetailsItemDto]


@router.get("/receipt/{receipt_id}", response_model=GoodsReceiptDetailsDto)
def get_receipt(
    receipt_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    receipt = db.query(models.GoodsReceipt).filter(models.GoodsReceipt.id == receipt_id).first()
    if not receipt:
        raise HTTPException(404, "Dokument nie istnieje")

    if receipt.car_plate != user.car_plate:
        raise HTTPException(403, "Brak dostępu do tego dokumentu")

    items = []
    for row in receipt.items:
        p = row.product
        items.append(GoodsReceiptDetailsItemDto(
            product_id=p.id,
            name=p.name,
            index=p.index,
            unit=p.unit,
            quantity=row.quantity
        ))

    return GoodsReceiptDetailsDto(
        id=receipt.id,
        date=str(receipt.date),
        received_by=receipt.received_by,
        issued_by=receipt.issued_by,
        items=items
    )


# =====================================================
# EKSPORT DOKUMENTU DO EXCEL
# =====================================================

@router.get("/receipt/{receipt_id}/export/excel")
def export_receipt_excel(
    receipt_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    receipt = db.query(models.GoodsReceipt).filter(models.GoodsReceipt.id == receipt_id).first()
    if not receipt:
        raise HTTPException(404, "Dokument nie istnieje")

    if receipt.car_plate != user.car_plate:
        raise HTTPException(403, "Brak uprawnień")

    wb = Workbook()
    ws = wb.active
    ws.title = "Pobranie"

    ws.append(["Data", str(receipt.date)])
    ws.append(["Pobierający", receipt.received_by])
    ws.append(["Wydający", receipt.issued_by])
    ws.append([])
    ws.append(["Produkt", "Indeks", "Jednostka", "Ilość"])

    for item in receipt.items:
        ws.append([item.product.name, item.product.index, item.product.unit, item.quantity])

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)

    filename = f"pobranie_{receipt.id}.xlsx"

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# =====================================================
# EKSPORT DOKUMENTU DO PDF
# =====================================================

@router.get("/receipt/{receipt_id}/export/pdf")
def export_receipt_pdf(
    receipt_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    receipt = db.query(models.GoodsReceipt).filter(models.GoodsReceipt.id == receipt_id).first()
    if not receipt:
        raise HTTPException(404, "Dokument nie istnieje")

    if receipt.car_plate != user.car_plate:
        raise HTTPException(403, "Brak uprawnień")

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    x = 40
    y = 800

    c.setFont("Helvetica-Bold", 16)
    c.drawString(x, y, f"DOKUMENT POBRANIA #{receipt.id}")
    y -= 30

    c.setFont("Helvetica", 12)
    c.drawString(x, y, f"Data: {receipt.date}")
    y -= 20

    c.drawString(x, y, f"Pobierający: {receipt.received_by}")
    y -= 20

    c.drawString(x, y, f"Wydający: {receipt.issued_by}")
    y -= 30

    c.setFont("Helvetica-Bold", 12)
    c.drawString(x, y, "Produkt / Indeks / Jed. / Ilość")
    y -= 20

    c.setFont("Helvetica", 12)

    for item in receipt.items:
        line = f"{item.product.name}  |  {item.product.index}  |  {item.product.unit}  |  {item.quantity}"
        c.drawString(x, y, line)
        y -= 20
        if y < 40:
            c.showPage()
            y = 800

    c.save()

    buffer.seek(0)
    filename = f"pobranie_{receipt.id}.pdf"

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
