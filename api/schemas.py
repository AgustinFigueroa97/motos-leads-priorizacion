"""
api/schemas.py — Modelos Pydantic para las respuestas de la API
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class LeadResponse(BaseModel):
    lead_id: str
    canal: str
    fecha_registro: datetime
    modelo_interes_texto: Optional[str]
    marca_matcheada: Optional[str]
    linea_matcheada: Optional[str]
    estado_gestion: str
    nombre_cliente: Optional[str]
    telefono_cliente: str
    score: float
    temperatura: str
    modelo_interes_ia: Optional[str]
    presupuesto_cuota_inicial: Optional[float]
    forma_pago_declarada: Optional[str]
    intencion_declarada: Optional[str]
    objecion_principal: Optional[str]
    pidio_cita: Optional[bool]
    asesor_id: str
    fecha_asignacion: datetime

    class Config:
        from_attributes = True


class AsesorInfo(BaseModel):
    asesor_id: str
    nombre: str
    empresa_id: str
    punto_venta_id: str
    capacidad_diaria_leads: int

    class Config:
        from_attributes = True


class EmpresaInfo(BaseModel):
    empresa_id: str
    nombre: str

    class Config:
        from_attributes = True


class LeadsPorAsesorResponse(BaseModel):
    asesor: AsesorInfo
    total_leads: int
    leads: list[LeadResponse]


class LeadsPorEmpresaResponse(BaseModel):
    empresa: EmpresaInfo
    total_leads: int
    leads: list[LeadResponse]


class PipelineRunResponse(BaseModel):
    status: str
    mensaje: str