"""
normalize.py — Limpieza y normalización de leads.csv

Aplica las reglas validadas contra los datos reales:
- canal / estado_gestion: colapsar variantes de casing
- telefono: normalizar a 10 dígitos (Colombia)
- ciudad: colapsar variantes de formato
- fecha_registro: parsear con criterio DD/MM por defecto para casos
  ambiguos, marcando fecha_registro_supuesta=True en esos casos
- fila basura (LD-01501): se cuarentena, no entra al resto del pipeline
- dedup: por teléfono normalizado, siempre dentro del scope de una
  misma empresa (nunca se mergea entre empresas distintas)
"""
import re
from datetime import datetime

import pandas as pd

from pipeline.ingest import cargar_leads
from pipeline.models import ESTADOS_GESTION_VALIDOS, CANALES_VALIDOS


# ============================================================
# Normalización de canal
# ============================================================

MAPA_CANAL = {
    "whatsapp": "WhatsApp",
    "meta ads": "Meta Ads",
    "formulario web": "Formulario Web",
}


def normalizar_canal(valor: str) -> str | None:
    """Colapsa las 6 variantes de casing a los 3 canales válidos."""
    if pd.isna(valor):
        return None
    return MAPA_CANAL.get(str(valor).strip().lower())


# ============================================================
# Normalización de estado_gestion
# ============================================================

MAPA_ESTADO = {
    "contactado": "Contactado",
    "sin gestión": "Sin gestión",
    "sin gestion": "Sin gestión",
    "cotización enviada": "Cotización enviada",
    "cotizacion enviada": "Cotización enviada",
    "en proceso": "En proceso",
    "no contesta": "No contesta",
    "descartado": "Descartado",
}


def normalizar_estado(valor: str) -> str | None:
    """Colapsa las 10 variantes de casing a los 6 estados válidos."""
    if pd.isna(valor):
        return None
    resultado = MAPA_ESTADO.get(str(valor).strip().lower())
    if resultado is None:
        raise ValueError(f"estado_gestion no reconocido: {valor!r}")
    return resultado


# ============================================================
# Normalización de teléfono
# ============================================================

def normalizar_telefono(valor: str) -> str | None:
    """
    Devuelve 10 dígitos limpios (Colombia), sin +57 ni separadores.
    Devuelve None si, tras limpiar, no quedan 10 dígitos (caso basura).
    """
    if pd.isna(valor):
        return None
    digitos = re.sub(r"\D", "", str(valor))
    if digitos.startswith("57") and len(digitos) == 12:
        digitos = digitos[2:]
    if len(digitos) != 10:
        return None  # ej. el caso basura "300123"
    return digitos


# ============================================================
# Normalización de ciudad
# ============================================================

MAPA_CIUDAD = {
    # Bogotá
    "bogota": "Bogotá",
    "bogotá": "Bogotá",
    "bogota dc": "Bogotá",
    "bogota d.c": "Bogotá",
    "bogota d.c.": "Bogotá",
    # Medellín
    "medellin": "Medellín",
    "medellín": "Medellín",
    # Cartagena
    "cartagena": "Cartagena",
    "cartagena de indias": "Cartagena",
    # Santa Marta
    "santa marta": "Santa Marta",
    "sta marta": "Santa Marta",
    # Barranquilla
    "barranquilla": "Barranquilla",
    "b/quilla": "Barranquilla",
    # Soacha
    "soacha": "Soacha",
    # Soledad
    "soledad": "Soledad",
    # Bello
    "bello": "Bello",
    # Rionegro
    "rionegro": "Rionegro",
    "rio negro": "Rionegro",
    # Itagüí
    "itagui": "Itagüí",
    "itagüí": "Itagüí",
}


def normalizar_ciudad(valor: str) -> str | None:
    """Colapsa variantes de formato/casing/espacios a un nombre canónico."""
    if pd.isna(valor):
        return None
    limpio = str(valor).strip().lower()
    limpio = re.sub(r"\s+", " ", limpio)  # espacios múltiples -> uno
    limpio = limpio.rstrip(".")
    return MAPA_CIUDAD.get(limpio, str(valor).strip())  # si no está mapeada, deja el original limpio


# ============================================================
# Normalización de fecha_registro
# ============================================================

PATRON_SEPARADOR = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})(.*)$")


def parsear_fecha(valor: str) -> tuple[datetime | None, bool]:
    """
    Devuelve (fecha_parseada, es_supuesta).

    - Si el formato es ISO (con T o espacio, año primero) -> sin ambigüedad.
    - Si el formato tiene separador / o - y uno de los dos números > 12,
      el formato queda confirmado (DD/MM o MM/DD según corresponda).
    - Si ambos números son <= 12, es ambiguo: se asume DD/MM
      (criterio con evidencia 310 vs 59 a favor en el propio dataset)
      y se marca es_supuesta=True.
    - Si no se puede parsear de ninguna forma (ej. día 33), devuelve
      (None, False) — ese caso se maneja aparte como fila basura.
    """
    texto = str(valor).strip()

    m = PATRON_SEPARADOR.match(texto)
    if m:
        a, b, anio, resto = m.groups()
        a, b = int(a), int(b)
        if a > 12 and b <= 12:
            dia, mes = a, b
            supuesta = False
        elif b > 12 and a <= 12:
            dia, mes = b, a
            supuesta = False
        elif a <= 12 and b <= 12:
            dia, mes = a, b  # asume DD/MM
            supuesta = True
        else:
            return None, False  # ambos > 12, inválido

        try:
            hora_texto = resto.strip()
            fecha_str = f"{anio}-{mes:02d}-{dia:02d} {hora_texto}".strip()
            fecha = pd.to_datetime(fecha_str, errors="raise")
            return fecha.to_pydatetime(), supuesta
        except Exception:
            return None, False

    # Formato ISO (con T o espacio) — pandas lo resuelve sin ambigüedad
    try:
        fecha = pd.to_datetime(texto, errors="raise")
        return fecha.to_pydatetime(), False
    except Exception:
        return None, False


# ============================================================
# Pipeline de normalización completo
# ============================================================

def normalizar_leads() -> tuple[pd.DataFrame, pd.DataFrame]:
    df = cargar_leads()

    # --- normalizaciones campo por campo ---
    df["canal_norm"] = df["canal"].apply(normalizar_canal)

    # Corrección: si el lead tiene una conversación de WhatsApp
    # asociada, el canal real es WhatsApp, más allá de lo que diga
    # leads.csv. Una conversación de chat con mensajes solo puede
    # existir si el contacto fue por WhatsApp (Meta Ads y Formulario
    # Web son formularios de una sola vez, no generan chat) -- así
    # que se corrige el campo con la fuente más confiable disponible.
    from pipeline.ingest import cargar_conversaciones
    conversaciones = cargar_conversaciones()
    leads_con_conversacion = set(c["lead_id"] for c in conversaciones)

    mask_canal_incorrecto = (
        df["lead_id"].isin(leads_con_conversacion)
        & (df["canal_norm"] != "WhatsApp")
    )
    cantidad_corregidos = mask_canal_incorrecto.sum()
    if cantidad_corregidos:
        print(f"  {cantidad_corregidos} leads con canal corregido a WhatsApp (tenían conversación asociada)")
    df.loc[mask_canal_incorrecto, "canal_norm"] = "WhatsApp"

    df["estado_gestion_norm"] = df["estado_gestion"].apply(normalizar_estado)
    df["telefono_norm"] = df["telefono"].apply(normalizar_telefono)
    df["ciudad_norm"] = df["ciudad"].apply(normalizar_ciudad)

    fechas_parseadas = df["fecha_registro"].apply(parsear_fecha)
    df["fecha_registro_norm"] = fechas_parseadas.apply(lambda t: t[0])
    df["fecha_registro_supuesta"] = fechas_parseadas.apply(lambda t: t[1])

    # --- separar cuarentena: canal nulo, teléfono irrecuperable o fecha inválida ---
    mask_cuarentena = (
        df["canal_norm"].isna()
        | df["telefono_norm"].isna()
        | df["fecha_registro_norm"].isna()
    )

    leads_cuarentena = df[mask_cuarentena].copy()
    leads_ok = df[~mask_cuarentena].copy()

    return leads_ok, leads_cuarentena


def construir_clientes(leads_ok: pd.DataFrame) -> pd.DataFrame:
    """
    Dedup por teléfono normalizado, SIEMPRE dentro del scope de una
    misma empresa. Un mismo teléfono en empresas distintas genera
    2 clientes separados (no se mergea, por aislamiento estricto).
    """
    clientes = (
        leads_ok
        .sort_values("fecha_registro_norm")  # se queda con el dato más reciente al agrupar
        .groupby(["telefono_norm", "empresa_id"], as_index=False)
        .agg(
            nombre_normalizado=("nombre_cliente", "last"),
            email=("email", "last"),
            ciudad_normalizada=("ciudad_norm", "last"),
        )
    )
    return clientes


if __name__ == "__main__":
    leads_ok, leads_cuarentena = normalizar_leads()

    print(f"Leads normalizados OK: {len(leads_ok)}")
    print(f"Leads en cuarentena: {len(leads_cuarentena)}")
    print()
    print("--- Cuarentena ---")
    print(leads_cuarentena[["lead_id", "nombre_cliente", "canal", "telefono", "fecha_registro"]])
    print()

    clientes = construir_clientes(leads_ok)
    print(f"Clientes únicos tras dedup: {len(clientes)}")
    print(f"(de {len(leads_ok)} leads normalizados)")