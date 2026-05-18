"""
Milking Intelligence — API
==========================
Arrancar: uvicorn main:app --reload
URL:      http://localhost:8000
Docs:     http://localhost:8000/docs
"""

import os
import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Milking Intelligence API", version="0.1.0")

# CORS — permite que el dashboard en Vercel consulte esta API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL no configurada")
    return psycopg2.connect(DATABASE_URL)

def query(sql, params=None):
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()
    finally:
        conn.close()

# ─── Health check ─────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok", "producto": "Milking Intelligence"}

# ─── Granjas ──────────────────────────────────────────────────────────────────

@app.get("/granjas")
def granjas():
    """Lista de granjas activas con último sync."""
    rows = query("""
        SELECT farm_id, name, last_sync, active
        FROM farms
        WHERE active = TRUE
        ORDER BY name
    """)
    return list(rows)

# ─── KPIs globales ────────────────────────────────────────────────────────────

@app.get("/kpis/{farm_id}")
def kpis(farm_id: str):
    """KPIs globales de una granja."""
    rows = query("""
        SELECT f.farm_id, f.name, f.last_sync,
               k.produccion_media, k.ordenos_dia,
               k.rechazos_dia, k.incompletos_dia, k.duracion_media_seg
        FROM farms f
        LEFT JOIN farm_kpis k ON f.farm_id = k.farm_id
        WHERE f.farm_id = %s
    """, (farm_id,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"Granja '{farm_id}' no encontrada")
    return rows[0]

# ─── Producción mensual ───────────────────────────────────────────────────────

@app.get("/produccion/{farm_id}")
def produccion(farm_id: str):
    """Serie temporal de producción mensual y anual de una granja."""
    mensual = query("""
        SELECT periodo, produccion, ordenos, rechazos, incompletos
        FROM produccion_mensual
        WHERE farm_id = %s
        ORDER BY periodo
    """, (farm_id,))

    anual = query("""
        SELECT LEFT(periodo, 4) AS year,
               ROUND(AVG(produccion)::numeric, 2) AS produccion,
               ROUND(AVG(ordenos)::numeric, 2) AS ordenos,
               ROUND(AVG(rechazos)::numeric, 2) AS rechazos,
               ROUND(AVG(incompletos)::numeric, 2) AS incompletos
        FROM produccion_mensual
        WHERE farm_id = %s AND produccion IS NOT NULL
        GROUP BY LEFT(periodo, 4)
        ORDER BY year
    """, (farm_id,))

    return {"mensual": list(mensual), "anual": list(anual)}

# ─── Vacas ────────────────────────────────────────────────────────────────────

@app.get("/vacas/{farm_id}")
def vacas(farm_id: str, prioridad: str = None):
    """
    Lista de vacas de una granja.
    Filtro opcional: ?prioridad=Alta | Media | Baja
    """
    if prioridad:
        rows = query("""
            SELECT * FROM vacas
            WHERE farm_id = %s AND prioridad = %s
            ORDER BY CASE WHEN prioridad='Alta' THEN 1 WHEN prioridad='Media' THEN 2 ELSE 3 END,
                     leche_diaria ASC
        """, (farm_id, prioridad))
    else:
        rows = query("""
            SELECT * FROM vacas
            WHERE farm_id = %s
            ORDER BY CASE WHEN prioridad='Alta' THEN 1 WHEN prioridad='Media' THEN 2 ELSE 3 END,
                     leche_diaria ASC
        """, (farm_id,))
    return list(rows)

# ─── Robots ───────────────────────────────────────────────────────────────────

@app.get("/robots/{farm_id}")
def robots(farm_id: str):
    """KPIs de robots de una granja."""
    rows = query("""
        SELECT * FROM robots_kpis
        WHERE farm_id = %s
        ORDER BY milking_device NULLS FIRST
    """, (farm_id,))
    return list(rows)

@app.get("/robots/{farm_id}/evolucion")
def robots_evolucion(farm_id: str):
    """Evolución diaria de litros por robot."""
    rows = query("""
        SELECT milking_device, fecha, litros
        FROM robots_evolucion
        WHERE farm_id = %s
        ORDER BY milking_device, fecha
    """, (farm_id,))
    return list(rows)

# ─── Benchmark ────────────────────────────────────────────────────────────────

@app.get("/benchmark")
def benchmark():
    """
    Benchmark completo entre granjas activas.
    Incluye scores por dimensión y puntuación global 0-100.
    """
    rows = query("""
        WITH base AS (
            SELECT f.farm_id, f.name,
                   k.produccion_media, k.ordenos_dia,
                   k.rechazos_dia, k.incompletos_dia,
                   -- Agregados de vacas
                   AVG(v.pct_kickoffs)        AS kickoffs_media,
                   AVG(v.pct_incompletos)     AS incompletos_vaca_media,
                   AVG(v.conductividad_media) AS conductividad_media,
                   AVG(v.celulas_media)       AS celulas_media,
                   AVG(v.grasa_media)         AS grasa_media,
                   AVG(v.proteina_media)      AS proteina_media,
                   AVG(v.leche_desviada)      AS leche_desviada_media,
                   AVG(v.adaptibility_score)  AS adaptibility_media,
                   COUNT(v.id)                AS num_vacas,
                   COUNT(CASE WHEN v.prioridad = 'Alta' THEN 1 END) AS vacas_alta
            FROM farms f
            JOIN farm_kpis k ON f.farm_id = k.farm_id
            LEFT JOIN vacas v ON f.farm_id = v.farm_id
            WHERE f.active = TRUE
            GROUP BY f.farm_id, f.name, k.produccion_media, k.ordenos_dia,
                     k.rechazos_dia, k.incompletos_dia
        ),
        ranks AS (
            SELECT *,
                PERCENT_RANK() OVER (ORDER BY produccion_media)  * 100 AS rank_produccion,
                PERCENT_RANK() OVER (ORDER BY ordenos_dia)       * 100 AS rank_ordenos,
                PERCENT_RANK() OVER (ORDER BY rechazos_dia DESC) * 100 AS rank_rechazos,
                PERCENT_RANK() OVER (ORDER BY incompletos_dia DESC) * 100 AS rank_incompletos,
                PERCENT_RANK() OVER (ORDER BY kickoffs_media DESC NULLS LAST) * 100 AS rank_kickoffs,
                PERCENT_RANK() OVER (ORDER BY celulas_media DESC NULLS LAST) * 100 AS rank_celulas
            FROM base
        )
        SELECT *,
            ROUND((rank_produccion * 0.35 +
                   rank_ordenos * 0.15 +
                   rank_rechazos * 0.15 +
                   rank_incompletos * 0.15 +
                   rank_kickoffs * 0.10 +
                   rank_celulas * 0.10)::numeric, 1) AS score_global
        FROM ranks
        ORDER BY score_global DESC
    """)
    return list(rows)
