"""
SerbiaTracker - Service Altitude SRTM
Altitude via Open-Elevation API + SRTM data
"""
import httpx
import logging
from typing import Optional, Dict
from pathlib import Path

logger = logging.getLogger(__name__)

# Cache simple en memoire
_altitude_cache: Dict[str, float] = {}

# URL Open-Elevation (API gratuite, pas de cle)
OPEN_ELEVATION_URL = "https://api.open-elevation.com/api/v1/lookup"


async def get_elevation(lat: float, lon: float) -> Optional[float]:
    """
    Obtenir l'altitude d'un point via Open-Elevation (SRTM)
    Avec cache pour eviter les appels repetes
    """
    cache_key = f"{lat:.5f},{lon:.5f}"
    
    if cache_key in _altitude_cache:
        return _altitude_cache[cache_key]
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                OPEN_ELEVATION_URL,
                params={"locations": f"{lat},{lon}"}
            )
            data = resp.json()
            results = data.get("results", [])
            if results:
                elevation = results[0].get("elevation")
                _altitude_cache[cache_key] = elevation
                return elevation
    except Exception as e:
        logger.warning(f"Elevation lookup failed: {e}")
    
    return None


async def get_elevation_batch(points: list) -> list:
    """
    Obtenir l'altitude pour plusieurs points en un appel
    Points: [(lat1, lon1), (lat2, lon2), ...]
    """
    if not points:
        return []
    
    # Filtrer le cache
    uncached = []
    results = []
    
    for i, (lat, lon) in enumerate(points):
        cache_key = f"{lat:.5f},{lon:.5f}"
        if cache_key in _altitude_cache:
            results.append(_altitude_cache[cache_key])
        else:
            uncached.append((i, lat, lon))
            results.append(None)
    
    if not uncached:
        return results
    
    try:
        locations = "|".join(f"{lat},{lon}" for _, lat, lon in uncached)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                OPEN_ELEVATION_URL,
                params={"locations": locations}
            )
            data = resp.json()
            api_results = data.get("results", [])
            
            for j, (orig_idx, lat, lon) in enumerate(uncached):
                if j < len(api_results):
                    elevation = api_results[j].get("elevation")
                    cache_key = f"{lat:.5f},{lon:.5f}"
                    _altitude_cache[cache_key] = elevation
                    results[orig_idx] = elevation
    except Exception as e:
        logger.warning(f"Batch elevation lookup failed: {e}")
    
    return results


def adjust_accuracy_with_altitude(
    predicted_lat: float, predicted_lon: float,
    towers: list
) -> float:
    """
    Ajuster la precision estimee en utilisant l'altitude
    Si l'altitude predite est coherente avec la topographie locale, precision amelioree
    """
    # Calculer l'altitude moyenne des tours
    tower_altitudes = [t.get("altitude_m") for t in towers if t.get("altitude_m")]
    
    if not tower_altitudes:
        return 1.0  # Pas d'ajustement
    
    import statistics
    avg_tower_alt = statistics.mean(tower_altitudes)
    
    # Si variation d'altitude faible (<50m), terrain plat → meilleure precision
    if len(tower_altitudes) >= 3:
        std_alt = statistics.stdev(tower_altitudes)
        if std_alt < 20:
            return 0.7  # Amelioration 30%
        elif std_alt < 50:
            return 0.85  # Amelioration 15%
        elif std_alt > 200:
            return 1.3  # Degradation 30% (terrain montagneux)
    
    return 1.0  # Neutre
