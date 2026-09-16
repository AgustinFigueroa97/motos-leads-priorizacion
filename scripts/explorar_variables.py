"""
explorar_variables.py 

Objetivo: antes de definir la fórmula de score, usar el histórico real
(2200 leads con desenlace conocido) para medir qué variables realmente
se asocian con el cierre, y con qué peso relativo cada una, controlando
por el efecto de las demás.

No predice nada en producción — es un script de UNA SOLA corrida,
exploratorio, cuyo resultado (los coeficientes) se usó para elegir
a mano los pesos de la fórmula final en scoring.py. No se re-ejecuta
como parte del pipeline productivo.
"""
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

df = pd.read_csv("data/raw/historico_cierres.csv", encoding="utf-8")

# Solo leads gestionados: "Sin gestión" nunca tuvo chance de cerrar,
# incluirlo ensuciaría la comparación.
gestionados = df[df["desenlace"] != "Sin gestión"].copy()
gestionados["cerro"] = (gestionados["desenlace"] == "Cerrado").astype(int)

# ---------------------------------------------------------------------
# 1. Tasas de cierre simples por variable (primera mirada, sin controlar
#    por el resto — sirve para ver el patrón "a ojo" antes de la regresión)
# ---------------------------------------------------------------------
print("--- horas_al_primer_contacto (bins) ---")
bins = [0, 1, 4, 8, 24, 48, 200]
labels = ["0-1h", "1-4h", "4-8h", "8-24h", "24-48h", "48h+"]
gestionados["bin_horas"] = pd.cut(
    gestionados["horas_al_primer_contacto"], bins=bins, labels=labels
)
print(gestionados.groupby("bin_horas", observed=True)["cerro"].agg(["mean", "count"]))
print()

print("--- numero_contactos ---")
print(gestionados.groupby("numero_contactos")["cerro"].agg(["mean", "count"]))
print()

print("--- canal ---")
print(gestionados.groupby("canal")["cerro"].agg(["mean", "count"]))
print()

print("--- precio_lista (cuartiles) ---")
gestionados["bin_precio"] = pd.qcut(gestionados["precio_lista"], 4)
print(gestionados.groupby("bin_precio", observed=True)["cerro"].agg(["mean", "count"]))
print()

print("--- combo: pidio_cita SI + horas<=4h ---")
combo = gestionados[
    (gestionados["pidio_cita"] == "SI") & (gestionados["horas_al_primer_contacto"] <= 4)
]
print(f"tasa: {combo['cerro'].mean()*100:.1f}%  n={len(combo)}")
print()

print("--- combo: pidio_cita NO + horas>24h ---")
combo2 = gestionados[
    (gestionados["pidio_cita"] == "NO") & (gestionados["horas_al_primer_contacto"] > 24)
]
print(f"tasa: {combo2['cerro'].mean()*100:.1f}%  n={len(combo2)}")
print()

print("--- empresa_id (control: no debería haber sesgo fuerte por empresa) ---")
print(gestionados.groupby("empresa_id")["cerro"].agg(["mean", "count"]))
print()

# ---------------------------------------------------------------------
# 2. Regresión logística — peso de cada variable CONTROLANDO por las
#    demás a la vez. Esto es lo que realmente define qué entra a la
#    fórmula de score y con qué peso relativo.
# ---------------------------------------------------------------------
g = gestionados.copy()
g["pidio_cita_n"] = (g["pidio_cita"] == "SI").astype(int)
g["cuota_si"] = (g["manifesto_cuota_inicial"] == "SI").astype(int)
g["cuota_no_informa"] = (g["manifesto_cuota_inicial"] == "NO_INFORMA").astype(int)
g["pago_contado"] = (g["forma_pago_declarada"] == "contado").astype(int)
g["horas_log"] = np.log1p(g["horas_al_primer_contacto"])

variables = ["pidio_cita_n", "cuota_si", "cuota_no_informa", "pago_contado", "horas_log", "numero_contactos"]
X = g[variables]
y = g["cerro"]

# Estandarizar: pone todas las variables en la misma escala, así los
# coeficientes son comparables entre sí (si no, una variable en escala
# grande como "horas" parecería más importante solo por su magnitud).
scaler = StandardScaler()
X_estandarizado = scaler.fit_transform(X)

modelo = LogisticRegression()
modelo.fit(X_estandarizado, y)

print("--- Coeficientes regresión logística (estandarizados) ---")
print("(mayor valor absoluto = más peso real, controlando por el resto)\n")
coeficientes = sorted(
    zip(variables, modelo.coef_[0]), key=lambda t: abs(t[1]), reverse=True
)
for nombre, coef in coeficientes:
    signo = "+" if coef > 0 else ""
    print(f"  {nombre:20s} {signo}{coef:.3f}")

print()
print("Conclusión (usada para definir scoring.py):")
print("  - horas_log domina claramente -> peso más alto en la fórmula (45)")
print("  - cuota_si, pidio_cita_n, pago_contado -> peso moderado y similar (20, 18, 12)")
print("  - cuota_no_informa, numero_contactos -> peso ~0, no entran a la fórmula")