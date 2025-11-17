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



router = APIRouter(
    prefix="/warehouse",
    tags=["warehouse"]
)


# ---------- Schemy ----------

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

class SettlementExportRequest(BaseModel):
    date_from: date
    date_to: date
    place: str
    format: Literal["pdf", "xlsx"]
# ---------- Endpointy ----------


@router.post("/receive")
def receive_goods(
    req: ReceiveRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    """
    Przyjęcie towaru do auta (produkt + ilość)
    """

    # sprawdzamy czy produkt istnieje
    product = db.query(models.Product).filter(models.Product.id == req.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Produkt nie istnieje")

    # znajdź rekord dla auta i produktu
    stock = db.query(models.CarStock).filter(
        models.CarStock.car_plate == user.car_plate,
        models.CarStock.product_id == req.product_id
    ).first()

    if not stock:
        # jeśli nie istnieje — tworzymy nowy
        stock = models.CarStock(
            car_plate=user.car_plate,
            product_id=req.product_id,
            quantity=req.quantity
        )
        db.add(stock)
    else:
        # zwiększamy ilość
        stock.quantity += req.quantity

    # zapisujemy historię
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


@router.post("/issue")
def issue_goods(
    req: IssueRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    """
    Rozchód towaru — wymaga miejsca
    """

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
        raise HTTPException(
            status_code=400,
            detail=f"Brak wystarczającej ilości. Dostępne: {stock.quantity}"
        )

    # odejmujemy ilość
    stock.quantity -= req.quantity

    # zapisujemy historię
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

@router.get("/history", response_model=list[MovementItem])
def history(
    product_id: int | None = None,
    type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    """
    Zwraca historię operacji magazynowych dla zalogowanego użytkownika.
    Obsługuje filtry:
    - product_id
    - type ('IN' / 'OUT')
    - date_from
    - date_to
    """

    q = (
        db.query(models.StockMovement, models.Product)
        .join(models.Product, models.Product.id == models.StockMovement.product_id)
        .filter(models.StockMovement.car_plate == user.car_plate)
    )

    # Filtry opcjonalne
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

@router.post("/settlement/export")
def export_settlement(
    req: SettlementExportRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    """
    Generuje rozliczenie tylko z ROZCHODÓW (OUT)
    w formacie PDF lub XLSX, dla zalogowanego użytkownika.
    """

    # zakres dat: od północy date_from do końca dnia date_to
    start_dt = datetime.combine(req.date_from, time.min)
    end_dt = datetime.combine(req.date_to + timedelta(days=1), time.min)

    # agregujemy dane: tylko OUT, tylko dla auta użytkownika
    rows = (
        db.query(
            models.Product.name,
            models.Product.index,
            models.Product.unit,
            func.sum(models.StockMovement.quantity).label("sum_qty"),
        )
        .join(models.Product, models.Product.id == models.StockMovement.product_id)
        .filter(models.StockMovement.car_plate == user.car_plate)
        .filter(models.StockMovement.type == "OUT")
        .filter(models.StockMovement.timestamp >= start_dt)
        .filter(models.StockMovement.timestamp < end_dt)
        .group_by(models.Product.id)
        .all()
    )

    # przeliczamy ilość na wartość dodatnią (bo OUT trzymamy jako ujemne quantity)
    data = []
    for name, index, unit, sum_qty in rows:
        used_qty = float(-sum_qty) if sum_qty is not None else 0.0
        if used_qty <= 0:
            continue
        data.append({
            "name": name,
            "index": index,
            "unit": unit,
            "quantity": used_qty,
        })

    if not data:
        # nic nie zużyto w tym okresie
        raise HTTPException(
            status_code=400,
            detail="Brak rozchodów w podanym zakresie dat."
        )

    if req.format == "xlsx":
        return _generate_excel_report(
            data=data,
            user_name=user.name,
            car_plate=user.car_plate,
            date_from=req.date_from,
            date_to=req.date_to,
            place=req.place,
        )
    else:
        return _generate_pdf_report(
            data=data,
            user_name=user.name,
            car_plate=user.car_plate,
            date_from=req.date_from,
            date_to=req.date_to,
            place=req.place,
        )

def _generate_excel_report(data, user_name, car_plate, date_from, date_to, place):
    wb = Workbook()
    ws = wb.active
    ws.title = "Rozliczenie"

    # Nagłówek
    ws["A1"] = "Rozliczenie zużycia towaru"
    ws["A2"] = f"Pracownik: {user_name}"
    ws["A3"] = f"Samochód: {car_plate}"
    ws["A4"] = f"Okres: {date_from} - {date_to}"
    ws["A5"] = f"Miejsce: {place}"

    # Tabela
    ws.append([])
    ws.append(["Nazwa", "Indeks", "Jednostka", "Ilość zużyta"])

    for row in data:
        ws.append([
            row["name"],
            row["index"],
            row["unit"],
            row["quantity"],
        ])

    # zapis do pamięci
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"rozliczenie_{car_plate}_{date_from}_{date_to}.xlsx"

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"'
    }

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )
def _generate_pdf_report(data, user_name, car_plate, date_from, date_to, place):
    buffer = io.BytesIO()

    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    y = height - 40

    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, "Rozliczenie zużycia towaru")
    y -= 30

    c.setFont("Helvetica", 10)
    c.drawString(40, y, f"Pracownik: {user_name}")
    y -= 15
    c.drawString(40, y, f"Samochód: {car_plate}")
    y -= 15
    c.drawString(40, y, f"Okres: {date_from} - {date_to}")
    y -= 15
    c.drawString(40, y, f"Miejsce: {place}")
    y -= 30

    # Nagłówki tabeli
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, y, "Nazwa")
    c.drawString(220, y, "Indeks")
    c.drawString(340, y, "Jednostka")
    c.drawString(420, y, "Ilość")
    y -= 15

    c.setFont("Helvetica", 10)

    for row in data:
        if y < 50:
            c.showPage()
            y = height - 40

        c.drawString(40, y, str(row["name"])[:30])
        c.drawString(220, y, str(row["index"])[:20])
        c.drawString(340, y, str(row["unit"])[:10])
        c.drawRightString(480, y, str(row["quantity"]))
        y -= 15

    c.showPage()
    c.save()

    buffer.seek(0)

    filename = f"rozliczenie_{car_plate}_{date_from}_{date_to}.pdf"

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"'
    }

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers=headers,
    )
