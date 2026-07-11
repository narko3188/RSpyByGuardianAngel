#!/usr/bin/env python3
"""
SerbiaTracker — OpenCellID Ingestion Script
Charge le CSV filtre Serbie dans la base de donnees
"""
import csv
import gzip
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("/root/serbia-tracker/data/cell_towers.db")
CSV_PATH = Path("/root/serbia-tracker/data/cell_towers/serbia_towers.csv.gz")
FULL_CSV = Path("/root/serbia-tracker/data/cell_towers/cell_towers_full.csv.gz")

def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cell_towers (
            source TEXT DEFAULT 'opencellid',
            radio TEXT, mcc INTEGER, mnc INTEGER,
            lac INTEGER, cell_id INTEGER, unit INTEGER DEFAULT 0,
            lon REAL, lat REAL, radius_km REAL DEFAULT 0,
            samples INTEGER DEFAULT 0, last_seen TEXT,
            altitude_m REAL, azimuth INTEGER,
            tx_power_dbm INTEGER, band TEXT,
            imported_at TEXT DEFAULT (datetime('now')),
            UNIQUE(source, mcc, mnc, lac, cell_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_op ON cell_towers(mcc,mnc,lac,cell_id)")

def ingest_csv(csv_path: Path, source_name: str = "opencellid"):
    """Import un CSV OpenCellID dans la DB"""
    conn = sqlite3.connect(str(DB_PATH))
    ensure_table(conn)
    
    opener = gzip.open if str(csv_path).endswith('.gz') else open
    count = 0
    serbia_count = 0
    total_rows = 0
    
    print(f"Ingestion: {csv_path.name} ({csv_path.stat().st_size / 1024 / 1024:.0f} MB)")
    
    with opener(csv_path, 'rt', encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        batch = []
        
        for row in reader:
            total_rows += 1
            
            mcc = int(row.get('mcc', 0))
            if mcc != 220:
                continue
            
            serbia_count += 1
            batch.append((
                source_name,
                row.get('radio', 'GSM'),
                220,
                int(row.get('net', row.get('mnc', 0))),
                int(row.get('area', 0)),
                int(row.get('cell', 0)),
                int(row.get('unit', 0)),
                float(row.get('lon', 0)),
                float(row.get('lat', 0)),
                float(row.get('range', 0)) / 1000,
                int(row.get('samples', 0)),
                row.get('updated', ''),
                None, None, None, None,
            ))
            
            if len(batch) >= 10000:
                conn.executemany(
                    """INSERT OR REPLACE INTO cell_towers 
                    (source,radio,mcc,mnc,lac,cell_id,unit,lon,lat,radius_km,samples,last_seen,altitude_m,azimuth,tx_power_dbm,band)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    batch
                )
                conn.commit()
                count += len(batch)
                print(f"   {count:,} antennes Serbie / {total_rows:,} lignes...")
                batch = []
        
        if batch:
            conn.executemany(
                """INSERT OR REPLACE INTO cell_towers 
                (source,radio,mcc,mnc,lac,cell_id,unit,lon,lat,radius_km,samples,last_seen,altitude_m,azimuth,tx_power_dbm,band)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                batch
            )
            conn.commit()
            count += len(batch)
    
    conn.close()
    
    return count, serbia_count, total_rows


def show_stats():
    conn = sqlite3.connect(str(DB_PATH))
    
    total = conn.execute("SELECT COUNT(*) FROM cell_towers").fetchone()[0]
    by_source = conn.execute(
        "SELECT source, COUNT(*) FROM cell_towers GROUP BY source ORDER BY COUNT(*) DESC"
    ).fetchall()
    by_mnc = conn.execute(
        "SELECT mnc, COUNT(*), AVG(samples) FROM cell_towers WHERE mcc=220 GROUP BY mnc ORDER BY COUNT(*) DESC"
    ).fetchall()
    
    op_names = {'1':'Yettel','3':'mt:s','5':'A1','7':'Orion','11':'Mundio'}
    
    print(f"\n{'='*60}")
    print(f"  BASE DE DONNÉES ANTENNES — SERBIE (MCC 220)")
    print(f"{'='*60}")
    print(f"  Total: {total:,} antennes")
    print(f"\n  Par source:")
    for src, cnt in by_source:
        pct = cnt / total * 100 if total else 0
        print(f"    {src:20s}: {cnt:>8,} ({pct:.1f}%)")
    print(f"\n  Par opérateur:")
    for mnc, cnt, avg_samp in by_mnc:
        name = op_names.get(str(mnc), f'MNC {mnc}')
        print(f"    {name:20s}: {cnt:>8,} antennes | {avg_samp:.0f} éch./tour")
    print(f"{'='*60}")
    
    conn.close()


if __name__ == "__main__":
    print("SerbiaTracker — OpenCellID Ingestion")
    print()
    
    if CSV_PATH.exists():
        print(f"Fichier Serbie trouvé: {CSV_PATH.stat().st_size / 1024 / 1024:.0f} MB")
        count, serb, total = ingest_csv(CSV_PATH, "opencellid")
        print(f"\n✅ {count:,} antennes Serbie importées ({serb:,} trouvées / {total:,} mondiales)")
    elif FULL_CSV.exists():
        print(f"Fichier complet trouvé: {FULL_CSV.stat().st_size / 1024 / 1024:.0f} MB")
        print("Filtrage Serbie en cours...")
        count, serb, total = ingest_csv(FULL_CSV, "opencellid")
        print(f"\n✅ {count:,} antennes Serbie importées ({serb:,} trouvées / {total:,} mondiales)")
    else:
        print("Aucun CSV trouvé. Utilisez download_serbia_towers.py d'abord.")
        print("Données built-in déjà chargées (90 antennes).")
    
    show_stats()
