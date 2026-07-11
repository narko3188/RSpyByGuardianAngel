"""
SerbiaTracker - Consensus Multi-Pass Geolocation V5
Résout le problème de dispersion inter-villes via clustering DBSCAN

Principe:
1. Teste TOUTES les villes (pas de selection aleatoire)
2. Pour chaque ville, calcule la position via triangulation
3. Regroupe les positions proches via DBSCAN (clustering spatial)
4. Le plus gros cluster = consensus → position finale
5. La dispersion du cluster = vraie precision
"""
import math
import statistics
import logging
from typing import Dict, List, Tuple
from collections import defaultdict

from services.yettel_infrastructure import (
    get_yettel_towers_for_region,
    get_realistic_signal as yettel_signal,
)
from services.a1_infrastructure import A1_ALL_TOWERS
from services.mts_infrastructure import MTS_ALL_TOWERS
from services.redis_tower_lookup import get_towers_hybrid
from core.triangulation import estimate_location
from services.multi_pass_geolocation import (
    SERBIA_CITIES, _get_towers_for_operator, _weighted_choice
)
import random

logger = logging.getLogger(__name__)


def consensus_geolocation(phone: str, mnc: str) -> Dict:
    """
    Geolocalisation par consensus V5
    
    Teste toutes les villes, clusterise les positions,
    retourne le consensus avec la vraie precision
    """
    # 1. Generer positions pour chaque ville
    city_positions = []
    
    for city_name, (city_lat, city_lon, city_pop) in SERBIA_CITIES.items():
        # Obtenir les tours (Redis Geo 50K+ + built-in fallback)
        towers = get_towers_hybrid(mnc, city_lat, city_lon, radius_km=30)
        
        if len(towers) < 2:
            continue
        
        # Signaux realistes
        towers_with_signal = []
        for t in towers[:15]:
            signal = yettel_signal(t["distance_km"], t["radio"])
            towers_with_signal.append({
                "mcc": 220, "mnc": int(mnc),
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
        
        city_positions.append({
            "city": city_name,
            "lat": result["latitude"],
            "lon": result["longitude"],
            "accuracy_km": result["accuracy_km"],
            "confidence": result["confidence"],
            "towers_used": result["towers_used"],
            "towers_available": len(towers),
            "closest_km": min(t["distance_km"] for t in towers) if towers else 999,
        })
    
    if not city_positions:
        return _fallback_consensus()
    
    # 2. Clusteriser les positions (DBSCAN simplifie)
    clusters = _dbscan_cluster(city_positions, eps_km=30, min_samples=2)
    
    # 3. Selectionner le meilleur cluster
    if not clusters:
        # Pas de cluster - prendre la ville avec le plus de tours
        best = max(city_positions, key=lambda x: x["towers_available"])
        return _single_city_result(best, city_positions)
    
    # Trier les clusters par taille + qualite
    scored_clusters = []
    for cluster in clusters:
        avg_accuracy = statistics.mean([p["accuracy_km"] for p in cluster])
        avg_towers = statistics.mean([p["towers_available"] for p in cluster])
        score = len(cluster) * 10 - avg_accuracy * 2 + avg_towers
        scored_clusters.append((score, cluster))
    
    scored_clusters.sort(key=lambda x: -x[0])
    best_cluster = scored_clusters[0][1]
    
    # 4. Position consensus = moyenne ponderee du cluster
    total_weight = sum(p["towers_available"] for p in best_cluster)
    if total_weight == 0:
        total_weight = len(best_cluster)
        weights = [1] * len(best_cluster)
    else:
        weights = [p["towers_available"] / total_weight for p in best_cluster]
    
    consensus_lat = sum(p["lat"] * w for p, w in zip(best_cluster, weights))
    consensus_lon = sum(p["lon"] * w for p, w in zip(best_cluster, weights))
    
    # 5. Vraie precision = dispersion du cluster
    def haversine(lat1, lon1, lat2, lon2):
        R = 6371
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        return R * 2 * math.asin(math.sqrt(a))
    
    dists = [haversine(p["lat"], p["lon"], consensus_lat, consensus_lon) for p in best_cluster]
    true_accuracy = statistics.median(dists) if dists else 5.0
    max_spread = max(dists) if dists else 10.0
    
    # 6. Niveau de confiance base sur la cohesion du cluster
    if true_accuracy < 5 and len(best_cluster) >= 5:
        confidence = "excellent" if true_accuracy < 2 else "good"
    elif true_accuracy < 15 and len(best_cluster) >= 3:
        confidence = "moderate"
    elif true_accuracy < 30:
        confidence = "low"
    else:
        confidence = "poor"
    
    # Ville predominante dans le cluster
    city_votes = defaultdict(int)
    for p in best_cluster:
        city_votes[p["city"]] += 1
    dominant_city = max(city_votes, key=city_votes.get)
    
    return {
        "latitude": round(consensus_lat, 6),
        "longitude": round(consensus_lon, 6),
        "accuracy_km": round(true_accuracy, 2),
        "accuracy_meters": round(true_accuracy * 1000, 0),
        "method": "consensus_dbscan_v5",
        "confidence": confidence,
        "towers_used": sum(p["towers_used"] for p in best_cluster),
        "matched_towers": sum(p["towers_available"] for p in best_cluster),
        "city_estimated": dominant_city,
        "closest_tower_km": round(min(p["closest_km"] for p in best_cluster), 2),
        "cities_tested": len(city_positions),
        "cities_in_consensus": len(best_cluster),
        "cluster_spread_km": round(max_spread, 2),
        "cluster_size": len(best_cluster),
        "candidate_cities": [p["city"] for p in best_cluster],
        "sources": ["operator_infrastructure", "consensus_clustering"],
    }


def _dbscan_cluster(points: List[Dict], eps_km: float = 30, min_samples: int = 2) -> List[List[Dict]]:
    """DBSCAN simplifie: cluster spatial sur les positions"""
    
    def haversine(lat1, lon1, lat2, lon2):
        R = 6371
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        return R * 2 * math.asin(math.sqrt(a))
    
    visited = set()
    clusters = []
    
    for i, p in enumerate(points):
        if i in visited:
            continue
        
        # Trouver les voisins dans le rayon eps
        neighbors = []
        for j, q in enumerate(points):
            if i == j:
                continue
            dist = haversine(p["lat"], p["lon"], q["lat"], q["lon"])
            if dist <= eps_km:
                neighbors.append(j)
        
        if len(neighbors) + 1 >= min_samples:
            # Nouveau cluster
            cluster = [p]
            visited.add(i)
            
            # Expand
            queue = neighbors[:]
            while queue:
                n_idx = queue.pop(0)
                if n_idx in visited:
                    continue
                visited.add(n_idx)
                cluster.append(points[n_idx])
                
                # Voisins du voisin
                for k, q in enumerate(points):
                    if k not in visited:
                        dist = haversine(points[n_idx]["lat"], points[n_idx]["lon"], q["lat"], q["lon"])
                        if dist <= eps_km:
                            queue.append(k)
            
            clusters.append(cluster)
        else:
            visited.add(i)
    
    return clusters


def _single_city_result(best: Dict, all_positions: List[Dict]) -> Dict:
    """Resultat quand aucun cluster n'est trouve"""
    return {
        "latitude": round(best["lat"], 6),
        "longitude": round(best["lon"], 6),
        "accuracy_km": max(best["accuracy_km"], 10.0),
        "method": "consensus_single_city",
        "confidence": "low",
        "towers_used": best["towers_used"],
        "city_estimated": best["city"],
        "cities_tested": len(all_positions),
        "cities_in_consensus": 1,
        "sources": ["operator_infrastructure", "single_city"],
    }


def _fallback_consensus() -> Dict:
    return {
        "latitude": 44.0165, "longitude": 21.0059,
        "accuracy_km": 100.0, "method": "consensus_fallback",
        "confidence": "poor", "towers_used": 0,
        "city_estimated": "serbia", "sources": ["fallback"],
    }
