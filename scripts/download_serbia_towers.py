#!/usr/bin/env python3
"""
Script de telechargement de la base OpenCellID pour la Serbie
Filtre automatiquement sur MCC=220 (Serbie) et compresse en CSV

Usage:
    python download_serbia_towers.py [--api-key KEY]
"""
import os
import sys
import gzip
import csv
import argparse
import urllib.request
from pathlib import Path

OPENCELLID_DOWNLOAD_URL = "https://opencellid.org/cells/downloads/cell_towers.csv.gz"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "cell_towers"
OUTPUT_FILE = OUTPUT_DIR / "serbia_towers.csv.gz"
SERBIA_MCC = "220"
EXPECTED_COLUMNS = ["radio", "mcc", "net", "area", "cell", "unit", "lon", "lat", "range", "samples", "changeable", "created", "updated", "averageSignal"]


def download_opencellid(api_key: str = None):
    """Telecharge le CSV complet OpenCellID (~1.5GB)"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    full_gz = OUTPUT_DIR / "cell_towers_full.csv.gz"
    
    print(f"[1/3] Telechargement OpenCellID (~1.5GB)...")
    
    url = OPENCELLID_DOWNLOAD_URL
    if api_key:
        url += f"?token={api_key}"
    
    try:
        urllib.request.urlretrieve(url, str(full_gz))
        print(f"      Fichier: {full_gz.stat().st_size / 1024 / 1024:.1f} MB")
    except Exception as e:
        print(f"ERREUR telechargement: {e}")
        print("Verifiez votre token API sur https://opencellid.org/downloads.php")
        sys.exit(1)
    
    return full_gz


def filter_serbia(input_gz: Path):
    """Filtre le CSV pour ne garder que la Serbie (MCC=220)"""
    print(f"\n[2/3] Filtrage Serbie (MCC={SERBIA_MCC})...")
    
    serbia_count = 0
    total_count = 0
    
    with gzip.open(input_gz, 'rt', encoding='utf-8') as fin:
        reader = csv.reader(fin)
        header = next(reader)
        
        with gzip.open(OUTPUT_FILE, 'wt', encoding='utf-8') as fout:
            writer = csv.writer(fout)
            writer.writerow(header)
            
            for row in reader:
                total_count += 1
                if total_count % 1000000 == 0:
                    print(f"      Parcouru: {total_count/1e6:.1f}M lignes, Serbie: {serbia_count}")
                
                if row[1] == SERBIA_MCC:  # Colonne mcc
                    writer.writerow(row)
                    serbia_count += 1
    
    print(f"      Total mondial: {total_count:,} antennes")
    print(f"      Serbie: {serbia_count:,} antennes")
    
    # Cleanup du fichier complet
    input_gz.unlink()
    print(f"      Fichier complet supprime (economise ~1.5GB)")
    
    return serbia_count


def print_stats():
    """Affiche des statistiques sur les antennes serbes"""
    print(f"\n[3/3] Statistiques...")
    
    by_operator = {}
    by_radio = {}
    total = 0
    
    with gzip.open(OUTPUT_FILE, 'rt', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            mnc = row.get('net', '?')
            radio = row.get('radio', '?')
            by_operator[mnc] = by_operator.get(mnc, 0) + 1
            by_radio[radio] = by_radio.get(radio, 0) + 1
    
    print(f"\n  Total antennes Serbie: {total:,}")
    print(f"\n  Par operateur (MNC):")
    operator_names = {
        "1": "Yettel (ex Telenor)",
        "3": "mt:s (Telekom Srbija)",
        "5": "A1 Srbija (ex VIP)",
        "7": "Orion Telekom",
        "11": "MUNDIO MOBILE",
    }
    for mnc, count in sorted(by_operator.items(), key=lambda x: -x[1]):
        name = operator_names.get(mnc, f"MNC {mnc}")
        print(f"    220-{mnc} {name}: {count:,} antennes")
    
    print(f"\n  Par technologie:")
    for radio, count in sorted(by_radio.items(), key=lambda x: -x[1]):
        print(f"    {radio}: {count:,}")
    
    print(f"\n  Fichier: {OUTPUT_FILE}")
    print(f"  Taille: {OUTPUT_FILE.stat().st_size / 1024 / 1024:.1f} MB")


def main():
    parser = argparse.ArgumentParser(description="Telecharger la base antennes Serbie depuis OpenCellID")
    parser.add_argument("--api-key", help="Token API OpenCellID (optionnel)")
    args = parser.parse_args()
    
    print("=" * 60)
    print("  SerbiaTracker - Telechargement base antennes")
    print("  OpenCellID → Filtre Serbie (MCC=220)")
    print("=" * 60)
    
    # Verifier si deja telecharge
    if OUTPUT_FILE.exists():
        size_mb = OUTPUT_FILE.stat().st_size / 1024 / 1024
        print(f"\nBase existante: {size_mb:.1f} MB")
        resp = input("Re-telecharger? [o/N] ")
        if resp.lower() != 'o':
            print_stats()
            return
    
    full_gz = download_opencellid(args.api_key)
    count = filter_serbia(full_gz)
    
    if count > 0:
        print_stats()
        print(f"\n✅ Pret! Lancez: cd backend && python main.py")
    else:
        print("\n❌ Aucune antenne serbe trouvee dans le fichier")


if __name__ == "__main__":
    main()
