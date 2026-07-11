"""
SerbiaTracker - Reverse Geocoding Service
Nominatim / OpenStreetMap reverse geocoding with:
- HTTP caching
- Area type detection
- Graceful fallback when external service is unavailable
"""
import asyncio
import hashlib
import logging
import time
from typing import Dict, Optional

import httpx
from geopy.distance import geodesic
from geopy.point import Point

from config.settings import settings

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "SerbiaTracker/1.0 (contact: admin@example.com)"
DEFAULT_LANG = "fr"


class ReverseGeocodingError(Exception):
    pass


class ReverseGeocodingService:
    def __init__(self, ttl_seconds: int = 3600, max_cache_size: int = 5000):
        self._cache: Dict[str, Dict] = {}
        self._ttl = ttl_seconds
        self._max_cache = max_cache_size
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(6.0, connect=3.0),
            headers={"User-Agent": USER_AGENT},
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _cache_key(self, lat: float, lon: float, lang: str) -> str:
        raw = f"{lat:.5f},{lon:.5f}:{lang}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_get(self, key: str) -> Optional[Dict]:
        entry = self._cache.get(key)
        if not entry:
            return None
        if time.time() - entry.get("ts", 0) > self._ttl:
            self._cache.pop(key, None)
            return None
        return entry.get("data")

    def _cache_set(self, key: str, data: Dict) -> None:
        if len(self._cache) >= self._max_cache:
            # simple eviction
            for k in list(self._cache)[: max(1, self._max_cache // 10)]:
                self._cache.pop(k, None)
        self._cache[key] = {"ts": time.time(), "data": data}

    async def reverse(self, lat: float, lon: float, lang: str = DEFAULT_LANG) -> Dict:
        key = self._cache_key(lat, lon, lang)
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        result = await self._fetch(lat, lon, lang)
        self._cache_set(key, result)
        return result

    async def _fetch(self, lat: float, lon: float, lang: str) -> Dict:
        params = {
            "lat": str(lat),
            "lon": str(lon),
            "format": "jsonv2",
            "accept-language": lang,
            "zoom": "18",
            "addressdetails": "1",
            "extratags": "1",
        }
        try:
            resp = await self._client.get(NOMINATIM_URL, params=params)
            if resp.status_code == 429:
                raise ReverseGeocodingError("NOMINATIM_RATE_LIMITED")
            if resp.status_code == 404:
                return self._empty(lat, lon, "not_found")
            if resp.status_code != 200:
                raise ReverseGeocodingError(f"NOMINATIM_HTTP_{resp.status_code}")
            data = resp.json()
        except httpx.TimeoutException:
            return self._empty(lat, lon, "timeout")
        except httpx.HTTPError as e:
            logger.debug("Nominatim HTTP error: %s", e)
            return self._empty(lat, lon, "http_error")

        if not data or not isinstance(data, dict):
            return self._empty(lat, lon, "empty")

        return self._parse(lat, lon, data)

    def _empty(self, lat: float, lon: float, reason: str) -> Dict:
        return {
            "status": "ok" if reason in ("not_found",) else "fallback",
            "reason": reason,
            "display_name": "",
            "road": None,
            "city": None,
            "suburb": None,
            "municipality": None,
            "county": None,
            "state": None,
            "country": None,
            "country_code": None,
            "postcode": None,
            "area_type": self._detect_area_type(lat, lon, None),
            "formatted": "",
            "raw": {},
        }

    def _parse(self, lat: float, lon: float, data: Dict) -> Dict:
        addr = data.get("address", {})
        display = data.get("display_name", "")
        area_type = self._detect_area_type(lat, lon, addr)

        road = addr.get("road") or addr.get("pedestrian") or addr.get("path")
        city = (
            addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("municipality")
        )
        suburb = addr.get("suburb") or addr.get("neighbourhood")
        municipality = addr.get("municipality")
        county = addr.get("county")
        state = addr.get("state")
        country = addr.get("country")
        country_code = addr.get("country_code")
        postcode = addr.get("postcode")

        formatted = ", ".join(
            x for x in [road, suburb or city, city, state, country] if x
        )

        return {
            "status": "ok",
            "reason": None,
            "display_name": display,
            "road": road,
            "city": city,
            "suburb": suburb,
            "municipality": municipality,
            "county": county,
            "state": state,
            "country": country,
            "country_code": country_code,
            "postcode": postcode,
            "area_type": area_type,
            "formatted": formatted or display,
            "raw": data,
        }

    def _detect_area_type(self, lat: float, lon: float, addr: Optional[Dict]) -> str:
        """
        Detect area type using OSM tags + simple heuristics.
        Types: urban, suburban, rural, industrial, commercial, forest, water, highway, unknown
        """
        if not addr:
            addr = {}

        # Landuse / amenity / highway hints from raw data are already inside addr sometimes.
        landuse = addr.get("landuse")
        amenity = addr.get("amenity")
        highway = addr.get("highway")
        leisure = addr.get("leisure")
        natural = addr.get("natural")
        waterway = addr.get("waterway")

        if any(x in (landuse, amenity, leisure) for x in ["industrial", "commercial", "retail"]):
            if landuse == "industrial" or amenity in ("factory", "warehouse"):
                return "industrial"
            if landuse == "commercial" or amenity in ("supermarket", "mall", "marketplace"):
                return "commercial"
        if natural in ("water", "waterway", "wetland") or waterway:
            return "water"
        if natural in ("wood", "forest", "tree_row") or landuse == "forest":
            return "forest"
        if highway:
            return "highway"

        # Population density heuristic based on distance to Belgrade center and urban cores.
        # Belgrade: 44.7866, 20.4489
        cores = [
            (44.7866, 20.4489, 12.0),  # Belgrade core radius km
            (45.2671, 19.8335, 8.0),   # Novi Sad
            (43.3209, 21.8954, 8.0),   # Nis
            (44.0118, 20.9114, 5.0),   # Kragujevac
            (45.0013, 19.6644, 5.0),   # Subotica
        ]
        near_core = False
        for clat, clon, radius_km in cores:
            if geodesic((lat, lon), (clat, clon)).km <= radius_km:
                near_core = True
                break

        if near_core:
            # try to decide urban/suburban by coarse osm data if available
            if landuse in ("residential", "retail", "commercial"):
                return "urban"
            if addr.get("suburb") or addr.get("neighbourhood"):
                return "suburban"
            return "urban"

        # In Serbia outside cores most areas are either suburban or rural.
        # Without more data we keep a conservative classification.
        if addr.get("city") or addr.get("town") or addr.get("municipality"):
            return "suburban"
        return "rural"


reverse_geocoding = ReverseGeocodingService()
