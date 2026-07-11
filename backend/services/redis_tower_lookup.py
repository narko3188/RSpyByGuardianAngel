"""
SerbiaTracker — Redis Tower Lookup Service
Remplace les listes built-in par Redis Geo (50K+ antennes)
"""
import logging
import math
from typing import List, Dict, Optional
from services.redis_geo_cache import GeoCache
from services.yettel_infrastructure import get_realistic_signal as yettel_signal

logger = logging.getLogger(__name__)


def get_towers_from_redis(mnc: int, lat: float, lon: float, radius_km: float = 30, limit: int = 50) -> List[Dict]:
    """
    Recherche d'antennes via Redis Geo (9.5K indexees)
    Utilise le connection pool
    """
    from services.connection_pool import get_redis, get_sqlite
    
    r = get_redis()
    conn = get_sqlite()
    
    geo_key = f"towers:220:{mnc}"
    
    try:
        results = r.georadius(
            geo_key,
            longitude=lon, latitude=lat,
            radius=radius_km, unit="km",
            sort="ASC", count=limit,
            withdist=True
        )
    except Exception as e:
        logger.warning(f"Redis GEORADIUS error: {e}")
        return []
    
    towers = []
    for item in results:
        if isinstance(item, (list, tuple)):
            member, dist = item
        else:
            member = item
            dist = float(radius_km)
        
        if isinstance(member, bytes):
            member = member.decode('utf-8', errors='ignore')
        if isinstance(dist, bytes):
            dist = float(dist.decode('utf-8', errors='ignore'))
        else:
            dist = float(dist)
        
        parts = member.split(":")
        if len(parts) >= 2:
            try:
                towers.append({
                    "lac": int(parts[0]),
                    "cell_id": int(parts[1]),
                    "distance_km": dist
                })
            except ValueError:
                continue
    
    # Lookup SQLite pour les vraies coordonnees (utilise le pool)
    enriched = []
    for t in towers:
        lac = t.get("lac", 0)
        cid = t.get("cell_id", 0)
        dist = t.get("distance_km", 5)
        
        # Chercher les vraies coordonnees
        row = conn.execute(
            'SELECT lat, lon, radio, radius_km, samples, altitude_m FROM cell_towers WHERE mcc=220 AND mnc=? AND lac=? AND cell_id=? LIMIT 1',
            (mnc, lac, cid)
        ).fetchone()
        
        if row:
            t_lat, t_lon, radio, radius, samples, alt = row
        else:
            # Fallback: positionner la tour a la distance mesuree
            angle = 0  # Direction inconnue, on met a 0
            t_lat = lat + (dist * math.cos(angle)) / 111.32
            t_lon = lon + (dist * math.sin(angle)) / (111.32 * math.cos(math.radians(lat)))
            radio = 'LTE'
            radius = dist
            samples = 100
            alt = None
        
        signal = yettel_signal(dist, radio or 'LTE')
        
        enriched.append({
            "mcc": 220,
            "mnc": mnc,
            "lac": lac,
            "cell_id": cid,
            "lat": round(t_lat, 6),
            "lon": round(t_lon, 6),
            "radio": radio or "LTE",
            "signal_dbm": signal["rssi_dbm"],
            "ta": signal["timing_advance"],
            "distance_km": dist,
            "radius_km": (radius or dist) + 0.5,
            "samples": samples or 100,
        })
    
    return enriched


def get_towers_hybrid(mnc: str, lat: float, lon: float, radius_km: float = 30) -> List[Dict]:
    """
    Mode hybride: Redis Geo + Built-in fallback
    """
    mnc_int = int(mnc) if isinstance(mnc, str) else mnc
    
    # 1. Essayer Redis Geo (50K+ antennes)
    redis_towers = get_towers_from_redis(mnc_int, lat, lon, radius_km)
    
    if redis_towers and len(redis_towers) >= 5:
        return redis_towers
    
    # 2. Fallback: built-in infrastructure
    from services.multi_pass_geolocation import _get_towers_for_operator
    builtin_towers = _get_towers_for_operator(str(mnc), lat, lon, radius_km)
    
    if builtin_towers:
        # Ajouter signaux
        result = []
        for t in builtin_towers[:15]:
            signal = yettel_signal(t["distance_km"], t["radio"])
            result.append({
                **t,
                "signal_dbm": signal["rssi_dbm"],
                "ta": signal["timing_advance"],
            })
        return result
    
    # 3. Si Redis a des tours mais <5
    return redis_towers
