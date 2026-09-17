"""
api/routers/pipeline.py — Disparo del pipeline completo
"""
from fastapi import APIRouter, HTTPException

from .. import schemas

router = APIRouter(redirect_slashes=False)


@router.post("/run", response_model=schemas.PipelineRunResponse)
def correr_pipeline():
    """
    Corre el pipeline completo (normalización, matching de catálogo,
    scoring y asignación) y persiste el resultado en la base.

    El enriquecimiento con IA no se re-ejecuta acá: usa el archivo ya
    generado por enrich_ai.py. Volver a extraer con IA es un proceso
    aparte, pensado para correr cuando llegan conversaciones nuevas.
    """
    from pipeline.run_pipeline import cargar_todo

    try:
        cargar_todo()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error corriendo el pipeline: {str(e)}")

    return {"status": "ok", "mensaje": "Pipeline ejecutado y datos actualizados en la base"}