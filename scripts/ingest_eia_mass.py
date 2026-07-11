#!/usr/bin/env python3
"""
SerbiaTracker — Ingestion massive EIA BTS
Intègre les GPS exacts extraits des documents EIA
dans cell_towers.db et Redis Geo
"""
import sqlite3, redis, json, sys

DB = '/root/serbia-tracker/data/cell_towers.db'
EIA_JSON = '/root/telecom_recon/all_operators/eia_gps_mass.json'

def main():
    with open(EIA_JSON) as f:
        eia_data = json.load(f)
    
    print(f"  {len(eia_data)} BTS EIA à ingérer")
    
    conn = sqlite3.connect(DB)
    r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=False)
    
    # Compter avant
    before = conn.execute('SELECT COUNT(*) FROM cell_towers').fetchone()[0]
    eia_before = conn.execute("SELECT COUNT(*) FROM cell_towers WHERE source='EIA'").fetchone()[0]
    
    inserted = 0
    skipped = 0
    
    for entry in eia_data:
        lat = entry.get('lat')
        lon = entry.get('lon')
        
        if not lat or not lon:
            skipped += 1
            continue
        
        operator = entry.get('operator', 'Unknown')
        
        if 'A1' in operator:
            mnc = 5
        elif 'Telekom' in operator:
            mnc = 3
        elif 'Yettel' in operator or 'Telenor' in operator or 'Cetin' in operator:
            mnc = 1
        else:
            mnc = 0
        
        # Vérifier doublon (même lat/lon ±10m)
        existing = conn.execute(
            'SELECT 1 FROM cell_towers WHERE mcc=220 AND mnc=? AND ABS(lat-?)<0.0001 AND ABS(lon-?)<0.0001',
            (mnc, lat, lon)
        ).fetchone()
        
        if existing:
            skipped += 1
            continue
        
        # Générer LAC/CellID uniques
        site_id = entry.get('site_id', '')
        lac = abs(hash(site_id)) % 65535
        cell_id = abs(hash(site_id + str(lat))) % 65535
        
        techs = entry.get('techs', '')
        height = entry.get('height')
        
        radio = 'LTE' if 'LTE' in techs else ('UMTS' if 'UMTS' in techs else 'GSM')
        
        try:
            conn.execute("""
                INSERT INTO cell_towers 
                (mcc, mnc, lac, cell_id, lat, lon, radio, radius_km, samples, source, altitude_m, band, imported_at)
                VALUES (220, ?, ?, ?, ?, ?, ?, ?, 500, 'EIA', ?, ?, datetime('now'))
            """, (mnc, lac, cell_id, lat, lon, radio,
                  max(0.3, (height or 50) / 1000),
                  int(height) if height else None,
                  techs[:20] if techs else None))
            
            # Ajouter à Redis Geo
            key = f'towers:220:{mnc}'
            member = f'{lac}:{cell_id}'
            r.geoadd(key, (lon, lat, member))
            
            inserted += 1
        except Exception as e:
            skipped += 1
    
    conn.commit()
    
    # Stats finales
    total = conn.execute('SELECT COUNT(*) FROM cell_towers').fetchone()[0]
    eia_total = conn.execute("SELECT COUNT(*) FROM cell_towers WHERE source='EIA'").fetchone()[0]
    by_mnc = conn.execute('SELECT mnc, COUNT(*) FROM cell_towers GROUP BY mnc').fetchall()
    
    print(f"\n{'='*70}")
    print(f"  INGESTION TERMINÉE")
    print(f"  Ajoutés: {inserted} | Ignorés: {skipped}")
    print(f"  TOTAL: {total} antennes")
    print(f"  EIA (GPS exacts): {eia_total}")
    for mnc, cnt in by_mnc:
        names = {1: 'Yettel/CETIN', 3: 'mt:s', 5: 'A1'}
        print(f"    {names.get(mnc, f'MNC {mnc}')}: {cnt}")
    
    # Redis stats
    for mnc in [1, 3, 5]:
        zcard = r.zcard(f'towers:220:{mnc}')
        print(f"    Redis towers:220:{mnc}: {zcard}")
    print(f"{'='*70}")
    
    conn.close()

if __name__ == '__main__':
    main()
