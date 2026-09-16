import pandas as pd

df = pd.read_csv("data/processed/enriquecimiento_ia.csv", encoding="utf-8")

for col in ["modelo_interes_ia", "intencion_declarada", "objecion_principal"]:
    df[col] = df[col].apply(lambda v: v.replace("\x00", "").strip() if pd.notna(v) else v)

df.to_csv("data/processed/enriquecimiento_ia.csv", index=False, encoding="utf-8")
print("Listo, bytes nulos eliminados.")