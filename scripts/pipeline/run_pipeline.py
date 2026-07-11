#!/usr/bin/env python3
"""
SerbiaTracker — Pipeline Orchestrator
Lance tous les collecteurs en sequence et remplit la base unifiee
"""
import sys
import asyncio
import time
import sqlite3
from pathlib import Path

# Ajouter le backend au path pour les imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

DATA_DIR = Path(__file__).parent.parent.parent / "data"
DB_PATH = DATA_DIR / "cell_towers.db"


async def run_pipeline():
    print("=" * 60)
    print("  SERBIATRACKER — PIPELINE DE DONNÉES ANTENNES")
    print("=" * 60)
    
    results = {}
    
    # 1. OpenCellID (bulk + streaming)
    print("\n[1/3] OpenCellID Collector...")
    try:
        from collectors.opencellid_collector import OpenCellIDCollector
        collector = OpenCellIDCollector()
        await collector._ensure_schema()
        # Stream all Serbia cells
        count = 0
        async for batch in collector.stream_all_serbia(batch_size=5000):
            await collector.ingest_to_db(batch)
            count += len(batch)
        results['opencellid'] = count
        print(f"   ✅ {count} antennes OpenCellID")
    except Exception as e:
        print(f"   ⚠️ OpenCellID: {e}")
        results['opencellid'] = 0
    
    # 2. CellMapper
    print("\n[2/3] CellMapper Collector...")
    try:
        from collectors.cellmapper_collector import CellMapperCollector
        collector = CellMapperCollector()
        await collector._ensure_schema()
        count = 0
        # Stream for each operator MNC
        for mnc in [1, 3, 5]:  # Yettel, mt:s, A1
            towers = await collector.scrape_map_page(mnc)
            if towers:
                await collector.ingest_to_db(towers)
                count += len(towers)
        results['cellmapper'] = count
        print(f"   ✅ {count} antennes CellMapper")
    except Exception as e:
        print(f"   ⚠️ CellMapper: {e}")
        results['cellmapper'] = 0
    
    # 3. RATEL (régulateur serbe)
    print("\n[3/3] RATEL Collector...")
    try:
        from collectors.ratel_collector import RATELCollector
        collector = RATELCollector()
        await collector._ensure_schema()
        # Search for datasets
        datasets = await collector.search_ckan_datasets()
        count = 0
        for ds in datasets[:5]:  # Top 5 datasets
            resources = await collector.get_dataset_resources(ds.get('id', ''))
            for res in resources[:3]:  # Top 3 resources per dataset
                url = res.get('url', '')
                if url and url.endswith('.csv'):
                    dest = Path(f"/tmp/ratel_{ds.get('id','unknown')}.csv")
                    if await collector.download_resource(url, dest):
                        n = await collector.ingest_csv(dest, f"ratel_{ds.get('id','unknown')}")
                        count += n
        results['ratel'] = count
        print(f"   ✅ {count} antennes RATEL")
    except Exception as e:
        print(f"   ⚠️ RATEL: {e}")
        results['ratel'] = 0
    
    # 4. Built-in infrastructure
    print("\n[4/4] Built-in Infrastructure...")
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))
        from services.yettel_infrastructure import YETTEL_ALL_TOWERS
        from services.a1_infrastructure import A1_ALL_TOWERS
        from services.mts_infrastructure import MTS_ALL_TOWERS
        
        import sqlite3
        db = Path(__file__).parent.parent.parent / "data" / "cell_towers.db"
        conn = sqlite3.connect(str(db))
        
        # Ensure table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cell_towers (
                source TEXT, radio TEXT, mcc INTEGER, mnc INTEGER,
                lac INTEGER, cell_id INTEGER, unit INTEGER DEFAULT 0,
                lon REAL, lat REAL, radius_km REAL DEFAULT 2.0,
                samples INTEGER DEFAULT 100, last_seen TEXT,
                altitude_m REAL, azimuth INTEGER,
                tx_power_dbm INTEGER, band TEXT,
                imported_at TEXT DEFAULT (datetime('now')),
                UNIQUE(source, mcc, mnc, lac, cell_id)
            )
        """)
        
        total_builtin = 0
        for name, towers, mnc in [('yettel', YETTEL_ALL_TOWERS, 1), ('a1', A1_ALL_TOWERS, 5), ('mts', MTS_ALL_TOWERS, 3)]:
            for t in towers:
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO cell_towers 
                        (source, radio, mcc, mnc, lac, cell_id, lon, lat, radius_km, samples, last_seen, altitude_m, azimuth, tx_power_dbm)
                        VALUES (?,?,220,?,?,?,?,?,2.0,100,datetime('now'),?,?,?)""",
                        (name, t[4] if len(t)>4 else 'LTE', mnc, t[2] if len(t)>2 else 0, 
                         t[3] if len(t)>3 else 0, t[1], t[0],
                         t[5] if len(t)>5 else None,
                         t[6] if len(t)>6 else None,
                         t[7] if len(t)>7 else 43)
                    )
                    total_builtin += 1
                except Exception as e:
                    pass
        conn.commit()
        
        # Stats
        total = conn.execute("SELECT COUNT(*) FROM cell_towers").fetchone()[0]
        by_src = conn.execute("SELECT source, COUNT(*) FROM cell_towers GROUP BY source").fetchall()
        conn.close()
        
        results['builtin'] = total_builtin
        print(f"   ✅ {total_builtin} antennes built-in")
        print(f"   Total DB: {total} antennes")
        for src, cnt in by_src:
            print(f"     {src}: {cnt}")
    except Exception as e:
        print(f"   ⚠️ Built-in: {e}")
        results['builtin'] = 0
    
    # Stats finales
    print("\n" + "=" * 60)
    print("  RÉSULTATS")
    print("=" * 60)
    total = sum(results.values())
    for source, count in results.items():
        print(f"  {source:20s}: {count:>8,} antennes")
    print(f"  {'─' * 35}")
    print(f"  {'TOTAL':20s}: {total:>8,} antennes")
    print("=" * 60)
    
    return results


if __name__ == "__main__":
    asyncio.run(run_pipeline())
