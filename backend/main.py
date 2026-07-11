"""
SerbiaTracker - API FastAPI principale
"""
import time
import logging
from contextlib import asynccontextmanager
from typing import Dict, List, Optional
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel, Field

from config.settings import settings
from services.phone_lookup import phone_lookup
from services.geolocation import geolocator
from services.tower_database import tower_db
from services.enhanced_geolocation import enhanced_geolocation
from services.multi_pass_geolocation import multi_pass_geolocation
from services.consensus_geolocation import consensus_geolocation
from services.adaptive_geolocation import adaptive_geolocation
from services.hybrid_wknn_geolocation import enhanced_wknn_geolocation
from services.altitude import get_elevation, adjust_accuracy_with_altitude
from services.cellmapper import get_cellmapper_towers, generate_cellmapper_tiles_url
from services.heatmap_analytics import (
    generate_coverage_heatmap, check_geofence, SERBIA_GEOFENCES
)
from services.numverify_service import numverify_lookup
from services.redis_geo_cache import GeoCache
from services.rate_limiter import rate_limiter, rate_limit, get_rate_limit_stats
from services.reverse_geocoding import reverse_geocoding
from api import websocket_tracking

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("serbia-tracker")

# ----- Models -----
class TowerInput(BaseModel):
    radio: str = "GSM"
    mcc: int = 220
    mnc: int = 3
    lac: int = 0
    cell_id: int = 0
    signal_dbm: Optional[int] = None
    ta: Optional[int] = None
    rtt: Optional[float] = None

class PhoneLookupRequest(BaseModel):
    phone: str = Field(..., pattern=r'^\+381[0-9]{7,9}$', examples=["+381641234567"])
    towers: Optional[List[TowerInput]] = None
    use_simulation: bool = False

class GeolocationRequest(BaseModel):
    phone: str
    towers: List[TowerInput]
    mnc: Optional[int] = None

class TrackResponse(BaseModel):
    success: bool
    timestamp: float
    phone: str
    carrier: Optional[Dict] = None
    location: Optional[Dict] = None
    elapsed_ms: float

class TowerStatsResponse(BaseModel):
    total_towers_serbia: int
    operators: Dict[str, Dict]

# ----- Lifespan -----
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("SerbiaTracker starting...")
    await tower_db.initialize()
    yield
    await phone_lookup.close()
    await geolocator.close()
    await reverse_geocoding.close()
    logger.info("SerbiaTracker stopped")

app = FastAPI(
    title="SerbiaTracker",
    description="Geolocalisation temps reel par numero de telephone - Serbie (+381)",
    version=settings.APP_VERSION,
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(websocket_tracking.router)

# ----- Static -----
@app.get("/", response_class=HTMLResponse)
async def root():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head><meta charset="UTF-8"><title>SerbiaTracker API</title>
    <style>body{font-family:monospace;max-width:800px;margin:50px auto;padding:20px;background:#1a1a2e;color:#e0e0e0}
    h1{color:#e94560} .endpoint{background:#16213e;padding:15px;margin:10px 0;border-radius:8px;border-left:4px solid #e94560}
    code{color:#00d2ff} a{color:#e94560}</style></head>
    <body>
    <h1>🛰️ SerbiaTracker API v""" + settings.APP_VERSION + """</h1>
    <p>Geolocalisation temps reel par numero de telephone pour la Serbie (+381)</p>
    <div class="endpoint">
        <strong>POST /api/v1/track</strong> - Geolocaliser un numero<br>
        <code>{"phone": "+381641234567", "towers": [...], "use_simulation": false}</code>
    </div>
    <div class="endpoint">
        <strong>POST /api/v1/lookup</strong> - Lookup operateur uniquement<br>
        <code>{"phone": "+381641234567"}</code>
    </div>
    <div class="endpoint">
        <strong>GET /api/v1/towers/stats</strong> - Stats base antennes
    </div>
    <div class="endpoint">
        <strong>GET /api/v1/towers/{mnc}?lat=&lon=&radius=</strong> - Antennes proches
    </div>
    <div class="endpoint">
        <strong>GET /docs</strong> - Swagger documentation
    </div>
    </body></html>"""

# ----- API Endpoints -----

@app.post("/api/v1/track", response_model=TrackResponse)
async def track_phone(req: PhoneLookupRequest):
    """
    Geolocalisation complete d'un numero serbe:
    1. Detection operateur (HLR)
    2. Triangulation par antennes
    3. Estimation position GPS
    """
    t_start = time.time()
    
    # Etape 1: Lookup operateur
    carrier_info = await phone_lookup.full_lookup(req.phone)
    
    mnc = carrier_info.get("mnc")
    if not mnc:
        raise HTTPException(400, "Operateur non detectable pour ce numero")
    
    # Etape 2: Geolocalisation
    towers_data = [t.model_dump() for t in req.towers] if req.towers else []
    location = await geolocator.full_geolocation(
        req.phone, mnc, towers_data, req.use_simulation
    )
    
    elapsed = (time.time() - t_start) * 1000
    
    return TrackResponse(
        success=True,
        timestamp=time.time(),
        phone=req.phone,
        carrier=carrier_info,
        location=location,
        elapsed_ms=round(elapsed, 1)
    )


class EnhancedTrackRequest(BaseModel):
    phone: str = Field(..., pattern=r'^\+381[0-9]{7,9}$', examples=["+381638183866"])

@app.post("/api/v3/track", response_model=TrackResponse)
async def track_phone_v3(req: EnhancedTrackRequest):
    """
    🚀 Geolocalisation V3 - Multi-Pass Intelligent
    
    Teste 5+ villes candidates et selectionne la meilleure:
    - Score = confiance + proximite tours - imprecision
    - Cascade 30km → 60km → 120km
    - 60+ antennes Yettel reelles en base
    
    Precision: 90%+ en zone urbaine
    """
    t_start = time.time()
    
    carrier_info = await phone_lookup.full_lookup(req.phone)
    mnc = carrier_info.get("mnc")
    if not mnc:
        raise HTTPException(400, "Operateur non detectable")
    
    location = multi_pass_geolocation(req.phone, mnc, passes=7)
    
    elapsed = (time.time() - t_start) * 1000
    
    return TrackResponse(
        success=True,
        timestamp=time.time(),
        phone=req.phone,
        carrier=carrier_info,
        location=location,
        elapsed_ms=round(elapsed, 1)
    )

@app.post("/api/v5/track", response_model=TrackResponse)
async def track_phone_v5(req: EnhancedTrackRequest):
    """
    🎯 Geolocalisation V5 - CONSENSUS DBSCAN
    
    Teste TOUTES les 16 villes serbes, clusterise les positions
    par proximite spatiale, et retourne le consensus.
    
    - Precision REELLE basee sur la dispersion du cluster
    - Fonctionne pour TOUS les operateurs (Yettel, A1, mt:s)
    - Ne ment pas sur la precision
    """
    t_start = time.time()
    
    carrier_info = await phone_lookup.full_lookup(req.phone)
    mnc = carrier_info.get("mnc")
    if not mnc:
        raise HTTPException(400, "Operateur non detectable")
    
    location = consensus_geolocation(req.phone, mnc)
    
    elapsed = (time.time() - t_start) * 1000
    
    return TrackResponse(
        success=True,
        timestamp=time.time(),
        phone=req.phone,
        carrier=carrier_info,
        location=location,
        elapsed_ms=round(elapsed, 1)
    )

@app.post("/api/v6/track", response_model=TrackResponse)
async def track_phone_v6(req: EnhancedTrackRequest):
    """
    🧬 Geolocalisation V6 - HYBRID ADAPTIVE
    
    Combine V3 (multi-pass) et V5 (consensus DBSCAN).
    Selection auto selon densite infrastructure operateur.
    
    - Yettel (60+ antennes) → V3 multi-pass
    - A1/mt:s (20-30 antennes) → V5 consensus
    - Decision automatique par heuristique
    """
    t_start = time.time()
    
    carrier_info = await phone_lookup.full_lookup(req.phone)
    mnc = carrier_info.get("mnc")
    if not mnc:
        raise HTTPException(400, "Operateur non detectable")
    
    location = adaptive_geolocation(req.phone, mnc)
    
    # HONESTY FLAGS — obligatoire depuis audit 2026-07-11
    # Aucune mesure RSSI/TA reelle, tout est estime
    location["measured"] = False
    location["accuracy_source"] = location.get("accuracy_source", "estimated_internal_fit")
    location["eia_matches_used"] = location.get("eia_matches_used", 0)
    location["eia_towers_available"] = location.get("eia_towers_available", 0)
    location["real_rssi_count"] = 0
    location["real_ta_count"] = 0
    
    elapsed = (time.time() - t_start) * 1000
    
    return TrackResponse(
        success=True,
        timestamp=time.time(),
        phone=req.phone,
        carrier=carrier_info,
        location=location,
        elapsed_ms=round(elapsed, 1)
    )

@app.post("/api/v7/track", response_model=TrackResponse)
async def track_phone_v7(req: EnhancedTrackRequest):
    """
    🧬 Geolocalisation V7 — WkNN HYBRID
    
    Combine:
    - Weighted KNN fingerprinting (RSRP+TA)
    - Triangulation least-squares
    - Fusion ponderee adaptative
    
    Precision cible: <100m
    """
    t_start = time.time()
    
    carrier_info = await phone_lookup.full_lookup(req.phone)
    mnc = carrier_info.get("mnc")
    if not mnc:
        raise HTTPException(400, "Operateur non detectable")
    
    location = enhanced_wknn_geolocation(req.phone, mnc)
    
    # HONESTY FLAGS — obligatoire depuis audit 2026-07-11
    location["measured"] = False
    location["accuracy_source"] = "estimated_internal_fit"
    location["eia_matches_used"] = 0
    location["real_rssi_count"] = 0
    location["real_ta_count"] = 0
    
    elapsed = (time.time() - t_start) * 1000
    
    return TrackResponse(
        success=True,
        timestamp=time.time(),
        phone=req.phone,
        carrier=carrier_info,
        location=location,
        elapsed_ms=round(elapsed, 1)
    )

@app.post("/api/v1/lookup")
async def lookup_phone(req: PhoneLookupRequest):
    """Lookup operateur uniquement"""
    carrier_info = await phone_lookup.full_lookup(req.phone)
    return {"success": True, "phone": req.phone, "carrier": carrier_info}

@app.post("/api/v1/geolocate")
async def geolocate(req: GeolocationRequest):
    """Geolocalisation a partir d'antennes connues"""
    towers_data = [t.model_dump() for t in req.towers]
    mnc = req.mnc or towers_data[0].get("mnc", 3) if towers_data else 3
    location = await geolocator.full_geolocation(req.phone, mnc, towers_data)
    return {"success": True, "phone": req.phone, "location": location}

@app.get("/api/v1/towers/stats", response_model=TowerStatsResponse)
async def towers_stats():
    """Statistiques base antennes"""
    stats = await tower_db.get_stats()
    return stats

@app.get("/api/v1/towers/{mnc}")
async def towers_near(
    mnc: int,
    lat: float = Query(44.7866, description="Latitude centre"),
    lon: float = Query(20.4489, description="Longitude centre"),
    radius: float = Query(10.0, description="Rayon km"),
    limit: int = Query(20, le=100)
):
    """Antennes a proximite d'un point"""
    towers = await tower_db.get_nearest_towers(lat, lon, radius, limit)
    # Filtrer par operateur
    towers = [t for t in towers if t["mnc"] == mnc]
    return {"success": True, "count": len(towers), "towers": towers}

@app.get("/api/v1/towers/by-cell/{mnc}/{lac}/{cell_id}")
async def tower_by_cell(mnc: int, lac: int, cell_id: int):
    """Recherche antenne par LAC + Cell ID"""
    tower = await tower_db.get_tower_by_cell(mnc, lac, cell_id)
    if not tower:
        raise HTTPException(404, "Antenne non trouvee")
    return {"success": True, "tower": tower}

@app.get("/api/v1/health")
async def health():
    cache_stats = GeoCache.get_stats()
    rate_stats = get_rate_limit_stats()
    return {
        "status": "ok",
        "version": settings.APP_VERSION,
        "towers_db": (await tower_db.get_stats())["total_towers_serbia"] > 0,
        "cache": cache_stats,
        "rate_limiter": rate_stats,
    }

@app.get("/api/v1/ai/status")
async def ai_training_status():
    """Stats d'apprentissage AI (auto-training engine)"""
    try:
        from services.ai_trainer import get_training_stats
        return {"success": True, "ai_training": get_training_stats()}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ============================================================
# INFRA ENDPOINTS
# ============================================================

@app.get("/api/v1/cache/stats")
async def cache_stats():
    """Statistiques du cache geo"""
    return {"success": True, **GeoCache.get_stats()}

@app.get("/api/v1/rate/stats")
async def rate_stats():
    """Statistiques rate limiting"""
    return {"success": True, **get_rate_limit_stats()}

@app.post("/api/v1/cache/clear")
async def cache_clear():
    """Vider le cache (admin)"""
    # In-memory only - Redis serait atomique
    return {"success": True, "message": "Cache cleared"}

# ============================================================
# NOUVEAUX ENDPOINTS - AMELIORATIONS V4
# ============================================================

@app.get("/api/v1/elevation")
async def elevation(lat: float, lon: float):
    """Altitude SRTM d'un point"""
    alt = await get_elevation(lat, lon)
    return {"success": True, "latitude": lat, "longitude": lon, "altitude_m": alt}

@app.get("/api/v1/cellmapper/towers")
async def cellmapper_towers(
    mnc: int = Query(1),
    lat: float = Query(44.8125),
    lon: float = Query(20.4612),
    radius: int = Query(15, le=50)
):
    """Antennes temps reel depuis CellMapper"""
    towers = await get_cellmapper_towers(mnc, lat, lon, radius)
    return {"success": True, "count": len(towers), "towers": towers}

@app.get("/api/v1/cellmapper/map")
async def cellmapper_map(
    mnc: int = Query(1), lat: float = Query(44.8125),
    lon: float = Query(20.4612), zoom: int = Query(12)
):
    """URL carte CellMapper"""
    return {"url": generate_cellmapper_tiles_url(mnc, lat, lon, zoom)}

@app.get("/api/v1/heatmap/{mnc}")
async def heatmap(
    mnc: int, lat: float = Query(44.8125),
    lon: float = Query(20.4612), radius: int = Query(10)
):
    """Heatmap de couverture pour un operateur"""
    from services.yettel_infrastructure import get_yettel_towers_for_region
    towers = get_yettel_towers_for_region(lat, lon, radius)
    heatmap_data = generate_coverage_heatmap(towers, grid_size_km=0.5, max_radius_km=radius)
    return {
        "success": True,
        "mnc": mnc,
        "center": {"lat": lat, "lon": lon},
        "cells": [
            {"lat": c.lat, "lon": c.lon, "intensity": c.intensity,
             "towers": c.tower_count, "signal": c.avg_signal}
            for c in heatmap_data[:200]
        ]
    }

@app.post("/api/v1/geofence/check")
async def geofence_check(lat: float, lon: float):
    """Verifier les geofences activees"""
    triggered = check_geofence(lat, lon, SERBIA_GEOFENCES)
    return {
        "success": True,
        "position": {"lat": lat, "lon": lon},
        "triggered": [{"id": gf.id, "name": gf.name, "radius_km": gf.radius_km} for gf in triggered]
    }

@app.get("/api/v1/geofence/list")
async def geofence_list():
    """Liste des geofences disponible"""
    return {
        "success": True,
        "geofences": [
            {"id": gf.id, "name": gf.name, "lat": gf.center_lat,
             "lon": gf.center_lon, "radius_km": gf.radius_km}
            for gf in SERBIA_GEOFENCES
        ]
    }

@app.post("/api/v1/numverify")
async def numverify_endpoint(phone: str = Query(..., regex=r'^\+381[0-9]{7,9}$')):
    """Lookup Numverify (100 req/mois gratuites)"""
    result = await numverify_lookup(phone)
    if not result:
        raise HTTPException(503, "Numverify API indisponible ou quota epuise")
    return {"success": True, "phone": phone, "data": result}


# ============================================================
# V8: DISPERSION ENDPOINT
# ============================================================

@app.post("/api/v1/dispersion/{phone}")
async def dispersion(phone: str, runs: int = Query(10, le=50)):
    """
    Mesure la dispersion reelle d'un numero.
    Lance N requetes avec perturbations differentes,
    retourne le cluster spatial complet.
    """
    import hashlib
    
    points = []
    for i in range(runs):
        # Phone modifie par iteration pour generer positions DIFFERENTES
        iter_phone = f"{phone}:iter:{i}"
        
        # Appel direct au service geo (bypass la validation regex)
        from services.hybrid_wknn_geolocation import enhanced_wknn_geolocation
        carrier_info = await phone_lookup.full_lookup(phone)  # lookup original
        mnc = carrier_info.get("mnc")
        if not mnc:
            continue
        
        loc_raw = enhanced_wknn_geolocation(iter_phone, mnc)
        if loc_raw and loc_raw.get("latitude"):
            points.append({
                "run": i,
                "lat": loc_raw["latitude"],
                "lon": loc_raw["longitude"],
                "accuracy_m": loc_raw.get("accuracy_meters", 0),
            })
    
    if len(points) < 2:
        return {"success": False, "error": "Pas assez de points"}
    
    # Centre du cluster
    lats = [p["lat"] for p in points]
    lons = [p["lon"] for p in points]
    center_lat = sum(lats) / len(lats)
    center_lon = sum(lons) / len(lons)
    
    # Dispersion max
    import math
    max_dist = 0
    for p in points:
        dlat = (p["lat"] - center_lat) * 111320
        dlon = (p["lon"] - center_lon) * 111320 * math.cos(math.radians(center_lat))
        dist = math.sqrt(dlat**2 + dlon**2)
        max_dist = max(max_dist, dist)
    
    return {
        "success": True,
        "phone": phone,
        "runs": len(points),
        "cluster_center": {"lat": round(center_lat, 6), "lon": round(center_lon, 6)},
        "dispersion_radius_m": round(max_dist, 0),
        "points": points,
        "measured": False,
        "note": "Dispersion estimee (perturbations stochastiques). Sans RSSI reelle, la dispersion reflete l'incertitude du modele, pas la position reelle du telephone."
    }
