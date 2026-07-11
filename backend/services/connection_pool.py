"""
SerbiaTracker — Connection Pool Manager
Pooling Redis + SQLite pour eliminer le bottleneck critique
"""
import redis
import sqlite3
import threading
from pathlib import Path
from config.settings import settings

# Pool Redis — connection unique réutilisée
_redis_pool = None
_redis_lock = threading.Lock()


def get_redis() -> redis.Redis:
    """Retourne une connexion Redis poolée (thread-safe)"""
    global _redis_pool
    if _redis_pool is None:
        with _redis_lock:
            if _redis_pool is None:
                _redis_pool = redis.Redis(
                    host="localhost",
                    port=6379,
                    db=0,
                    decode_responses=False,
                    socket_keepalive=True,
                    socket_connect_timeout=2,
                    retry_on_timeout=True,
                    health_check_interval=30,
                )
    return _redis_pool


# Pool SQLite — connexion unique réutilisée (WAL mode)
_sqlite_conn = None
_sqlite_lock = threading.Lock()

DB_PATH = Path(__file__).parent.parent.parent / "data" / "cell_towers.db"


def get_sqlite() -> sqlite3.Connection:
    """Retourne une connexion SQLite poolée en WAL mode"""
    global _sqlite_conn
    if _sqlite_conn is None:
        with _sqlite_lock:
            if _sqlite_conn is None:
                _sqlite_conn = sqlite3.connect(
                    str(DB_PATH),
                    check_same_thread=False,
                    timeout=10,
                )
                _sqlite_conn.execute("PRAGMA journal_mode=WAL")
                _sqlite_conn.execute("PRAGMA synchronous=NORMAL")
                _sqlite_conn.execute("PRAGMA cache_size=-8000")  # 8MB
                _sqlite_conn.execute("PRAGMA temp_store=MEMORY")
    return _sqlite_conn


def close_all():
    """Fermer toutes les connexions (appel au shutdown)"""
    global _sqlite_conn, _redis_pool
    if _sqlite_conn:
        _sqlite_conn.close()
    if _redis_pool:
        _redis_pool.close()
