# SerbiaTracker — Analyse de performance & goulots d'étranglement

## Résumé exécutif
Temps de réponse actuel observé : **800 ms – 1 500 ms** sur l'endpoint de géolocalisation.
Après audit du code, les goulots sont concentrés sur 5 axes :
1. Connexions Redis/SQLite créées à chaque requête (pas de pooling).
2. V6/V7 exécutent 2 à 3 algorithmes complets en séquence.
3. Pas de cache suffisant sur les résultats de géolocalisation.
4. Génération de nombres aléatoires et d'objets lourds dans le hot path.
5. Index SQLite incomplets et requêtes boundings-box sans optimisation.

---

## 1. Goulots identifiés

### 1.1 Connexions Redis non poolées (CRITIQUE)
- `redis_tower_lookup.py:22` → `redis.Redis(...)` instancié à chaque appel `get_towers_from_redis`.
- `redis_geo_cache.py:22` → même pattern, nouvelle connexion par opération.
- Coût : TCP handshake + auth + sélection DB ≈ 1–3 ms par connexion. Multiplié par 3–6 appels par requête, cela ajoute 10–30 ms de latence pure et une pression forte sur le kernel.

### 1.2 Connexions SQLite non poolées (CRITIQUE)
- `tower_database.py:161`, `:174`, `:193`, `:212` → `aiosqlite.connect(...)` ouvert/fermé à chaque requête.
- `redis_tower_lookup.py:73` → `sqlite3.connect(...)` fermé après chaque lookup enrichi.
- SQLite sans pooling réutilise des handles OS, désalloue le cache de pages et re-vérifie le schema à chaque ouverture.

### 1.3 Algorithmes en séquence dans V6/V7 (MAJEUR)
- `adaptive_geolocation.py:25-26` → V3 + V5 systématiquement lancés.
- Si heuristique V7 : `hybrid_wknn_geolocation.py:26` relance V5 en intégralité, puis refait un lookup Redis + SQLite.
- Coût : 2–3 géolocalisations complètes par requête, soit 1 500–3 000 ms de travail CPU.

### 1.4 Cache Redis trop léger (MAJEUR)
- `redis_geo_cache.py:38` → TTL position = 60 s.
- `redis_geo_cache.py:78` → TTL index antennes = 1 h, mais l'index n'est jamais pré-chargé au démarrage.
- Pas de cache sur :
  - résultats de triangulation par `(phone, mnc, lat, lon, radius)` ;
  - résultats de consensus par `(phone, mnc)` ;
  - lookup opérateur (`phone_lookup.full_lookup` fait des appels HTTP externes sans cache Redis).

### 1.5 Random et allocations dans le hot path (MODÉRÉ)
- `multi_pass_geolocation.py:115-122` → `random.randint` pour cell_id, `random.uniform` pour radius et samples à chaque tour, pour chaque ville, pour chaque pass.
- `consensus_geolocation.py:58-64` → même pattern.
- Chaque appel crée des dicts/strings éphémères qui pressent le GC.

### 1.6 Triangulation overkill (MODÉRÉ)
- `triangulation.py:187-219` → `least_squares` avec `scipy.optimize.least_squares` pour ≥5 antennes.
- Pour 3–7 antennes, la trilatération linéaire (`np.linalg.lstsq`) est 5–10× plus rapide et donne une erreur quasi identique.

### 1.7 Index SQLite incomplets (MODÉRÉ)
- Index existants : `idx_mcc_mnc`, `idx_lac_cell`, `idx_location(lat, lon)`.
- Manque un index composite `(mnc, lat, lon)` pour accélérer le `BETWEEN` de `get_nearest_towers`.
- Le `idx_location` sur `(lat, lon)` est mal utilisé car `mcc=220` n'est pas inclus en première position.

### 1.8 Stats rate limiter en O(n) (MINEUR)
- `rate_limiter.py:176` → `_redis_client.keys("ratelimit:*")` scanne toutes les clés. À remplacer par `SCAN` ou `DBSIZE`.

---

## 2. Plan d'optimisation détaillé

### 2.1 Pooling Redis + singleton client (gain estimé : 10–20 ms/req)
**Fichiers** : `services/redis_geo_cache.py`, `services/redis_tower_lookup.py`, `services/rate_limiter.py`

```python
# services/redis_client.py
import redis
from redis.connection import ConnectionPool

_pool = ConnectionPool(
    host="localhost", port=6379, db=0,
    decode_responses=True, max_connections=20, socket_timeout=2
)
redis_client = redis.Redis(connection_pool=_pool)

# Pour rate_limiter (db=1), créer un second pool.
```

Tous les modules importent `redis_client` au lieu de créer leur propre instance.
Supprimer les `redis.Redis(...)` inline dans `get_towers_from_redis` et `GeoCache`.

### 2.2 Pooling SQLite via aiosqlite + cache de connexion (gain estimé : 3–8 ms/req)
**Fichier** : `services/tower_database.py`

```python
class CellTowerDatabase:
    def __init__(self):
        self.db_path = DB_PATH
        self._initialized = False
        self._conn: Optional[aiosqlite.Connection] = None  # keep-alive

    async def _get_conn(self):
        if self._conn is None:
            self._conn = await aiosqlite.connect(str(self.db_path))
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA synchronous=NORMAL")
            await self._conn.execute("PRAGMA cache_size=-64000")  # 64 MB
            await self._conn.execute("PRAGMA mmap_size=268435456")  # 256 MB
            self._conn.row_factory = aiosqlite.Row
        return self._conn
```

Avantages :
- WAL + `cache_size` gardent les pages en mémoire.
- `mmap_size` permet au kernel de mapper le fichier DB en mémoire.
- Une seule connexion au lieu d'une par requête.

### 2.3 Cache agressif Redis pour géolocalisation (gain estimé : 400–800 ms)
**Fichier** : `services/redis_geo_cache.py`, `services/adaptive_geolocation.py`

Ajouter un cache de **résultat de géolocalisation** par `(phone, mnc, method_hash)` avec TTL 30–60 s.
Comme les antennes changent lentement, un résultat identique pour le même numéro pendant 30 s est acceptable.

```python
@staticmethod
def cache_geo_result(phone: str, mnc: str, method: str, data: dict, ttl: int = 30):
    key = f"geo:{phone}:{mnc}:{method}"
    GeoCache._redis_client.setex(key, ttl, json.dumps(data))

@staticmethod
def get_geo_result(phone: str, mnc: str, method: str) -> Optional[dict]:
    key = f"geo:{phone}:{mnc}:{method}"
    val = GeoCache._redis_client.get(key)
    return json.loads(val) if val else None
```

Appeler ce cache en amont de V3/V5/V7 dans `adaptive_geolocation`.

### 2.4 Cache lookup opérateur (gain estimé : 50–200 ms)
**Fichier** : `services/phone_lookup.py`

`full_lookup` fait jusqu'à 4 appels HTTP séquentiels. Ajouter un cache Redis de 5 min sur `(phone)` après un hit réussi.

### 2.5 Parallelisation des appels dans V6 (gain estimé : 300–600 ms)
**Fichier** : `services/adaptive_geolocation.py`

Actuellement V3 et V5 sont séquentiels. Les lancer en parallèle avec `asyncio.gather` si les sous-fonctions sont async, ou avec `ThreadPoolExecutor` si elles restent synchrones.

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

loop = asyncio.get_event_loop()
with ThreadPoolExecutor(max_workers=2) as pool:
    v3_future = loop.run_in_executor(pool, multi_pass_geolocation, phone, mnc, 7)
    v5_future = loop.run_in_executor(pool, consensus_geolocation, phone, mnc)
    v3_result, v5_result = await asyncio.gather(v3_future, v5_future)
```

Idem dans V7 : paralléliser le lookup consensus et le enrichissement signal.

### 2.6 Réduction des allocations mémoire (gain estimé : 10–30 ms)
**Fichiers** : `services/multi_pass_geolocation.py`, `services/consensus_geolocation.py`, `services/hybrid_wknn_geolocation.py`

- Supprimer le `random` dans les signatures : utiliser une seed déterministe basée sur `(phone, mnc, city)` pour générer les mêmes signaux d'un appel à l'autre. Cela permet aussi de cache-ér le résultat.
- Réutiliser des listes pré-allouées (`towers_with_signal.clear()` + `append`) au lieu de créer des listes/dicts à chaque pass.
- Éviter `dict(**t)` inutiles : construire le dict final directement.

### 2.7 Pré-indexer Redis Geo au démarrage (gain estimé : 50–150 ms)
**Fichier** : `backend/main.py`

Au démarrage, appeler `tower_db.get_towers_by_operator(mnc)` pour chaque MNC et peupler `towers:220:{mnc}` en une seule fois, plutôt que de remplir l'index au fil de l'eau lors de la première requête.

### 2.8 Optimisation triangulation (gain estimé : 2–5 ms/req)
**Fichier** : `core/triangulation.py`

- Pour ≤ 5 antennes, toujours utiliser `trilateration_linear` au lieu de `least_squares`. Le gain CPU est net et la précision reste < 100 m.
- Réutiliser le `np.linalg.lstsq` sans recréer `np.array` à chaque appel si les entrées sont petites : utiliser des listes Python puis ne convertir que la partie A et b.

### 2.9 Index SQLite manquants (gain estimé : 1–3 ms/req)
**Fichier** : `services/tower_database.py`

```sql
CREATE INDEX IF NOT EXISTS idx_mnc_lat_lon
    ON cell_towers(mnc, lat, lon);
```

Et modifier `get_nearest_towers` pour utiliser `idx_mnc_lat_lon` :

```sql
SELECT * FROM cell_towers
WHERE mcc = 220 AND mnc = ?
  AND lat BETWEEN ? AND ?
  AND lon BETWEEN ? AND ?
ORDER BY samples DESC
LIMIT ?
```

Avec l'index composite, SQLite utilise un range scan au lieu d'un filtre post-index.

### 2.10 Bypass rate limiter stats O(n)
**Fichier** : `services/rate_limiter.py`

```python
# Remplacer
keys = _redis_client.keys("ratelimit:*")
# Par
keys = [k for k in _redis_client.scan_iter("ratelimit:*", count=1000)]
```

Ou stocker un compteur incrémental dans `is_allowed` plutôt que de scanner à chaque appel de stats.

---

## 3. Ordre de mise en œuvre recommandé

| Priorité | Optimisation | Effort | Gain estimé |
|----------|--------------|--------|-------------|
| P0 | Pooling Redis + SQLite | 1h | 10–20 ms + stabilité |
| P0 | Cache géolocalisation par `(phone,mnc)` | 30 min | 400–800 ms |
| P0 | Supprimer V3+V5 séquentiels en doublon | 1h | 300–600 ms |
| P1 | Cache lookup opérateur | 15 min | 50–200 ms |
| P1 | Pré-index Redis Geo au démarrage | 20 min | 50–150 ms |
| P1 | Parallelisation V6/V7 | 45 min | 300–600 ms |
| P2 | Index SQLite composite + PRAGMA | 15 min | 1–3 ms |
| P2 | Random déterministe + réduction allocations | 30 min | 10–30 ms |
| P2 | Linear least squares par défaut ≤5 tours | 20 min | 2–5 ms |
| P3 | SCAN pour rate limiter stats | 10 min | mineur |

**Objectif après P0+P1+P2** : descendre sous **200 ms** pour la plupart des requêtes, avec un 95e percentile < 400 ms.

---

## 4. Points de vigilance

- **Cohérence cache** : un TTL court (30–60 s) sur la position est suffisant car les antennes ne bougent pas et la position d'un téléphone évolue lentement.
- **Fallback mémoire** : `_mem_cache` dans `redis_geo_cache.py` n'a pas de TTL automatique. Ajouter un cleanup périodique ou un TTL via `threading.Timer`.
- **WAL + PRAGMA** : le mode WAL améliore la concurrence mais nécessite SQLite ≥ 3.7.0. `synchronous=NORMAL` est un bon compromis.
- **Cache chaud** : au redémarrage, pré-charger les 9 454 antennes dans Redis Geo en batch pipeline pour éviter le miss storm.
