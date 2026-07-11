"""
SerbiaTracker - Service de references antennes
Chargement et interrogation de la base OpenCellID pour la Serbie
"""
import aiosqlite
import csv
import gzip
import logging
import asyncio
from pathlib import Path
from typing import List, Dict, Optional, AsyncGenerator
from dataclasses import dataclass
from config.settings import settings

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "cell_towers.db"
CSV_PATH = Path(__file__).parent.parent / "data" / "cell_towers" / "serbia_towers.csv.gz"


@dataclass
class TowerRecord:
    """Enregistrement antenne en base"""
    radio: str
    mcc: int
    mnc: int
    lac: int
    cell_id: int
    unit: int
    lon: float
    lat: float
    radius_km: float
    samples: int
    changeable: int
    created: int
    updated: int
    average_signal: int


class CellTowerDatabase:
    """Base de donnees antennes pour la Serbie"""
    
    def __init__(self):
        self.db_path = DB_PATH
        self._initialized = False
    
    async def initialize(self):
        """Creation de la base et des index"""
        if self._initialized:
            return
        
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS cell_towers (
                    radio TEXT,
                    mcc INTEGER,
                    mnc INTEGER,
                    lac INTEGER,
                    cell_id INTEGER,
                    unit INTEGER,
                    lon REAL,
                    lat REAL,
                    radius_km REAL DEFAULT 0,
                    samples INTEGER DEFAULT 0,
                    changeable INTEGER DEFAULT 0,
                    created INTEGER,
                    updated INTEGER,
                    average_signal INTEGER
                )
            """)
            
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_mcc_mnc ON cell_towers(mcc, mnc)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_lac_cell ON cell_towers(mcc, mnc, lac, cell_id)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_location ON cell_towers(lat, lon)
            """)
            
            await db.commit()
        
        self._initialized = True
        logger.info("Base antennes initialisee")
    
    async def load_from_opencellid(self, csv_path: Optional[str] = None):
        """Chargement du CSV OpenCellID (filtre Serbie MCC=220)"""
        path = Path(csv_path) if csv_path else CSV_PATH
        
        if not path.exists():
            logger.warning(f"Fichier CSV non trouve: {path}")
            return
        
        await self.initialize()
        
        opener = gzip.open if path.suffix == '.gz' else open
        count = 0
        
        async with aiosqlite.connect(str(self.db_path)) as db:
            # Vidage table avant rechargement
            await db.execute("DELETE FROM cell_towers WHERE mcc = 220")
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=OFF")
            
            with opener(path, 'rt', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                batch = []
                
                for row in reader:
                    mcc = int(row.get('mcc', 0))
                    
                    # Filtre Serbie uniquement
                    if mcc != 220:
                        continue
                    
                    batch.append((
                        row.get('radio', 'GSM'),
                        mcc,
                        int(row.get('net', row.get('mnc', 0))),
                        int(row.get('area', 0)),
                        int(row.get('cell', 0)),
                        int(row.get('unit', 0)),
                        float(row.get('lon', 0)),
                        float(row.get('lat', 0)),
                        float(row.get('range', 0)) / 1000,  # m -> km
                        int(row.get('samples', 0)),
                        int(row.get('changeable', 0)),
                        int(row.get('created', 0)),
                        int(row.get('updated', 0)),
                        int(row.get('averageSignal', 0)),
                    ))
                    
                    if len(batch) >= 5000:
                        await db.executemany(
                            "INSERT INTO cell_towers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            batch
                        )
                        await db.commit()
                        count += len(batch)
                        batch = []
                        logger.info(f"Charge {count} antennes Serbie...")
                
                # Dernier batch
                if batch:
                    await db.executemany(
                        "INSERT INTO cell_towers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        batch
                    )
                    await db.commit()
                    count += len(batch)
        
        logger.info(f"Charge termine: {count} antennes serbes importees")
    
    async def get_towers_by_operator(self, mnc: int, limit: int = 10000) -> List[Dict]:
        """Toutes les antennes d'un operateur"""
        await self.initialize()
        
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM cell_towers WHERE mcc = 220 AND mnc = ? ORDER BY samples DESC LIMIT ?",
                (mnc, limit)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
    
    async def get_tower_by_cell(self, mnc: int, lac: int, cell_id: int) -> Optional[Dict]:
        """Recherche antenne par LAC + Cell ID"""
        await self.initialize()
        
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM cell_towers WHERE mcc = 220 AND mnc = ? AND lac = ? AND cell_id = ?",
                (mnc, lac, cell_id)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None
    
    async def get_nearest_towers(
        self, lat: float, lon: float, mnc: int = None, radius_km: float = 10, limit: int = 20
    ) -> List[Dict]:
        """Antennes les plus proches d'un point GPS"""
        await self.initialize()
        
        # Requete approximative par bounding box
        lat_delta = radius_km / 111.32
        lon_delta = radius_km / (111.32 * abs(__import__('math').cos(__import__('math').radians(lat))))
        
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            if mnc is not None:
                cursor = await db.execute(
                    """SELECT * FROM cell_towers 
                    WHERE mcc = 220 
                    AND mnc = ?
                    AND lat BETWEEN ? AND ? 
                    AND lon BETWEEN ? AND ?
                    LIMIT ?""",
                    (mnc, lat - lat_delta, lat + lat_delta, lon - lon_delta, lon + lon_delta, limit)
                )
            else:
                cursor = await db.execute(
                    """SELECT * FROM cell_towers 
                    WHERE mcc = 220 
                    AND lat BETWEEN ? AND ? 
                    AND lon BETWEEN ? AND ?
                    LIMIT ?""",
                    (lat - lat_delta, lat + lat_delta, lon - lon_delta, lon + lon_delta, limit)
                )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
    
    async def get_towers_by_region(
        self, mnc: int, min_lat: float, max_lat: float, min_lon: float, max_lon: float
    ) -> List[Dict]:
        """Antennes dans une zone geographique"""
        await self.initialize()
        
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM cell_towers 
                WHERE mcc = 220 AND mnc = ?
                AND lat BETWEEN ? AND ?
                AND lon BETWEEN ? AND ?
                LIMIT 5000""",
                (mnc, min_lat, max_lat, min_lon, max_lon)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
    
    async def get_stats(self) -> Dict:
        """Statistiques de la base antennes"""
        await self.initialize()
        
        async with aiosqlite.connect(str(self.db_path)) as db:
            total = await db.execute_fetchall("SELECT COUNT(*) FROM cell_towers WHERE mcc = 220")
            
            by_operator = await db.execute_fetchall(
                "SELECT mnc, COUNT(*) as cnt, AVG(samples) as avg_samples FROM cell_towers WHERE mcc = 220 GROUP BY mnc"
            )
            
            return {
                "total_towers_serbia": total[0][0] if total else 0,
                "operators": {
                    row[0]: {"count": row[1], "avg_samples": round(row[2], 1) if row[2] else 0}
                    for row in by_operator
                }
            }


# Instance singleton
tower_db = CellTowerDatabase()
