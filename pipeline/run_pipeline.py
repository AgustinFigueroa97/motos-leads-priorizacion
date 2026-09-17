"""
pipeline/run_pipeline.py — Carga el resultado del pipeline completo a Postgres.

Encadena: normalize -> match_catalogo -> lee enriquecimiento IA (CSV ya
generado por enrich_ai.py) -> scoring -> assign, y persiste todo en las
tablas del modelo (models.py), respetando el orden de foreign keys.

Es re-corrible sin duplicar: usa ON CONFLICT DO NOTHING sobre la clave
primaria de cada tabla, así que correrlo dos veces no rompe nada, solo
no vuelve a insertar lo que ya está.

Uso:
    python -m pipeline.run_pipeline
"""
import os
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from pipeline.ingest import cargar_asesores, cargar_catalogo_motos, cargar_historico_cierres
from pipeline.normalize import normalizar_leads, construir_clientes
from pipeline.match_catalogo import matchear_leads_contra_catalogo
from pipeline.scoring import calcular_score
from pipeline.assign import asignar_leads

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "No se encontró DATABASE_URL en el .env. "
    )

RUTA_ENRIQUECIMIENTO = "data/processed/enriquecimiento_ia.csv"


# ============================================================
# Helper genérico: insertar un DataFrame fila por fila con
# ON CONFLICT DO NOTHING sobre la clave primaria indicada.
# ============================================================

def insertar_upsert(conn, tabla: str, columnas: list[str], filas: list[tuple], clave_primaria: str):
    """
    Inserta 'filas' (lista de tuplas, mismo orden que 'columnas') en
    'tabla'. Si ya existe una fila con esa clave_primaria, no hace nada
    (permite re-correr el script sin duplicar).
    """
    if not filas:
        print(f"  {tabla}: no hay filas para insertar")
        return

    columnas_sql = ", ".join(columnas)
    placeholders = ", ".join(f":{c}" for c in columnas)

    query = text(
        f"INSERT INTO {tabla} ({columnas_sql}) VALUES ({placeholders}) "
        f"ON CONFLICT ({clave_primaria}) DO NOTHING"
    )

    filas_dict = [dict(zip(columnas, fila)) for fila in filas]
    conn.execute(query, filas_dict)
    print(f"  {tabla}: {len(filas_dict)} filas procesadas (insertadas o ya existentes)")


# ============================================================
# 1. empresas, puntos_venta, asesores  <- de asesores.csv
# ============================================================

def cargar_empresas_puntos_venta_asesores(conn):
    asesores_df = cargar_asesores()

    # --- empresas: valores únicos de empresa_id ---
    empresas_unicas = asesores_df["empresa_id"].dropna().unique()
    filas_empresas = [(eid, eid) for eid in empresas_unicas]  # nombre = mismo id por ahora
    insertar_upsert(conn, "empresas", ["empresa_id", "nombre"], filas_empresas, "empresa_id")

    # --- puntos_venta: pares únicos (punto_venta_id, empresa_id) ---
    pv_unicos = asesores_df[["punto_venta_id", "empresa_id"]].drop_duplicates()
    filas_pv = list(pv_unicos.itertuples(index=False, name=None))
    insertar_upsert(conn, "puntos_venta", ["punto_venta_id", "empresa_id"], filas_pv, "punto_venta_id")

    # --- asesores ---
    # activo viene como texto 'SI'/'NO' en el CSV, pero la columna en
    # Postgres es boolean -> convertir antes de insertar.
    asesores_df = asesores_df.copy()
    asesores_df["activo"] = asesores_df["activo"].astype(str).str.upper() == "SI"

    filas_asesores = list(
        asesores_df[[
            "asesor_id", "nombre", "punto_venta_id", "empresa_id",
            "capacidad_diaria_leads", "activo", "fecha_ingreso",
        ]].itertuples(index=False, name=None)
    )
    insertar_upsert(
        conn, "asesores",
        ["asesor_id", "nombre", "punto_venta_id", "empresa_id",
         "capacidad_diaria_leads", "activo", "fecha_ingreso"],
        filas_asesores, "asesor_id",
    )


# ============================================================
# 2. motos_catalogo, motos_disponibilidad  <- de catalogo_motos.csv
# ============================================================

def cargar_catalogo(conn):
    catalogo_df = cargar_catalogo_motos()

    # --- motos_catalogo ---
    filas_catalogo = list(
        catalogo_df[["sku", "marca", "linea", "cilindraje", "segmento", "precio_lista"]]
        .itertuples(index=False, name=None)
    )
    insertar_upsert(
        conn, "motos_catalogo",
        ["sku", "marca", "linea", "cilindraje", "segmento", "precio_lista"],
        filas_catalogo, "sku",
    )

    # --- motos_disponibilidad: explota "PV-001|PV-003" en filas separadas ---
    filas_disponibilidad = []
    for _, fila in catalogo_df.iterrows():
        puntos = str(fila["puntos_venta_disponibles"]).split("|")
        for pv in puntos:
            pv = pv.strip()
            if pv:
                filas_disponibilidad.append((fila["sku"], pv, fila["unidades_disponibles"]))

    # clave primaria compuesta (sku, punto_venta_id) -> ON CONFLICT necesita los 2 campos
    if filas_disponibilidad:
        query = text(
            "INSERT INTO motos_disponibilidad (sku, punto_venta_id, unidades_disponibles) "
            "VALUES (:sku, :punto_venta_id, :unidades_disponibles) "
            "ON CONFLICT (sku, punto_venta_id) DO NOTHING"
        )
        filas_dict = [
            {"sku": s, "punto_venta_id": p, "unidades_disponibles": u}
            for s, p, u in filas_disponibilidad
        ]
        conn.execute(query, filas_dict)
        print(f"  motos_disponibilidad: {len(filas_dict)} filas procesadas")


# ============================================================
# 3. clientes  <- de construir_clientes(leads_ok)
# ============================================================

def cargar_clientes(conn, leads_ok: pd.DataFrame) -> pd.DataFrame:
    """Devuelve el DataFrame de clientes CON el cliente_id que Postgres
    asignó, para poder resolver la FK al insertar leads después."""
    clientes_df = construir_clientes(leads_ok)

    filas_clientes = list(
        clientes_df[["telefono_norm", "empresa_id", "nombre_normalizado", "email", "ciudad_normalizada"]]
        .itertuples(index=False, name=None)
    )

    query = text(
        "INSERT INTO clientes (telefono_normalizado, empresa_id, nombre_normalizado, email, ciudad_normalizada) "
        "VALUES (:telefono_normalizado, :empresa_id, :nombre_normalizado, :email, :ciudad_normalizada) "
        "ON CONFLICT (telefono_normalizado, empresa_id) DO NOTHING"
    )
    filas_dict = [
        {
            "telefono_normalizado": t, "empresa_id": e,
            "nombre_normalizado": n, "email": em, "ciudad_normalizada": c,
        }
        for t, e, n, em, c in filas_clientes
    ]
    conn.execute(query, filas_dict)
    print(f"  clientes: {len(filas_dict)} filas procesadas")

    # Traer de vuelta los cliente_id ya asignados por Postgres (autoincrement),
    # para poder mapear cada lead a su cliente_id real.
    resultado = conn.execute(text(
        "SELECT cliente_id, telefono_normalizado, empresa_id FROM clientes"
    ))
    clientes_bd = pd.DataFrame(resultado.fetchall(), columns=["cliente_id", "telefono_norm", "empresa_id"])
    return clientes_bd


# ============================================================
# 4. leads  <- de leads_ok normalizado + match_catalogo, resolviendo cliente_id
# ============================================================

def cargar_leads(conn, leads_ok: pd.DataFrame, clientes_bd: pd.DataFrame):
    from pipeline.normalize import parsear_fecha

    # Mapear cada lead a su cliente_id real (recién insertado en Postgres)
    leads_con_cliente = leads_ok.merge(
        clientes_bd, on=["telefono_norm", "empresa_id"], how="left"
    )

    faltantes = leads_con_cliente["cliente_id"].isna().sum()
    if faltantes:
        print(f"  ADVERTENCIA: {faltantes} leads sin cliente_id resuelto, se omiten")
        leads_con_cliente = leads_con_cliente[leads_con_cliente["cliente_id"].notna()]

    # fecha_primer_contacto nunca se normalizó en normalize.py (solo
    # fecha_registro pasa por ahí) -> aplicar el mismo parser acá.
    def parsear_o_none(valor):
        if pd.isna(valor):
            return None
        fecha, _ = parsear_fecha(valor)
        return fecha

    leads_con_cliente["fecha_primer_contacto"] = (
        leads_con_cliente["fecha_primer_contacto"].apply(parsear_o_none)
    )

    columnas = [
        "lead_id", "cliente_id", "empresa_id", "punto_venta_id", "canal_norm",
        "fecha_registro_norm", "fecha_registro_supuesta", "modelo_interes_texto",
        "sku_matcheado", "estado_gestion_norm", "fecha_primer_contacto", "campania",
    ]
    df_insert = leads_con_cliente[columnas].rename(columns={
        "canal_norm": "canal",
        "fecha_registro_norm": "fecha_registro",
        "estado_gestion_norm": "estado_gestion",
    })

    # Convertir TODO el DataFrame a tipo object antes de limpiar NaN.
    # Necesario porque columnas tipadas (datetime64, float64) vuelven a
    # convertir None -> NaT/NaN al reconstruirse; con dtype object el
    # None se queda como None de verdad, y así psycopg2 lo manda como
    # NULL en vez de como el string "NaN" o el tipo numérico NaN.
    df_insert = df_insert.astype(object)
    df_insert = df_insert.where(df_insert.notna(), None)

    filas_dict = df_insert.to_dict(orient="records")

    query = text(
        "INSERT INTO leads (lead_id, cliente_id, empresa_id, punto_venta_id, canal, "
        "fecha_registro, fecha_registro_supuesta, modelo_interes_texto, sku_matcheado, "
        "estado_gestion, fecha_primer_contacto, campania) "
        "VALUES (:lead_id, :cliente_id, :empresa_id, :punto_venta_id, :canal, "
        ":fecha_registro, :fecha_registro_supuesta, :modelo_interes_texto, :sku_matcheado, "
        ":estado_gestion, :fecha_primer_contacto, :campania) "
        "ON CONFLICT (lead_id) DO NOTHING"
    )
    conn.execute(query, filas_dict)
    print(f"  leads: {len(filas_dict)} filas procesadas")


# ============================================================
# 5. lead_enriquecimiento  <- CSV de enrich_ai.py
# ============================================================

def cargar_enriquecimiento(conn, enriquecimiento_df: pd.DataFrame):
    df = enriquecimiento_df.copy()

    # Puede haber lead_id en el enriquecimiento que no existan en leads
    # (ej. conversación huérfana, o el lead quedó en cuarentena en
    # normalize.py) -> se descartan acá, no tiene sentido insertar
    # enriquecimiento de un lead que no está.
    leads_existentes = pd.read_sql(text("SELECT lead_id FROM leads"), conn)["lead_id"]
    antes = len(df)
    df = df[df["lead_id"].isin(leads_existentes)]
    descartados = antes - len(df)
    if descartados:
        print(f"  ADVERTENCIA: {descartados} filas de enriquecimiento sin lead_id correspondiente en leads, se omiten")

    columnas = [
        "lead_id", "conversacion_id", "modelo_interes_ia", "presupuesto_cuota_inicial",
        "forma_pago_declarada", "intencion_declarada", "objecion_principal", "pidio_cita",
    ]
    df = df[columnas].astype(object)
    df = df.where(df.notna(), None)

    query = text(
        "INSERT INTO lead_enriquecimiento (lead_id, conversacion_id, modelo_interes_ia, "
        "presupuesto_cuota_inicial, forma_pago_declarada, intencion_declarada, "
        "objecion_principal, pidio_cita) "
        "VALUES (:lead_id, :conversacion_id, :modelo_interes_ia, :presupuesto_cuota_inicial, "
        ":forma_pago_declarada, :intencion_declarada, :objecion_principal, :pidio_cita) "
        "ON CONFLICT (lead_id) DO NOTHING"
    )
    filas_dict = df.to_dict(orient="records")
    conn.execute(query, filas_dict)
    print(f"  lead_enriquecimiento: {len(filas_dict)} filas procesadas")


# ============================================================
# 6. lead_score  <- de scoring.py
# ============================================================

def cargar_scores(conn, leads_scoreados: pd.DataFrame):
    import json

    df = leads_scoreados[["lead_id", "score", "temperatura", "factores"]].copy()
    df["factores_json"] = df["factores"].apply(json.dumps)
    df["fecha_calculo"] = datetime.now()

    query = text(
        "INSERT INTO lead_score (lead_id, score, temperatura, factores_json, fecha_calculo) "
        "VALUES (:lead_id, :score, :temperatura, :factores_json, :fecha_calculo) "
        "ON CONFLICT (lead_id) DO NOTHING"
    )
    filas_dict = df[["lead_id", "score", "temperatura", "factores_json", "fecha_calculo"]].to_dict(orient="records")
    conn.execute(query, filas_dict)
    print(f"  lead_score: {len(filas_dict)} filas procesadas")


# ============================================================
# 7. asignaciones  <- de assign.py
# ============================================================

def cargar_asignaciones(conn, asignaciones_df: pd.DataFrame):
    df = asignaciones_df[asignaciones_df["asesor_id"].notna()].copy()
    df["fecha_asignacion"] = datetime.now()

    query = text(
        "INSERT INTO asignaciones (lead_id, asesor_id, fecha_asignacion) "
        "VALUES (:lead_id, :asesor_id, :fecha_asignacion) "
        "ON CONFLICT (lead_id) DO NOTHING"
    )
    filas_dict = df[["lead_id", "asesor_id", "fecha_asignacion"]].to_dict(orient="records")
    conn.execute(query, filas_dict)
    print(f"  asignaciones: {len(filas_dict)} filas procesadas")


# ============================================================
# 8. historico_cierres  <- carga directa
# ============================================================

def cargar_historico(conn):
    historico_df = cargar_historico_cierres()
    historico_df = historico_df.where(pd.notna(historico_df), None)

    columnas = [
        "lead_id", "fecha_registro", "canal", "empresa_id", "punto_venta_id",
        "modelo_cotizado", "precio_lista", "horas_al_primer_contacto",
        "numero_contactos", "manifesto_cuota_inicial", "forma_pago_declarada",
        "pidio_cita", "desenlace",
    ]
    query = text(
        "INSERT INTO historico_cierres (lead_id, fecha_registro, canal, empresa_id, punto_venta_id, "
        "modelo_cotizado, precio_lista, horas_al_primer_contacto, numero_contactos, "
        "manifesto_cuota_inicial, forma_pago_declarada, pidio_cita, desenlace) "
        "VALUES (:lead_id, :fecha_registro, :canal, :empresa_id, :punto_venta_id, "
        ":modelo_cotizado, :precio_lista, :horas_al_primer_contacto, :numero_contactos, "
        ":manifesto_cuota_inicial, :forma_pago_declarada, :pidio_cita, :desenlace) "
        "ON CONFLICT (lead_id) DO NOTHING"
    )
    filas_dict = historico_df[columnas].to_dict(orient="records")
    conn.execute(query, filas_dict)
    print(f"  historico_cierres: {len(filas_dict)} filas procesadas")


# ============================================================
# Orquestador principal
# ============================================================

def cargar_todo():
    print("=== Corriendo pipeline completo ===")
    leads_ok, leads_cuarentena = normalizar_leads()
    leads_ok = matchear_leads_contra_catalogo(leads_ok)
    enriquecimiento_df = pd.read_csv(RUTA_ENRIQUECIMIENTO, encoding="utf-8")
    leads_scoreados = calcular_score(leads_ok, enriquecimiento_df)
    asignaciones_df = asignar_leads(leads_scoreados)

    print(f"Leads normalizados: {len(leads_ok)} (cuarentena: {len(leads_cuarentena)})")
    print(f"Leads scoreados: {len(leads_scoreados)}")
    print(f"Asignaciones: {len(asignaciones_df)}")

    engine = create_engine(DATABASE_URL)
    with engine.begin() as conn:  # begin() = todo en una transacción, rollback si algo falla
        print("\n=== 1. empresas / puntos_venta / asesores ===")
        cargar_empresas_puntos_venta_asesores(conn)

        print("\n=== 2. catálogo ===")
        cargar_catalogo(conn)

        print("\n=== 3. clientes ===")
        clientes_bd = cargar_clientes(conn, leads_ok)

        print("\n=== 4. leads ===")
        cargar_leads(conn, leads_ok, clientes_bd)

        print("\n=== 5. lead_enriquecimiento ===")
        cargar_enriquecimiento(conn, enriquecimiento_df)

        print("\n=== 6. lead_score ===")
        cargar_scores(conn, leads_scoreados)

        print("\n=== 7. asignaciones ===")
        cargar_asignaciones(conn, asignaciones_df)

        print("\n=== 8. historico_cierres ===")
        cargar_historico(conn)

    engine.dispose()
    print("\n✔ Carga completa.")


if __name__ == "__main__":
    cargar_todo()