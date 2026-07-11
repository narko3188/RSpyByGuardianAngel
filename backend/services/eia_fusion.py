#!/usr/bin/env python3
"""
services/eia_fusion.py — Fusion GPS EIA + OpenCellID
"""
import math
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

import asyncpg
import numpy as np
from geopy.distance import geodesic

logger = logging.getLogger(__name__)


@dataclass
class EIAMatch:
    """Résultat d'appariement EIA ↔ OpenCellID"""
    eia_id: int
    oci_cell_id: int
    distance_m: float
    confidence: str  # 'exact'|'approximate'|'none'|'eia_only'
    eia_lat: float
    eia_lon: float
    oci_lat: float
    oci_lon: float
    merged_lat: float
    merged_lon: float


class EIAFusionEngine:
    """
    Moteur de fusion entre:
    - eia_towers (GPS exacts, source officielle)
    - cell_towers (OpenCellID, données communautaires)
    
    Stratégie:
    1. Les EIA sont la vérité terrain (poids fort)
    2. OpenCellID est utilisé pour compléter la couverture
    3. Les positions EIA améliorent directement la triangulation
    """
    
    def __init__(self, db_pool: asyncpg.Pool):
        self.db_pool = db_pool
        # Seuils de matching (mètres)
        self.EXACT_MATCH_THRESHOLD = 200      # < 200m = match exact
        self.APPROX_MATCH_THRESHOLD = 1000    # < 1km = match approximatif
        self.EIA_WEIGHT = 0.85                # Poids EIA dans la fusion
        self.OCI_WEIGHT = 0.15                # Poids OpenCellID
        
    async def match_eia_to_oci(self, mnc: int, radius_m: float = 1000) -> List[EIAMatch]:
        """
        Apparie chaque tour EIA avec les antennes OpenCellID
        
        Returns: liste des matches avec positions fusionnées
        """
        matches = []
        
        async with self.db_pool.acquire() as conn:
            # Récupérer toutes les EIA pour cet opérateur
            eia_towers = await conn.fetch("""
                SELECT id, lat, lon, band, radio, site_name, gps_accuracy_m
                FROM eia_towers
                WHERE mnc = $1
            """, mnc)
            
            for eia in eia_towers:
                # Chercher les antennes OCI proches
                oci_matches = await conn.fetch("""
                    SELECT cell_id, lat, lon, radius_km, samples
                    FROM cell_towers
                    WHERE mcc = 220 
                      AND mnc = $1
                      AND ST_DWithin(
                          geom,
                          ST_SetSRID(ST_MakePoint($2, $3), 4326),
                          $4
                      )
                    ORDER BY samples DESC
                    LIMIT 5
                """, mnc, eia['lon'], eia['lat'], radius_m)
                
                if oci_matches:
                    # Prendre le match le plus proche
                    best = oci_matches[0]
                    dist = geodesic(
                        (eia['lat'], eia['lon']),
                        (best['lat'], best['lon'])
                    ).meters
                    
                    if dist <= self.EXACT_MATCH_THRESHOLD:
                        confidence = 'exact'
                    elif dist <= self.APPROX_MATCH_THRESHOLD:
                        confidence = 'approximate'
                    else:
                        confidence = 'none'
                    
                    # Position fusionnée = moyenne pondérée
                    merged_lat = (
                        self.EIA_WEIGHT * eia['lat'] + 
                        self.OCI_WEIGHT * best['lat']
                    )
                    merged_lon = (
                        self.EIA_WEIGHT * eia['lon'] + 
                        self.OCI_WEIGHT * best['lon']
                    )
                    
                    matches.append(EIAMatch(
                        eia_id=eia['id'],
                        oci_cell_id=best['cell_id'],
                        distance_m=dist,
                        confidence=confidence,
                        eia_lat=eia['lat'],
                        eia_lon=eia['lon'],
                        oci_lat=best['lat'],
                        oci_lon=best['lon'],
                        merged_lat=merged_lat,
                        merged_lon=merged_lon
                    ))
                else:
                    # Pas de match OCI — EIA est la référence
                    matches.append(EIAMatch(
                        eia_id=eia['id'],
                        oci_cell_id=-1,
                        distance_m=0,
                        confidence='eia_only',
                        eia_lat=eia['lat'],
                        eia_lon=eia['lon'],
                        oci_lat=eia['lat'],
                        oci_lon=eia['lon'],
                        merged_lat=eia['lat'],
                        merged_lon=eia['lon']
                    ))
        
        return matches
    
    async def enrich_oci_with_eia(self, mnc: int, radius_m: float = 500) -> int:
        """
        Enrichit les antennes OpenCellID avec les GPS EIA
        
        Stratégie:
        - Pour chaque antenne OCI dans un rayon de 500m d'une EIA
        - Mettre à jour ses coordonnées vers la position EIA
        - Ajouter un flag 'eia_corrected = true'
        """
        updated = 0
        
        async with self.db_pool.acquire() as conn:
            # Mettre à jour les OCI proches des EIA
            result = await conn.execute("""
                UPDATE cell_towers oci
                SET 
                    lat = eia.lat,
                    lon = eia.lon,
                    radius_km = GREATEST(oci.radius_km, 0.1),
                    samples = oci.samples + 100  -- Bonus de confiance
                FROM eia_towers eia
                WHERE oci.mcc = 220 
                  AND oci.mnc = eia.mnc
                  AND oci.mnc = $1
                  AND ST_DWithin(
                      oci.geom,
                      eia.geom,
                      $2
                  )
                  AND oci.source != 'eia_ground_truth'
            """, mnc, radius_m)
            
            updated = int(result.split()[-1]) if result else 0
            logger.info(f"Enrichissement OCI: {updated} antennes mises à jour")
        
        return updated
    
    def compute_fused_weights(
        self, 
        towers: List[Dict],
        eia_matches: Dict[int, EIAMatch]
    ) -> List[float]:
        """
        Calcule les poids de fusion pour chaque antenne
        
        Poids = base_signal_weight * source_quality_multiplier
        """
        weights = []
        
        for tower in towers:
            cell_id = tower.get('cell_id')
            base_weight = 1.0 / max(tower.get('distance_km', 1.0), 0.1)
            
            # Boost si c'est une EIA
            if cell_id in eia_matches:
                match = eia_matches[cell_id]
                if match.confidence == 'exact':
                    base_weight *= 5.0  # Boost x5 pour EIA confirmée
                elif match.confidence == 'approximate':
                    base_weight *= 3.0  # Boost x3 pour EIA approchée
            
            weights.append(base_weight)
        
        # Normaliser
        total = sum(weights)
        if total > 0:
            weights = [w / total for w in weights]
        
        return weights
    
    async def fused_triangulation(
        self,
        towers: List[Dict],
        mnc: int
    ) -> Dict:
        """
        Triangulation fusionnée EIA + OpenCellID
        
        Returns: position avec précision améliorée
        """
        # 1. Récupérer les matches EIA
        eia_matches = await self.match_eia_to_oci(mnc)
        
        # 2. Créer un index cell_id -> match
        match_index = {m.oci_cell_id: m for m in eia_matches if m.oci_cell_id > 0}
        
        # 3. Enrichir les tours avec positions EIA si disponibles
        enriched_towers = []
        for tower in towers:
            cell_id = tower.get('cell_id')
            if cell_id in match_index:
                match = match_index[cell_id]
                if match.confidence in ('exact', 'approximate'):
                    # Remplacer coordonnées par EIA
                    enriched = dict(tower)
                    enriched['lat'] = match.eia_lat
                    enriched['lon'] = match.eia_lon
                    enriched['source'] = 'eia_fused'
                    enriched['eia_confidence'] = match.confidence
                    enriched_towers.append(enriched)
                else:
                    enriched_towers.append(tower)
            else:
                enriched_towers.append(tower)
        
        # 4. Calculer les poids fusionnés
        weights = self.compute_fused_weights(enriched_towers, match_index)
        
        # 5. Triangulation pondérée
        if len(enriched_towers) >= 3:
            result = self._weighted_centroid(enriched_towers, weights)
            result['eia_matches'] = len([m for m in eia_matches if m.confidence != 'none'])
            result['total_towers'] = len(enriched_towers)
            result['fusion_method'] = 'eia_weighted_centroid'
        else:
            # Fallback: pas assez de tours
            result = {
                'latitude': sum(t['lat'] for t in enriched_towers) / len(enriched_towers),
                'longitude': sum(t['lon'] for t in enriched_towers) / len(enriched_towers),
                'accuracy_km': 2.0,
                'fusion_method': 'eia_fallback'
            }
        
        return result
    
    def _weighted_centroid(
        self, 
        towers: List[Dict], 
        weights: List[float]
    ) -> Dict:
        """Centroïde pondéré des positions"""
        lat = sum(t['lat'] * w for t, w in zip(towers, weights))
        lon = sum(t['lon'] * w for t, w in zip(towers, weights))
        
        # Erreur = écart-type pondéré des distances
        distances = []
        for t, w in zip(towers, weights):
            d = geodesic((lat, lon), (t['lat'], t['lon'])).meters
            distances.append(d * w)
        
        error_m = np.std(distances) if distances else 500
        
        return {
            'latitude': lat,
            'longitude': lon,
            'accuracy_km': error_m / 1000.0,
            'accuracy_meters': error_m,
            'method': 'eia_fused_centroid'
        }
    
    async def validate_eia_coverage(
        self, 
        city_lat: float, 
        city_lon: float, 
        radius_km: float = 10
    ) -> Dict:
        """
        Valide la couverture EIA dans une zone
        
        Returns: statistiques de couverture
        """
        async with self.db_pool.acquire() as conn:
            # EIA dans la zone
            eia_in_zone = await conn.fetch("""
                SELECT COUNT(*) as cnt, 
                       COUNT(DISTINCT operator) as operators,
                       AVG(gps_accuracy_m) as avg_gps_acc
                FROM eia_towers
                WHERE ST_DWithin(
                    geom,
                    ST_SetSRID(ST_MakePoint($1, $2), 4326),
                    $3 * 1000
                )
            """, city_lon, city_lat, radius_km)
            
            # OpenCellID dans la zone
            oci_in_zone = await conn.fetch("""
                SELECT COUNT(*) as cnt,
                       COUNT(DISTINCT mnc) as operators
                FROM cell_towers
                WHERE mcc = 220
                  AND ST_DWithin(
                      geom,
                      ST_SetSRID(ST_MakePoint($1, $2), 4326),
                      $3 * 1000
                  )
            """, city_lon, city_lat, radius_km)
            
            eia_stats = eia_in_zone[0]
            oci_stats = oci_in_zone[0]
            
            coverage_ratio = (
                eia_stats['cnt'] / max(oci_stats['cnt'], 1)
            ) if oci_stats['cnt'] > 0 else 0
            
            return {
                'eia_towers': eia_stats['cnt'],
                'eia_operators': eia_stats['operators'],
                'oci_towers': oci_stats['cnt'],
                'oci_operators': oci_stats['operators'],
                'coverage_ratio': round(coverage_ratio, 3),
                'eia_avg_gps_acc_m': round(eia_stats['avg_gps_acc_m'], 1) if eia_stats['avg_gps_acc_m'] else None,
                'recommendation': self._coverage_recommendation(coverage_ratio)
            }
    
    def _coverage_recommendation(self, ratio: float) -> str:
        if ratio > 0.5:
            return "excellent: EIA couvre >50% des antennes"
        elif ratio > 0.2:
            return "good: EIA couvre 20-50%, fusion bénéfique"
        elif ratio > 0:
            return "moderate: EIA sparse, utile comme points d'ancrage"
        else:
            return "poor: aucune EIA dans cette zone"


# Singleton
eia_fusion_engine = None

async def get_eia_fusion_engine() -> EIAFusionEngine:
    """Singleton pattern pour le moteur de fusion"""
    global eia_fusion_engine
    if eia_fusion_engine is None:
        # Import ici pour éviter les dépendances circulaires
        from services.connection_pool import get_pool
        pool = await get_pool()
        eia_fusion_engine = EIAFusionEngine(pool)
    return eia_fusion_engine
