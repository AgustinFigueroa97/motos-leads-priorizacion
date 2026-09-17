"""
assign.py — Asignación de leads a asesores, por punto de venta.

Criterio: capacidad relativa disponible. Cada asesor tiene un techo de
leads activos simultáneos; se asigna al que tenga mayor % de capacidad
libre en el momento, recalculando después de cada asignación. Dentro
de cada punto de venta, se reparte en orden de score descendente.

El reparto es siempre local a un punto de venta (nunca cruza PV ni
empresas), y solo considera asesores activos.
"""
import pandas as pd

from pipeline.ingest import cargar_asesores


def contar_leads_activos_por_asesor(asignaciones_previas: pd.DataFrame) -> dict[str, int]:
    """
    Cuenta leads actualmente asignados a cada asesor, a partir de
    asignaciones previas (vacío en la corrida inicial).
    """
    if asignaciones_previas.empty:
        return {}
    return asignaciones_previas["asesor_id"].value_counts().to_dict()


def asignar_leads(
    leads_scoreados: pd.DataFrame,
    asignaciones_previas: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    leads_scoreados: salida de scoring.calcular_score(), con columnas
        lead_id, empresa_id, punto_venta_id, score.
    asignaciones_previas: asignaciones ya existentes (lead_id, asesor_id),
        para leads en gestión activa. None o vacío en la corrida inicial.

    Devuelve un DataFrame con lead_id, asesor_id, y un flag
    pv_sobrecargado por si el punto de venta llegó al tope de todos
    sus asesores.
    """
    asesores = cargar_asesores()
    asesores = asesores[asesores["activo"].astype(str).str.upper() == "SI"].copy()

    leads_activos_por_asesor = contar_leads_activos_por_asesor(
        asignaciones_previas if asignaciones_previas is not None else pd.DataFrame()
    )

    resultados = []

    # Agrupar leads por punto de venta + empresa: el reparto nunca cruza
    for (empresa_id, punto_venta_id), leads_pv in leads_scoreados.groupby(
        ["empresa_id", "punto_venta_id"]
    ):
        asesores_pv = asesores[
            (asesores["empresa_id"] == empresa_id)
            & (asesores["punto_venta_id"] == punto_venta_id)
        ].copy()

        if asesores_pv.empty:
            # No hay asesores activos en este PV -> no se puede asignar
            for _, lead in leads_pv.iterrows():
                resultados.append({
                    "lead_id": lead["lead_id"],
                    "asesor_id": None,
                    "pv_sobrecargado": True,
                })
            continue

        asesores_pv["leads_activos"] = asesores_pv["asesor_id"].map(
            lambda aid: leads_activos_por_asesor.get(aid, 0)
        )

        leads_ordenados = leads_pv.sort_values("score", ascending=False)

        for _, lead in leads_ordenados.iterrows():
            asesores_pv["margen_libre_pct"] = (
                asesores_pv["capacidad_diaria_leads"] - asesores_pv["leads_activos"]
            ) / asesores_pv["capacidad_diaria_leads"]

            disponibles = asesores_pv[asesores_pv["margen_libre_pct"] > 0]
            pv_sobrecargado = disponibles.empty

            if pv_sobrecargado:
                # Todos al tope: asignar al de menor exceso relativo
                asesores_pv["exceso"] = (
                    asesores_pv["leads_activos"] - asesores_pv["capacidad_diaria_leads"]
                )
                idx_elegido = asesores_pv["exceso"].idxmin()
            else:
                idx_elegido = disponibles["margen_libre_pct"].idxmax()

            asesor_elegido = asesores_pv.loc[idx_elegido, "asesor_id"]

            resultados.append({
                "lead_id": lead["lead_id"],
                "asesor_id": asesor_elegido,
                "pv_sobrecargado": pv_sobrecargado,
            })

            # Recalcular capacidad de ese asesor para la siguiente iteración
            asesores_pv.loc[idx_elegido, "leads_activos"] += 1

    return pd.DataFrame(resultados)


if __name__ == "__main__":
    from pipeline.normalize import normalizar_leads
    from pipeline.match_catalogo import matchear_leads_contra_catalogo
    from pipeline.scoring import calcular_score
    import pandas as pd

    leads_ok, _ = normalizar_leads()
    leads_ok = matchear_leads_contra_catalogo(leads_ok)
    enriquecimiento = pd.read_csv("data/processed/enriquecimiento_ia.csv", encoding="utf-8")
    leads_scoreados = calcular_score(leads_ok, enriquecimiento)

    asignaciones = asignar_leads(leads_scoreados)

    print(f"Total leads asignados: {len(asignaciones)}")
    print(f"Sin asesor disponible: {asignaciones['asesor_id'].isna().sum()}")
    print(f"En PV sobrecargado: {asignaciones['pv_sobrecargado'].sum()}")
    print(f"\n--- Leads por asesor (top 10) ---")
    print(asignaciones["asesor_id"].value_counts().head(10))