"""
SerbiaTracker - RATEL Collector
Récupération des données de couverture mobile officielles du régulateur serbe RATEL
Source: https://www.ratel.rs / https://data.gov.rs
"""
import os
import csv
import json
import logging
import asyncio
import aiohttp
import aiosqlite
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)
SERBIA_MCC = 220
RATEL_DATAGOV = "https://data.gov.rs/api/3"
RATEL_PORTAL = "https://www.ratel.rs"


class RATELCollector:
    """
    Collecteur RATEL.
    Les données RATEL sont publiées via le portail national data.gov.rs (CKAN).
    """

    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path) if db_path else Path("/root/serbia-tracker/data/cell_towers.db")
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def _ensure_schema(self):
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS cell_towers (
                    source TEXT DEFAULT 'ratel',
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

    async def search_ckan_datasets(self, query: str = "telekom mobilne antene") -> List[Dict]:
        """
        Recherche des datasets RATEL sur data.gov.rs via API CKAN.
        Retourne la liste des packages matchants.
        """
        url = f"{RATEL_DATAGOV}/action/package_search"
        params = {"q": query, "rows": 20, "fl": "title,id,notes,resources"}
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("result", {}).get("results", [])
        except Exception as e:
            logger.error(f"Erreur recherche CKAN RATEL: {e}")
        return []

    async def get_dataset_resources(self, dataset_id: str) -> List[Dict]:
        """Retourne les ressources (fichiers) d'un dataset CKAN"""
        url = f"{RATEL_DATAGOV}/action/package_show"
        params = {"id": dataset_id}
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("result", {}).get("resources", [])
        except Exception as e:
            logger.error(f"Erreur ressources CKAN: {e}")
        return []

    async def download_resource(self, url: str, dest: Path) -> bool:
        """Télécharge une ressource CKAN (CSV/JSON/API)"""
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with open(dest, "wb") as f:
                        async for chunk in resp.content.iter_chunked(1024 * 1024):
                            f.write(chunk)
                    logger.info(f"Ressource RATEL téléchargée: {dest}")
                    return True
        except Exception as e:
            logger.error(f"Erreur téléchargement ressource RATEL: {e}")
        return False

    async def ingest_csv(self, csv_path: Path, source_name: str = "ratel") -> int:
        """
        Ingestion générique CSV RATEL.
        Essaie de deviner les colonnes mcc/mnc/lac/cell_id/lat/lon/radio/band.
        """
        if not csv_path.exists():
            logger.warning(f"Fichier introuvable: {csv_path}")
            return 0

        await self._ensure_schema()
        opener = gzip.open if str(csv_path).endswith(".gz") else open
        count = 0

        try:
            with opener(csv_path, "rt", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                batch = []
                for row in reader:
                    # Filtre Serbie si colonne MCC présente
                    mcc = self._coerce_int(row.get("mcc") or row.get("MCC"))
                    if mcc and mcc != SERBIA_MCC:
                        continue

                    batch.append((
                        source_name,
                        row.get("radio", row.get("RADIO", "GSM")),
                        mcc or SERBIA_MCC,
                        self._coerce_int(row.get("mnc") or row.get("MNC") or row.get("net")),
                        self._coerce_int(row.get("lac") or row.get("LAC") or row.get("area")),
                        self._coerce_int(row.get("cell_id") or row.get("cell") or row.get("CID")),
                        0,
                        self._coerce_float(row.get("lon") or row.get("LON") or row.get("longitude")),
                        self._coerce_float(row.get("lat") or row.get("LAT") or row.get("latitude")),
                        self._coerce_float(row.get("radius_km") or row.get("range")) or 2.0,
                        self._coerce_int(row.get("samples")) or 1,
                        datetime.utcnow().isoformat(),
                        self._coerce_float(row.get("altitude_m") or row.get("altitude")),
                        self._coerce_int(row.get("azimuth")),
                        self._coerce_int(row.get("tx_power_dbm") or row.get("tx_power")),
                        row.get("band") or row.get("BAND"),
                    ))

                    if len(batch) >= 1000:
                        await self._insert_batch(batch)
                        count += len(batch)
                        batch = []

                if batch:
                    await self._insert_batch(batch)
                    count += len(batch)
        except Exception as e:
            logger.error(f"Erreur ingestion CSV RATEL: {e}")

        logger.info(f"✅ RATEL ingesté: {count} antennes")
        return count

    async def ingest_coverage_api(self) -> int:
        """
        Ingestion depuis les APIs de couverture officielles RATEL.
        Ces APIs changent souvent; adapter les endpoints selon la doc en vigueur.
        """
        endpoints = [
            f"{RATEL_PORTAL}/api/v1/coverage/mobile/serbia.geojson",
            f"{RATEL_PORTAL}/api/coverage/serbia.json",
        ]

        count = 0
        await self._ensure_schema()
        for ep in endpoints:
            try:
                async with self.session.get(ep) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        features = data if isinstance(data, list) else data.get("features", [])
                        batch = []
                        for feat in features:
                            props = feat.get("properties", feat) if isinstance(feat, dict) else {}
                            geom = feat.get("geometry", {}) if isinstance(feat, dict) else {}
                            lon, lat = 0.0, 0.0
                            if geom.get("type") == "Point":
                                lon, lat = geom.get("coordinates", [0, 0])[:2]

                            batch.append((
                                "ratel_api",
                                props.get("radio", "LTE"),
                                SERBIA_MCC,
                                self._coerce_int(props.get("mnc") or props.get("operator_code")),
                                self._coerce_int(props.get("lac")),
                                self._coerce_int(props.get("cell_id")),
                                0,
                                float(lon),
                                float(lat),
                                float(props.get("radius_km", 2.0)),
                                int(props.get("samples", 1)),
                                datetime.utcnow().isoformat(),
                                self._coerce_float(props.get("altitude_m")),
                                self._coerce_int(props.get("azimuth")),
                                self._coerce_int(props.get("tx_power_dbm")),
                                props.get("band"),
                            ))

                            if len(batch) >= 1000:
                                await self._insert_batch(batch)
                                count += len(batch)
                                batch = []

                        if batch:
                            await self._insert_batch(batch)
                            count += len(batch)
                        logger.info(f"RATEL API ingesté: {count} antennes")
                        return count
            except Exception as e:
                logger.debug(f"Endpoint RATEL {ep} échoué: {e}")
                continue

        logger.warning("Aucun dataset RATEL exploitable automatiquement; voir doc manuelle")
        return count

    async def _insert_batch(self, batch):
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.executemany(
                """INSERT OR REPLACE INTO cell_towers 
                   (source, radio, mcc, mnc, lac, cell_id, unit, lon, lat, radius_km, samples, last_seen, altitude_m, azimuth, tx_power_dbm, band)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                batch,
            )
            await db.commit()

    @staticmethod
    def _coerce_int(v):
        if v is None:
            return 0
        try:
            return int(float(str(v).strip()))
        except Exception:
            return 0

    @staticmethod
    def _coerce_float(v):
        if v is None:
            return None
        try:
            return float(str(v).strip())
        except Exception:
            return None
