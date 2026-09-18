# Priorización de leads — Motos y Servicios de Colombia S.A.S.

Sistema que convierte los leads crudos de tres canales (WhatsApp, Meta Ads,
formulario web) en una lista diaria priorizada de gestión por asesor,
enriquecida con la información que hoy queda enterrada en las conversaciones
de WhatsApp.

**URL pública:** https://motos-leads-priorizacion-production.up.railway.app
**Swagger / documentación interactiva:** https://motos-leads-priorizacion-production.up.railway.app/docs

---

## El problema

Motos y Motores del Norte S.A.S. recibe más de 3.000 leads al mes entre sus
tres canales, y cierra menos del 10%. Los asesores gestionan por orden de
llegada, no por probabilidad de compra; 4 de cada 10 leads no se tocan en
las primeras 24 horas y ahí se pierden; y la conversación de WhatsApp tiene
información valiosa (modelo de interés, cuota inicial, forma de pago,
objeciones) que nunca llega al CRM.

Este sistema no reemplaza al CRM: es un servicio complementario que toma los
leads crudos más las conversaciones, calcula un score explicable por lead, y
expone por API la lista ya priorizada que le corresponde a cada asesor —
respetando en todo momento el aislamiento entre las tres comercializadoras
del grupo.

---

## Qué hace

1. **Ingesta** los 5 archivos fuente (`leads.csv`, `conversaciones.json`,
   `catalogo_motos.csv`, `asesores.csv`, `historico_cierres.csv`) sin
   intervención manual.
2. **Normaliza y consolida**: unifica formatos de teléfono, fecha, ciudad y
   canal; detecta y separa en cuarentena los registros irrecuperables;
   deduplica clientes (siempre dentro del scope de una misma empresa, nunca
   entre empresas distintas).
3. **Extrae con IA** la información estructurada de cada conversación de
   WhatsApp: modelo de interés, presupuesto/cuota inicial, forma de pago,
   intención declarada, objeción principal y si pidió cita.
4. **Calcula un score explicable** (0-100) para cada lead, con una
   regresión logística entrenada y calibrada contra 2.200 cierres
   históricos reales — no una fórmula inventada a ciegas.
5. **Asigna** cada lead a un asesor del punto de venta correspondiente, por
   capacidad relativa disponible (nunca cruza puntos de venta ni empresas).
6. **Persiste todo** en PostgreSQL, con un modelo de datos propio de 10
   tablas versionado con Alembic.
7. **Expone el resultado** por una API REST desplegada en producción
   (Railway), consumible por cada asesor como "mis leads de hoy".

Todo el proceso corre de punta a punta con un solo comando:

```bash
python -m pipeline.run_pipeline
```

---

## Arquitectura

```
5 archivos fuente
   │
   ▼
┌─────────────────────────────────────────────────────┐
│  Pipeline (Python)                                    │
│  ingest → normalize → match_catalogo → enrich_ai       │
│  (offline) → scoring → assign                          │
└─────────────────────────────────────────────────────┘
   │
   ▼
PostgreSQL (10 tablas, esquema Alembic — local y Railway)
   │
   ▼
API FastAPI (desplegada en Railway)
   │
   ▼
Asesor consulta su lista priorizada vía Swagger / API
```

Ver `docs/arquitectura.png` para el diagrama completo.


### Endpoints principales

| Método | Ruta | Qué hace |
|---|---|---|
| `GET` | `/leads/{asesor_id}` | Lista priorizada de un asesor, ordenada por score descendente |
| `GET` | `/leads/empresa/{empresa_id}` | Vista general de una empresa (demuestra el aislamiento), con filtro opcional `?temperatura=` |
| `POST` | `/pipeline/run` | Dispara el pipeline completo — ver limitación conocida más abajo |

---

## Cómo se ejecuta

### Local

```bash
# 1. Crear el entorno (conda) e instalar dependencias
conda env create -f environment.yml
conda activate motos-leads

# 2. Configurar variables de entorno
cp .env.example .env
# completar DATABASE_URL (Postgres local) y OPENAI_API_KEY

# 3. Aplicar el esquema de base de datos
alembic upgrade head

# 4. Colocar los 5 archivos fuente en data/raw/ (no se versionan en git)

# 5. Correr el pipeline completo (carga todo en Postgres)
python -m pipeline.run_pipeline

# 6. Levantar la API
uvicorn api.main:app --reload
```

La API queda disponible en `http://localhost:8000/docs`.

### Contra producción (Railway)

La API ya está desplegada y corriendo. Para reproducir el deploy desde cero:

1. Railway detecta el proyecto vía `requirements.txt` + `Procfile`
   (`web: uvicorn api.main:app --host 0.0.0.0 --port $PORT`), no vía
   `environment.yml` (conda), que es solo para uso local.
2. Variables de entorno del servicio de la API: `DATABASE_URL` (referencia
   al servicio Postgres del mismo proyecto Railway, host interno) y
   `OPENAI_API_KEY`.
3. Migraciones: `alembic upgrade head` contra la base de Railway.
4. Carga inicial: `python -m pipeline.run_pipeline` contra Railway (usa una
   sola transacción — un corte a mitad de camino hace rollback completo, es
   seguro pero hay que volver a correr todo).

---

## Decisiones de diseño

### Por qué una regresión logística validada, no una fórmula arbitraria

El score se calibró contra `historico_cierres.csv` (2.200 leads con
desenlace real conocido). Se identificaron las señales con mayor peso real
sobre la tasa de cierre — la más fuerte es el **tiempo transcurrido hasta el
primer contacto**: los leads contactados dentro de la primera hora cierran a
una tasa notablemente mayor que los contactados después de 48 horas. A eso
se suman tres señales extraídas por IA de la conversación (manifestó cuota
inicial, pidió cita, declaró forma de pago). Los cortes de temperatura
(Frío / Tibio / Caliente) se tomaron de los percentiles 25 y 75 reales del
score sobre el histórico, no de un umbral arbitrario.

Si un lead no tiene conversación de WhatsApp asociada (o la conversación no
aportó señal), esas tres señales valen 0 — el criterio es conservador: nunca
se infla el score con información que no existe.

### Por qué el campo `canal` no se corrige automáticamente

290 leads tienen canal declarado "Meta Ads" o "Formulario Web" en
`leads.csv`, pero tienen una conversación de WhatsApp real y coherente
asociada al mismo `lead_id`. Se evaluó (y se revirtió) una corrección
automática que sobreescribiera el canal a "WhatsApp" en esos casos. Se
decidió no aplicarla: es más honesto con los datos mostrar el canal tal cual
lo declara la fuente original, y documentar la inconsistencia como tal, en
vez de imponer una heurística de reescritura que el enunciado no pide.

### Por qué la asignación es por capacidad relativa, no absoluta

Cada lead se asigna al asesor con mayor **porcentaje** de capacidad libre
respecto a su propio techo diario, recalculado después de cada asignación
— no al que tenga menos leads en números absolutos. Esto evita que un
asesor con capacidad chica (por ejemplo 12 leads/día) se sature antes que
uno con capacidad grande (25 leads/día), aunque ambos reciban leads en el
mismo orden. El reparto es siempre local a un punto de venta: nunca cruza
puntos de venta ni empresas, y solo considera asesores activos.

### Deduplicación siempre dentro de una empresa

1.502 leads se consolidan en 1.451 clientes únicos, deduplicando por
(teléfono normalizado, empresa_id). La deduplicación **nunca** cruza el
límite de empresa — dos leads con el mismo teléfono en empresas distintas
son dos clientes distintos, porque fusionarlos violaría el aislamiento
estricto que exige el enunciado.

---

## Supuestos asumidos

- Una conversación de WhatsApp representa la intención más reciente y
  confiable del cliente; cuando el modelo de interés declarado en el
  formulario difiere del mencionado en la conversación, se guardan ambos
  por separado (ver "Qué haría con más tiempo").
- El aislamiento por empresa se implementa como filtro obligatorio por
  `empresa_id` en cada consulta del backend, en vez de Row-Level Security
  a nivel de Postgres — decisión de alcance por tiempo, no de diseño ideal
  (ver más abajo).
- La corrida de carga inicial del pipeline procesa los 1.502 leads del
  dataset de una sola vez. Como el dataset es una foto histórica de ~11
  meses corrida contra una capacidad de asesores pensada para un día real,
  la mayoría de los puntos de venta queda marcada `pv_sobrecargado=True` en
  esta corrida puntual — esperable dado que es carga inicial, no un error
  de la lógica de asignación (el campo existe justamente para hacer esto
  visible).

---

## Inconsistencias del dataset detectadas

Parte del ejercicio era detectarlas, no solo tolerarlas:

- **1 fila 100% corrupta** (`LD-01501`): `fecha_registro = 2026-08-33`, un
  día que no existe en ningún calendario. Se cuarentena y no entra al
  pipeline productivo, pero queda documentada, no borrada en silencio.
- **12 `lead_id` fantasma** en `conversaciones.json` (ej. `LD-98570`): sin
  lead correspondiente en `leads.csv`. Se descartan al insertar el
  enriquecimiento.
- **290 leads con canal contradictorio**: canal declarado Meta Ads/Web,
  pero con una conversación de WhatsApp real y coherente asociada al mismo
  `lead_id` (ver decisión de diseño arriba).
- **Cambios de intención reales, no ruido**: por ejemplo `LD-01255` declaró
  interés en "Hero Eco Deluxe 100" en el formulario, y en la conversación
  posterior pidió específicamente "AKT Dynamic R3 125" con cuota inicial —
  confirmado leyendo la conversación completa, es un cambio de intención
  genuino, no un dato sucio.

Cada una de estas se investigó con una consulta real contra los datos antes
de decidir qué hacer — nunca se asumió que algo "raro" fuera un error.

---

## Limitación conocida

**`POST /pipeline/run` en producción (Railway) va a fallar si se invoca.**
`data/raw/` y `data/processed/` nunca se versionan en git (son datos, no
código), así que no existen en el entorno de Railway. El endpoint fue
probado y funciona correctamente en local. Es una decisión de arquitectura
consciente, no un bug: el pipeline batch está pensado para correr en un
entorno con acceso a los archivos fuente (local, o un job/entorno separado
en un caso real), mientras que la API en producción sirve los resultados ya
procesados desde Postgres.

---

## Qué haría con más tiempo

- **Campo `modelo_interes_final`** resuelto con
  `COALESCE(modelo_interes_ia, modelo_interes_texto)`, para que el asesor
  tenga un único campo de qué ofrecer en vez de dos que a veces difieren.
- **Reparto incremental real entre corridas**: hoy `run_pipeline.py` no le
  pasa a la asignación las asignaciones ya existentes en la base antes de
  repartir los leads nuevos, así que cada corrida recalcula la carga de
  cada asesor desde cero. Se identificó el problema y se escribió la
  corrección (leer `asignaciones` antes de asignar y pasarla como
  contexto), pendiente de validar en profundidad y desplegar.
- **Señal de stock bajo** como variable adicional del score — hoy el
  catálogo y la disponibilidad por punto de venta se persisten pero no
  entran en el cálculo de prioridad.
- **Scheduler** (cron / APScheduler) para correr el pipeline
  automáticamente cada pocas horas sobre leads nuevos, en vez de disparo
  manual.
- **Frontend simple** (Next.js) para el asesor, más allá de Swagger — hoy
  no es necesario porque el enunciado admite una API consumible como
  entregable, pero mejoraría la usabilidad real.

---

## Estructura del repositorio

```
motos-leads-priorizacion/
├── Procfile
├── requirements.txt
├── environment.yml            (conda, uso local)
├── alembic.ini
├── alembic/versions/           esquema versionado (2 migraciones)
├── pipeline/
│   ├── ingest.py
│   ├── normalize.py
│   ├── match_catalogo.py
│   ├── enrich_ai.py
│   ├── scoring.py
│   ├── assign.py
│   ├── run_pipeline.py         orquestador end-to-end
│   └── models.py
├── api/
│   ├── database.py
│   ├── schemas.py
│   ├── main.py
│   └── routers/
│       ├── leads.py
│       └── pipeline.py
├── scripts/
│   ├── explorar_variables.py   análisis de variables vs. histórico
│   └── validar_score.py        validación del score por cuartiles
└── docs/
    └── arquitectura.png
```
