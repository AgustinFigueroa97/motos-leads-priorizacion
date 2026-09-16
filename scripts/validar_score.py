"""
validar_score.py 

Objetivo: probar, con el histórico real de cierres, que la fórmula de
score propuesta (con los pesos definidos a partir de
explorar_variables.py) separa efectivamente a los leads que cierran
de los que no. No usa el score para "producir" nada, solo valida el
criterio antes de aplicarlo en producción (scoring.py).
"""
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------
# 1. Cargar histórico
# ---------------------------------------------------------------------
df = pd.read_csv("data/raw/historico_cierres.csv", encoding="utf-8")

# Solo tiene sentido evaluar el score sobre leads que SÍ fueron
# gestionados. "Sin gestión" nunca tuvo la chance de cerrar por
# definición (nadie lo llamó), incluirlo ensuciaría la comparación.
df = df[df["desenlace"] != "Sin gestión"].copy()
df["cerro"] = (df["desenlace"] == "Cerrado").astype(int)

# ---------------------------------------------------------------------
# 2. Normalizar cada señal a una escala 0-1
#    (misma lógica que se aplica después sobre leads.csv en producción,
#    salvo la parte de "urgencia", que ahí se calcula con fecha actual
#    en vez de con horas_al_primer_contacto ya conocido)
# ---------------------------------------------------------------------

# --- Urgencia por tiempo al contacto -----------------------------------
# "Menos horas hasta el contacto" = más urgencia aplicada. Se invierte
# a una escala 0-1 (más rápido = valor más alto) usando log para no
# dejar que los outliers de 120h+ dominen la escala.
df["horas_log"] = np.log1p(df["horas_al_primer_contacto"])
max_log = df["horas_log"].max()
df["señal_urgencia"] = 1 - (df["horas_log"] / max_log)

# --- Cuota inicial manifestada ------------------------------------------
df["señal_cuota"] = (df["manifesto_cuota_inicial"] == "SI").astype(float)

# --- Pidió cita -----------------------------------------------------------
df["señal_cita"] = (df["pidio_cita"] == "SI").astype(float)

# --- Forma de pago declarada (contado o crédito = declaró algo; no_informa = 0) --
df["señal_forma_pago"] = (df["forma_pago_declarada"] != "no_informa").astype(float)

# ---------------------------------------------------------------------
# 3. Fórmula de score (pesos basados en la regresión logística de
#    explorar_variables.py: horas_al_primer_contacto fue, por lejos,
#    la variable con mayor coeficiente estandarizado)
# ---------------------------------------------------------------------
PESOS = {
    "señal_urgencia":   45,
    "señal_cuota":      20,
    "señal_cita":       18,
    "señal_forma_pago": 12,
}
# Nota: en producción se evaluó sumar un 5to componente de "stock bajo"
# (catalogo_motos.csv), pero se descartó por no existir esa columna en
# el histórico -> no se puede validar con datos reales. Los 4 pesos de
# acá suman 95, se re-normalizan a 100 dentro de la fórmula.
suma_pesos = sum(PESOS.values())

df["score"] = sum(
    df[col] * (peso / suma_pesos * 100) for col, peso in PESOS.items()
)

# Es decir normalizamos 45+20+18+12=95 a 100, y cada señal queda en escala 0-100.
# Para urgencia = 100 * (0.45*urgencia) / 0.95 = 47.37
# Para cuota = 100 * (0.20*cuota) / 0.95 = 21.05
# Para cita = 100 * (0.18*cita) / 0.95 = 18.95
# Para forma_pago = 100 * (0.12*forma_pago) / 0.95 = 12.63

# Entonces la formula final de score queda:
# score = 47.37*urgencia + 21.05*cuota + 18.95*cita + 12.63*forma_pago
# que es lo que se aplica en scoring.py sobre leads.csv en producción. 

# Entonces si tenemos un lead que:
# - fue contactado en 2h (urgencia=1)
# - declaró cuota inicial (cuota=1)
# - pidió cita (cita=1)
# - declaró forma de pago (forma_pago=1)
# su score sería: 1*(47.37) + 1*(21.05) + 1*(18.95) + 1*(12.63) = 100 (máximo posible)

# y asi sucesivamente para otros leads con diferentes combinaciones de señales.

# Una vez hecho eso, se puede cortar el score en cuartiles y medir la tasa de cierre real por cuartil para validar que el score efectivamente separa a los leads que cierran de los que no. Es decir ordeno los leads por score y veo si los que tienen score más alto efectivamente cierran más que los que tienen score más bajo. Esto es lo que se hace en la sección 4 del código. 

# ---------------------------------------------------------------------
# 4. Cortar en cuartiles y medir tasa de cierre real por cuartil
# ---------------------------------------------------------------------
df["cuartil"] = pd.qcut(df["score"], 4, labels=["Q1 (bajo)", "Q2", "Q3", "Q4 (alto)"])

resumen = df.groupby("cuartil", observed=True).agg(
    n_leads=("cerro", "count"),
    tasa_cierre=("cerro", "mean"),
    score_promedio=("score", "mean"),
).round(4)
resumen["tasa_cierre_%"] = (resumen["tasa_cierre"] * 100).round(1)

print("=" * 70)
print("VALIDACIÓN DEL CRITERIO DE SCORING CONTRA HISTÓRICO REAL")
print("=" * 70)
print(f"\nTotal leads gestionados evaluados: {len(df)}")
print(f"Tasa de cierre global: {df['cerro'].mean()*100:.1f}%\n")
print(resumen[["n_leads", "score_promedio", "tasa_cierre_%"]])

q4 = resumen.loc["Q4 (alto)", "tasa_cierre_%"]
q1 = resumen.loc["Q1 (bajo)", "tasa_cierre_%"]
print(f"\nRatio Q4/Q1: {q4/q1:.2f}x")
print(f"(El cuartil de score más alto cierra {q4/q1:.1f} veces más que el cuartil más bajo)")

# Guardar los cortes de score por cuartil -> se reusan en scoring.py
# para definir las etiquetas Frío/Tibio/Caliente sobre el score 0-100
print("\n--- Cortes de score por cuartil (para temperatura en producción) ---")
cortes = df["score"].quantile([0.25, 0.5, 0.75]).round(1)
print(cortes)