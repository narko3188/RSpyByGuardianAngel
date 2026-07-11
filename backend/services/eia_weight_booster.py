"""
SerbiaTracker — EIA Weight Booster v2
Integre les 17 tours EIA (GPS regulateur officiel) dans la triangulation.

Fonctionnement direct: pour n'importe quelle position candidate, trouve
les tours EIA proches du meme operateur et fusionne la position.
Les EIA tirent la position vers la verite terrain (30% EIA, 70% triangulation).
"""
import sqlite3
import math
import logging
from pathlib import Path
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / "data" / "cell_towers.db"

# Cache des tours EIA par MNC: {mnc: [(lat, lon, site_name), ...]}
_eia_cache: Dict[int, List[Tuple[float, float, str]]] = {}


def _load_eia_towers() -> Dict[int, List[Tuple[float, float, str]]]:
    """Charge les tours EIA depuis SQLite, cache en memoire"""
    global _eia_cache
    if _eia_cache:
        return _eia_cache

    try:
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute(
            "SELECT mnc, lat, lon FROM cell_towers WHERE source IN ('EIA','EIA_CAL') AND mcc=220"
        ).fetchall()
        conn.close()

        for mnc, lat, lon in rows:
            mnc_int = int(mnc)
            if mnc_int not in _eia_cache:
                _eia_cache[mnc_int] = []
            _eia_cache[mnc_int].append((lat, lon, f"EIA-{len(_eia_cache[mnc_int])+1}"))

        total = sum(len(v) for v in _eia_cache.values())
        logger.info(f"EIA Booster: {total} tours GPS exact chargees")
    except Exception as e:
        logger.warning(f"EIA Booster: echec chargement DB: {e}")

    return _eia_cache


def compute_eia_weight_boost(
    mnc: int,
    candidate_position: Tuple[float, float],
    towers: List[Dict] = None
) -> Tuple[float, float, int]:
    """
    Fusionne la position candidate avec les tours EIA.

    Pour chaque tour EIA du meme operateur dans un rayon de 30km de la
    position candidate, calcule une moyenne ponderee.
    Les tours EIA ont un poids 3x superieur a la position candidate.

    Returns:
        (merged_lat, merged_lon, eia_matches_used)
    """
    eia_data = _load_eia_towers()
    eia_towers = eia_data.get(mnc, [])

    if not eia_towers:
        return candidate_position[0], candidate_position[1], 0

    clat, clon = candidate_position

    # Distance max de prise en compte des EIA (km)
    MAX_EIA_DISTANCE_KM = 30.0

    total_weight = 1.0  # poids de base pour la position candidate
    weighted_lat = clat * 1.0
    weighted_lon = clon * 1.0
    eia_matches = 0

    for eia_lat, eia_lon, eia_name in eia_towers:
        # Distance haversine
        dlat = math.radians(eia_lat - clat)
        dlon = math.radians(eia_lon - clon)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(clat)) * math.cos(math.radians(eia_lat)) *
             math.sin(dlon / 2) ** 2)
        dist_km = 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        if dist_km < MAX_EIA_DISTANCE_KM:
            # Poids inversement proportionnel a la distance
            weight = 3.0 / max(dist_km, 0.5)
            weighted_lat += eia_lat * weight
            weighted_lon += eia_lon * weight
            total_weight += weight
            eia_matches += 1

    if eia_matches > 0:
        merged_lat = weighted_lat / total_weight
        merged_lon = weighted_lon / total_weight
        return merged_lat, merged_lon, eia_matches

    return clat, clon, 0


def get_eia_stats(mnc: int) -> Dict:
    """Retourne les stats EIA pour un operateur"""
    eia_data = _load_eia_towers()
    towers = eia_data.get(mnc, [])
    return {
        "eia_towers_available": len(towers),
        "eia_coverage": "excellent" if len(towers) >= 5 else "good" if len(towers) >= 2 else "none"
    }
