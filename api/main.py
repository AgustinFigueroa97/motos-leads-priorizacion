"""
api/main.py — API de priorización de leads
Motos y Servicios de Colombia S.A.S.

Expone los resultados del pipeline por asesor y por empresa, con
aislamiento estricto: cada consulta queda acotada a la empresa del
asesor o empresa solicitada, nunca cruza datos entre comercializadoras.

Uso local:
    uvicorn api.main:app --reload

Documentación interactiva: /docs
"""
from fastapi import FastAPI

from .routers import leads, pipeline

app = FastAPI(
    title="Priorización de Leads API",
    description="Lista diaria de leads priorizados por asesor, con enriquecimiento IA y aislamiento por empresa.",
    version="1.0.0",
)

app.include_router(leads.router, prefix="/leads", tags=["Leads"])
app.include_router(pipeline.router, prefix="/pipeline", tags=["Pipeline"])


@app.get("/")
def root():
    return {
        "message": "API Priorización de Leads",
        "version": "1.0.0",
        "docs": "/docs",
    }


@app.get("/health")
def health_check():
    return {"status": "ok"}