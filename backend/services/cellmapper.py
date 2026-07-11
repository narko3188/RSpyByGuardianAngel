"""
SerbiaTracker - Service CellMapper
Recuperation de donnees d'antennes temps reel depuis CellMapper
"""
import httpx
import logging
from typing import List, Dict, Optional
import json

logger = logging.getLogger(__name__)

CELLMAPPER_API = "https://www.cellmapper.net/api/v1"


async def get_cellmapper_towers(mnc: int, lat: float, lon: float, radius_km: int = 15) -> List[Dict]:
    """
    Recuperer les antennes CellMapper pour la Serbie (MCC 220)
    Donnees temps reel + historiques de mesures
    """
    towers = []
    
    try:
        async with httpx.AsyncClient(timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36"
        }) as client:
            # API CellMapper - recherche par zone
            resp = await client.get(
                f"{CELLMAPPER_API}/cells",
                params={
                    "mcc": 220,
                    "mnc": mnc,
                    "lat": lat,
                    "lon": lon,
                    "distance": radius_km,
                    "limit": 50,
                }
            )
            
            if resp.status_code == 200:
                data = resp.json()
                cells = data.get("cells", [])
                
                for cell in cells:
                    towers.append({
                        "mcc": 220,
                        "mnc": mnc,
                        "lac": cell.get("lac", cell.get("tac", 0)),
                        "cell_id": cell.get("cellId", 0),
                        "lat": cell.get("lat"),
                        "lon": cell.get("lon"),
                        "radio": cell.get("radio", "LTE"),
                        "band": cell.get("band", ""),
                        "samples": cell.get("samples", 0),
                        "last_seen": cell.get("updated"),
                        "altitude_m": cell.get("altitude"),
                        "source": "cellmapper",
                    })
    except Exception as e:
        logger.warning(f"CellMapper API error: {e}")
    
    return towers


async def get_cellmapper_stats() -> Dict:
    """Statistiques CellMapper pour la Serbie"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{CELLMAPPER_API}/stats",
                params={"mcc": 220}
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.warning(f"CellMapper stats error: {e}")
    
    return {"total_cells": 0, "operators": {}}


def generate_cellmapper_tiles_url(mnc: int, lat: float, lon: float, zoom: int = 12) -> str:
    """Generer URL de tuile CellMapper pour l'affichage carte"""
    return (
        f"https://www.cellmapper.net/map"
        f"?MCC=220&MNC={mnc}"
        f"&lat={lat}&lon={lon}&zoom={zoom}"
    )
