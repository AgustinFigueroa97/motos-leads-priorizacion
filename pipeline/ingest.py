"""
ingest.py — Lectura de los 5 archivos fuente desde data/raw/

Por ahora solo LEE y valida que cargan bien (shape, columnas, encoding).
La limpieza/normalización real va en normalize.py, este script es
el primer paso del pipeline: confirmar que los datos entran sin
intervención manual.
"""
import json
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"


def cargar_leads() -> pd.DataFrame:
    path = DATA_DIR / "leads.csv"
    df = pd.read_csv(path, encoding="utf-8")
    return df


def cargar_conversaciones() -> list[dict]:
    path = DATA_DIR / "conversaciones.json"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def cargar_catalogo_motos() -> pd.DataFrame:
    path = DATA_DIR / "catalogo_motos.csv"
    df = pd.read_csv(path, encoding="utf-8")
    return df


def cargar_asesores() -> pd.DataFrame:
    path = DATA_DIR / "asesores.csv"
    df = pd.read_csv(path, encoding="utf-8")
    return df


def cargar_historico_cierres() -> pd.DataFrame:
    path = DATA_DIR / "historico_cierres.csv"
    df = pd.read_csv(path, encoding="utf-8")
    return df


def verificar_carga():
    """Carga los 5 archivos y muestra shape/columnas/muestra para validar."""
    print("=== leads.csv ===")
    leads = cargar_leads()
    print(f"Shape: {leads.shape}")
    print(f"Columnas: {list(leads.columns)}")
    print(leads.head(3))
    print()

    print("=== conversaciones.json ===")
    conversaciones = cargar_conversaciones()
    print(f"Cantidad de conversaciones: {len(conversaciones)}")
    print(f"Claves del primer objeto: {list(conversaciones[0].keys())}")
    print()

    print("=== catalogo_motos.csv ===")
    catalogo = cargar_catalogo_motos()
    print(f"Shape: {catalogo.shape}")
    print(f"Columnas: {list(catalogo.columns)}")
    print(catalogo.head(3))
    print()

    print("=== asesores.csv ===")
    asesores = cargar_asesores()
    print(f"Shape: {asesores.shape}")
    print(f"Columnas: {list(asesores.columns)}")
    print(asesores.head(3))
    print()

    print("=== historico_cierres.csv ===")
    historico = cargar_historico_cierres()
    print(f"Shape: {historico.shape}")
    print(f"Columnas: {list(historico.columns)}")
    print(historico.head(3))


if __name__ == "__main__":
    verificar_carga()