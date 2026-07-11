"""
SerbiaTracker - Module de prediction de position ML + Fingerprinting radio
==========================================================================
Fonctionnalites:
1. RadioMap fingerprinting: grille 100m×100m pour Belgrade/Novi Sad/Nis
2. KNN matcher pour trouver la position la plus probable
3. XGBoost regressor entraine sur features [rssi, rsrp, rsrq, ta, n_towers, mnc, hour]
4. Fonction ensemble combinant triangulation + fingerprinting + ML
5. Pre-entrainement sur donnees synthetiques basees sur les antennes Yettel connues

Source: base sur les antennes Yettel (220-01) dans services/yettel_infrastructure.py
"""

import math
import random
import hashlib
import logging
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Machine Learning
from xgboost import XGBRegressor
from sklearn.neighbors import KNeighborsRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, mean_squared_error
import joblib

# Geospatial
from geopy.distance import geodesic

# Infrastructure Yettel existante
import sys
from pathlib import Path
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.yettel_infrastructure import (
    YETTEL_TOWERS_BELGRADE,
    YETTEL_TOWERS_NOVI_SAD,
    YETTEL_TOWERS_NIS,
    YETTEL_TOWERS_KRAGUJEVAC,
    YETTEL_TOWERS_SUBOTICA,
    YETTEL_ALL_TOWERS,
    YETTEL_NETWORK_PARAMS,
    get_realistic_signal,
    get_yettel_towers_for_region,
)

logger = logging.getLogger(__name__)

# ============================================================================
# CONSTANTES ET CONFIGURATION
# ============================================================================

# Chemins
MODEL_DIR = Path(__file__).parent
XGBOOST_MODEL_PATH = MODEL_DIR / "xgboost_position_model.joblib"
KNN_MODEL_PATH = MODEL_DIR / "knn_fingerprint_model.joblib"
SCALER_PATH = MODEL_DIR / "feature_scaler.joblib"
FINGERPRINT_MAP_PATH = MODEL_DIR / "radio_map_fingerprints.npy"

# Grille RadioMap: 100m × 100m par defaut
DEFAULT_GRID_STEP_METERS = 100

# Villes principales avec leur grille RadioMap
CITY_GRIDS = {
    "belgrade": {
        "lat_min": 44.750,
        "lat_max": 44.860,
        "lon_min": 20.380,
        "lon_max": 20.520,
        "step_m": DEFAULT_GRID_STEP_METERS,
    },
    "novi_sad": {
        "lat_min": 45.220,
        "lat_max": 45.300,
        "lon_min": 19.800,
        "lon_max": 19.900,
        "step_m": DEFAULT_GRID_STEP_METERS,
    },
    "nis": {
        "lat_min": 43.290,
        "lat_max": 43.360,
        "lon_min": 21.860,
        "lon_max": 21.930,
        "step_m": DEFAULT_GRID_STEP_METERS,
    },
}

# Parametres XGBoost
XGBOOST_PARAMS = {
    "n_estimators": 200,
    "max_depth": 8,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "objective": "reg:squarederror",
    "n_jobs": 4,
    "random_state": 42,
}

# Parametres KNN
KNN_PARAMS = {
    "n_neighbors": 7,
    "weights": "distance",
    "metric": "manhattan",
    "n_jobs": 4,
}

# Features pour le modele ML
FEATURE_NAMES = [
    "rssi",
    "rsrp",
    "rsrq",
    "ta",
    "n_towers",
    "mnc",
    "hour",
]

# ============================================================================
# STRUCTURES DE DONNEES
# ============================================================================


@dataclass
class FingerprintSignature:
    """Signature RSSI pour une position donnee."""
    lat: float
    lon: float
    grid_cell: str  # Identifiant de cellule de grille
    rssi_by_cell: Dict[str, float] = field(default_factory=dict)
    rsrp_by_cell: Dict[str, float] = field(default_factory=dict)
    rsrq_by_cell: Dict[str, float] = field(default_factory=dict)
    n_towers: int = 0
    mnc: int = 1
    hour: int = 12


@dataclass
class Observation:
    """Observation radio observee."""
    rssi: Optional[float] = None
    rsrp: Optional[float] = None
    rsrq: Optional[float] = None
    ta: Optional[float] = None
    n_towers: int = 0
    mnc: int = 1
    hour: int = 12
    cell_id: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None


@dataclass
class PredictionResult:
    """Resultat de prediction de position."""
    latitude: float
    longitude: float
    accuracy_meters: float
    confidence: str
    method: str
    weights: Dict[str, float]
    details: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# GRILLE RADIOMAP
# ============================================================================


def latlon_to_grid_cell(lat: float, lon: float, step_m: float = 100) -> str:
    """Convertit lat/lon en identifiant de cellule de grille."""
    deg_per_m_lat = 1.0 / 111320.0
    deg_per_m_lon = 1.0 / (111320.0 * math.cos(math.radians(lat)))
    grid_lat = math.floor(lat / (deg_per_m_lat * step_m))
    grid_lon = math.floor(lon / (deg_per_m_lon * step_m))
    return f"{grid_lat}_{grid_lon}"


def generate_grid_points(
    city_name: str,
    step_m: float = 100,
) -> List[Tuple[float, float, str]]:
    """
    Genere tous les points de la grille RadioMap pour une ville.
    Retourne [(lat, lon, cell_id), ...]
    """
    if city_name not in CITY_GRIDS:
        raise ValueError(f"Ville inconnue: {city_name}")

    cfg = CITY_GRIDS[city_name]
    lat_min, lat_max = cfg["lat_min"], cfg["lat_max"]
    lon_min, lon_max = cfg["lon_min"], cfg["lon_max"]
    step_m = cfg["step_m"]

    deg_per_m_lat = 1.0 / 111320.0
    deg_per_m_lon_ref = 1.0 / (111320.0 * math.cos(math.radians((lat_min + lat_max) / 2.0)))

    dlat = deg_per_m_lat * step_m
    dlon = deg_per_m_lon_ref * step_m

    points = []
    lat = lat_min
    while lat <= lat_max:
        lon = lon_min
        while lon <= lon_max:
            cell_id = latlon_to_grid_cell(lat, lon, step_m)
            points.append((round(lat, 6), round(lon, 6), cell_id))
            lon += dlon
        lat += dlat

    return points


# ============================================================================
# GENERATION DE DONNEES SYNTHETIQUES
# ============================================================================


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance haversine en km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def get_towers_for_point(lat: float, lon: float, radius_km: float = 5.0) -> List[Dict]:
    """Retourne les antennes Yettel proches d'un point."""
    return get_yettel_towers_for_region(lat, lon, radius_km=radius_km)


def _estimate_signal_for_obs(lat: float, lon: float, tower: Dict) -> Dict[str, Optional[float]]:
    """Genere un signal synthetique coherent pour une position et une antenne donnees."""
    dist_km = _haversine_km(lat, lon, tower["lat"], tower["lon"])
    radio = tower.get("radio", "LTE")

    sig = get_realistic_signal(dist_km, radio)

    result = {
        "rssi": float(sig["rssi_dbm"]),
        "rsrp": float(sig["rsrp_dbm"]) if sig.get("rsrp_dbm") is not None else None,
        "rsrq": None,
        "ta": float(sig["timing_advance"]),
    }

    if result["rsrp"] is not None:
        rsrp = result["rsrp"]
        if rsrp > -80:
            result["rsrq"] = float(random.randint(-8, -3))
        elif rsrp > -100:
            result["rsrq"] = float(random.randint(-12, -6))
        else:
            result["rsrq"] = float(random.randint(-16, -10))

    return result


def generate_synthetic_training_data(
    n_samples: int = 2000,
    cities: Optional[List[str]] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Genere des donnees d'entrainement synthetiques pour XGBoost.
    Features: [rssi, rsrp, rsrq, ta, n_towers, mnc, hour]
    Targets: [lat, lon]
    """
    if cities is None:
        cities = ["belgrade", "novi_sad", "nis"]

    all_features = []
    all_targets = []

    city_weights = {
        "belgrade": 0.5,
        "novi_sad": 0.3,
        "nis": 0.2,
    }

    for _ in range(n_samples):
        city = random.choices(cities, weights=[city_weights.get(c, 0.2) for c in cities], k=1)[0]

        if city == "belgrade":
            lat = random.uniform(44.750, 44.860)
            lon = random.uniform(20.380, 20.520)
        elif city == "novi_sad":
            lat = random.uniform(45.220, 45.300)
            lon = random.uniform(19.800, 19.900)
        else:
            lat = random.uniform(43.290, 43.360)
            lon = random.uniform(21.860, 21.930)

        towers = get_towers_for_point(lat, lon, radius_km=6.0)
        if not towers:
            continue

        n_obs = random.randint(1, min(6, len(towers)))
        obs_towers = random.sample(towers, n_obs)

        rssi_vals = []
        rsrp_vals = []
        rsrq_vals = []
        ta_vals = []

        for tw in obs_towers:
            sig = _estimate_signal_for_obs(lat, lon, tw)
            if sig["rssi"] is not None:
                rssi_vals.append(sig["rssi"])
            if sig["rsrp"] is not None:
                rsrp_vals.append(sig["rsrp"])
            if sig["rsrq"] is not None:
                rsrq_vals.append(sig["rsrq"])
            if sig["ta"] is not None:
                ta_vals.append(sig["ta"])

        def _safe_mean(vals: List[float], default: float) -> float:
            return float(np.mean(vals)) if vals else default

        rssi = _safe_mean(rssi_vals, -90.0)
        rsrp = _safe_mean(rsrp_vals, -100.0)
        rsrq = _safe_mean(rsrq_vals, -10.0)
        ta = _safe_mean(ta_vals, 5.0)
        n_towers = float(len(obs_towers))
        mnc = 1.0
        hour = float(random.randint(0, 23))

        features = [rssi, rsrp, rsrq, ta, n_towers, mnc, hour]
        targets = [lat, lon]

        all_features.append(features)
        all_targets.append(targets)

    if not all_features:
        return np.zeros((0, 7)), np.zeros((0, 2))

    return np.array(all_features, dtype=np.float32), np.array(all_targets, dtype=np.float32)


# ============================================================================
# FINGERPRINTING / RADIOMAP
# ============================================================================


class RadioMap:
    """
    RadioMap: stocke les signatures RSSI par position sur une grille 100m×100m.
    """

    def __init__(self, city_name: str, step_m: float = 100):
        self.city_name = city_name
        self.step_m = step_m
        self._fingerprints: Dict[str, FingerprintSignature] = {}
        self._grid_points: List[Tuple[float, float, str]] = []

    def build_from_synthetic_data(self, samples_per_cell: int = 3):
        """Construit la RadioMap a partir de donnees synthetiques."""
        logger.info(f"Construction RadioMap pour {self.city_name}...")
        self._grid_points = generate_grid_points(self.city_name, self.step_m)

        count = 0
        for lat, lon, cell_id in self._grid_points:
            towers = get_towers_for_point(lat, lon, radius_km=4.0)
            if not towers:
                continue

            for _ in range(samples_per_cell):
                n_obs = random.randint(1, min(5, len(towers)))
                obs_towers = random.sample(towers, n_obs)

                fp = FingerprintSignature(
                    lat=lat,
                    lon=lon,
                    grid_cell=cell_id,
                    n_towers=n_obs,
                    mnc=1,
                    hour=random.randint(0, 23),
                )

                for tw in obs_towers:
                    sig = _estimate_signal_for_obs(lat, lon, tw)
                    key = f"{tw['lat']:.4f}_{tw['lon']:.4f}"
                    if sig["rssi"] is not None:
                        fp.rssi_by_cell[key] = sig["rssi"]
                    if sig["rsrp"] is not None:
                        fp.rsrp_by_cell[key] = sig["rsrp"]
                    if sig["rsrq"] is not None:
                        fp.rsrq_by_cell[key] = sig["rsrq"]

                self._fingerprints[f"{cell_id}_{len(self._fingerprints)}"] = fp
                count += 1

        logger.info(f"RadioMap {self.city_name}: {count} signatures generees.")
        return count

    def get_fingerprints(self) -> List[FingerprintSignature]:
        """Retourne toutes les signatures."""
        return list(self._fingerprints.values())

    def save(self, path: Path):
        """Sauvegarde la RadioMap sur disque."""
        data = []
        for fp in self._fingerprints.values():
            data.append({
                "lat": fp.lat,
                "lon": fp.lon,
                "grid_cell": fp.grid_cell,
                "rssi_by_cell": fp.rssi_by_cell,
                "rsrp_by_cell": fp.rsrp_by_cell,
                "rsrq_by_cell": fp.rsrq_by_cell,
                "n_towers": fp.n_towers,
                "mnc": fp.mnc,
                "hour": fp.hour,
            })
        np.save(path, np.array(data, dtype=object), allow_pickle=True)

    def load(self, path: Path):
        """Charge la RadioMap depuis disque."""
        if not path.exists():
            return False
        data = np.load(path, allow_pickle=True)
        self._fingerprints = {}
        for item in data:
            fp = FingerprintSignature(
                lat=item["lat"],
                lon=item["lon"],
                grid_cell=item["grid_cell"],
                rssi_by_cell=item.get("rssi_by_cell", {}),
                rsrp_by_cell=item.get("rsrp_by_cell", {}),
                rsrq_by_cell=item.get("rsrq_by_cell", {}),
                n_towers=item.get("n_towers", 0),
                mnc=item.get("mnc", 1),
                hour=item.get("hour", 12),
            )
            key = f"{fp.grid_cell}_{len(self._fingerprints)}"
            self._fingerprints[key] = fp
        logger.info(f"RadioMap chargee: {len(self._fingerprints)} signatures.")
        return True


# ============================================================================
# KNN MATCHER
# ============================================================================


class KNNRadioMatcher:
    """
    KNN matcher pour trouver la position la plus probable a partir d'une
    signature RSSI observee.
    """

    def __init__(self, n_neighbors: int = 7, metric: str = "manhattan"):
        self.n_neighbors = n_neighbors
        self.metric = metric
        self.knn: Optional[KNeighborsRegressor] = None
        self.scaler: Optional[StandardScaler] = None
        self._trained = False

    def _signature_to_features(self, fp: FingerprintSignature) -> np.ndarray:
        """Convertit une signature Fingerprint en vecteur de features."""
        rssi_vals = list(fp.rssi_by_cell.values())
        rsrp_vals = list(fp.rsrp_by_cell.values())
        rsrq_vals = list(fp.rsrq_by_cell.values())

        rssi = float(np.mean(rssi_vals)) if rssi_vals else -90.0
        rsrp = float(np.mean(rsrp_vals)) if rsrp_vals else -100.0
        rsrq = float(np.mean(rsrq_vals)) if rsrq_vals else -10.0
        ta = 5.0
        n_towers = float(fp.n_towers) if fp.n_towers > 0 else 1.0
        mnc = float(fp.mnc)
        hour = float(fp.hour)

        return np.array([[rssi, rsrp, rsrq, ta, n_towers, mnc, hour]], dtype=np.float32)

    def _obs_to_features(self, obs: Observation) -> np.ndarray:
        """Convertit une observation en vecteur de features."""
        rssi = float(obs.rssi) if obs.rssi is not None else -90.0
        rsrp = float(obs.rsrp) if obs.rsrp is not None else -100.0
        rsrq = float(obs.rsrq) if obs.rsrq is not None else -10.0
        ta = float(obs.ta) if obs.ta is not None else 5.0
        n_towers = float(obs.n_towers) if obs.n_towers > 0 else 1.0
        mnc = float(obs.mnc)
        hour = float(obs.hour)

        return np.array([[rssi, rsrp, rsrq, ta, n_towers, mnc, hour]], dtype=np.float32)

    def train(self, fingerprints: List[FingerprintSignature]):
        """Entraine le KNN sur les signatures RadioMap."""
        if len(fingerprints) < self.n_neighbors:
            logger.warning(f"Pas assez de fingerprints ({len(fingerprints)}) pour KNN.")
            return False

        X = []
        y_lat = []
        y_lon = []

        for fp in fingerprints:
            feat = self._signature_to_features(fp)[0]
            X.append(feat)
            y_lat.append(fp.lat)
            y_lon.append(fp.lon)

        X = np.array(X, dtype=np.float32)
        y = np.column_stack([y_lat, y_lon]).astype(np.float32)

        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        self.knn = KNeighborsRegressor(
            n_neighbors=min(self.n_neighbors, len(X)),
            weights="distance",
            metric=self.metric,
            n_jobs=KNN_PARAMS["n_jobs"],
        )
        self.knn.fit(X_scaled, y)
        self._trained = True
        logger.info(f"KNN entraine sur {len(X)} signatures.")
        return True

    def predict(self, obs: Observation) -> Optional[PredictionResult]:
        """Retourne la position la plus probable pour une observation."""
        if not self._trained or self.knn is None or self.scaler is None:
            logger.error("KNN non entraine.")
            return None

        x = self._obs_to_features(obs)
        x_scaled = self.scaler.transform(x)

        y_pred = self.knn.predict(x_scaled)[0]
        lat, lon = float(y_pred[0]), float(y_pred[1])

        distances, indices = self.knn.kneighbors(x_scaled)
        neighbor_std = float(np.std(distances[0]))

        if neighbor_std < 0.1:
            accuracy_m = 50.0
            confidence = "good"
        elif neighbor_std < 0.5:
            accuracy_m = 150.0
            confidence = "moderate"
        else:
            accuracy_m = 500.0
            confidence = "low"

        return PredictionResult(
            latitude=round(lat, 6),
            longitude=round(lon, 6),
            accuracy_meters=accuracy_m,
            confidence=confidence,
            method="knn_fingerprinting",
            weights={"knn": 1.0},
            details={
                "n_neighbors_used": self.knn.n_neighbors,
                "neighbor_std": round(neighbor_std, 4),
            },
        )

    def save(self, path: Path):
        """Sauvegarde le modele KNN."""
        if not self._trained:
            return
        joblib.dump({"knn": self.knn, "scaler": self.scaler}, path)

    def load(self, path: Path) -> bool:
        """Charge le modele KNN."""
        if not path.exists():
            return False
        data = joblib.load(path)
        self.knn = data["knn"]
        self.scaler = data["scaler"]
        self._trained = True
        return True


# ============================================================================
# XGBOOST REGRESSOR
# ============================================================================


class XGBoostPositionRegressor:
    """
    XGBoost regressor pour predire lat/lon a partir de features radio.
    """

    def __init__(self, params: Optional[Dict] = None):
        self.params = params or XGBOOST_PARAMS
        self.model_lat: Optional[XGBRegressor] = None
        self.model_lon: Optional[XGBRegressor] = None
        self.scaler: Optional[StandardScaler] = None
        self._trained = False

    def train(self, X: np.ndarray, y: np.ndarray, test_size: float = 0.15):
        """
        Entraine deux modeles XGBoost: un pour la latitude, un pour la longitude.
        """
        if len(X) < 50:
            logger.error(f"Pas assez de donnees d'entrainement: {len(X)} echantillons.")
            return False

        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        y_lat = y[:, 0]
        y_lon = y[:, 1]

        X_tr, X_val, y_lat_tr, y_lat_val = train_test_split(
            X_scaled, y_lat, test_size=test_size, random_state=42
        )
        _, _, y_lon_tr, y_lon_val = train_test_split(
            X_scaled, y_lon, test_size=test_size, random_state=42
        )

        self.model_lat = XGBRegressor(**self.params)
        self.model_lon = XGBRegressor(**self.params)

        self.model_lat.fit(
            X_tr, y_lat_tr,
            eval_set=[(X_val, y_lat_val)],
            verbose=False,
        )
        self.model_lon.fit(
            X_tr, y_lon_tr,
            eval_set=[(X_val, y_lon_val)],
            verbose=False,
        )

        # Evaluation
        lat_pred = self.model_lat.predict(X_val)
        lon_pred = self.model_lon.predict(X_val)

        mae_lat = mean_absolute_error(y_lat_val, lat_pred)
        mae_lon = mean_absolute_error(y_lon_val, lon_pred)
        rmse_lat = np.sqrt(mean_squared_error(y_lat_val, lat_pred))
        rmse_lon = np.sqrt(mean_squared_error(y_lon_val, lon_pred))

        logger.info(f"XGBoost entraine sur {len(X)} echantillons.")
        logger.info(f"MAE lat={mae_lat:.6f} (~{mae_lat*111320:.1f}m), lon={mae_lon:.6f} (~{mae_lon*111320*math.cos(math.radians(y_lat_val.mean())):.1f}m)")
        logger.info(f"RMSE lat={rmse_lat:.6f}, lon={rmse_lon:.6f}")

        self._trained = True
        return True

    def predict(self, obs: Observation) -> Optional[PredictionResult]:
        """Prediction de position pour une observation."""
        if not self._trained or self.model_lat is None or self.scaler is None:
            logger.error("XGBoost non entraine.")
            return None

        x = np.array([[
            float(obs.rssi) if obs.rssi is not None else -90.0,
            float(obs.rsrp) if obs.rsrp is not None else -100.0,
            float(obs.rsrq) if obs.rsrq is not None else -10.0,
            float(obs.ta) if obs.ta is not None else 5.0,
            float(obs.n_towers) if obs.n_towers > 0 else 1.0,
            float(obs.mnc),
            float(obs.hour),
        ]], dtype=np.float32)

        x_scaled = self.scaler.transform(x)
        lat_pred = float(self.model_lat.predict(x_scaled)[0])
        lon_pred = float(self.model_lon.predict(x_scaled)[0])

        lat_pred = max(-90.0, min(90.0, lat_pred))
        lon_pred = max(-180.0, min(180.0, lon_pred))

        return PredictionResult(
            latitude=round(lat_pred, 6),
            longitude=round(lon_pred, 6),
            accuracy_meters=250.0,
            confidence="moderate",
            method="xgboost_regressor",
            weights={"xgboost": 1.0},
            details={},
        )

    def save(self, path: Path):
        """Sauvegarde le modele XGBoost."""
        if not self._trained:
            return
        joblib.dump({
            "model_lat": self.model_lat,
            "model_lon": self.model_lon,
            "scaler": self.scaler,
        }, path)

    def load(self, path: Path) -> bool:
        """Charge le modele XGBoost."""
        if not path.exists():
            return False
        data = joblib.load(path)
        self.model_lat = data["model_lat"]
        self.model_lon = data["model_lon"]
        self.scaler = data["scaler"]
        self._trained = True
        return True


# ============================================================================
# TRIANGULATION WRAPPER
# ============================================================================


def triangulate(towers: List[Dict]) -> Optional[PredictionResult]:
    """
    Wrapper autour du moteur de triangulation existant.
    """
    if not towers or len(towers) < 2:
        return None

    try:
        # Import dynamique pour eviter les dependances circulaires
        from core.triangulation import estimate_location as _estimate_location
        result = _estimate_location(towers, method="auto")
        return PredictionResult(
            latitude=result["latitude"],
            longitude=result["longitude"],
            accuracy_meters=result["accuracy_meters"],
            confidence=result["confidence"],
            method=f"triangulation_{result['method']}",
            weights={"triangulation": 1.0},
            details={"towers_used": result["towers_used"]},
        )
    except Exception as e:
        logger.error(f"Triangulation echouee: {e}")
        return None


# ============================================================================
# FUSION ENSEMBLE
# ============================================================================


class EnsemblePredictor:
    """
    Combine triangulation + fingerprinting KNN + XGBoost ML avec poids adaptatifs.
    """

    def __init__(self):
        self.xgb_model = XGBoostPositionRegressor()
        self.knn_matcher = KNNRadioMatcher()
        self.radio_maps: Dict[str, RadioMap] = {}
        self._initialized = False

    def initialize(self, force_retrain: bool = False, n_synthetic_samples: int = 3000):
        """
        Initialise le predicteur ensemble: entraine XGBoost, KNN et construit
        les RadioMaps si necessaire.
        """
        logger.info("Initialisation EnsemblePredictor...")

        # 1. Donnees synthetiques pour XGBoost
        xgb_path = MODEL_DIR / "xgboost_ensemble_model.joblib"
        if not force_retrain and xgb_path.exists():
            logger.info("Chargement XGBoost existant...")
            self.xgb_model.load(xgb_path)
        else:
            logger.info(f"Generation de {n_synthetic_samples} echantillons synthetiques...")
            X, y = generate_synthetic_training_data(n_samples=n_synthetic_samples)
            if len(X) > 0:
                logger.info(f"Entrainement XGBoost sur {len(X)} echantillons...")
                self.xgb_model.train(X, y)
                self.xgb_model.save(xgb_path)
            else:
                logger.error("Aucune donnee synthetique generee.")

        # 2. RadioMaps et KNN par ville
        for city_name in ["belgrade", "novi_sad", "nis"]:
            rm = RadioMap(city_name, step_m=DEFAULT_GRID_STEP_METERS)
            fp_path = MODEL_DIR / f"radio_map_{city_name}.npy"

            if not force_retrain and fp_path.exists():
                rm.load(fp_path)
            else:
                rm.build_from_synthetic_data(samples_per_cell=2)
                rm.save(fp_path)

            if len(rm.get_fingerprints()) > 0:
                self.radio_maps[city_name] = rm

        # 3. KNN global sur tous les fingerprints
        knn_path = MODEL_DIR / "knn_global_fingerprints.joblib"
        if not force_retrain and knn_path.exists():
            self.knn_matcher.load(knn_path)
        else:
            all_fps = []
            for rm in self.radio_maps.values():
                all_fps.extend(rm.get_fingerprints())

            if len(all_fps) > 10:
                self.knn_matcher.train(all_fps)
                self.knn_matcher.save(knn_path)

        self._initialized = True
        logger.info(f"EnsemblePredictor initialise. RadioMaps: {list(self.radio_maps.keys())}")
        return True

    def _detect_city(self, lat: float, lon: float) -> Optional[str]:
        """Detecte la ville la plus proche."""
        for city_name, cfg in CITY_GRIDS.items():
            if cfg["lat_min"] <= lat <= cfg["lat_max"] and cfg["lon_min"] <= lon <= cfg["lon_max"]:
                return city_name
        return None

    def _adaptive_weights(self, obs: Observation, triang_ok: bool, knn_ok: bool) -> Dict[str, float]:
        """
        Calcule les poids adaptatifs pour la fusion.
        Signal fort -> plus de poids a triangulation.
        Peu de tours -> plus de poids a ML/fingerprinting.
        """
        w_triang = 0.0
        w_knn = 0.0
        w_xgb = 0.0

        signal_quality = 1.0
        if obs.rssi is not None:
            signal_quality = max(0.0, min(1.0, (obs.rssi + 110) / 60))

        n_towers = max(obs.n_towers, 1)

        if triang_ok:
            if signal_quality > 0.7 and n_towers >= 4:
                w_triang = 0.6
            elif signal_quality > 0.4:
                w_triang = 0.4
            else:
                w_triang = 0.25

        if knn_ok:
            w_knn = 0.3 if n_towers >= 3 else 0.45

        w_xgb = max(0.15, 1.0 - w_triang - w_knn)

        total = w_triang + w_knn + w_xgb
        if total > 0:
            w_triang /= total
            w_knn /= total
            w_xgb /= total

        return {
            "triangulation": round(w_triang, 3),
            "knn": round(w_knn, 3),
            "xgboost": round(w_xgb, 3),
        }

    def predict(
        self,
        observation: Observation,
        towers: Optional[List[Dict]] = None,
    ) -> PredictionResult:
        """
        Prediction de position par fusion ensemble.
        """
        if not self._initialized:
            raise RuntimeError("EnsemblePredictor non initialise. Appelez initialize() d'abord.")

        triang_result = None
        knn_result = None
        xgb_result = None

        # Triangulation
        if towers and len(towers) >= 2:
            triang_result = triangulate(towers)

        # KNN Fingerprinting
        city = self._detect_city(
            observation.lat or 44.7866,
            observation.lon or 20.4489,
        )
        if city and city in self.radio_maps:
            # On essaie KNN global
            knn_result = self.knn_matcher.predict(observation)

        # XGBoost
        xgb_result = self.xgb_model.predict(observation)

        # Poids adaptatifs
        weights = self._adaptive_weights(
            observation,
            triang_ok=triang_result is not None,
            knn_ok=knn_result is not None,
        )

        # Fusion ponderee
        predictions = []
        if triang_result:
            predictions.append((triang_result.latitude, triang_result.longitude, weights["triangulation"]))
        if knn_result:
            predictions.append((knn_result.latitude, knn_result.longitude, weights["knn"]))
        if xgb_result:
            predictions.append((xgb_result.latitude, xgb_result.longitude, weights["xgboost"]))

        if not predictions:
            return PredictionResult(
                latitude=44.7866,
                longitude=20.4489,
                accuracy_meters=5000.0,
                confidence="poor",
                method="fallback",
                weights=weights,
                details={"error": "Aucune methode disponible"},
            )

        total_w = sum(w for _, _, w in predictions)
        if total_w > 0:
            lat_ens = sum(lat * w for lat, _, w in predictions) / total_w
            lon_ens = sum(lon * w for _, lon, w in predictions) / total_w
        else:
            lat_ens = predictions[0][0]
            lon_ens = predictions[0][1]

        # Estimation d'erreur
        errors = []
        for lat, lon, w in predictions:
            d = _haversine_km(lat_ens, lon_ens, lat, lon)
            errors.append(d * 1000.0 * w)
        accuracy_m = min(max(sum(errors) * 1.5, 50.0), 5000.0)

        if accuracy_m < 200:
            confidence = "excellent"
        elif accuracy_m < 500:
            confidence = "good"
        elif accuracy_m < 1000:
            confidence = "moderate"
        elif accuracy_m < 3000:
            confidence = "low"
        else:
            confidence = "poor"

        return PredictionResult(
            latitude=round(lat_ens, 6),
            longitude=round(lon_ens, 6),
            accuracy_meters=round(accuracy_m, 1),
            confidence=confidence,
            method="ensemble_fusion",
            weights=weights,
            details={
                "triangulation": {
                    "lat": triang_result.latitude,
                    "lon": triang_result.longitude,
                    "accuracy_m": triang_result.accuracy_meters,
                } if triang_result else None,
                "knn_fingerprinting": {
                    "lat": knn_result.latitude,
                    "lon": knn_result.longitude,
                    "accuracy_m": knn_result.accuracy_meters,
                } if knn_result else None,
                "xgboost": {
                    "lat": xgb_result.latitude,
                    "lon": xgb_result.longitude,
                    "accuracy_m": xgb_result.accuracy_meters,
                } if xgb_result else None,
            },
        )


# ============================================================================
# INSTANCES GLOBALES
# ============================================================================

_ensemble_predictor: Optional[EnsemblePredictor] = None


def get_ensemble_predictor() -> EnsemblePredictor:
    """Retourne l'instance singleton du predicteur ensemble."""
    global _ensemble_predictor
    if _ensemble_predictor is None:
        _ensemble_predictor = EnsemblePredictor()
        _ensemble_predictor.initialize(force_retrain=False)
    return _ensemble_predictor


def predict_position(
    rssi: Optional[float] = None,
    rsrp: Optional[float] = None,
    rsrq: Optional[float] = None,
    ta: Optional[float] = None,
    n_towers: int = 1,
    mnc: int = 1,
    hour: Optional[int] = None,
    towers: Optional[List[Dict]] = None,
    lat_hint: Optional[float] = None,
    lon_hint: Optional[float] = None,
) -> Dict:
    """
    API de haut niveau pour predire une position.
    Compatible avec les services de geolocalisation existants.
    """
    if hour is None:
        hour = datetime.now().hour

    obs = Observation(
        rssi=rssi,
        rsrp=rsrp,
        rsrq=rsrq,
        ta=ta,
        n_towers=n_towers,
        mnc=mnc,
        hour=hour,
        lat=lat_hint,
        lon=lon_hint,
    )

    predictor = get_ensemble_predictor()
    result = predictor.predict(observation=obs, towers=towers)

    return {
        "latitude": result.latitude,
        "longitude": result.longitude,
        "accuracy_meters": result.accuracy_meters,
        "confidence": result.confidence,
        "method": result.method,
        "weights": result.weights,
        "details": result.details,
    }


# ============================================================================
# UTILITAIRES
# ============================================================================

def build_radio_map_for_city(city_name: str, step_m: float = 100) -> RadioMap:
    """Construit une RadioMap pour une ville."""
    rm = RadioMap(city_name, step_m)
    rm.build_from_synthetic_data(samples_per_cell=3)
    return rm


def train_models_from_scratch(n_samples: int = 5000):
    """Re-entraine tous les modeles depuis zero."""
    logger.info(f"Re-entrainement complet avec {n_samples} echantillons...")
    global _ensemble_predictor
    _ensemble_predictor = EnsemblePredictor()
    _ensemble_predictor.initialize(force_retrain=True, n_synthetic_samples=n_samples)
    return _ensemble_predictor


# ============================================================================
# AUTO-INITIALISATION LEGERE
# ============================================================================

try:
    from datetime import datetime
except ImportError:
    pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("=== Test du module position_predictor ===")

    predictor = EnsemblePredictor()
    predictor.initialize(force_retrain=False, n_synthetic_samples=1500)

    obs = Observation(
        rssi=-72.0,
        rsrp=-92.0,
        rsrq=-8.0,
        ta=2.0,
        n_towers=3,
        mnc=1,
        hour=14,
    )

    # Convertir les tuples Yettel en dicts compatibles avec la triangulation
    def _tower_tuple_to_dict(t):
        return {
            "lat": t[0],
            "lon": t[1],
            "lac": t[2],
            "tac": t[3],
            "radio": t[4],
            "altitude_m": t[5],
            "azimuth": t[6],
            "tx_power_dbm": t[7],
            "bandwidth_mhz": t[8],
            "mcc": 220,
            "mnc": 1,
        }

    bg_towers = [_tower_tuple_to_dict(t) for t in YETTEL_TOWERS_BELGRADE[:5]]
    result = predictor.predict(obs, towers=bg_towers)

    print("\n=== Resultat de prediction ===")
    print(f"Latitude:  {result.latitude}")
    print(f"Longitude: {result.longitude}")
    print(f"Precision: {result.accuracy_meters:.1f} m")
    print(f"Confiance: {result.confidence}")
    print(f"Methode:   {result.method}")
    print(f"Poids:     {result.weights}")
    print(f"Details:   {result.details}")
