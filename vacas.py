import pyodbc
import pandas as pd
import json
import math

conn = pyodbc.connect(
    "DRIVER={SQL Server};"
    "SERVER=localhost\\SQLEXPRESS;"
    "DATABASE=DelPro_Terri;"
    "Trusted_Connection=yes;"
)

sql = """
WITH Periodo AS (
    SELECT
        DATEADD(day, -30, CAST(MAX([DayDate]) AS date)) AS FechaInicio,
        CAST(MAX([DayDate]) AS date) AS FechaFinNoIncluida
    FROM dbo.HistoryAnimalDailyData
),
Base AS (
    SELECT
        COALESCE(h.[BasicAnimal], h.[Animal]) AS AnimalOID,
        h.[DayDate], h.[Group], h.[LactationNumber], h.[ReproStatus],
        h.[DIM], h.[DailyYield], h.[RelativeYield], h.[Milkings],
        h.[Kickoffs], h.[Incompletes], h.[AvgConductivity],
        h.[AvgCellCount], h.[AvgFat], h.[AvgProtein],
        h.[TotalConsumed], h.[DMConsumed]
    FROM dbo.HistoryAnimalDailyData AS h
    CROSS JOIN Periodo AS p
    WHERE h.[DayDate] >= p.FechaInicio
      AND h.[DayDate] < p.FechaFinNoIncluida
      AND COALESCE(h.[BasicAnimal], h.[Animal]) IS NOT NULL
),
UltimoDato AS (
    SELECT AnimalOID, [Group], [LactationNumber], [ReproStatus], [DIM]
    FROM (
        SELECT b.*, ROW_NUMBER() OVER (
            PARTITION BY b.[AnimalOID] ORDER BY b.[DayDate] DESC
        ) AS rn FROM Base AS b
    ) AS x WHERE x.rn = 1
),
Agregado AS (
    SELECT
        b.[AnimalOID],
        COUNT(*) AS DiasConDato,
        CAST(SUM(ISNULL(b.[DailyYield],0)) AS decimal(10,2)) AS LecheTotalPeriodo,
        CAST(AVG(b.[RelativeYield]) AS decimal(10,2)) AS RelativeYieldMedio,
        CAST(AVG(CAST(b.[Milkings] AS float)) AS decimal(10,2)) AS OrdenosDiariosMedia,
        CAST(100.0*SUM(ISNULL(b.[Kickoffs],0))/NULLIF(SUM(ISNULL(b.[Milkings],0)),0) AS decimal(10,2)) AS PctKickoffs,
        CAST(100.0*SUM(ISNULL(b.[Incompletes],0))/NULLIF(SUM(ISNULL(b.[Milkings],0)),0) AS decimal(10,2)) AS PctIncompletos,
        CAST(AVG(b.[AvgConductivity]) AS decimal(10,2)) AS ConductividadMedia,
        CAST(AVG(b.[AvgCellCount]) AS decimal(10,2)) AS CelulasMedia,
        CAST(AVG(b.[AvgFat]) AS decimal(10,2)) AS GrasaMedia,
        CAST(AVG(b.[AvgProtein]) AS decimal(10,2)) AS ProteinaMedia,
        CAST(SUM(ISNULL(b.[TotalConsumed],0)) AS decimal(10,2)) AS ConsumoTotal,
        CAST(SUM(ISNULL(b.[DMConsumed],0)) AS decimal(10,2)) AS MateriaSecaTotal
    FROM Base AS b GROUP BY b.[AnimalOID]
),
RobotScore AS (
    SELECT v.[Animal] AS AnimalOID,
        CAST(AVG(CAST([AdaptibilityScore] AS float)) AS decimal(10,2)) AS AdaptibilityScore,
        CAST(AVG([DelProPlusRank]) AS decimal(10,2)) AS DelProPlusRank
    FROM dbo.VMPAnimalRobotScore AS v
    CROSS JOIN Periodo AS p
    WHERE v.[DateAndTime] >= p.FechaInicio
      AND v.[DateAndTime] < p.FechaFinNoIncluida
      AND v.[Animal] IS NOT NULL
    GROUP BY v.[Animal]
),
LecheDesviada AS (
    SELECT hmdi.[Animal] AS AnimalOID,
        CAST(SUM(ISNULL(hmdi.[DivertedMilk],0)) AS decimal(10,2)) AS LecheDesviada
    FROM dbo.HistoryMilkDiversionInfo AS hmdi
    CROSS JOIN Periodo AS p
    WHERE hmdi.[DivertDate] >= p.FechaInicio
      AND hmdi.[DivertDate] < p.FechaFinNoIncluida
      AND hmdi.[Animal] IS NOT NULL
    GROUP BY hmdi.[Animal]
),
Final AS (
    SELECT
        ba.[OID] AS AnimalOID,
        ba.[Number] AS Numero,
        ba.[Name] AS Nombre,
        ba.[OfficialRegNo] AS Registro,
        p.FechaInicio, DATEADD(day,-1,p.FechaFinNoIncluida) AS FechaFin,
        a.DiasConDato,
        u.[Group] AS Grupo,
        u.[LactationNumber] AS NumLactacion,
        u.[DIM] AS DiasEnLeche,
        CAST(a.LecheTotalPeriodo/NULLIF(DATEDIFF(day,p.FechaInicio,p.FechaFinNoIncluida),0) AS decimal(10,2)) AS LecheDiaria,
        a.RelativeYieldMedio,
        a.OrdenosDiariosMedia,
        a.PctKickoffs,
        a.PctIncompletos,
        a.ConductividadMedia,
        a.CelulasMedia,
        a.GrasaMedia,
        a.ProteinaMedia,
        CAST(a.LecheTotalPeriodo/NULLIF(a.MateriaSecaTotal,0) AS decimal(10,2)) AS EficienciaMS,
        ISNULL(ld.LecheDesviada,0) AS LecheDesviada,
        rs.AdaptibilityScore,
        rs.DelProPlusRank,
        CASE
            WHEN a.DiasConDato < 20 THEN 'Media'
            WHEN a.RelativeYieldMedio < 80 THEN 'Alta'
            WHEN a.PctIncompletos >= 10 THEN 'Alta'
            WHEN a.PctKickoffs >= 10 THEN 'Alta'
            WHEN a.CelulasMedia >= 300 THEN 'Alta'
            WHEN ISNULL(ld.LecheDesviada,0) > 0 THEN 'Media'
            WHEN rs.DelProPlusRank IS NOT NULL AND rs.DelProPlusRank < 70 THEN 'Media'
            ELSE 'Baja'
        END AS Prioridad,
        CONCAT(
            CASE WHEN a.DiasConDato < 20 THEN 'Pocos dias con dato; ' ELSE '' END,
            CASE WHEN a.RelativeYieldMedio < 80 THEN 'Rendimiento bajo; ' ELSE '' END,
            CASE WHEN a.PctIncompletos >= 10 THEN 'Ordenos incompletos; ' ELSE '' END,
            CASE WHEN a.PctKickoffs >= 10 THEN 'Kickoffs altos; ' ELSE '' END,
            CASE WHEN a.CelulasMedia >= 300 THEN 'Celulas altas; ' ELSE '' END,
            CASE WHEN ISNULL(ld.LecheDesviada,0) > 0 THEN 'Leche desviada; ' ELSE '' END,
            CASE WHEN rs.DelProPlusRank IS NOT NULL AND rs.DelProPlusRank < 70 THEN 'Score robot bajo; ' ELSE '' END
        ) AS Motivo
    FROM Agregado AS a
    LEFT JOIN UltimoDato AS u ON a.AnimalOID = u.AnimalOID
    LEFT JOIN dbo.BasicAnimal AS ba ON a.AnimalOID = ba.[OID]
    LEFT JOIN RobotScore AS rs ON a.AnimalOID = rs.AnimalOID
    LEFT JOIN LecheDesviada AS ld ON a.AnimalOID = ld.AnimalOID
    CROSS JOIN Periodo AS p
    WHERE ba.[Number] NOT IN (0, 999999)
)
SELECT * FROM Final
ORDER BY
    CASE WHEN Prioridad='Alta' THEN 1 WHEN Prioridad='Media' THEN 2 ELSE 3 END,
    LecheDiaria ASC
"""

df = pd.read_sql(sql, conn)
conn.close()

print(f"Vacas analizadas: {len(df)}")
print(f"\nPor prioridad:")
print(df['Prioridad'].value_counts())

def limpiar(obj):
    if isinstance(obj, float) and math.isnan(obj):
        return None
    if isinstance(obj, dict):
        return {k: limpiar(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [limpiar(i) for i in obj]
    return obj

vacas = df.where(pd.notnull(df), None).to_dict(orient='records')
vacas = limpiar(vacas)

with open('C:\\DelPro\\vacas_terri.json', 'w', encoding='utf-8') as f:
    json.dump(vacas, f, ensure_ascii=False, indent=2, default=str)

print(f"\nExportado: vacas_terri.json")
