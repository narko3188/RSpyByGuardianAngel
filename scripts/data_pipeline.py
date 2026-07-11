#!/usr/bin/env python3
"""
SerbiaTracker — Data Pipeline v1
Collecte automatique multi-source de donnees d'antennes pour la Serbie

Sources:
1. OpenCellID — bulk download (gratuit, necessite token)
2. Tower Collector API — crowdsourcing Android
3. CellMapper — scraping web (donnees communautaires)
4. Donnees RATEL — couverture officielle
"""
import os
import sys
import gzip
import csv
import json
import time
import sqlite3
from pathlib import Path
from datetime import datetime

DATA_DIR = Path("/root/serbia-tracker/data")
DB_PATH = DATA_DIR / "cell_towers.db"
TOWERS_CSV = DATA_DIR / "cell_towers" / "serbia_towers.csv.gz"

SERBIA_MCC = 220

class DataPipeline:
    """Pipeline de collecte et fusion de donnees antennes"""
    
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_PATH))
        self._init_db()
    
    def _init_db(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS cell_towers (
                source TEXT,
                radio TEXT, mcc INTEGER, mnc INTEGER,
                lac INTEGER, cell_id INTEGER, unit INTEGER,
                lon REAL, lat REAL, radius_km REAL,
                samples INTEGER, last_seen TEXT,
                altitude_m REAL, azimuth INTEGER,
                tx_power_dbm INTEGER, band TEXT,
                imported_at TEXT DEFAULT (datetime('now')),
                UNIQUE(source, mcc, mnc, lac, cell_id)
            )
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_towers_op 
            ON cell_towers(mcc, mnc, lac, cell_id)
        """)
        self.conn.commit()
    
    # ----- SOURCE 1: OpenCellID -----
    def ingest_opencellid(self, csv_path=None):
        """Import du CSV OpenCellID (filtre Serbie)"""
        path = csv_path or TOWERS_CSV
        if not Path(path).exists():
            print(f"❌ Fichier introuvable: {path}")
            return 0
        
        print(f"[1/4] Import OpenCellID: {path}")
        count = 0
        opener = gzip.open if str(path).endswith('.gz') else open
        
        with opener(path, 'rt', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            batch = []
            
            for row in reader:
                mcc = int(row.get('mcc', 0))
                if mcc != SERBIA_MCC:
                    continue
                
                batch.append((
                    'opencellid',
                    row.get('radio', 'GSM'),
                    mcc,
                    int(row.get('net', row.get('mnc', 0))),
                    int(row.get('area', 0)),
                    int(row.get('cell', 0)),
                    int(row.get('unit', 0)),
                    float(row.get('lon', 0)),
                    float(row.get('lat', 0)),
                    float(row.get('range', 0)) / 1000,
                    int(row.get('samples', 0)),
                    datetime.utcfromtimestamp(int(row.get('updated', 0))).isoformat() if row.get('updated') else None,
                    None, None, None, None,
                ))
                
                if len(batch) >= 5000:
                    self._insert_batch(batch)
                    count += len(batch)
                    batch = []
                    print(f"   {count} antennes...")
            
            if batch:
                self._insert_batch(batch)
                count += len(batch)
        
        print(f"   ✅ {count} antennes OpenCellID importees")
        return count
    
    # ----- SOURCE 2: Données Yettel / A1 / mt:s intégrées -----
    def ingest_builtin_infrastructure(self):
        """Import des antennes intégrées dans le code"""
        from services.yettel_infrastructure import YETTEL_ALL_TOWERS
        from services.a1_infrastructure import A1_ALL_TOWERS
        from services.mts_infrastructure import MTS_ALL_TOWERS
        
        sources = [
            ('yettel_builtin', YETTEL_ALL_TOWERS, 1),
            ('a1_builtin', A1_ALL_TOWERS, 5),
            ('mts_builtin', MTS_ALL_TOWERS, 3),
        ]
        
        total = 0
        for source_name, towers, mnc in sources:
            print(f"[2/4] Import {source_name}: {len(towers)} antennes")
            batch = []
            
            for t in towers:
                batch.append((
                    source_name,
                    t[4] if len(t) > 4 else 'LTE',
                    SERBIA_MCC,
                    mnc,
                    t[2] if len(t) > 2 else 0,  # lac
                    t[3] if len(t) > 3 else 0,  # cell_id
                    0,
                    t[1], t[0],  # lon, lat
                    2.0,  # radius_km estime
                    100,
                    datetime.now().isoformat(),
                    t[5] if len(t) > 5 else None,  # altitude
                    t[6] if len(t) > 6 else None,  # azimuth
                    t[7] if len(t) > 7 else 43,    # tx_power
                    None,
                ))
            
            self._insert_batch(batch)
            total += len(batch)
        
        print(f"   ✅ {total} antennes built-in importees")
        return total
    
    # ----- SOURCE 3: Tower Collector (Android crowdsourcing) -----
    def ingest_tower_collector(self, csv_path=None):
        """Import CSV Tower Collector (format: mcc,mnc,lac,cell_id,lon,lat,...)"""
        if not csv_path or not Path(csv_path).exists():
            print(f"[3/4] Tower Collector: pas de fichier")
            return 0
        
        print(f"[3/4] Import Tower Collector: {csv_path}")
        count = 0
        
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            batch = []
            
            for row in reader:
                mcc = int(row.get('mcc', 0))
                if mcc != SERBIA_MCC:
                    continue
                
                batch.append((
                    'tower_collector',
                    row.get('radio', 'LTE'),
                    mcc,
                    int(row.get('mnc', 0)),
                    int(row.get('lac', 0)),
                    int(row.get('cell_id', 0)),
                    0,
                    float(row.get('lon', 0)),
                    float(row.get('lat', 0)),
                    float(row.get('range', 1000)) / 1000,
                    int(row.get('samples', 1)),
                    row.get('timestamp', datetime.now().isoformat()),
                    float(row.get('altitude', 0)) if row.get('altitude') else None,
                    None, None, row.get('band'),
                ))
                
                if len(batch) >= 1000:
                    self._insert_batch(batch)
                    count += len(batch)
                    batch = []
            
            if batch:
                self._insert_batch(batch)
                count += len(batch)
        
        print(f"   ✅ {count} antennes Tower Collector importees")
        return count
    
    # ----- STATS -----
    def print_stats(self):
        """Afficher les statistiques de la base"""
        print(f"\n[4/4] STATISTIQUES BASE ANTENNES:")
        
        total = self.conn.execute("SELECT COUNT(*) FROM cell_towers").fetchone()[0]
        print(f"   Total: {total:,} antennes")
        
        by_source = self.conn.execute(
            "SELECT source, COUNT(*) FROM cell_towers GROUP BY source ORDER BY COUNT(*) DESC"
        ).fetchall()
        print(f"   Par source:")
        for src, cnt in by_source:
            pct = cnt / total * 100 if total else 0
            print(f"     {src:25s}: {cnt:6,} ({pct:.1f}%)")
        
        by_operator = self.conn.execute(
            "SELECT mnc, COUNT(*), AVG(samples) FROM cell_towers WHERE mcc=220 GROUP BY mnc ORDER BY COUNT(*) DESC"
        ).fetchall()
        op_names = {'1':'Yettel','3':'mt:s','5':'A1','7':'Orion','11':'Mundio'}
        print(f"   Par operateur:")
        for mnc, cnt, avg_samp in by_operator:
            name = op_names.get(str(mnc), f'MNC {mnc}')
            print(f"     {name:25s}: {cnt:6,} antennes | {avg_samp:.0f} echantillons/antenne")
    
    def _insert_batch(self, batch):
        self.conn.executemany(
            """INSERT OR REPLACE INTO cell_towers 
            (source, radio, mcc, mnc, lac, cell_id, unit, lon, lat, radius_km, samples, last_seen, altitude_m, azimuth, tx_power_dbm, band)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            batch
        )
        self.conn.commit()
    
    def close(self):
        self.conn.close()


def main():
    pipeline = DataPipeline()
    
    # 1. OpenCellID (si dispo)
    pipeline.ingest_opencellid()
    
    # 2. Données intégrées (Yettel/A1/mt:s)
    pipeline.ingest_builtin_infrastructure()
    
    # 3. Tower Collector (si dispo)
    tc_path = DATA_DIR / "cell_towers" / "tower_collector_export.csv"
    if tc_path.exists():
        pipeline.ingest_tower_collector(str(tc_path))
    
    # 4. Stats
    pipeline.print_stats()
    
    pipeline.close()
    print("\n✅ Pipeline terminé")


if __name__ == "__main__":
    main()
