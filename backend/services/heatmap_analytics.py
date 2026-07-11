"""
SerbiaTracker - Service Heatmap & Analytics
Generation de heatmaps de couverture et geofencing
"""
import math
import random
from typing import Dict, List, Tuple
from dataclasses import dataclass
from collections import defaultdict


@dataclass
class HeatmapCell:
    lat: float
    lon: float
    intensity: float  # 0-1
    tower_count: int
    avg_signal: float


@dataclass 
class Geofence:
    id: str
    name: str
    center_lat: float
    center_lon: float
    radius_km: float
    active: bool = True


def generate_coverage_heatmap(
    towers: List[dict],
    grid_size_km: float = 0.5,
    max_radius_km: float = 10.0
) -> List[HeatmapCell]:
    """
    Generer une heatmap de couverture a partir des antennes
    
    Chaque antenne contribue a sa zone de couverture
    avec une decroissance du signal en fonction de la distance
    """
    if not towers:
        return []
    
    # Calculer les bornes
    lats = [t["lat"] for t in towers]
    lons = [t["lon"] for t in towers]
    
    min_lat, max_lat = min(lats) - 0.05, max(lats) + 0.05
    min_lon, max_lon = min(lons) - 0.05, max(lons) + 0.05
    
    deg_per_km_lat = 1 / 111.32
    deg_per_km_lon = 1 / (111.32 * math.cos(math.radians(sum(lats) / len(lats))))
    
    grid_lat_step = grid_size_km * deg_per_km_lat
    grid_lon_step = grid_size_km * deg_per_km_lon
    
    cells = []
    lat = min_lat
    
    while lat <= max_lat:
        lon = min_lon
        while lon <= max_lon:
            # Calculer la contribution de chaque antenne a ce point
            total_signal = 0
            contributing_towers = 0
            
            for tower in towers:
                dist_km = haversine(lat, lon, tower["lat"], tower["lon"])
                if dist_km <= max_radius_km:
                    # Signal decroit avec la distance
                    signal = max(0, 1.0 - dist_km / max_radius_km)
                    total_signal += signal
                    contributing_towers += 1
            
            if total_signal > 0:
                intensity = min(1.0, total_signal / max_radius_km)
                cells.append(HeatmapCell(
                    lat=round(lat, 5),
                    lon=round(lon, 5),
                    intensity=round(intensity, 3),
                    tower_count=contributing_towers,
                    avg_signal=round(-50 - (1 - intensity) * 60, 1),
                ))
            
            lon += grid_lon_step
        lat += grid_lat_step
    
    return cells


def check_geofence(
    lat: float, lon: float,
    geofences: List[Geofence]
) -> List[Geofence]:
    """Verifier si une position est dans une ou plusieurs geofences"""
    triggered = []
    for gf in geofences:
        if not gf.active:
            continue
        dist = haversine(lat, lon, gf.center_lat, gf.center_lon)
        if dist <= gf.radius_km:
            triggered.append(gf)
    return triggered


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


# Geofences predefinies pour la Serbie
SERBIA_GEOFENCES = [
    Geofence("bg", "Belgrade Center", 44.8125, 20.4612, 5.0),
    Geofence("ns", "Novi Sad Center", 45.2671, 19.8335, 3.0),
    Geofence("ni", "Nis Center", 43.3209, 21.8954, 3.0),
    Geofence("kg", "Kragujevac Center", 44.0128, 20.9114, 3.0),
    Geofence("bg-airport", "Belgrade Airport (BEG)", 44.8194, 20.3069, 2.0),
    Geofence("ns-station", "Novi Sad Railway", 45.2671, 19.8278, 1.0),
]

# Zones de couverture par operateur pour geofencing
OPERATOR_COVERAGE_ZONES = {
    "01": [  # Yettel
        Geofence("yettel-north", "Yettel Vojvodine", 45.6, 20.0, 80.0),
        Geofence("yettel-central", "Yettel Central Serbia", 44.0, 20.9, 80.0),
    ],
    "03": [  # mt:s
        Geofence("mts-national", "mt:s National", 44.0, 20.9, 250.0),
    ],
    "05": [  # A1
        Geofence("a1-urban", "A1 Urban Areas", 44.8, 20.5, 50.0),
    ],
}
