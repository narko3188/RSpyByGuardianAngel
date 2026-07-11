"""
SerbiaTracker - CellMapper Collector
Scraping et collecte de données depuis CellMapper.net
"""
import os
import json
import logging
import asyncio
import aiohttp
import aiosqlite
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, AsyncGenerator

logger = logging.getLogger(__name__)
SERBIA_MCC = 220
CELLMAPPER_BASE = "https://www.cellmapper.net"
CELLMAPPER_API = f"{CELLMAPPER_BASE}/api/v1"


class CellMapperCollector:
    """
    Collecteur CellMapper.
    NOTE: CellMapper n'a pas d'API publique officielle pour le bulk.
    Ce module propose 2 méthodes:
      1. Scraping de la carte via endpoints JSON internes
      2. Intégration de l'application Android Tower Collector / CellMapper app
    """

    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path) if db_path else Path("/root/serbia-tracker/data/cell_towers.db")
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={
                "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": CELLMAPPER_BASE,
            },
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def _ensure_schema(self):
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS cell_towers (
                    source TEXT DEFAULT 'cellmapper',
                    radio TEXT,
                    mcc INTEGER,
                    mnc INTEGER,
                    lac INTEGER,
                    cell_id INTEGER,
                    unit INTEGER,
                    lon REAL,
                    lat REAL,
                    radius_km REAL,
                    samples INTEGER,
                    last_seen TEXT,
                    altitude_m REAL,
                    azimuth INTEGER,
                    tx_power_dbm INTEGER,
                    band TEXT,
                    imported_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(source, mcc, mnc, lac, cell_id)
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_towers_op ON cell_towers(mcc, mnc, lac, cell_id)")
            await db.commit()

    async def stream_towers_by_mnc(
        self,
        mnc: int,
        lat: float = 44.7866,
        lon: float = 20.4489,
        radius_km: int = 50,
        zoom: int = 12,
    ) -> AsyncGenerator[Dict, None]:
        """
        Stream les tours CellMapper pour un MNC donné via endpoint interne.
        L'endpoint exact peut changer; ceci est un template à adapter.
        """
        # Template d'endpoint connu utilisé par CellMapper pour la carte
        endpoints = [
            f"{CELLMAPPER_API}/towerinfo",
            f"{CELLMAPPER_API}/cells",
            f"{CELLMAPPER_BASE}/mapdata",
        ]

        params = {
            "mcc": SERBIA_MCC,
            "mnc": mnc,
            "lat": lat,
            "lon": lon,
            "radius": radius_km,
            "zoom": zoom,
            "limit": 500,
        }

        for ep in endpoints:
            try:
                async with self.session.get(ep, params=params) as resp:
                    if resp.status == 200:
                        try:
                            data = await resp.json()
                        except Exception:
                            continue
                        cells = data if isinstance(data, list) else data.get("cells", data.get("towers", []))
                        for cell in cells:
                            yield self._normalize(cell)
                        return
            except Exception as e:
                logger.debug(f"CellMapper endpoint {ep} échoué: {e}")
                continue

        logger.warning(f"Aucune donnée CellMapper pour MNC {mnc}")

    async def scrape_map_page(self, mnc: int, lat: float = 44.8125, lon: float = 20.4612, zoom: int = 12) -> List[Dict]:
        """
        Scrape la page de carte CellMapper pour extraire les tours.
        Méthode de fallback si l'API JSON n'est pas disponible.
        """
        url = f"{CELLMAPPER_BASE}/map?MCC={SERBIA_MCC}&MNC={mnc}&lat={lat}&lon={lon}&zoom={zoom}"
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    # Cherche des blobs JSON dans la page (__NEXT_DATA__ ou variables globales)
                    import re
                    matches = re.findall(r'"lat":([0-9.]+),"lon":([0-9.]+).*?"cellId":(\d+)', text)
                    towers = []
                    for m in matches:
                        towers.append({
                            "lat": float(m[0]),
                            "lon": float(m[1]),
                            "cell_id": int(m[2]),
                            "radio": "LTE",
                            "samples": 1,
                        })
                    return towers
        except Exception as e:
            logger.error(f"Erreur scrape CellMapper: {e}")
        return []

    async def ingest_to_db(self, towers: List[Dict]):
        await self._ensure_schema()
        if not towers:
            return
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.executemany(
                """INSERT OR REPLACE INTO cell_towers 
                   (source, radio, mcc, mnc, lac, cell_id, unit, lon, lat, radius_km, samples, last_seen, altitude_m, azimuth, tx_power_dbm, band)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        "cellmapper",
                        t.get("radio", "LTE"),
                        SERBIA_MCC,
                        t.get("mnc", 0),
                        t.get("lac", t.get("tac", 0)),
                        t.get("cell_id", 0),
                        t.get("unit", 0),
                        float(t.get("lon", 0)),
                        float(t.get("lat", 0)),
                        float(t.get("range", 2000)) / 1000.0,
                        int(t.get("samples", 1)),
                        datetime.utcnow().isoformat(),
                        t.get("altitude"),
                        t.get("azimuth"),
                        t.get("tx_power_dbm", 43),
                        t.get("band"),
                    )
                    for t in towers
                ],
            )
            await db.commit()

    async def get_stats(self) -> Dict:
        await self._ensure_schema()
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            total = await db.execute_fetchall(
                "SELECT COUNT(*) FROM cell_towers WHERE source='cellmapper' AND mcc=220"
            )
            by_mnc = await db.execute_fetchall(
                "SELECT mnc, COUNT(*) FROM cell_towers WHERE source='cellmapper' AND mcc=220 GROUP BY mnc"
            )
            return {
                "source": "cellmapper",
                "total_serbia": total[0][0] if total else 0,
                "by_operator": {str(row[0]): row[1] for row in by_mnc},
            }

    def _normalize(self, cell: Dict) -> Dict:
        return {
            "radio": cell.get("radio", "LTE"),
            "mcc": cell.get("mcc", SERBIA_MCC),
            "mnc": cell.get("mnc", 0),
            "lac": cell.get("lac", cell.get("tac", 0)),
            "cell_id": cell.get("cell_id", cell.get("cid", cell.get("cellId", 0))),
            "unit": cell.get("unit", 0),
            "lon": float(cell.get("lon", cell.get("longitude", 0))),
            "lat": float(cell.get("lat", cell.get("latitude", 0))),
            "range": float(cell.get("range", cell.get("radius", 2000))) / 1000.0,
            "samples": int(cell.get("samples", 1)),
            "updated": cell.get("updated"),
            "altitude": cell.get("altitude"),
            "azimuth": cell.get("azimuth"),
            "averageSignal": cell.get("averageSignal"),
            "band": cell.get("band"),
        }
