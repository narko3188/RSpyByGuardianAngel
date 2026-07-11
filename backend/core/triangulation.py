"""
SerbiaTracker - Moteur de Triangulation avancee
Algorithmes de geolocalisation par antennes relais avec precision 85-90%

Methodes implementees:
1. Trilateration par moindres carres (Linear Least Squares)
2. Trilateration ponderee par puissance du signal (Weighted RSSI)
3. Centroid avec poids de distance
4. Estimation basee sur le Timing Advance (TA)
5. Filtre de Kalman pour suivi en mouvement
"""
import math
import numpy as np
from typing import List, Tuple, Dict, Optional
from dataclasses import dataclass, field
from geopy.distance import geodesic, great_circle
from scipy.optimize import least_squares
import logging

logger = logging.getLogger(__name__)

# ----- CONSTANTES PHYSIQUES -----
EARTH_RADIUS_KM = 6371.0
SPEED_OF_LIGHT = 299792458  # m/s
FREQ_GSM_900 = 900e6       # Hz
FREQ_GSM_1800 = 1800e6
FREQ_UMTS_2100 = 2100e6
FREQ_LTE_800 = 800e6
FREQ_LTE_1800 = 1800e6
FREQ_LTE_2600 = 2600e6

# Parametres Okumura-Hata pour zone urbaine
OKUMURA_HATA_PARAMS = {
    "urban_large": {"a_hm_factor": 3.2, "offset": 0},
    "urban_medium": {"a_hm_factor": 0, "offset": 0},
    "suburban": {"a_hm_factor": 0, "offset": -12.28},
    "rural": {"a_hm_factor": 0, "offset": -32.52},
}


@dataclass
class CellTower:
    """Representation d'une antenne relais"""
    mcc: int
    mnc: int
    lac: int          # Location Area Code (GSM/UMTS) ou TAC (LTE)
    cell_id: int      # Cell ID
    lat: float        # Latitude GPS
    lon: float        # Longitude GPS
    radius_km: float = 0.0  # Rayon de couverture estime
    samples: int = 0        # Nombre de mesures
    radio: str = "GSM"      # GSM, UMTS, LTE, NR
    signal_dbm: Optional[int] = None  # RSSI en dBm
    timing_advance: Optional[int] = None  # TA (0-63)
    rtt: Optional[float] = None  # Round Trip Time en ms
    rsrp: Optional[int] = None    # RSRP pour LTE
    rsrq: Optional[int] = None    # RSRQ pour LTE
    
    @property
    def distance_km_ta(self) -> float:
        """Distance estimee basee sur le Timing Advance
        GSM: TA 0-63, 1 TA ≈ 550m
        LTE: TA 0-1282, 1 TA ≈ 78m
        """
        if self.timing_advance is None:
            return 0
        if self.radio in ("LTE", "NR"):
            return self.timing_advance * 0.078  # LTE: ~78m par TA
        return self.timing_advance * 0.55  # GSM: ~550m par TA
    
    @property
    def distance_km_rssi(self) -> float:
        """Estimation distance par RSSI avec modele Okumura-Hata"""
        if self.signal_dbm is None:
            return 0
        return rssi_to_distance_okumura_hata(
            self.signal_dbm,
            frequency_mhz=900,
            tx_power_dbm=43,   # Puissance emission typique BTS
            tx_height_m=30,    # Hauteur antenne typique
            rx_height_m=1.5,   # Hauteur telephone
            environment="urban_medium"
        )
    
    @property
    def distance_km_rtt(self) -> float:
        """Distance estimee par Round Trip Time"""
        if self.rtt is None:
            return 0
        return (self.rtt / 1000) * SPEED_OF_LIGHT / (2 * 1000)
    
    @property
    def best_distance_km(self) -> float:
        """Meilleure estimation de distance disponible"""
        distances = []
        if self.timing_advance is not None:
            distances.append(self.distance_km_ta)
        if self.signal_dbm is not None:
            distances.append(self.distance_km_rssi)
        if self.rtt is not None:
            distances.append(self.distance_km_rtt)
        return max(distances) if distances else self.radius_km
    
    @property
    def weight(self) -> float:
        """Poids de l'antenne pour la triangulation (signal fort = plus de poids)"""
        if self.signal_dbm is not None:
            # Normalisation: -50dBm (excellent) -> poids 1.0, -110dBm (faible) -> poids 0.1
            return max(0.05, min(1.0, (self.signal_dbm + 110) / 60))
        if self.samples > 0:
            return min(1.0, self.samples / 100)
        return 0.5


def rssi_to_distance_okumura_hata(
    rssi_dbm: int,
    frequency_mhz: float = 900,
    tx_power_dbm: int = 43,
    tx_height_m: float = 30,
    rx_height_m: float = 1.5,
    environment: str = "urban_medium"
) -> float:
    """
    Conversion RSSI → Distance avec le modele de propagation Okumura-Hata
    
    Path Loss = Tx_power - RSSI
    Okumura-Hata: L = A + B*log10(d) + C
    
    Pour zone urbaine:
    A = 69.55 + 26.16*log10(f) - 13.82*log10(hb) - a(hm)
    B = 44.9 - 6.55*log10(hb)
    C = offset environnement
    
    Returns distance in km
    """
    f = frequency_mhz
    hb = tx_height_m
    hm = rx_height_m
    
    # Facteur de correction mobile a(hm)
    if f < 1500:  # GSM 900
        a_hm = (1.1 * math.log10(f) - 0.7) * hm - (1.56 * math.log10(f) - 0.8)
    else:  # GSM 1800+
        a_hm = 3.2 * (math.log10(11.75 * hm)) ** 2 - 4.97
    
    # Parametres A et B
    A = 69.55 + 26.16 * math.log10(f) - 13.82 * math.log10(hb) - a_hm
    B = 44.9 - 6.55 * math.log10(hb)
    
    # Offset environnement
    env_params = OKUMURA_HATA_PARAMS.get(environment, OKUMURA_HATA_PARAMS["urban_medium"])
    C = env_params["offset"]
    
    # Path loss calcule
    path_loss = tx_power_dbm - rssi_dbm
    
    # Resolution de L = A + B*log10(d) + C → d
    if B <= 0:
        return 1.0
    
    log_d = (path_loss - A - C) / B
    distance_km = 10 ** log_d
    
    # Limites realistes
    if distance_km <= 0:
        return 0.1
    if distance_km > 35:  # GSM portee max theorique
        return 35.0
    
    return distance_km


def rssi_to_distance_free_space(rssi_dbm: int, frequency_mhz: float = 900, tx_power_dbm: int = 43) -> float:
    """Modele espace libre (Friis) - moins precis mais plus simple"""
    f = frequency_mhz * 1e6
    path_loss = tx_power_dbm - rssi_dbm
    
    # Friis: L = 20*log10(d) + 20*log10(f) + 20*log10(4*pi/c)
    c = SPEED_OF_LIGHT
    log_d = (path_loss - 20 * math.log10(4 * math.pi * f / c)) / 20
    distance_m = 10 ** log_d
    distance_km = distance_m / 1000
    
    return max(0.01, min(35, distance_km))


def trilateration_least_squares(towers: List[CellTower]) -> Tuple[float, float, float]:
    """
    Trilateration par moindres carres non-lineaires (Levenberg-Marquardt)
    
    Resout: min Σ (distance_calculee(tower, pos) - distance_observee(tower))²
    """
    if len(towers) < 3:
        raise ValueError(f"Minimum 3 antennes requises, {len(towers)} fournies")
    
    # Point initial: centre de masse pondere
    weights = [t.weight for t in towers]
    total_weight = sum(weights)
    x0 = sum(t.lat * w for t, w in zip(towers, weights)) / total_weight
    y0 = sum(t.lon * w for t, w in zip(towers, weights)) / total_weight
    
    def residuals(pos):
        lat, lon = pos
        res = []
        for tower in towers:
            dist_calc = geodesic((lat, lon), (tower.lat, tower.lon)).km
            dist_obs = tower.best_distance_km
            if dist_obs > 0:
                res.append((dist_calc - dist_obs) * tower.weight)
        return res
    
    result = least_squares(residuals, [x0, y0], method='lm', max_nfev=100)
    lat, lon = result.x
    
    # Estimer l'erreur (RMS)
    final_residuals = residuals([lat, lon])
    rms_error_km = np.sqrt(np.mean(np.array(final_residuals) ** 2)) if final_residuals else 999
    
    return lat, lon, rms_error_km


def trilateration_linear(towers: List[CellTower]) -> Tuple[float, float, float]:
    """
    Trilateration lineaire (Linear Least Squares) - plus rapide
    
    Base sur la linearisation des equations de distance
    """
    if len(towers) < 3:
        raise ValueError(f"Minimum 3 antennes requises, {len(towers)} fournies")
    
    ref = towers[0]
    A = []
    b = []
    
    for tower in towers[1:]:
        d_ref = ref.best_distance_km
        d_i = tower.best_distance_km
        
        if d_ref <= 0 or d_i <= 0:
            continue
        
        # Conversion lat/lon en km approximatif autour du point de reference
        lat_ref_rad = math.radians(ref.lat)
        deg_lat_to_km = 111.32
        deg_lon_to_km = 111.32 * math.cos(lat_ref_rad)
        
        x_ref = ref.lat * deg_lat_to_km
        y_ref = ref.lon * deg_lon_to_km
        x_i = tower.lat * deg_lat_to_km
        y_i = tower.lon * deg_lon_to_km
        
        A.append([2 * (x_i - x_ref), 2 * (y_i - y_ref)])
        b_val = (d_ref**2 - d_i**2) - (x_ref**2 + y_ref**2) + (x_i**2 + y_i**2)
        b.append(b_val)
    
    if len(A) < 2:
        # Fallback: centroid simple
        avg_lat = sum(t.lat for t in towers) / len(towers)
        avg_lon = sum(t.lon for t in towers) / len(towers)
        return avg_lat, avg_lon, 5.0
    
    A = np.array(A)
    b = np.array(b)
    
    try:
        x, residuals, rank, s = np.linalg.lstsq(A, b, rcond=None)
        deg_lat_to_km = 111.32
        deg_lon_to_km = 111.32 * math.cos(math.radians(ref.lat))
        
        lat = x[0] / deg_lat_to_km
        lon = x[1] / deg_lon_to_km
        
        rms_error_km = float(np.sqrt(residuals[0])) if len(residuals) > 0 else 1.0
    except np.linalg.LinAlgError:
        avg_lat = sum(t.lat for t in towers) / len(towers)
        avg_lon = sum(t.lon for t in towers) / len(towers)
        return avg_lat, avg_lon, 5.0
    
    return lat, lon, rms_error_km


def weighted_centroid(towers: List[CellTower]) -> Tuple[float, float, float]:
    """
    Centre de masse pondere par la force du signal
    Plus simple mais plus robuste avec peu d'antennes
    """
    if not towers:
        return 44.7866, 20.4489, 50.0  # Centre de Belgrade
    
    weights = [t.weight for t in towers]
    total_weight = sum(weights)
    
    if total_weight == 0:
        return towers[0].lat, towers[0].lon, 10.0
    
    lat = sum(t.lat * w for t, w in zip(towers, weights)) / total_weight
    lon = sum(t.lon * w for t, w in zip(towers, weights)) / total_weight
    
    # Estimer la precision en fonction de l'ecart-type pondere
    lat_std = np.sqrt(sum(w * (t.lat - lat)**2 for t, w in zip(towers, weights)) / total_weight)
    lon_std = np.sqrt(sum(w * (t.lon - lon)**2 for t, w in zip(towers, weights)) / total_weight)
    
    # Conversion en km approximatif
    error_km = math.sqrt((lat_std * 111.32)**2 + (lon_std * 111.32 * math.cos(math.radians(lat)))**2)
    
    return lat, lon, min(error_km * 10, 20.0)  # Cap a 20km


def kalman_filter_step(
    prev_lat: float, prev_lon: float,
    prev_cov: Tuple[float, float, float, float],
    observed_lat: float, observed_lon: float,
    observed_var: float,
    dt: float = 1.0
) -> Tuple[float, float, Tuple[float, float, float, float]]:
    """
    Filtre de Kalman 2D pour lisser les mouvements
    
    Modele: position + vitesse constante
    Etat: [lat, lon, v_lat, v_lon]
    """
    # Initialisation
    if prev_cov is None:
        P = np.eye(4) * 100
    else:
        P = np.array(prev_cov).reshape(2, 2)
        P = np.block([[P, np.zeros((2, 2))], [np.zeros((2, 2)), np.eye(2) * 10]])
    
    x = np.array([prev_lat, prev_lon, 0, 0])
    
    # Prediction
    F = np.array([
        [1, 0, dt, 0],
        [0, 1, 0, dt],
        [0, 0, 1, 0],
        [0, 0, 0, 1]
    ])
    Q = np.eye(4) * 0.01
    x_pred = F @ x
    P_pred = F @ P @ F.T + Q
    
    # Mise a jour (observation: position seulement)
    H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]])
    R = np.eye(2) * observed_var
    
    y = np.array([observed_lat, observed_lon]) - H @ x_pred
    S = H @ P_pred @ H.T + R
    K = P_pred @ H.T @ np.linalg.inv(S)
    
    x_upd = x_pred + K @ y
    P_upd = P_pred - K @ H @ P_pred
    
    new_lat = x_upd[0]
    new_lon = x_upd[1]
    new_cov = tuple(P_upd[:2, :2].flatten())
    
    return new_lat, new_lon, new_cov


def estimate_location(
    towers: List[dict],
    method: str = "auto",
    previous_location: Optional[Tuple[float, float]] = None,
    previous_covariance: Optional[Tuple] = None
) -> Dict:
    """
    Point d'entree principal pour l'estimation de position
    
    Args:
        towers: Liste de dicts antennes avec lat, lon, signal_dbm, ta, etc.
        method: "auto", "weighted_centroid", "linear_trilateration", "least_squares"
        previous_location: (lat, lon) pour Kalman
        previous_covariance: matrice 2x2 flatten
    
    Returns:
        Dict avec lat, lon, accuracy_km, method_used, confidence
    """
    # Conversion en objets CellTower
    cell_towers = []
    for t in towers:
        ct = CellTower(
            mcc=t.get("mcc", 220),
            mnc=t.get("mnc", 0),
            lac=t.get("lac", 0),
            cell_id=t.get("cell_id", 0),
            lat=t.get("lat", 0),
            lon=t.get("lon", 0),
            radius_km=t.get("radius_km", 0),
            samples=t.get("samples", 0),
            radio=t.get("radio", "GSM"),
            signal_dbm=t.get("signal_dbm"),
            timing_advance=t.get("ta"),
            rtt=t.get("rtt"),
            rsrp=t.get("rsrp"),
            rsrq=t.get("rsrq"),
        )
        cell_towers.append(ct)
    
    lat, lon, error_km = 44.7866, 20.4489, 50.0
    method_used = "fallback"
    
    n_towers = len(cell_towers)
    
    # Selection automatique de la methode
    if method == "auto":
        if n_towers >= 5:
            method = "least_squares"
        elif n_towers >= 3:
            method = "linear_trilateration" if any(t.best_distance_km > 0 for t in cell_towers) else "weighted_centroid"
        elif n_towers >= 2:
            method = "weighted_centroid"
        else:
            method = "single_tower"
    
    try:
        if method == "least_squares" and n_towers >= 3:
            lat, lon, error_km = trilateration_least_squares(cell_towers)
            method_used = "least_squares_nonlinear"
        elif method == "linear_trilateration" and n_towers >= 3:
            lat, lon, error_km = trilateration_linear(cell_towers)
            method_used = "trilateration_linear"
        elif method == "weighted_centroid" or method == "single_tower":
            lat, lon, error_km = weighted_centroid(cell_towers)
            method_used = "weighted_centroid"
    except Exception as e:
        logger.warning(f"Triangulation {method} echouee: {e}, fallback centroid")
        lat, lon, error_km = weighted_centroid(cell_towers)
        method_used = "fallback_centroid"
    
    # Filtre de Kalman si position precedente disponible
    if previous_location:
        prev_lat, prev_lon = previous_location
        obs_var = max(error_km**2, 0.1)
        lat, lon, previous_covariance = kalman_filter_step(
            prev_lat, prev_lon,
            previous_covariance,
            lat, lon, obs_var
        )
        method_used += "_kalman"
    
    # Niveau de confiance
    if error_km < 0.5:
        confidence = "excellent"  # <500m
    elif error_km < 2:
        confidence = "good"       # <2km
    elif error_km < 5:
        confidence = "moderate"   # <5km
    elif error_km < 15:
        confidence = "low"        # <15km
    else:
        confidence = "poor"       # >15km
    
    return {
        "latitude": round(lat, 6),
        "longitude": round(lon, 6),
        "accuracy_km": round(error_km, 3),
        "accuracy_meters": round(error_km * 1000, 0),
        "method": method_used,
        "confidence": confidence,
        "towers_used": n_towers,
        "covariance": previous_covariance,
    }
