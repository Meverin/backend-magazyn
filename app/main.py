from fastapi import FastAPI
from .database import Base, engine
from . import models
from .routers import health, auth, products, car, warehouse

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Magazyn Promax API",
    version="0.1.0",
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(products.router)
app.include_router(car.router)
app.include_router(warehouse.router)


@app.get("/")
async def root():
    return {"message": "Magazyn API działa"}
