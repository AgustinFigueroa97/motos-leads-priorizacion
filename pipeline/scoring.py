"""
scoring.py — Cálculo del score de priorización de leads

Aplica a TODOS los leads normalizados (tengan o no enriquecimiento IA).

Criterio conservador: si un lead no tiene enriquecimiento IA (no tuvo
conversación, o la conversación no dio señal), las 3 señales que
dependen de eso (cuota, cita, forma de pago) valen 0 — nunca se
infla el score con una señal que no está.
"""
from datetime import datetime, timezone

import pandas as pd

# ---------------------------------------------------------------------
# Pesos y cortes — EXACTAMENTE los validados en scripts/validar_score.py
# ---------------------------------------------------------------------
PESOS = {
    "senal_urgencia":    45,
    "senal_cuota":       20,
    "senal_cita":        18,
    "senal_forma_pago":  12,
}
SUMA_PESOS = sum(PESOS.values())  # 95, se re-normaliza a 100 abajo

# Cortes de temperatura, tomados de los cuartiles reales del histórico
# (scripts/validar_score.py): percentil 25 y percentil 75 del score.
CORTE_FRIO_TIBIO = 33.7
CORTE_TIBIO_CALIENTE = 61.6


def calcular_senal_urgencia(fecha_registro: pd.Series, ahora: datetime) -> pd.Series:
    """
    Señal de urgencia por tiempo esperando, en tramos calcados de la
    tabla de tasas de cierre real (scripts/explorar_variables.py):

        0-1h   -> 15.1% cierre -> señal 1.0  (máxima urgencia)
        1-4h   -> 11.6% cierre -> señal 0.8
        4-48h  -> ~7.7% cierre -> señal 0.5  (3 ventanas con tasa similar,
                                              se agrupan en una sola señal)
        48h+   ->  5.8% cierre -> señal 0.3  (mínima urgencia)
    """
    horas_esperando = (ahora - fecha_registro).dt.total_seconds() / 3600
    horas_esperando = horas_esperando.clip(lower=0)  # por si algún reloj está desfasado

    def asignar_senal(horas):
        if horas <= 1:
            return 1.0
        elif horas <= 4:
            return 0.8
        elif horas <= 48:
            return 0.5
        else:
            return 0.3

    return horas_esperando.apply(asignar_senal)


def calcular_score(
    leads_ok: pd.DataFrame,
    enriquecimiento: pd.DataFrame,
    ahora: datetime | None = None,
) -> pd.DataFrame:
    """
    leads_ok: salida de normalize.normalizar_leads() (los leads OK,
        sin los de cuarentena), con columna fecha_registro_norm.
    enriquecimiento: salida de enrich_ai.py (data/processed/enriquecimiento_ia.csv),
        con columnas lead_id, manifesto_cuota_inicial, pidio_cita,
        forma_pago_declarada.

    Devuelve leads_ok con las columnas nuevas: score, temperatura,
    factores (dict con el detalle de cada señal, para explicabilidad).
    """
    if ahora is None:
        ahora = datetime.now()

    df = leads_ok.copy()

    # --- Traer las 3 señales de enriquecimiento (join por lead_id) ---
    # left join: leads SIN conversación quedan con NaN en estas columnas,
    # y NaN se trata como "no" / "no informa" -> señal en 0.
    enr = enriquecimiento[
        ["lead_id", "manifesto_cuota_inicial", "pidio_cita", "forma_pago_declarada"]
    ]
    df = df.merge(enr, on="lead_id", how="left")

    # --- Calcular cada señal en escala 0-1 ---
    df["senal_urgencia"] = calcular_senal_urgencia(df["fecha_registro_norm"], ahora)
    df["senal_cuota"] = df["manifesto_cuota_inicial"].fillna(False).astype(float)
    df["senal_cita"] = df["pidio_cita"].fillna(False).astype(float)
    df["senal_forma_pago"] = (
        df["forma_pago_declarada"].notna()
        & (df["forma_pago_declarada"] != "no_informa")
    ).astype(float)

    # --- Score final (0-100) ---
    # Cada señal aporta, como máximo, su peso completo (re-normalizado
    # sobre 95 -> 100). Escrito explícito, señal por señal, para que sea
    # fácil de leer y de explicar en la sustentación.
    aporte_urgencia = df["senal_urgencia"] * (PESOS["senal_urgencia"] / SUMA_PESOS * 100)
    aporte_cuota = df["senal_cuota"] * (PESOS["senal_cuota"] / SUMA_PESOS * 100)
    aporte_cita = df["senal_cita"] * (PESOS["senal_cita"] / SUMA_PESOS * 100)
    aporte_forma_pago = df["senal_forma_pago"] * (PESOS["senal_forma_pago"] / SUMA_PESOS * 100)

    df["score"] = aporte_urgencia + aporte_cuota + aporte_cita + aporte_forma_pago
    df["score"] = df["score"].round(2)

    # --- Temperatura, con los cortes reales del histórico ---
    def clasificar_temperatura(score):
        if score < CORTE_FRIO_TIBIO:
            return "Frío"
        elif score < CORTE_TIBIO_CALIENTE:
            return "Tibio"
        else:
            return "Caliente"

    df["temperatura"] = df["score"].apply(clasificar_temperatura)

    # --- Factores (para guardar en lead_score.factores_json, explicabilidad) ---
    def armar_factores(row):
        return {
            "urgencia_por_tiempo": {
                "valor_senal": row["senal_urgencia"],
                "peso": PESOS["senal_urgencia"],
                "puntos_aportados": round(row["senal_urgencia"] * PESOS["senal_urgencia"] / SUMA_PESOS * 100, 2),
            },
            "manifesto_cuota_inicial": {
                "valor_senal": row["senal_cuota"],
                "peso": PESOS["senal_cuota"],
                "puntos_aportados": round(row["senal_cuota"] * PESOS["senal_cuota"] / SUMA_PESOS * 100, 2),
            },
            "pidio_cita": {
                "valor_senal": row["senal_cita"],
                "peso": PESOS["senal_cita"],
                "puntos_aportados": round(row["senal_cita"] * PESOS["senal_cita"] / SUMA_PESOS * 100, 2),
            },
            "forma_pago_declarada": {
                "valor_senal": row["senal_forma_pago"],
                "peso": PESOS["senal_forma_pago"],
                "puntos_aportados": round(row["senal_forma_pago"] * PESOS["senal_forma_pago"] / SUMA_PESOS * 100, 2),
            },
        }

    df["factores"] = df.apply(armar_factores, axis=1)

    return df


if __name__ == "__main__":
    from pipeline.normalize import normalizar_leads
    from pipeline.match_catalogo import matchear_leads_contra_catalogo

    leads_ok, _ = normalizar_leads()
    leads_ok = matchear_leads_contra_catalogo(leads_ok)

    enriquecimiento = pd.read_csv(
        "data/processed/enriquecimiento_ia.csv", encoding="utf-8"
    )

    leads_scoreados = calcular_score(leads_ok, enriquecimiento, ahora=datetime.now())

    print(f"Total leads scoreados: {len(leads_scoreados)}")
    print(f"\nDistribución de temperatura:")
    print(leads_scoreados["temperatura"].value_counts())
    print(f"\nScore: min={leads_scoreados['score'].min():.1f}, "
          f"max={leads_scoreados['score'].max():.1f}, "
          f"promedio={leads_scoreados['score'].mean():.1f}")
    print(f"\n--- Top 10 leads por score ---")
    print(
        leads_scoreados.sort_values("score", ascending=False)
        [["lead_id", "score", "temperatura", "senal_urgencia", "senal_cuota", "senal_cita", "senal_forma_pago"]]
        .head(10)
    )