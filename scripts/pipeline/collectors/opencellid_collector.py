"""
SerbiaTracker - OpenCellID Collector
Collecte de données cell tower via OpenCellID API et bulk download
Source: https://opencellid.org
"""
import os
import csv
import gzip
import logging
import asyncio
import aiohttp
import aiosqlite
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, AsyncGenerator

logger = logging.getLogger(__name__)

# Serbia MCC
SERBIA_MCC = 220
OPENCELLID_API = "https://api.opencellid.org/cells/getCellsInArea"
OPENCELLID_DOWNLOAD = "https://opencellid.org/cells/downloads/cell_towers.csv.gz"


class OpenCellIDCollector:
    """Collecteur OpenCellID avec API streaming et bulk download"""

    def __init__(self, api_token: str = None, db_path: str = None):
        self.api_token = api_token or os.getenv("OPENCELLID_API_TOKEN") or os.getenv("OPENCELLID_API_KEY")
        self.db_path = Path(db_path) if db_path else Path("/root/serbia-tracker/data/cell_towers.db")
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "SerbiaTracker/1.0"}
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def _ensure_schema(self):
        """Crée la table unifiée multi-source si nécessaire"""
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS cell_towers (
                    source TEXT DEFAULT 'opencellid',
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
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_towers_op 
                ON cell_towers(mcc, mnc, lac, cell_id)
            """)
            await db.commit()

    async def stream_cells_in_bbox(
        self,
        min_lat: float,
        max_lat: float,
        min_lon: float,
        max_lon: float,
        mcc: int = SERBIA_MCC,
        mnc: Optional[int] = None,
        limit: int = 10000,
    ) -> AsyncGenerator[Dict, None]:
        """
        Stream les cellules dans une bounding box via API OpenCellID.
        Compatible avec bounding box Serbie.
        """
        if not self.api_token or not self.session:
            logger.warning("OpenCellID API token manquant ou session non initialisée")
            return

        params = {
            "token": self.api_token,
            "BBOX": f"{min_lat},{min_lon},{max_lat},{max_lon}",
            "mcc": mcc,
            "limit": min(limit, 10000),
            "output": "json",
        }
        if mnc:
            params["mnc"] = mnc

        try:
            async with self.session.get(OPENCELLID_API, params=params) as resp:
                if resp.status == 200:
                    cells = await resp.json()
                    if isinstance(cells, list):
                        for cell in cells:
                            yield self._normalize(cell)
                    else:
                        logger.error(f"Format inattendu OpenCellID: {type(cells)}")
                elif resp.status == 429:
                    logger.warning("Rate limit OpenCellID atteint")
                else:
                    text = await resp.text()
                    logger.error(f"Erreur API OpenCellID {resp.status}: {text[:200]}")
        except Exception as e:
            logger.error(f"Exception stream_cells_in_bbox: {e}")

    async def stream_all_serbia(self, batch_size: int = 5000) -> AsyncGenerator[List[Dict], None]:
        """
        Stream toutes les cellules de Serbie (MCC=220).
        Découpe le pays en grille pour ne pas dépasser les limites API.
        """
        # Grille Serbie ~ 2°x2° pour rester sous les limites API
        lat_min, lat_max = 42.0, 46.5
        lon_min, lon_max = 18.5, 23.0
        step_lat = 1.0
        step_lon = 1.0

        lat = lat_min
        while lat < lat_max:
            lon = lon_min
            while lon < lon_max:
                batch = []
                async for cell in self.stream_cells_in_bbox(
                    lat, lat + step_lat, lon, lon + step_lon, limit=batch_size
                ):
                    batch.append(cell)
                    if len(batch) >= batch_size:
                        yield batch
                        batch = []
                if batch:
                    yield batch
                lon += step_lon
            lat += step_lat

    async def download_bulk_csv(self, output_path: Optional[Path] = None) -> Optional[Path]:
        """
        Télécharge le bulk CSV OpenCellID (~1.5GB) et filtre sur la Serbie.
        Retourne le chemin du fichier filtré.
        """
        output_path = output_path or (Path(__file__).resolve().parents[4] / "data" / "cell_towers" / "serbia_opencellid.csv.gz")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.api_token:
            logger.error("Token OpenCellID requis pour le bulk download")
            return None

        url = f"{OPENCELLID_DOWNLOAD}?token={self.api_token}"
        logger.info(f"Téléchargement bulk OpenCellID vers {output_path}")

        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    full_gz = output_path.parent / "cell_towers_full.csv.gz"
                    with open(full_gz, "wb") as f:
                        async for chunk in resp.content.iter_chunked(1024 * 1024):
                            f.write(chunk)
                    logger.info(f"Téléchargement complet: {full_gz.stat().st_size / 1024 / 1024:.1f} MB")
                else:
                    logger.error(f"Erreur bulk download: {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"Erreur bulk download: {e}")
            return None

        # Filtrage Serbie en streaming
        serbia_path = output_path
        logger.info(f"Filtrage Serbie (MCC={SERBIA_MCC})...")
        count = 0
        with gzip.open(full_gz, "rt", encoding="utf-8") as fin:
            reader = csv.reader(fin)
            header = next(reader)
            with gzip.open(serbia_path, "wt", encoding="utf-8") as fout:
                writer = csv.writer(fout)
                writer.writerow(header)
                for row in reader:
                    if len(row) > 1 and row[1] == str(SERBIA_MCC):
                        writer.writerow(row)
                        count += 1
                        if count % 100000 == 0:
                            logger.info(f"   Filtre: {count:,} cellules serbes...")

        # Suppression du fichier complet
        full_gz.unlink(missing_ok=True)
        logger.info(f"✅ Bulk filtré: {count:,} antennes serbes -> {serbia_path}")
        return serbia_path

    async def ingest_to_db(self, cells: List[Dict]):
        """Ingestion batch dans SQLite/PostgreSQL"""
        await self._ensure_schema()
        if not cells:
            return

        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.executemany(
                """INSERT OR REPLACE INTO cell_towers 
                   (source, radio, mcc, mnc, lac, cell_id, unit, lon, lat, radius_km, samples, last_seen, altitude_m, azimuth, tx_power_dbm, band)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        "opencellid",
                        c.get("radio", "GSM"),
                        c.get("mcc", SERBIA_MCC),
                        c.get("mnc", 0),
                        c.get("lac", c.get("tac", 0)),
                        c.get("cell_id", c.get("cid", 0)),
                        c.get("unit", 0),
                        float(c.get("lon", 0)),
                        float(c.get("lat", 0)),
                        float(c.get("range", 0)) / 1000.0,
                        int(c.get("samples", 0)),
                        c.get("updated"),
                        c.get("altitude"),
                        c.get("azimuth"),
                        c.get("averageSignal"),
                        c.get("band"),
                    )
                    for c in cells
                ],
            )
            await db.commit()
            logger.debug(f"Ingéré {len(cells)} cellules OpenCellID")

    async def get_stats(self) -> Dict:
        """Statistiques des données OpenCellID en base"""
        await self._ensure_schema()
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            total = await db.execute_fetchall(
                "SELECT COUNT(*) FROM cell_towers WHERE source='opencellid' AND mcc=220"
            )
            by_mnc = await db.execute_fetchall(
                """SELECT mnc, COUNT(*) as cnt, AVG(samples) as avg_samples 
                   FROM cell_towers WHERE source='opencellid' AND mcc=220 
                   GROUP BY mnc ORDER BY cnt DESC"""
            )
            return {
                "source": "opencellid",
                "total_serbia": total[0][0] if total else 0,
                "by_operator": {str(row[0]): {"count": row[1], "avg_samples": round(row[2] or 0, 1)} for row in by_mnc},
            }

    def _normalize(self, cell: Dict) -> Dict:
        """Normalise un record OpenCellID vers schéma unifié"""
        return {
            "radio": cell.get("radio", "GSM"),
            "mcc": cell.get("mcc", SERBIA_MCC),
            "mnc": cell.get("mnc", 0),
            "lac": cell.get("lac", cell.get("tac", 0)),
            "cell_id": cell.get("cell_id", cell.get("cid", 0)),
            "unit": cell.get("unit", 0),
            "lon": float(cell.get("lon", 0)),
            "lat": float(cell.get("lat", 0)),
            "range": float(cell.get("range", 0)) / 1000.0,
            "samples": int(cell.get("samples", 0)),
            "updated": cell.get("updated"),
            "altitude": cell.get("altitude"),
            "azimuth": cell.get("azimuth"),
            "averageSignal": cell.get("averageSignal"),
            "band": cell.get("band"),
        }
