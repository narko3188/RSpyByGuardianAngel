"""
SerbiaTracker — Rate Limiter (Redis Sliding Window)
Protection API avec limites par IP et par API key

Algorithm: Sliding Window avec Redis sorted sets
- Fenetre glissante: compte les requetes dans les N dernieres secondes
- Atomic via Redis ZADD + ZREMRANGEBYSCORE + ZCARD
- Headers standards: X-RateLimit-*
"""
import time
import logging
from typing import Dict, Tuple, Optional
from functools import wraps

logger = logging.getLogger(__name__)

# Fallback in-memory si Redis non dispo
_USE_REDIS = False
try:
    import redis
    _redis_client = redis.Redis(host='localhost', port=6379, db=1, decode_responses=True)
    _redis_client.ping()
    _USE_REDIS = True
except:
    _redis_client = None
    _mem_limits = {}

# Tiers de rate limiting
RATE_LIMITS = {
    "basic":    {"rpm": 60,  "burst": 100},   # 1 req/s
    "pro":      {"rpm": 300, "burst": 500},    # 5 req/s
    "enterprise": {"rpm": 1000, "burst": 2000}, # 16 req/s
    "default":  {"rpm": 30,  "burst": 50},     # Fallback
}

# Limites par endpoint
ENDPOINT_LIMITS = {
    "/api/v6/track": "pro",      # Tracking = haute limite
    "/api/v3/track": "basic",
    "/api/v1/track": "basic",
    "/api/v1/geofence/check": "basic",
    "/api/v1/cellmapper/towers": "basic",
}


class RateLimiter:
    """Rate limiter sliding window"""
    
    def __init__(self):
        self.limits = RATE_LIMITS
    
    def is_allowed(self, key: str, tier: str = "default", window_s: int = 60) -> Tuple[bool, Dict]:
        """
        Verifier si une requete est autorisee
        
        Returns: (allowed, headers)
        """
        limit_config = self.limits.get(tier, self.limits["default"])
        max_requests = limit_config["rpm"]
        
        now = time.time()
        window_start = now - window_s
        
        redis_key = f"ratelimit:{key}"
        
        if _USE_REDIS:
            pipe = _redis_client.pipeline()
            # Ajouter le timestamp courant
            pipe.zadd(redis_key, {str(now): now})
            # Supprimer les entrees hors fenetre
            pipe.zremrangebyscore(redis_key, 0, window_start)
            # Compter les requetes dans la fenetre
            pipe.zcard(redis_key)
            # TTL sur la cle
            pipe.expire(redis_key, window_s + 10)
            
            _, _, count, _ = pipe.execute()
            count = int(count)
        else:
            # In-memory fallback
            if redis_key not in _mem_limits:
                _mem_limits[redis_key] = []
            
            timestamps = _mem_limits[redis_key]
            # Nettoyer les vieux timestamps
            timestamps = [t for t in timestamps if t > window_start]
            timestamps.append(now)
            _mem_limits[redis_key] = timestamps
            count = len(timestamps)
        
        remaining = max(0, max_requests - count)
        reset_at = int(now + window_s)
        
        headers = {
            "X-RateLimit-Limit": str(max_requests),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset": str(reset_at),
        }
        
        if count > max_requests:
            headers["Retry-After"] = str(window_s)
            return False, headers
        
        return True, headers
    
    def get_client_tier(self, api_key: Optional[str] = None, ip: Optional[str] = None) -> str:
        """Determiner le tier d'un client"""
        # TODO: lookup API key en DB pour tier reel
        if api_key and api_key.startswith("sk_pro_"):
            return "pro"
        elif api_key and api_key.startswith("sk_ent_"):
            return "enterprise"
        return "default"
    
    def get_client_key(self, api_key: Optional[str] = None, ip: Optional[str] = None) -> str:
        """Generer une cle unique pour le client"""
        if api_key:
            return f"key:{api_key}"
        return f"ip:{ip or 'unknown'}"


# Singleton
rate_limiter = RateLimiter()


# Decorateur FastAPI pour rate limiting
def rate_limit(endpoint: str = None):
    """Decorateur de rate limiting pour endpoints FastAPI"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            from fastapi import Request, HTTPException
            
            # Trouver le Request object
            request = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            
            if not request:
                return await func(*args, **kwargs)
            
            # Déterminer le client
            api_key = request.headers.get("X-API-Key")
            client_ip = request.client.host if request.client else "unknown"
            
            tier = rate_limiter.get_client_tier(api_key, client_ip)
            client_key = rate_limiter.get_client_key(api_key, client_ip)
            
            allowed, headers = rate_limiter.is_allowed(client_key, tier)
            
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded. Retry later.",
                    headers=headers
                )
            
            # Ajouter les headers a la reponse
            result = await func(*args, **kwargs)
            
            # Si le resultat est une Response, ajouter les headers
            if hasattr(result, 'headers'):
                for k, v in headers.items():
                    result.headers[k] = v
            
            return result
        return wrapper
    return decorator


def get_rate_limit_stats() -> Dict:
    """Statistiques rate limiting"""
    if _USE_REDIS:
        keys = _redis_client.keys("ratelimit:*")
        return {
            "provider": "redis",
            "active_limits": len(keys),
            "total_keys": _redis_client.dbsize(),
        }
    return {
        "provider": "memory",
        "active_limits": len(_mem_limits),
    }
