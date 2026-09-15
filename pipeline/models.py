"""
models.py — Modelo de datos SQLAlchemy
Motos y Servicios de Colombia S.A.S.

La limpieza/normalización se hace en pandas antes de insertar
(ver pipeline/normalize.py). Los archivos fuente originales quedan
en data/raw/ como respaldo — no se persisten sucios en la base.
"""
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Numeric, Boolean, DateTime, Date,
    ForeignKey, CheckConstraint, UniqueConstraint, Text
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

# ============================================================
# CAPA CURADA
# ============================================================

ESTADOS_GESTION_VALIDOS = (
    "Contactado", "Sin gestión", "Cotización enviada",
    "En proceso", "No contesta", "Descartado",
)

CANALES_VALIDOS = ("WhatsApp", "Meta Ads", "Formulario Web")


class Empresa(Base):
    __tablename__ = "empresas"

    empresa_id = Column(String, primary_key=True)
    nombre = Column(String)

    puntos_venta = relationship("PuntoVenta", back_populates="empresa")


class PuntoVenta(Base):
    __tablename__ = "puntos_venta"

    punto_venta_id = Column(String, primary_key=True)
    empresa_id = Column(String, ForeignKey("empresas.empresa_id"), nullable=False)

    empresa = relationship("Empresa", back_populates="puntos_venta")


class Asesor(Base):
    __tablename__ = "asesores"

    asesor_id = Column(String, primary_key=True)
    nombre = Column(String, nullable=False)
    punto_venta_id = Column(String, ForeignKey("puntos_venta.punto_venta_id"), nullable=False)
    empresa_id = Column(String, ForeignKey("empresas.empresa_id"), nullable=False)
    capacidad_diaria_leads = Column(Integer, nullable=False)
    activo = Column(Boolean, nullable=False, default=True)
    fecha_ingreso = Column(Date)


class MotoCatalogo(Base):
    __tablename__ = "motos_catalogo"

    sku = Column(String, primary_key=True)
    marca = Column(String, nullable=False)
    linea = Column(String, nullable=False)
    cilindraje = Column(String)
    segmento = Column(String)
    precio_lista = Column(Numeric(12, 2))


class MotoDisponibilidad(Base):
    """Resuelve el campo 'PV-001|PV-003|...' del catálogo original."""
    __tablename__ = "motos_disponibilidad"

    sku = Column(String, ForeignKey("motos_catalogo.sku"), primary_key=True)
    punto_venta_id = Column(String, ForeignKey("puntos_venta.punto_venta_id"), primary_key=True)
    unidades_disponibles = Column(Integer, nullable=False, default=0)


class Cliente(Base):
    """
    Resultado de la deduplicación: dedup por teléfono normalizado,
    SIEMPRE dentro del scope de una misma empresa. Nunca se mergea
    entre empresas distintas (aislamiento estricto, condición 8).
    """
    __tablename__ = "clientes"

    cliente_id = Column(Integer, primary_key=True)
    nombre_normalizado = Column(Text)
    telefono_normalizado = Column(String, nullable=False)  # 10 dígitos, Colombia
    email = Column(String)
    ciudad_normalizada = Column(String)
    empresa_id = Column(String, ForeignKey("empresas.empresa_id"), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("telefono_normalizado", "empresa_id", name="uq_cliente_telefono_empresa"),
    )


class Lead(Base):
    __tablename__ = "leads"

    lead_id = Column(String, primary_key=True)
    cliente_id = Column(Integer, ForeignKey("clientes.cliente_id"), nullable=False)
    empresa_id = Column(String, ForeignKey("empresas.empresa_id"), nullable=False, index=True)
    punto_venta_id = Column(String, ForeignKey("puntos_venta.punto_venta_id"), nullable=False, index=True)
    canal = Column(String, nullable=False)
    fecha_registro = Column(DateTime, nullable=False)
    fecha_registro_supuesta = Column(Boolean, nullable=False, default=False)
    modelo_interes_texto = Column(Text)
    sku_matcheado = Column(String, ForeignKey("motos_catalogo.sku"), nullable=True)
    estado_gestion = Column(String, nullable=False, index=True)
    fecha_primer_contacto = Column(DateTime, nullable=True)
    campania = Column(String)

    __table_args__ = (
        CheckConstraint(f"canal IN {CANALES_VALIDOS}", name="ck_lead_canal_valido"),
        CheckConstraint(f"estado_gestion IN {ESTADOS_GESTION_VALIDOS}", name="ck_lead_estado_valido"),
    )


class LeadEnriquecimiento(Base):
    """
    Solo existe fila para leads con conversación de WhatsApp asociada
    (Meta Ads y Formulario Web nunca tienen conversación).
    Asumimos máx. 1 conversación por lead; si aparece más de una en
    los datos reales, se usa la más reciente (documentado en README).
    """
    __tablename__ = "lead_enriquecimiento"

    lead_id = Column(String, ForeignKey("leads.lead_id"), primary_key=True)
    conversacion_id = Column(String, nullable=False)
    modelo_interes_ia = Column(Text)
    presupuesto_cuota_inicial = Column(Numeric(12, 2))
    forma_pago_declarada = Column(String)  # contado / credito / no_informa
    intencion_declarada = Column(Text)
    objecion_principal = Column(Text)
    pidio_cita = Column(Boolean, nullable=False, default=False)
    confianza_extraccion = Column(Numeric(4, 3))  # opcional, 0-1


class LeadScore(Base):
    __tablename__ = "lead_score"

    lead_id = Column(String, ForeignKey("leads.lead_id"), primary_key=True)
    score = Column(Numeric(5, 2), nullable=False)
    temperatura = Column(String, nullable=False, index=True)  # Frío / Tibio / Caliente
    factores_json = Column(JSONB, nullable=False)  # qué pesó y cuánto, para explicabilidad
    fecha_calculo = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("score BETWEEN 0 AND 100", name="ck_score_rango"),
        CheckConstraint("temperatura IN ('Frío', 'Tibio', 'Caliente')", name="ck_temperatura_valida"),
    )


class Asignacion(Base):
    __tablename__ = "asignaciones"

    lead_id = Column(String, ForeignKey("leads.lead_id"), primary_key=True)
    asesor_id = Column(String, ForeignKey("asesores.asesor_id"), nullable=False, index=True)
    fecha_asignacion = Column(DateTime, default=datetime.utcnow, nullable=False)


class HistoricoCierre(Base):
    """
    Carga directa desde historico_cierres.csv.
    Solo se usa como referencia/calibración (scripts/validar_score.py),
    NO entra al flujo productivo del pipeline.
    """
    __tablename__ = "historico_cierres"

    lead_id = Column(String, primary_key=True)
    fecha_registro = Column(DateTime)
    canal = Column(String)
    empresa_id = Column(String)
    punto_venta_id = Column(String)
    modelo_cotizado = Column(String)
    precio_lista = Column(Numeric(12, 2))
    horas_al_primer_contacto = Column(Numeric(8, 2))
    numero_contactos = Column(Integer)
    manifesto_cuota_inicial = Column(String)  # SI / NO / NO_INFORMA
    forma_pago_declarada = Column(String)      # contado / credito / no_informa
    pidio_cita = Column(String)                # SI / NO
    desenlace = Column(String)                 # Cerrado / Perdido / Sin gestión