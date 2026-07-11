"""
SerbiaTracker - Service de geolocalisation
Integration APIs externes + triangulation locale
"""
import httpx
import logging
from typing import Dict, List, Optional
from config.settings import settings
from core.triangulation import estimate_location
from services.tower_database import tower_db

logger = logging.getLogger(__name__)


class GeolocationService:
    """Service de geolocalisation par antennes"""
    
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=15.0)
    
    async def unwired_labs_locate(self, towers_data: List[Dict]) -> Optional[Dict]:
        """
        Unwired Labs LocationAPI - geolocalisation par antennes
        Retourne position + precision basee sur les antennes visibles
        """
        if not settings.UNWIRED_LABS_TOKEN:
            return None
        
        cells = []
        for t in towers_data:
            cell = {
                "radio": t.get("radio", "gsm"),
                "mcc": t.get("mcc", 220),
                "mnc": int(t.get("mnc", 3)),
                "lac": int(t.get("lac", 0)),
                "cid": int(t.get("cell_id", 0)),
            }
            if t.get("signal_dbm"):
                cell["signal"] = t["signal_dbm"]
            if t.get("ta"):
                cell["advance"] = t["ta"]
            cells.append(cell)
        
        try:
            resp = await self.client.post(
                "https://eu1.unwiredlabs.com/v2/process.php",
                json={
                    "token": settings.UNWIRED_LABS_TOKEN,
                    "radio": cells[0].get("radio", "gsm") if cells else "gsm",
                    "mcc": 220,
                    "mnc": cells[0].get("mnc") if cells else 3,
                    "cells": cells[:7],  # Max 7
                    "address": 1
                }
            )
            data = resp.json()
            
            if data.get("status") == "ok":
                return {
                    "latitude": data.get("lat"),
                    "longitude": data.get("lon"),
                    "accuracy_meters": data.get("accuracy"),
                    "source": "unwired_labs",
                    "address": data.get("address"),
                }
        except Exception as e:
            logger.error(f"Unwired Labs error: {e}")
        return None
    
    async def google_geolocation(self, towers_data: List[Dict]) -> Optional[Dict]:
        """Google Geolocation API (fallback)"""
        if not settings.GOOGLE_GEOLOCATION_API_KEY:
            return None
        
        cell_towers = []
        for t in towers_data:
            ct = {
                "cellId": int(t.get("cell_id", 0)),
                "locationAreaCode": int(t.get("lac", 0)),
                "mobileCountryCode": int(t.get("mcc", 220)),
                "mobileNetworkCode": int(t.get("mnc", 3)),
            }
            if t.get("signal_dbm"):
                ct["signalStrength"] = t["signal_dbm"]
            cell_towers.append(ct)
        
        try:
            resp = await self.client.post(
                f"https://www.googleapis.com/geolocation/v1/geolocate?key={settings.GOOGLE_GEOLOCATION_API_KEY}",
                json={"cellTowers": cell_towers[:7], "considerIp": "false"}
            )
            data = resp.json()
            
            if "location" in data:
                return {
                    "latitude": data["location"]["lat"],
                    "longitude": data["location"]["lng"],
                    "accuracy_meters": data.get("accuracy", 1500),
                    "source": "google_geolocation"
                }
        except Exception as e:
            logger.error(f"Google Geolocation error: {e}")
        return None
    
    async def local_triangulation(
        self, towers_data: List[Dict], mnc: int
    ) -> Dict:
        """
        Triangulation locale avec la base OpenCellID
        
        1. Cherche les coordonnees GPS des antennes dans la DB locale
        2. Utilise les coordonnees deja presentes si dispo (simulation)
        3. Applique la trilateration avec les signaux
        """
        enriched_towers = []
        
        for t in towers_data:
            lac = int(t.get("lac", 0))
            cell_id = int(t.get("cell_id", 0))
            signal_dbm = t.get("signal_dbm")
            ta = t.get("ta")
            
            # Verifier si l'antenne a deja des coordonnees (simulation)
            has_coords = t.get("lat") and t.get("lon")
            
            if has_coords:
                # Utiliser directement les coordonnees fournies
                enriched_towers.append({
                    "mcc": 220,
                    "mnc": mnc,
                    "lac": lac,
                    "cell_id": cell_id,
                    "lat": float(t["lat"]),
                    "lon": float(t["lon"]),
                    "radius_km": float(t.get("radius_km", 0)),
                    "samples": int(t.get("samples", 1)),
                    "radio": t.get("radio", "LTE"),
                    "signal_dbm": signal_dbm,
                    "ta": ta,
                })
                continue
            
            # Chercher l'antenne dans la DB locale
            db_tower = await tower_db.get_tower_by_cell(mnc, lac, cell_id)
            
            if db_tower:
                enriched_towers.append({
                    "mcc": 220,
                    "mnc": mnc,
                    "lac": lac,
                    "cell_id": cell_id,
                    "lat": db_tower["lat"],
                    "lon": db_tower["lon"],
                    "radius_km": db_tower.get("radius_km", 0),
                    "samples": db_tower.get("samples", 0),
                    "radio": db_tower.get("radio", "GSM"),
                    "signal_dbm": signal_dbm,
                    "ta": ta,
                })
            else:
                logger.debug(f"Antenne inconnue: MCC=220 MNC={mnc} LAC={lac} CID={cell_id}")
        
        if not enriched_towers:
            return {
                "latitude": 44.7866, "longitude": 20.4489,
                "accuracy_km": 50, "confidence": "poor",
                "method": "no_data", "towers_used": 0,
                "note": "Aucune antenne trouvee dans la base locale"
            }
        
        # Triangulation
        result = estimate_location(enriched_towers, method="auto")
        result["matched_towers"] = len(enriched_towers)
        return result
    
    async def full_geolocation(
        self,
        phone_number: str,
        mnc: int,
        towers_data: Optional[List[Dict]] = None,
        use_simulated_towers: bool = False
    ) -> Dict:
        """
        Geolocalisation complete: cascade de methodes
        
        1. Tente APIs externes (Unwired Labs → Google)
        2. Fallback triangulation locale
        3. Si aucune antenne: estimation operateur + zone
        """
        result = {
            "phone": phone_number,
            "mnc": mnc,
            "latitude": 44.7866,
            "longitude": 20.4489,
            "accuracy_km": 50.0,
            "method": "none",
            "confidence": "poor",
            "sources": []
        }
        
        towers = towers_data or []
        
        if use_simulated_towers:
            # Mode simulation: generer antennes fictives autour d'un point
            towers = self._simulate_towers(mnc, towers)
        
        if towers:
            # 1. Unwired Labs
            ul_result = await self.unwired_labs_locate(towers)
            if ul_result:
                result.update(ul_result)
                result["sources"].append("unwired_labs")
                return result
            
            # 2. Google
            gl_result = await self.google_geolocation(towers)
            if gl_result:
                result.update(gl_result)
                result["sources"].append("google")
                return result
            
            # 3. Triangulation locale
            local_result = await self.local_triangulation(towers, mnc)
            if local_result.get("matched_towers", 0) >= 1:
                result.update(local_result)
                result["sources"].append("local_triangulation")
                return result
        
        # 4. Aucune donnee - estimation grossiere par operateur
        result.update(self._fallback_by_operator(mnc))
        result["sources"].append("operator_fallback")
        return result
    
    def _simulate_towers(self, mnc: int, existing: List[Dict]) -> List[Dict]:
        """Mode simulation - genere des antennes autour de Belgrade"""
        import random
        import math
        
        if existing:
            return existing
        
        towers = []
        # Centre de Belgrade
        base_lat, base_lon = 44.7866, 20.4489
        
        for i in range(5):
            angle = random.uniform(0, 2 * math.pi)
            distance = random.uniform(0.5, 5.0)  # 0.5-5km
            lat = base_lat + distance * math.cos(angle) / 111.32
            lon = base_lon + distance * math.sin(angle) / (111.32 * math.cos(math.radians(base_lat)))
            
            towers.append({
                "mcc": 220, "mnc": mnc,
                "lac": random.randint(100, 999),
                "cell_id": random.randint(10000, 99999),
                "lat": round(lat, 6), "lon": round(lon, 6),
                "radio": "LTE",
                "signal_dbm": random.randint(-85, -50),
                "ta": random.randint(0, 10),
            })
        
        return towers
    
    def _fallback_by_operator(self, mnc: int) -> Dict:
        """Estimation par operateur (centree sur la couverture de l'operateur)"""
        operator_zones = {
            "01": (44.7866, 20.4489, 15.0),   # Yettel - Belgrade/Vojvodine
            "03": (44.0165, 21.0059, 10.0),    # mt:s - couverture nationale
            "05": (44.8040, 20.4651, 12.0),    # A1 - zones urbaines
            "07": (44.8125, 20.4612, 20.0),    # Orion - Belgrade
            "11": (44.7866, 20.4489, 30.0),    # Mundio - Belgrade
        }
        
        mnc_str = f"{int(mnc):02d}"
        zone = operator_zones.get(mnc_str, (44.7866, 20.4489, 50.0))
        
        return {
            "latitude": zone[0],
            "longitude": zone[1],
            "accuracy_km": zone[2],
            "method": "operator_estimate",
            "confidence": "poor",
            "note": f"Estimation basee sur la couverture operateur (MNC={mnc})"
        }
    
    async def close(self):
        await self.client.aclose()


# Singleton
geolocator = GeolocationService()
