#!/usr/bin/env python3
"""
scripts/extract_eia_coords.py — Extraction des coordonnées GPS depuis les PDF EIA
"""
import re
import sys
from pathlib import Path
from typing import List, Tuple, Optional

try:
    import pdfplumber
    import fitz  # PyMuPDF
except ImportError:
    print("❌ Dépendances manquantes:")
    print("   pip install pdfplumber pymupdf")
    sys.exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def extract_coordinates_from_text(text: str) -> List[Tuple[float, float, str]]:
    """
    Extrait les coordonnées GPS depuis un texte
    
    Patterns recherchés:
    - 44.8205, 20.4612
    - 44°49'12"N, 20°27'40"E
    - N 44.8205 E 20.4612
    """
    coords = []
    
    # Pattern décimal simple: 44.8205, 20.4612
    decimal_pattern = r'(\d{2}\.\d{4,})\s*[,;]\s*(\d{2}\.\d{4,})'
    matches = re.findall(decimal_pattern, text)
    for lat_str, lon_str in matches:
        lat, lon = float(lat_str), float(lon_str)
        # Valider: Belgrade ~44-45N, 20-21E
        if 44.0 <= lat <= 45.5 and 19.5 <= lon <= 21.5:
            coords.append((lat, lon, 'decimal'))
    
    # Pattern DMS: 44°49'12"N 20°27'40"E
    dms_pattern = r'(\d{2})°(\d{2})\'(\d{2}(?:\.\d+)?)"([NS])\s+(\d{2})°(\d{2})\'(\d{2}(?:\.\d+)?)"([EW])'
    matches = re.findall(dms_pattern, text)
    for match in matches:
        lat_d, lat_m, lat_s, lat_dir, lon_d, lon_m, lon_s, lon_dir = match
        lat = float(lat_d) + float(lat_m)/60 + float(lat_s)/3600
        lon = float(lon_d) + float(lon_m)/60 + float(lon_s)/3600
        if lat_dir == 'S':
            lat = -lat
        if lon_dir == 'W':
            lon = -lon
        coords.append((lat, lon, 'dms'))
    
    return coords


def extract_from_pdf_plumber(pdf_path: Path) -> List[Tuple[float, float, int]]:
    """Extrait les coordonnées avec pdfplumber"""
    coords = []
    
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            text = page.extract_text()
            if text:
                page_coords = extract_coordinates_from_text(text)
                coords.extend([(lat, lon, page_num) for lat, lon, _ in page_coords])
    
    return coords


def extract_from_pymupdf(pdf_path: Path) -> List[Tuple[float, float, int]]:
    """Extrait les coordonnées avec PyMuPDF (meilleur pour PDF scannés)"""
    coords = []
    
    doc = fitz.open(pdf_path)
    for page_num in range(len(doc)):
        page = doc[page_num]
        
        # Essayer d'extraire le texte
        text = page.get_text()
        if text:
            page_coords = extract_coordinates_from_text(text)
            coords.extend([(lat, lon, page_num + 1) for lat, lon, _ in page_coords])
        
        # Si pas de texte, essayer l'OCR (si disponible)
        if not coords:
            try:
                import pytesseract
                from PIL import Image
                pix = page.get_pixmap(dpi=300)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                text = pytesseract.image_to_string(img, lang='srp+eng')
                page_coords = extract_coordinates_from_text(text)
                coords.extend([(lat, lon, page_num + 1) for lat, lon, _ in page_coords])
            except ImportError:
                pass
    
    doc.close()
    return coords


def extract_from_directory(directory: Path, output_file: Path = None) -> dict:
    """
    Extrait les coordonnées de tous les PDF EIA d'un répertoire
    
    Returns: {filename: [(lat, lon, page), ...]}
    """
    results = {}
    
    pdf_files = list(directory.glob("*.pdf"))
    logger.info(f"📄 {len(pdf_files)} PDF EIA trouvés dans {directory}")
    
    for pdf_path in sorted(pdf_files):
        logger.info(f"   Extraction: {pdf_path.name}")
        
        # Essayer pdfplumber d'abord
        try:
            coords = extract_from_pdf_plumber(pdf_path)
        except Exception as e:
            logger.warning(f"      pdfplumber échoué: {e}, essai PyMuPDF...")
            try:
                coords = extract_from_pymupdf(pdf_path)
            except Exception as e2:
                logger.error(f"      ❌ Extraction échouée: {e2}")
                coords = []
        
        if coords:
            logger.info(f"      ✅ {len(coords)} coordonnées trouvées: {coords}")
        else:
            logger.warning(f"      ⚠️  Aucune coordonnée trouvée")
        
        results[pdf_path.name] = coords
    
    # Sauvegarder en JSON
    if output_file:
        import json
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        logger.info(f"\n💾 Résultats sauvegardés dans {output_file}")
    
    return results


def generate_python_template(results: dict) -> str:
    """Génère un template Python pour le script d'ingestion"""
    lines = [
        "#!/usr/bin/env python3",
        '"""',
        "EIA_TOWERS — Coordonnées GPS extraites des documents EIA",
        f"Généré automatiquement — {len(results)} documents",
        '"""',
        "",
        "# Format: (doc_name, operator, mnc, lat, lon, site_name, band, radio, altitude_m, azimuth, tx_power_dbm)",
        "EIA_TOWERS = ["
    ]
    
    for doc, coords in results.items():
        if not coords:
            continue
        
        # Déduire l'opérateur depuis le nom de fichier
        if 'A1' in doc or 'a1' in doc.lower():
            operator = 'A1'
            mnc = 5
        elif 'Popovic' in doc or 'Sopot' in doc:
            operator = 'mt:s'
            mnc = 3
        else:
            operator = 'Unknown'
            mnc = 0
        
        # Prendre la première coordonnée
        lat, lon, page = coords[0]
        
        # Déduire le nom du site depuis le nom de fichier
        site_name = doc.replace('.pdf', '').replace('Zahtev-', '').replace('A1-', '').replace('Srbija-', '')
        
        lines.append(f'    # Page {page}: {lat}, {lon}')
        lines.append(f'    ("{doc}", "{operator}", {mnc}, {lat}, {lon}, "{site_name}", "LTE1800", "LTE", 150, [120], 46),')
    
    lines.append("]")
    lines.append("")
    
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Extraction GPS depuis PDF EIA")
    parser.add_argument("directory", type=Path, help="Répertoire contenant les PDF EIA")
    parser.add_argument("--output", "-o", type=Path, default=Path("eia_coords.json"), help="Fichier de sortie JSON")
    parser.add_argument("--template", "-t", type=Path, default=Path("eia_towers_template.py"), help="Template Python")
    
    args = parser.parse_args()
    
    # Extraire les coordonnées
    results = extract_from_directory(args.directory, args.output)
    
    # Générer le template Python
    template = generate_python_template(results)
    args.template.write_text(template, encoding='utf-8')
    logger.info(f"📝 Template Python généré: {args.template}")
    
    # Statistiques
    total_coords = sum(len(coords) for coords in results.values())
    docs_with_coords = sum(1 for coords in results.values() if coords)
    
    print(f"\n{'='*60}")
    print(f"  EXTRACTION GPS EIA TERMINÉE")
    print(f"{'='*60}")
    print(f"  Documents traités: {len(results)}")
    print(f"  Documents avec coordonnées: {docs_with_coords}")
    print(f"  Total coordonnées extraites: {total_coords}")
    print(f"{'='*60}")
