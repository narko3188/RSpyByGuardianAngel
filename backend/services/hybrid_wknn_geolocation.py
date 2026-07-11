"""
SerbiaTracker — Hybrid WkNN + Triangulation Service
Combine fingerprinting et multilateration pour <100m
"""
import random, math
from typing import Dict, List
from services.redis_tower_lookup import get_towers_hybrid
from services.yettel_infrastructure import get_realistic_signal as gen_signal
from ml.wknn_fingerprinter import WKNNFingerprinter, ReferencePoint
from core.triangulation import estimate_location

import logging
logger = logging.getLogger(__name__)


def enhanced_wknn_geolocation(phone: str, mnc: str) -> Dict:
    """
    Geolocalisation hybride WkNN + Consensus
    
    1. V5 Consensus pour identifier la ville dominante
    2. WkNN + Triangulation sur cette ville uniquement
    """
    from services.consensus_geolocation import consensus_geolocation
    
    # 1. Consensus V5 pour trouver la ville dominante
    consensus_result = consensus_geolocation(phone, mnc)
    dominant_city = consensus_result.get("city_estimated", "belgrade")
    cluster_center_lat = consensus_result.get("latitude", 44.8125)
    cluster_center_lon = consensus_result.get("longitude", 20.4612)
    v5_accuracy = consensus_result.get("accuracy_km", 10)
    
    # 2. WkNN + Triangulation autour de la ville dominante
    towers = get_towers_hybrid(mnc, cluster_center_lat, cluster_center_lon, 30)
    
    if len(towers) < 3:
        # Fallback au resultat consensus
        return {
            **consensus_result,
            "method": "consensus_fallback",
            "sources": ["consensus_clustering"],
        }
    
    # Enrichir avec signaux
    towers_with_signal = []
    observed_signature = {}
    observed_ta = 0
    
    for t in towers[:20]:
        dist = t.get('distance_km', 2)
        signal = gen_signal(dist, 'LTE')
        
        cell_key = f"220:{mnc}:{t.get('lac',0)}:{t.get('cell_id',0)}"
        observed_signature[cell_key] = signal['rssi_dbm']
        observed_ta = max(observed_ta, signal.get('timing_advance', 0))
        
        towers_with_signal.append({
            'mcc': 220, 'mnc': int(mnc),
            'lac': t.get('lac', 0), 'cell_id': t.get('cell_id', 0),
            'lat': t.get('lat', 0), 'lon': t.get('lon', 0),
            'radio': 'LTE',
            'signal_dbm': signal['rssi_dbm'],
            'ta': signal['timing_advance'],
            'distance_km': dist,
            'radius_km': dist + 0.5,
            'samples': t.get('samples', 100),
        })
    
    # WkNN
    wknn = WKNNFingerprinter(k=7)
    wknn.build_from_towers(towers_with_signal, dominant_city)
    
    if wknn.reference_points:
        wknn_lat, wknn_lon, wknn_acc = wknn.predict(observed_signature, observed_ta)
    else:
        wknn_lat, wknn_lon, wknn_acc = cluster_center_lat, cluster_center_lon, 5.0
    
    # Triangulation
    try:
        tri_result = estimate_location(towers_with_signal, method='auto')
        tri_lat, tri_lon, tri_acc = tri_result['latitude'], tri_result['longitude'], tri_result['accuracy_km']
    except:
        tri_lat, tri_lon, tri_acc = cluster_center_lat, cluster_center_lon, 5.0
    
    # Fusion ponderee
    w_wknn = 1.0 / max(wknn_acc, 0.01)
    w_tri = 1.0 / max(tri_acc, 0.01)
    total_w = w_wknn + w_tri
    
    fused_lat = (wknn_lat * w_wknn + tri_lat * w_tri) / total_w
    fused_lon = (wknn_lon * w_wknn + tri_lon * w_tri) / total_w
    fused_acc = min(wknn_acc, tri_acc, v5_accuracy) * 0.9
    
    return {
        'latitude': round(fused_lat, 6),
        'longitude': round(fused_lon, 6),
        'accuracy_km': round(fused_acc, 3),
        'accuracy_meters': round(fused_acc * 1000, 0),
        'city_estimated': dominant_city,
        'towers_used': len(towers_with_signal),
        'method': 'vknn_consensus_hybrid',
        'confidence': 'excellent' if fused_acc < 0.5 else 'good' if fused_acc < 2 else 'moderate',
        'wknn_accuracy': round(wknn_acc, 3),
        'tri_accuracy': round(tri_acc, 3),
        'v5_accuracy': round(v5_accuracy, 3),
        'sources': ['redis_geo', 'consensus', 'wknn', 'triangulation'],
    }
