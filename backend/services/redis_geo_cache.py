"""
SerbiaTracker — Redis Geo Caching Service
Cache spatial haute performance pour antennes et positions

Pattern: Redis Geo comme cache chaud, PostGIS/SQLite comme source verite
- SETEX pour positions recentes (TTL 60s)
- GEOADD + GEOSEARCH pour antennes par rayon
- H3 hexagonal grid pour hotspots
"""
import json
import logging
import time
from typing import Dict, List, Optional, Tuple
from functools import wraps

logger = logging.getLogger(__name__)

# Simulation Redis si non dispo (fallback in-memory dict)
_USE_REDIS = False
try:
    import redis
    _redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    _redis_client.ping()
    _USE_REDIS = True
    logger.info("Redis Geo cache connected")
except:
    logger.warning("Redis not available, using in-memory cache")
    _redis_client = None
    _mem_cache = {}
    _mem_geo = {}  # {key: (lon, lat, data)}


class GeoCache:
    """Cache geo-spatial avec fallback in-memory"""
    
    # ---- Position caching ----
    @staticmethod
    def cache_position(phone: str, data: Dict, ttl: int = 60):
        """Cache une position recente (TTL court)"""
        key = f"pos:{phone}"
        value = json.dumps(data)
        
        if _USE_REDIS:
            _redis_client.setex(key, ttl, value)
        else:
            _mem_cache[key] = (time.time() + ttl, value)
    
    @staticmethod
    def get_position(phone: str) -> Optional[Dict]:
        """Recuperer une position en cache"""
        key = f"pos:{phone}"
        
        if _USE_REDIS:
            val = _redis_client.get(key)
            return json.loads(val) if val else None
        else:
            entry = _mem_cache.get(key)
            if entry and time.time() < entry[0]:
                return json.loads(entry[1])
            elif entry:
                del _mem_cache[key]
        return None
    
    # ---- Tower geo-indexing ----
    @staticmethod
    def index_towers(towers: List[Dict], mnc: int):
        """Indexer les antennes dans Redis Geo"""
        if not towers:
            return
        
        geo_key = f"towers:220:{mnc}"
        
        if _USE_REDIS:
            pipe = _redis_client.pipeline()
            for t in towers:
                pipe.geoadd(geo_key, (t["lon"], t["lat"], f'{t["lac"]}:{t["cell_id"]}'))
            pipe.execute()
            _redis_client.expire(geo_key, 3600)  # TTL 1h
        else:
            for t in towers:
                _mem_geo[f"{mnc}:{t['lac']}:{t['cell_id']}"] = (t["lon"], t["lat"], t)
    
    @staticmethod
    def search_towers(mnc: int, lat: float, lon: float, radius_km: float, limit: int = 20) -> List[Dict]:
        """Rechercher les antennes dans un rayon"""
        geo_key = f"towers:220:{mnc}"
        
        if _USE_REDIS:
            # GEORADIUS avec WITHDIST pour avoir les distances
            results = _redis_client.georadius(
                geo_key,
                longitude=lon, latitude=lat,
                radius=radius_km, unit="km",
                sort="ASC", count=limit,
                withdist=True
            )
            # Results are [(member, distance_km), ...] or just [member, ...]
            towers = []
            for item in results:
                if isinstance(item, (list, tuple)):
                    member, dist = item
                else:
                    member = item
                    dist = radius_km  # distance inconnue
                
                # Decode bytes
                if isinstance(member, bytes):
                    member = member.decode('utf-8', errors='ignore')
                if isinstance(dist, bytes):
                    dist = float(dist.decode('utf-8', errors='ignore'))
                else:
                    dist = float(dist)
                
                parts = member.split(":")
                if len(parts) >= 2:
                    lac, cell_id = parts[0], parts[1]
                    try:
                        towers.append({"lac": int(lac), "cell_id": int(cell_id), "distance_km": dist})
                    except ValueError:
                        continue
            return towers
        else:
            # In-memory fallback - scan all
            from math import radians, cos, sin, asin, sqrt
            def haversine(lat1, lon1, lat2, lon2):
                R = 6371
                dlat = radians(lat2 - lat1)
                dlon = radians(lon2 - lon1)
                a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
                return R * 2 * asin(sqrt(a))
            
            nearby = []
            for key, (t_lon, t_lat, data) in _mem_geo.items():
                if not key.startswith(f"{mnc}:"):
                    continue
                dist = haversine(lat, lon, t_lat, t_lon)
                if dist <= radius_km:
                    nearby.append({**data, "distance_km": dist})
            
            nearby.sort(key=lambda x: x["distance_km"])
            return nearby[:limit]
    
    # ---- Stats ----
    @staticmethod
    def get_stats() -> Dict:
        if _USE_REDIS:
            info = _redis_client.info("stats")
            return {
                "provider": "redis",
                "connected": True,
                "keys": _redis_client.dbsize(),
                "hit_rate": info.get("keyspace_hits", 0) / max(info.get("keyspace_hits", 0) + info.get("keyspace_misses", 1), 1) * 100,
                "memory_mb": info.get("used_memory_rss", 0) / 1024 / 1024,
            }
        return {
            "provider": "memory",
            "connected": False,
            "keys": len(_mem_cache) + len(_mem_geo),
        }


# Decorateur de cache pour les endpoints
def cached_track(ttl: int = 60):
    """Decorateur: cache le resultat d'un tracking"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extraire le phone du premier arg (request)
            request = args[0] if args else None
            phone = getattr(request, 'phone', None) if request else None
            
            if phone:
                cached = GeoCache.get_position(phone)
                if cached:
                    cached["cached"] = True
                    return cached
            
            result = await func(*args, **kwargs)
            
            if phone and result:
                GeoCache.cache_position(phone, result, ttl)
            
            return result
        return wrapper
    return decorator


# Cache H3 hexagonal (simplifié - grille carrée)
def h3_cache_key(lat: float, lon: float, resolution: int = 8) -> str:
    """Générer une clé de cache par cellule de grille (~100m à res 10)"""
    # Simplification: grille 0.01° (~1km)
    grid_lat = round(lat * 100) / 100
    grid_lon = round(lon * 100) / 100
    return f"h3:{grid_lat}:{grid_lon}"
