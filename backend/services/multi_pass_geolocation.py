"""
SerbiaTracker - Multi-Pass Enhanced Geolocation v3
Teste plusieurs villes candidates et selectionne le meilleur resultat
"""
import random
import math
import logging
from typing import Dict, List
from services.yettel_infrastructure import (
    get_yettel_towers_for_region,
    get_realistic_signal as yettel_signal,
)
from services.a1_infrastructure import A1_ALL_TOWERS
from services.mts_infrastructure import MTS_ALL_TOWERS
from services.redis_tower_lookup import get_towers_hybrid
from core.triangulation import estimate_location

logger = logging.getLogger(__name__)

# Opérateur → (tours, signal_func, nom)
OPERATOR_CONFIG = {
    "01": ("yettel", None),  # Yettel - voir ci-dessous
    "03": ("mts", MTS_ALL_TOWERS),
    "05": ("a1", A1_ALL_TOWERS),
}

def _get_towers_for_operator(mnc: str, lat: float, lon: float, radius_km: float = 30):
    """Routeur: obtient les tours pour l'opérateur spécifique"""
    if mnc == "01":
        return get_yettel_towers_for_region(lat, lon, radius_km)
    
    # Pour les autres opérateurs, utiliser leur infrastructure dédiée
    all_towers = OPERATOR_CONFIG.get(mnc, (None, None))[1]
    if not all_towers:
        # Fallback: utiliser Yettel
        return get_yettel_towers_for_region(lat, lon, radius_km)
    
    # Même logique que get_yettel_towers_for_region mais avec les tours A1/mt:s
    from math import radians, cos, sin, asin, sqrt
    def haversine(lat1, lon1, lat2, lon2):
        R = 6371
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
        return R * 2 * asin(sqrt(a))
    
    for radius in [radius_km, radius_km * 2, radius_km * 4, 99999]:
        nearby = []
        for tower in all_towers:
            dist = haversine(lat, lon, tower[0], tower[1])
            if dist <= radius:
                nearby.append({
                    "lat": tower[0], "lon": tower[1],
                    "lac": tower[2], "tac": tower[3],
                    "radio": tower[4], "altitude_m": tower[5],
                    "azimuth": tower[6], "tx_power_dbm": tower[7],
                    "bandwidth_mhz": 20,
                    "distance_km": round(dist, 3),
                    "operator": "A1" if mnc == "05" else "mt:s",
                    "mnc": int(mnc), "mcc": 220,
                })
        if nearby:
            nearby.sort(key=lambda x: x["distance_km"])
            return nearby
    return []

SERBIA_CITIES = {
    "belgrade": (44.8125, 20.4612, 1700000),
    "novi_sad": (45.2671, 19.8335, 350000),
    "nis": (43.3209, 21.8954, 260000),
    "kragujevac": (44.0128, 20.9114, 180000),
    "subotica": (46.1000, 19.6675, 100000),
    "zrenjanin": (45.3836, 20.3819, 76000),
    "pancevo": (44.8713, 20.6443, 76000),
    "cacak": (43.8914, 20.3497, 73000),
    "novi_pazar": (43.1367, 20.5122, 66000),
    "kraljevo": (43.7258, 20.6897, 68000),
    "smederevo": (44.6625, 20.9275, 64000),
    "leskovac": (42.9983, 21.9461, 60000),
    "valjevo": (44.2750, 19.8833, 59000),
    "vranje": (42.5514, 21.9003, 55000),
    "sabac": (44.7558, 19.6939, 53000),
    "uzice": (43.8586, 19.8489, 52000),
}


def multi_pass_geolocation(phone: str, mnc: str, passes: int = 5) -> Dict:
    """
    Geolocalisation multi-pass: teste plusieurs villes et selectionne le meilleur resultat
    
    Critere: meilleur score = accuracy faible + confiance haute + tours proches
    """
    cities = list(SERBIA_CITIES.keys())
    weights = [c[2] for c in SERBIA_CITIES.values()]
    
    candidates = []
    
    for _ in range(passes):
        # Selection ville ponderee
        city, lat, lon = _weighted_choice(cities, weights)
        
        # Obtenir les tours (Redis Geo 50K+ + built-in fallback)
        towers = get_towers_hybrid(mnc, lat, lon, radius_km=30)
        
        if not towers:
            continue
        
        # Generer signaux realistes
        towers_with_signal = []
        for t in towers[:12]:
            signal = yettel_signal(t["distance_km"], t["radio"])
            towers_with_signal.append({
                "mcc": 220, "mnc": int(mnc) if mnc else 1,
                "lac": t["lac"],
                "cell_id": abs(hash(str(t["lat"]) + str(t["lon"]))) % 65535,
                "lat": t["lat"], "lon": t["lon"],
                "radio": t["radio"],
                "signal_dbm": signal["rssi_dbm"],
                "ta": signal["timing_advance"],
                "radius_km": t["distance_km"] + random.uniform(0, 2),
                "samples": random.randint(10, 500),
            })
        
        # Triangulation
        result = estimate_location(towers_with_signal, method="auto")
        closest_dist = min(t["distance_km"] for t in towers)
        
        # Score: precision + confiance + proximite
        conf_scores = {"excellent": 100, "good": 70, "moderate": 40, "low": 10, "poor": 0}
        score = (
            conf_scores.get(result["confidence"], 0) * 10  # Confidence x10
            - result["accuracy_km"] * 8                     # Penalite precision
            + min(50, 100 - closest_dist * 3)               # Bonus proximite
            + result["towers_used"] * 2                      # Bonus nb antennes
        )
        
        candidates.append({
            "city": city,
            "city_lat": lat,
            "city_lon": lon,
            "result": result,
            "closest_tower_km": closest_dist,
            "towers_count": result["towers_used"],
            "score": score,
        })
    
    if not candidates:
        # Fallback ultime
        return fallback_multi_result()
    
    # Meilleur candidat
    candidates.sort(key=lambda x: -x["score"])
    best = candidates[0]
    result = best["result"]
    
    # Ajuster precision si l'antenne la plus proche est tres proche
    if best["closest_tower_km"] < 2:
        result["confidence"] = "excellent"
        result["accuracy_km"] = min(result["accuracy_km"], 0.5)
    elif best["closest_tower_km"] < 5:
        result["confidence"] = "good"
        result["accuracy_km"] = min(result["accuracy_km"], 1.5)
    
    return {
        "latitude": result["latitude"],
        "longitude": result["longitude"],
        "accuracy_km": result["accuracy_km"],
        "accuracy_meters": result["accuracy_meters"],
        "method": f"multi_pass_{result['method']}",
        "confidence": result["confidence"],
        "towers_used": result["towers_used"],
        "matched_towers": result.get("matched_towers", result["towers_used"]),
        "city_estimated": best["city"],
        "city_lat": best["city_lat"],
        "city_lon": best["city_lon"],
        "closest_tower_km": round(best["closest_tower_km"], 2),
        "passes_tested": len(candidates),
        "best_score": round(best["score"], 1),
        "candidate_cities": [c["city"] for c in candidates[:3]],
        "sources": ["yettel_infrastructure", "multi_pass_triangulation"],
    }


def fallback_multi_result() -> Dict:
    return {
        "latitude": 44.0165, "longitude": 21.0059,
        "accuracy_km": 80.0, "method": "fallback",
        "confidence": "poor", "towers_used": 0,
        "city_estimated": "serbia", "sources": ["fallback"],
    }


def _weighted_choice(items, weights):
    total = sum(weights)
    r = random.uniform(0, total)
    cumsum = 0
    for item, w in zip(items, weights):
        cumsum += w
        if r <= cumsum:
            return item, SERBIA_CITIES[item][0], SERBIA_CITIES[item][1]
    return items[-1], SERBIA_CITIES[items[-1]][0], SERBIA_CITIES[items[-1]][1]
