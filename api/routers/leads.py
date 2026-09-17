"""
api/routers/leads.py — Endpoints de consulta de leads priorizados
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import schemas
from ..database import get_db
from pipeline.models import Asesor, Empresa

router = APIRouter(redirect_slashes=False)


# Query base compartida: trae un lead con todo lo que un asesor
# necesita ver, ya joineado. Se usa como texto SQL porque el JOIN
# cruza 6 tablas y el ORM lo haría más largo sin ganar claridad.
QUERY_LEADS_BASE = """
    SELECT
        l.lead_id,
        l.canal,
        l.fecha_registro,
        l.modelo_interes_texto,
        c.marca AS marca_matcheada,
        c.linea AS linea_matcheada,
        l.estado_gestion,
        cli.nombre_normalizado AS nombre_cliente,
        cli.telefono_normalizado AS telefono_cliente,
        s.score,
        s.temperatura,
        e.modelo_interes_ia,
        e.presupuesto_cuota_inicial,
        e.forma_pago_declarada,
        e.intencion_declarada,
        e.objecion_principal,
        e.pidio_cita,
        a.asesor_id,
        a.fecha_asignacion
    FROM leads l
    JOIN asignaciones a ON a.lead_id = l.lead_id
    JOIN lead_score s ON s.lead_id = l.lead_id
    JOIN clientes cli ON cli.cliente_id = l.cliente_id
    LEFT JOIN motos_catalogo c ON c.sku = l.sku_matcheado
    LEFT JOIN lead_enriquecimiento e ON e.lead_id = l.lead_id
"""


@router.get("/{asesor_id}", response_model=schemas.LeadsPorAsesorResponse)
def leads_por_asesor(asesor_id: str, db: Session = Depends(get_db)):
    """
    Lista priorizada de leads asignados a un asesor ("mis leads de
    hoy"), ordenada por score descendente.
    """
    asesor = db.query(Asesor).filter(Asesor.asesor_id == asesor_id).first()
    if asesor is None:
        raise HTTPException(status_code=404, detail=f"Asesor {asesor_id} no encontrado")

    query = text(QUERY_LEADS_BASE + " WHERE a.asesor_id = :asesor_id ORDER BY s.score DESC")
    filas = db.execute(query, {"asesor_id": asesor_id}).mappings().all()

    return {
        "asesor": asesor,
        "total_leads": len(filas),
        "leads": [dict(f) for f in filas],
    }


@router.get("/empresa/{empresa_id}", response_model=schemas.LeadsPorEmpresaResponse)
def leads_por_empresa(empresa_id: str, temperatura: str | None = None, db: Session = Depends(get_db)):
    """
    Todos los leads de una empresa, con su asesor asignado, ordenados
    por score. Filtro opcional por temperatura (Frío / Tibio / Caliente).

    El aislamiento es automático: el filtro por empresa_id es
    obligatorio, así que esta consulta nunca devuelve leads de otra
    comercializadora.
    """
    empresa = db.query(Empresa).filter(Empresa.empresa_id == empresa_id).first()
    if empresa is None:
        raise HTTPException(status_code=404, detail=f"Empresa {empresa_id} no encontrada")

    query_texto = QUERY_LEADS_BASE + " WHERE l.empresa_id = :empresa_id"
    params = {"empresa_id": empresa_id}

    if temperatura is not None:
        query_texto += " AND s.temperatura = :temperatura"
        params["temperatura"] = temperatura

    query_texto += " ORDER BY s.score DESC"

    filas = db.execute(text(query_texto), params).mappings().all()

    return {
        "empresa": empresa,
        "total_leads": len(filas),
        "leads": [dict(f) for f in filas],
    }