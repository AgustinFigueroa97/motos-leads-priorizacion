"""
match_catalogo.py — Fuzzy match de modelo_interes_texto contra el catálogo

Criterio conservador (ya decidido): si la similitud es alta, asigna el
SKU. Si es dudosa o el texto es insuficiente (solo marca, vacío), queda
sku_matcheado=None en vez de forzar un match — mejor "no sé" que un
match falso que después no se puede defender en la sustentación.
"""
import pandas as pd
from rapidfuzz import fuzz, process

from pipeline.ingest import cargar_catalogo_motos

UMBRAL_CONFIANZA = 85  # conservador: por debajo de esto, no se fuerza el match


def construir_indice_catalogo() -> pd.DataFrame:
    catalogo = cargar_catalogo_motos()
    catalogo["texto_busqueda"] = (
        catalogo["marca"].str.strip() + " " + catalogo["linea"].str.strip()
    )
    catalogo["texto_busqueda_lower"] = catalogo["texto_busqueda"].str.lower()
    return catalogo


def obtener_marcas_catalogo(catalogo: pd.DataFrame) -> set[str]:
    return set(catalogo["marca"].str.strip().str.lower())


def matchear_modelo(texto: str, catalogo: pd.DataFrame, marcas: set[str]) -> tuple[str | None, float]:
    if pd.isna(texto) or not str(texto).strip():
        return None, 0.0

    texto_limpio = str(texto).strip().lower()

    if texto_limpio in marcas:
        return None, 0.0  # es solo la marca, sin modelo -> no hay info suficiente

    resultado = process.extractOne(
        texto_limpio,
        catalogo["texto_busqueda_lower"].tolist(),
        scorer=fuzz.WRatio,
    )
    if resultado is None:
        return None, 0.0

    texto_match, score, idx = resultado
    if score < UMBRAL_CONFIANZA:
        return None, score

    sku = catalogo.iloc[idx]["sku"]
    return sku, score


def matchear_leads_contra_catalogo(leads_ok: pd.DataFrame) -> pd.DataFrame:
    catalogo = construir_indice_catalogo()
    marcas = obtener_marcas_catalogo(catalogo)

    resultados = leads_ok["modelo_interes_texto"].apply(
        lambda texto: matchear_modelo(texto, catalogo, marcas)
    )
    leads_ok = leads_ok.copy()
    leads_ok["sku_matcheado"] = resultados.apply(lambda t: t[0])
    leads_ok["match_score"] = resultados.apply(lambda t: t[1])

    return leads_ok


if __name__ == "__main__":
    from pipeline.normalize import normalizar_leads

    leads_ok, _ = normalizar_leads()
    leads_matcheados = matchear_leads_contra_catalogo(leads_ok)

    total = len(leads_matcheados)
    con_match = leads_matcheados["sku_matcheado"].notna().sum()
    sin_match = total - con_match

    print(f"Total de leads: {total}")
    print(f"Con match confiable (score >= {UMBRAL_CONFIANZA}): {con_match}")
    print(f"Sin match (texto vacío o score bajo): {sin_match}")
    print()

    print("--- Muestra de matches exitosos ---")
    print(
        leads_matcheados[leads_matcheados["sku_matcheado"].notna()]
        [["modelo_interes_texto", "sku_matcheado", "match_score"]]
        .sample(10, random_state=1)
    )
    print()

    print("--- Muestra de NO matcheados (para revisar si el umbral es razonable) ---")
    print(
        leads_matcheados[leads_matcheados["sku_matcheado"].isna()]
        [["modelo_interes_texto", "match_score"]]
        .sample(10, random_state=1)
    )