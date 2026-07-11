"""
SerbiaTracker — Weighted KNN Fingerprinting Engine
Remplace la triangulation pure par WkNN + filtrage median
pour passer de ~200m a <100m de precision

Algorithme:
1. Collecte les mesures RSRP/TA de toutes les antennes visibles
2. Compare avec une base de Reference Points (RP)
3. WkNN: les k plus proches voisins votes ponderes par similarite de signature
4. Filtre median glissant pour stabiliser
"""
import math
import statistics
from typing import Dict, List, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
import random

import logging
logger = logging.getLogger(__name__)


@dataclass
class ReferencePoint:
    """Point de reference avec signature radio connue"""
    lat: float
    lon: float
    # Signature: {cell_key: rssi_dbm}
    signatures: Dict[str, float] = field(default_factory=dict)
    ta: int = 0
    samples: int = 0
    city: str = ""


class WKNNFingerprinter:
    """
    Weighted K-Nearest Neighbors Fingerprinting
    
    k=7, distance Manhattan ponderee, filtrage median
    """
    
    def __init__(self, k: int = 7):
        self.k = k
        self.reference_points: List[ReferencePoint] = []
        self._history: List[Tuple[float, float]] = []  # Dernieres positions
        self._history_size = 5
    
    def add_reference_point(self, rp: ReferencePoint):
        self.reference_points.append(rp)
    
    def build_from_towers(self, towers: List[Dict], city: str = ""):
        """
        Construire des RPs a partir des antennes disponibles
        Chaque antenne devient un RP avec sa signature simulee
        """
        # Grouper les antennes proches en RPs
        # Pour l'instant: chaque antenne = 1 RP
        
        for t in towers:
            # Generer une signature realiste
            signatures = {}
            
            # Tour elle-meme: signal fort
            cell_key = f"{t.get('mcc',220)}:{t.get('mnc',1)}:{t.get('lac',0)}:{t.get('cell_id',0)}"
            signatures[cell_key] = t.get('signal_dbm', -50)
            
            # Tours voisines: signal plus faible (simule)
            rp = ReferencePoint(
                lat=t['lat'],
                lon=t['lon'],
                signatures=signatures,
                ta=t.get('ta', 0),
                samples=t.get('samples', 100),
                city=city,
            )
            self.reference_points.append(rp)
    
    def predict(self, observed_signature: Dict[str, float], observed_ta: int = 0) -> Tuple[float, float, float]:
        """
        Predire la position a partir d'une signature observee
        
        Args:
            observed_signature: {cell_key: rssi_dbm}
            observed_ta: Timing Advance observe
        
        Returns: (lat, lon, confidence_km)
        """
        if not self.reference_points or not observed_signature:
            return 44.8125, 20.4612, 5.0
        
        # Calculer les distances de signature pour chaque RP
        scored = []
        for rp in self.reference_points:
            dist = self._signature_distance(observed_signature, rp.signatures)
            
            # Bonus TA: si le TA correspond, penalite reduite
            ta_penalty = abs(observed_ta - rp.ta) * 50 if observed_ta > 0 else 0
            
            scored.append((rp, dist + ta_penalty))
        
        # Trier par distance de signature (plus petit = meilleur)
        scored.sort(key=lambda x: x[1])
        
        # Prendre les k meilleurs
        k = min(self.k, len(scored))
        top_k = scored[:k]
        
        if not top_k:
            return 44.8125, 20.4612, 5.0
        
        # WkNN: moyenne ponderee par l'inverse de la distance
        total_weight = 0
        weighted_lat = 0
        weighted_lon = 0
        
        epsilon = 0.001  # Eviter division par zero
        for rp, dist in top_k:
            weight = 1.0 / (dist + epsilon)
            weighted_lat += rp.lat * weight
            weighted_lon += rp.lon * weight
            total_weight += weight
        
        pred_lat = weighted_lat / total_weight
        pred_lon = weighted_lon / total_weight
        
        # Estimer la precision = distance moyenne ponderee entre RPs
        if len(top_k) >= 2:
            spread = 0
            for rp, _ in top_k:
                d = haversine(pred_lat, pred_lon, rp.lat, rp.lon)
                spread += d
            confidence_km = spread / len(top_k) * 2  # Facteur 2 pour etre conservateur
        else:
            confidence_km = 2.0
        
        # Filtre median glissant
        self._history.append((pred_lat, pred_lon))
        if len(self._history) > self._history_size:
            self._history.pop(0)
        
        if len(self._history) >= 3:
            lats = [p[0] for p in self._history]
            lons = [p[1] for p in self._history]
            pred_lat = statistics.median(lats)
            pred_lon = statistics.median(lons)
        
        return pred_lat, pred_lon, min(confidence_km, 5.0)
    
    def _signature_distance(self, obs: Dict[str, float], ref: Dict[str, float]) -> float:
        """
        Distance de Manhattan ponderee entre deux signatures
        
        Chaque cellule contribue avec la difference absolue de RSSI
        Les cellules absentes de l'une ou l'autre sont penalisees
        """
        all_keys = set(obs.keys()) | set(ref.keys())
        if not all_keys:
            return 999.0
        
        total_dist = 0.0
        matched = 0
        
        for key in all_keys:
            obs_rssi = obs.get(key)
            ref_rssi = ref.get(key)
            
            if obs_rssi is not None and ref_rssi is not None:
                # Les deux ont cette cellule: difference de RSSI
                diff = abs(obs_rssi - ref_rssi)
                total_dist += diff
                matched += 1
            elif obs_rssi is not None:
                # Cellule observee mais pas dans la reference
                total_dist += 30  # Penalite fixe
            else:
                # Cellule dans la reference mais pas observee
                total_dist += 20  # Penalite reduite (peut etre hors portee)
        
        if matched == 0:
            return 999.0
        
        # Normaliser par le nombre de cellules
        return total_dist / len(all_keys)


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


# Singleton
wknn_fingerprinter = WKNNFingerprinter(k=7)
