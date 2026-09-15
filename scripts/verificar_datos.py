import pandas as pd
import re

df = pd.read_csv('data/raw/leads.csv', encoding='utf-8')

# ============================================================
# 1. Fila basura
# ============================================================
print('=== 1. BUSCANDO FILA(S) BASURA ===')
sospechosas = df[
    df['telefono'].astype(str).str.replace(r'\D', '', regex=True).str.len() < 8
]
print(f'Filas con teléfono de menos de 8 dígitos: {len(sospechosas)}')
print(sospechosas[['lead_id', 'nombre_cliente', 'telefono', 'fecha_registro', 'canal']])
print()

def fecha_invalida(f):
    try:
        pd.to_datetime(str(f), errors='raise', dayfirst=True)
        return False
    except Exception:
        return True

df['fecha_parseable'] = df['fecha_registro'].apply(fecha_invalida)
print(f'Filas con fecha_registro no parseable de ninguna forma: {df["fecha_parseable"].sum()}')
print(df[df['fecha_parseable']][['lead_id', 'nombre_cliente', 'fecha_registro', 'telefono', 'canal']])
print()

# ============================================================
# 2. Duplicados por teléfono normalizado
# ============================================================
print('=== 2. DUPLICADOS POR TELEFONO NORMALIZADO ===')
def normalizar_telefono(t):
    digitos = re.sub(r'\D', '', str(t))
    if digitos.startswith('57') and len(digitos) == 12:
        digitos = digitos[2:]
    return digitos

df['telefono_norm'] = df['telefono'].apply(normalizar_telefono)

conteo = df['telefono_norm'].value_counts()
repetidos = conteo[conteo > 1]
print(f'Teléfonos normalizados que se repiten: {len(repetidos)}')
print(f'Total de filas involucradas: {repetidos.sum()}')
print()

mismo_tel = df[df['telefono_norm'].isin(repetidos.index)]
grupos = mismo_tel.groupby('telefono_norm')['empresa_id'].nunique()
mismo_tel_misma_empresa = (grupos == 1).sum()
mismo_tel_distinta_empresa = (grupos > 1).sum()
print(f'Grupos de teléfono repetido con MISMA empresa (dedup real): {mismo_tel_misma_empresa}')
print(f'Grupos de teléfono repetido con EMPRESA DISTINTA (no dedup): {mismo_tel_distinta_empresa}')
print()

# ============================================================
# 3. Ambigüedad de fechas (formato DD/MM vs MM/DD con separador / o -)
# ============================================================
print('=== 3. AMBIGUEDAD DE FECHAS (separador / o -, sin T ni espacio ISO) ===')
patron_ambiguo = df['fecha_registro'].astype(str).str.match(r'^\d{1,2}[/-]\d{1,2}[/-]\d{4}')
subset = df[patron_ambiguo].copy()
print(f'Filas con formato DD/MM/YYYY o similar (candidatas a ambigüedad): {len(subset)}')

def clasificar(f):
    m = re.match(r'^(\d{1,2})[/-](\d{1,2})[/-]\d{4}', str(f))
    if not m:
        return 'no_aplica'
    a, b = int(m.group(1)), int(m.group(2))
    if a > 12 and b <= 12:
        return 'confirma_DDMM'
    elif b > 12 and a <= 12:
        return 'confirma_MMDD'
    elif a <= 12 and b <= 12:
        return 'ambiguo'
    else:
        return 'invalido'

subset['clasificacion'] = subset['fecha_registro'].apply(clasificar)
print(subset['clasificacion'].value_counts())