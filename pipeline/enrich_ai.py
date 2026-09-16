"""
enrich_ai.py — Extracción con IA de información estructurada desde
conversaciones.json

Aplica SOLO a leads que tienen conversación de WhatsApp asociada
(Meta Ads y Formulario Web nunca tienen conversación).

Extrae, para cada conversación, las mismas 3 variables que el análisis
del histórico ya validó como predictoras de cierre: 
manifesto_cuota_inicial (vía presupuesto_cuota_inicial),
forma_pago_declarada, pidio_cita. Además dos campos de contexto para
el asesor que NO entran al score: modelo_interes_ia, intencion_declarada
y objecion_principal.

pidio_cita se define ESTRICTO: solo cuenta intención de visitar el
punto de venta en persona (alineado con el campo homónimo del
histórico). 

Salida: data/processed/enriquecimiento_ia.csv (archivo intermedio,
no se inserta a Postgres todavía — eso lo hace un paso aparte una vez
validada la extracción).

Uso:
    python -m pipeline.enrich_ai
"""
import json
import os
import time
from pathlib import Path
from typing import Literal, Optional

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

from pipeline.ingest import cargar_conversaciones

load_dotenv()

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUT_PATH = PROCESSED_DIR / "enriquecimiento_ia.csv"

MODELO_OPENAI = "gpt-4o-mini"

client = OpenAI()  # lee OPENAI_API_KEY de la variable de entorno (.env)


# ============================================================
# Schema de salida — Structured Outputs
# ============================================================

class ExtraccionConversacion(BaseModel):
    """
    Molde de lo que el LLM debe devolver por cada conversación.
    Los 3 primeros campos son los que entran al score (misma
    definición que las columnas homónimas de historico_cierres.csv).
    Los últimos 3 son contexto adicional para el asesor, no entran
    al score.
    """
    manifesto_cuota_inicial: bool = Field(
        description=(
            "True si el cliente mencionó explícitamente que tiene plata "
            "disponible como cuota inicial o para pagar de contado, con "
            "un monto o cantidad concreta (aunque sea en jerga coloquial "
            "como '1 palo' o '2 kilos'). False si no lo mencionó, o si "
            "mencionó que NO tiene nada ('0 millonzitos')."
        )
    )
    presupuesto_cuota_inicial: Optional[float] = Field(
        default=None,
        description=(
            "Monto en pesos colombianos (COP) que el cliente mencionó "
            "como cuota inicial o presupuesto, ya convertido a número "
            "completo. Resolver jerga coloquial: '1 palo' o '1 palos' = "
            "1000000, 'kilo' = 1000000, '0 millonzitos' = 0, "
            "'1000mil' = interpretar por contexto según el precio de la "
            "moto discutida (normalmente significa 1000000, no "
            "1000000000). Null si no mencionó ningún monto."
        )
    )
    forma_pago_declarada: Literal["contado", "credito", "no_informa"] = Field(
        description=(
            "Forma de pago que el cliente declaró explícitamente. "
            "'no_informa' si nunca lo dijo con claridad en la conversación."
        )
    )
    pidio_cita: bool = Field(
        description=(
            "True ÚNICAMENTE si el cliente manifestó intención de "
            "visitar el punto de venta EN PERSONA (ej. 'voy esta tarde', "
            "'mañana los visito', pidió horario de atención con intención "
            "de ir, aceptó que le 'separen' la moto). "
            "IMPORTANTE: pedir que le envíen la cotización por WhatsApp "
            "NO cuenta como pidio_cita — eso es una señal más débil, "
            "va reflejada en intencion_declarada si corresponde."
        )
    )
    modelo_interes_ia: Optional[str] = Field(
        default=None,
        description=(
            "Modelo de moto de interés según lo que el cliente dijo en "
            "el chat (marca + línea). Null si no quedó claro."
        )
    )
    intencion_declarada: Optional[str] = Field(
        default=None,
        description=(
            "Resumen breve (una frase corta) de la intención del cliente "
            "al final de la conversación, escrito SIEMPRE en tercera "
            "persona y con tus propias palabras — NUNCA cites ni copies "
            "texto literal del cliente entre comillas. Ejemplos correctos: "
            "'pidió que le manden cotización formal', 'se enfrió, solo "
            "comparaba precios', 'quiere financiar y visitar el punto de "
            "venta'. Null si no hay suficiente información."
        )
    )
    objecion_principal: Optional[str] = Field(
        default=None,
        description=(
            "Objeción o reparo principal expresado por el cliente, si "
            "lo hubo (ej. 'tasa de interés alta', 'no tienen motos "
            "usadas', 'está comparando con otra marca'). Null si no "
            "expresó ninguna objeción."
        )
    )


# ============================================================
# Armado del prompt
# ============================================================

SYSTEM_PROMPT = """Sos un asistente que analiza conversaciones de WhatsApp \
entre clientes y asesores comerciales de una concesionaria de motos en \
Colombia. Tu tarea es extraer información estructurada de cada \
conversación, siguiendo exactamente las definiciones de cada campo del \
schema provisto.

Tené en cuenta el lenguaje coloquial colombiano para montos de dinero:
- "1 palo" / "1 palos" / "un palo" = 1.000.000 COP
- "kilo" = 1.000.000 COP
- Si dice "0 millonzitos" o similar, es 0 (no tiene inicial)
- Montos como "1000mil" son ambiguos: interpretalos usando el precio de \
la moto mencionada en la conversación como referencia de escala (nunca \
asumas miles de millones para una moto que cuesta unos pocos millones)

Sé conservador: si una señal no está clara en el texto, usá el valor \
por defecto que indica "no se sabe" en vez de inferir de más."""


def construir_texto_conversacion(conversacion: dict) -> str:
    """Convierte la lista de mensajes en un texto legible para el prompt."""
    lineas = [f"Conversación iniciada: {conversacion['fecha_inicio']}"]
    for m in conversacion["mensajes"]:
        emisor = "CLIENTE" if m["emisor"] == "cliente" else "ASESOR"
        lineas.append(f"[{m['hora']}] {emisor}: {m['texto']}")
    return "\n".join(lineas)


def extraer_de_conversacion(conversacion: dict) -> ExtraccionConversacion:
    texto = construir_texto_conversacion(conversacion)
    respuesta = client.beta.chat.completions.parse(
        model=MODELO_OPENAI,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": texto},
        ],
        response_format=ExtraccionConversacion,
    )
    return respuesta.choices[0].message.parsed


# ============================================================
# Loop principal
# ============================================================

def enriquecer_conversaciones(limite: int | None = None) -> pd.DataFrame:
    """
    Procesa todas las conversaciones (o las primeras `limite`, útil para
    probar sin gastar de más mientras se calibra el prompt) y devuelve
    un DataFrame con una fila por conversación procesada exitosamente.

    Los errores por conversación individual no interrumpen el batch;
    se listan al final.
    """
    conversaciones = cargar_conversaciones()
    if limite is not None:
        conversaciones = conversaciones[:limite]

    resultados = []
    errores = []

    total = len(conversaciones)
    for i, conv in enumerate(conversaciones, start=1):
        try:
            extraccion = extraer_de_conversacion(conv)
            fila = extraccion.model_dump()
            fila["conversacion_id"] = conv["conversacion_id"]
            fila["lead_id"] = conv["lead_id"]
            resultados.append(fila)
        except Exception as e:
            errores.append({"conversacion_id": conv.get("conversacion_id"), "error": str(e)})

        if i % 25 == 0 or i == total:
            print(f"  procesadas {i}/{total} conversaciones...")

    if errores:
        print(f"\n{len(errores)} conversaciones fallaron:")
        for err in errores:
            print(f"  - {err['conversacion_id']}: {err['error']}")

    df = pd.DataFrame(resultados)
    columnas_orden = [
        "lead_id", "conversacion_id",
        "manifesto_cuota_inicial", "presupuesto_cuota_inicial",
        "forma_pago_declarada", "pidio_cita",
        "modelo_interes_ia", "intencion_declarada", "objecion_principal",
    ]
    df = df[columnas_orden]

    for col in ["modelo_interes_ia", "intencion_declarada", "objecion_principal"]:
        df[col] = df[col].apply(limpiar_texto)

    return df


def guardar(df: pd.DataFrame) -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8")
    print(f"\nGuardado en: {OUTPUT_PATH}")


def limpiar_texto(valor):
    """Elimina bytes nulos y caracteres de control que a veces devuelve
    el LLM en campos de texto libre — no afectan al score, pero rompen
    inserts en Postgres si no se limpian antes."""
    if pd.isna(valor):
        return valor
    return str(valor).replace("\x00", "").strip()


def mostrar_resumen(df: pd.DataFrame) -> None:
    print("\n=== RESUMEN ===")
    print(f"Conversaciones procesadas: {len(df)}")
    print(f"\nmanifesto_cuota_inicial:\n{df['manifesto_cuota_inicial'].value_counts()}")
    print(f"\nforma_pago_declarada:\n{df['forma_pago_declarada'].value_counts()}")
    print(f"\npidio_cita:\n{df['pidio_cita'].value_counts()}")
    print(f"\npresupuesto_cuota_inicial - nulos: {df['presupuesto_cuota_inicial'].isna().sum()} / {len(df)}")


if __name__ == "__main__":
    inicio = time.time()

    # Para probar con pocas conversaciones primero, cambiar a:
    #   df = enriquecer_conversaciones(limite=20)
    df = enriquecer_conversaciones()

    guardar(df)
    mostrar_resumen(df)

    print(f"\nTiempo total: {time.time() - inicio:.1f}s")