"""
SerbiaTracker - Module de propagation avance
COST-231 Hata, TDOA, Calibration RSSI, TA multi-techno

Integre directement avec core/triangulation.py (CellTower, estimate_location).
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Optional
import numpy as np
from scipy.optimize import least_squares

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes physiques
# ---------------------------------------------------------------------------
EARTH_RADIUS_KM = 6371.0
SPEED_OF_LIGHT = 299792458  # m/s

# Facteurs TA par techno (distance par TA en metres)
TA_FACTORS = {
    "GSM": {
        "ta_step_m": 550.0,
        "ta_max": 63,
        "description": "GSM classic: 1 TA = 3.69 us / ~550m",
    },
    "UMTS": {
        "ta_step_m": 78.0,
        "ta_max": 8191,
        "description": "UMTS: 1 TA = 0.99 us / ~78m",
    },
    "LTE": {
        "ta_step_m": 78.0,
        "ta_max": 1282,
        "description": "LTE FDD: 1 TA = 0.48 us / ~78m",
    },
    "LTE_TDD": {
        "ta_step_m": 39.0,
        "ta_max": 1282,
        "description": "LTE TDD: 1 TA = 0.24 us / ~39m",
    },
    "NR": {
        "ta_step_m": 9.0,
        "ta_max": 16383,
        "description": "5G NR: 1 TA ~ 9m (subcarrier spacing 30kHz)",
    },
    "NR_TDD": {
        "ta_step_m": 9.0,
        "ta_max": 16383,
        "description": "5G NR TDD: same TA step, different frame config",
    },
}

DEFAULT_TA_FACTOR = 78.0  # LTE-like default
DEFAULT_TA_MAX = 1282


# ---------------------------------------------------------------------------
# 1. COST-231 Hata complet
# ---------------------------------------------------------------------------
def cost231_hata_path_loss(
    frequency_mhz: float,
    tx_height_m: float,
    rx_height_m: float,
    distance_km: float,
    environment: str = "urban",
    city_size: str = "medium",
) -> float:
    """
    Modele COST-231 Hata (extension urbaine de Okumura-Hata).

    L = 46.3 + 33.9*log10(f) - 13.82*log10(hb) - a(hm)
        + (44.9 - 6.55*log10(hb))*log10(d) + C

    Args:
        frequency_mhz: frequence en MHz (1500-2000 MHz typique)
        tx_height_m: hauteur antenne BTS en metres
        rx_height_m: hauteur mobile en metres (typiquement 1.5m)
        distance_km: distance link en kilometres
        environment: urban_dense, urban, suburban, rural
        city_size: small, medium, large (influence C)

    Returns:
        path_loss en dB
    """
    if distance_km <= 0:
        distance_km = 0.001

    f = frequency_mhz
    hb = max(1.0, tx_height_m)
    hm = max(0.5, rx_height_m)
    d = max(0.001, distance_km)

    # Terme de base
    base = 46.3 + 33.9 * math.log10(f)

    # Correction hauteur BTS
    hb_term = -13.82 * math.log10(hb)

    # Correction hauteur mobile a(hm)
    # COST-231: a(hm) = (1.1*log10(f) - 0.7)*hm - (1.56*log10(f) - 0.8) pour f <= 1500
    # pour f > 1500, la correction est plus faible
    if f <= 1500:
        a_hm = (1.1 * math.log10(f) - 0.7) * hm - (1.56 * math.log10(f) - 0.8)
    else:
        # Approximation pour 1800/2600 MHz
        a_hm = 3.2 * (math.log10(11.75 * hm)) ** 2 - 4.97
        # Ajustement pour frequences plus elevees
        a_hm *= (1 + 0.001 * (f - 1500))

    # Terme de distance
    dist_coeff = 44.9 - 6.55 * math.log10(hb)
    dist_term = dist_coeff * math.log10(d)

    # Offset environnement + taille ville
    env_offset = _cost231_env_offset(environment, city_size)

    path_loss = base + hb_term - a_hm + dist_term + env_offset

    return path_loss


def _cost231_env_offset(environment: str, city_size: str) -> float:
    """Offset environnement pour COST-231 Hata."""
    offsets = {
        "urban_dense": {
            "small": 3.0,
            "medium": 4.0,
            "large": 5.0,
        },
        "urban": {
            "small": 0.0,
            "medium": 2.0,
            "large": 3.0,
        },
        "suburban": {
            "small": -2.0,
            "medium": -3.0,
            "large": -4.0,
        },
        "rural": {
            "small": -6.0,
            "medium": -8.0,
            "large": -10.0,
        },
    }
    env_map = offsets.get(environment, offsets["urban"])
    return env_map.get(city_size, env_map["medium"])


def cost231_hata_distance(
    rssi_dbm: int,
    frequency_mhz: float,
    tx_power_dbm: int = 43,
    tx_height_m: float = 30,
    rx_height_m: float = 1.5,
    environment: str = "urban",
    city_size: str = "medium",
) -> float:
    """
    Convertit RSSI en distance avec le modele COST-231 Hata.

    Args:
        rssi_dbm: puissance recue en dBm
        frequency_mhz: frequence porteuse en MHz
        tx_power_dbm: puissance emise BTS en dBm
        tx_height_m: hauteur antenne BTS en metres
        rx_height_m: hauteur mobile en metres
        environment: urban_dense, urban, suburban, rural
        city_size: small, medium, large

    Returns:
        distance estimee en kilometres
    """
    path_loss = tx_power_dbm - rssi_dbm
    f = frequency_mhz
    hb = max(1.0, tx_height_m)
    hm = max(0.5, rx_height_m)

    base = 46.3 + 33.9 * math.log10(f)
    hb_term = -13.82 * math.log10(hb)

    if f <= 1500:
        a_hm = (1.1 * math.log10(f) - 0.7) * hm - (1.56 * math.log10(f) - 0.8)
    else:
        a_hm = 3.2 * (math.log10(11.75 * hm)) ** 2 - 4.97
        a_hm *= (1 + 0.001 * (f - 1500))

    env_offset = _cost231_env_offset(environment, city_size)

    const_part = base + hb_term - a_hm + env_offset
    dist_coeff = 44.9 - 6.55 * math.log10(hb)

    if dist_coeff <= 0:
        return 1.0

    log_d = (path_loss - const_part) / dist_coeff
    distance_km = 10 ** log_d

    if distance_km <= 0:
        return 0.1
    if distance_km > 35:
        return 35.0

    return distance_km


def cost231_distance_rsrp(
    rsrp_dbm: int,
    frequency_mhz: float,
    tx_power_dbm: int = 46,
    tx_height_m: float = 30,
    rx_height_m: float = 1.5,
    environment: str = "urban",
    city_size: str = "medium",
    bandwidth_mhz: float = 20,
) -> float:
    """
    Distance estimee a partir de RSRP avec modele COST-231 Hata.

    Le RSRP est une mesure normalisee par bande passante. On approxime
    la puissance totalisee puis on applique COST-231.

    Approx: P_total ~ RSRP + 10*log10(N_RB)
    N_RB approx 100 pour 20 MHz LTE.
    """
    if rsrp_dbm is None:
        return 0.0

    # Approximation du nombre de RB pour LTE/NR
    rb_count = max(6, int(bandwidth_mhz * 5))
    bw_factor = 10 * math.log10(rb_count)

    approx_rssi_dbm = rsrp_dbm + bw_factor
    return cost231_hata_distance(
        rssi_dbm=approx_rssi_dbm,
        frequency_mhz=frequency_mhz,
        tx_power_dbm=tx_power_dbm,
        tx_height_m=tx_height_m,
        rx_height_m=rx_height_m,
        environment=environment,
        city_size=city_size,
    )


# ---------------------------------------------------------------------------
# 2. TDOA / TA trilateration basee sur Timing Advance
# ---------------------------------------------------------------------------
def ta_to_distance_improved(
    ta: Optional[int],
    radio: str = "GSM",
    ta_max: Optional[int] = None,
    ta_step_m: Optional[float] = None,
    apply_clipping: bool = True,
) -> float:
    """
    Conversion TA -> distance amelior multi-techno.

    - GSM: TA 0-63, 1 TA = 550m, couverture ~35km
    - UMTS: TA 0-8191, 1 TA = 78m, couverture ~640km
    - LTE FDD: TA 0-1282, 1 TA = 78m
    - LTE TDD: TA 0-1282, 1 TA = 39m
    - NR: TA 0-16383, 1 TA ~ 9m
    """
    if ta is None:
        return 0.0

    tech = (radio or "GSM").upper()
    cfg = TA_FACTORS.get(tech, TA_FACTORS["LTE"])

    step = ta_step_m if ta_step_m is not None else cfg["ta_step_m"]
    max_ta = ta_max if ta_max is not None else cfg["ta_max"]

    # Saturation securisee
    safe_ta = max(0, min(int(ta), max_ta))
    distance_m = safe_ta * step

    if apply_clipping:
        max_range_m = max_ta * step
        distance_m = min(distance_m, max_range_m)

    return distance_m / 1000.0  # km


def tdoa_trilateration(
    towers: List[Dict],
    reference_index: int = 0,
    weighting: str = "ta",
) -> Dict:
    """
    Trilateration TDOA basee sur les differences de Timing Advance.

    Principe:
    - Le Timing Advance donne une mesure de distance relative a l'antenne BTS.
    - La difference de TA entre deux antennes donne une hyperboloide de position.
    - Avec >=3 antennes, on resout par moindres carres.

    Args:
        towers: liste de dicts avec lat, lon, ta, radio, timing_advance
        reference_index: antenne de reference pour les differences
        weighting: 'ta', 'rssi', 'uniform'

    Returns:
        Dict avec latitude, longitude, accuracy_km, method, hyperbolae
    """
    if not towers:
        raise ValueError("Aucune antenne fournie pour TDOA")

    ref = towers[reference_index]
    ref_lat = ref.get("lat", 0.0)
    ref_lon = ref.get("lon", 0.0)
    ref_ta = ref.get("ta") or ref.get("timing_advance")
    ref_radio = ref.get("radio", "GSM")

    ref_dist_km = ta_to_distance_improved(ref_ta, ref_radio)

    if ref_dist_km <= 0 and len(towers) < 3:
        # Pas assez d'info pour TDOA pur -> centroid pondere
        lat = sum(t.get("lat", 0.0) for t in towers) / len(towers)
        lon = sum(t.get("lon", 0.0) for t in towers) / len(towers)
        return {
            "latitude": round(lat, 6),
            "longitude": round(lon, 6),
            "accuracy_km": 10.0,
            "accuracy_meters": 10000,
            "method": "tdoa_fallback_centroid",
            "confidence": "low",
            "tdoa_used": False,
        }

    # Construire les observations de distance relative
    obs = []
    for i, t in enumerate(towers):
        if i == reference_index:
            continue
        ta = t.get("ta") or t.get("timing_advance")
        radio = t.get("radio", "GSM")
        dist_i = ta_to_distance_improved(ta, radio)

        lat_i = t.get("lat", 0.0)
        lon_i = t.get("lon", 0.0)
        signal = t.get("signal_dbm")

        # Poids
        if weighting == "ta":
            # Plus le TA est eleve, plus la mesure est sensible
            w = max(0.1, min(1.0, (ta or 0) / 63.0))
        elif weighting == "rssi" and signal is not None:
            w = max(0.1, min(1.0, (signal + 110) / 60))
        else:
            w = 1.0

        obs.append(
            {
                "index": i,
                "lat": lat_i,
                "lon": lon_i,
                "dist_km": dist_i,
                "ref_dist_km": ref_dist_km,
                "weight": w,
                "ta": ta,
                "radio": radio,
            }
        )

    # Resolution non-lineaire: trouver (x,y) qui minimise les ecarts
    # sur les differences de distance estimees
    def tdoa_residuals(pos):
        lat, lon = pos
        residuals = []
        for o in obs:
            d_calc = geodesic_km((lat, lon), (o["lat"], o["lon"]))
            # Difference de distance observee
            d_ref_calc = geodesic_km((lat, lon), (ref_lat, ref_lon))
            delta_d_obs = ref_dist_km - o["dist_km"]
            delta_d_calc = d_ref_calc - d_calc
            residuals.append(o["weight"] * (delta_d_calc - delta_d_obs))
        return residuals

    # Point initial: centroid
    lats = [ref_lat] + [o["lat"] for o in obs]
    lons = [ref_lon] + [o["lon"] for o in obs]
    x0 = np.array([sum(lats) / len(lats), sum(lons) / len(lons)])

    try:
        result = least_squares(tdoa_residuals, x0, method="lm", max_nfev=200)
        lat, lon = result.x
        final_res = np.array(tdoa_residuals([lat, lon]))
        error_km = float(np.sqrt(np.mean(final_res ** 2))) if len(final_res) else 999.0
        error_km = min(error_km, 35.0)
    except Exception as exc:
        logger.warning(f"TDOA trilateration echouee: {exc}")
        lat = sum(lats) / len(lats)
        lon = sum(lons) / len(lons)
        error_km = 15.0

    confidence = "excellent" if error_km < 0.5 else ("good" if error_km < 2 else ("moderate" if error_km < 5 else "low"))

    return {
        "latitude": round(float(lat), 6),
        "longitude": round(float(lon), 6),
        "accuracy_km": round(error_km, 3),
        "accuracy_meters": round(error_km * 1000, 0),
        "method": "tdoa_ta_trilateration",
        "confidence": confidence,
        "tdoa_used": True,
        "reference_antenna": reference_index,
        "hyperbolae_count": len(obs),
    }


def geodesic_km(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    """Distance geodesique simple en km sans dependance externe."""
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c


# ---------------------------------------------------------------------------
# 3. Calibration RSSI par operateur/zone avec regression lineaire
# ---------------------------------------------------------------------------
@dataclass
class RSSICalibrationPoint:
    """Mesure de calibration: (rssi_dbm, distance_km, frequency_mhz, environment)."""
    rssi_dbm: int
    distance_km: float
    frequency_mhz: float
    environment: str
    tx_power_dbm: int = 43
    tx_height_m: float = 30
    rx_height_m: float = 1.5
    operator: str = "default"
    zone: str = "default"
    timestamp: Optional[float] = None


@dataclass
class RSSICalibrationModel:
    """Modele de calibration RSSI pour un operateur/zone donne."""
    operator: str
    zone: str
    environment: str
    frequency_mhz: float
    slope: float = 0.0
    intercept: float = 0.0
    tx_power_dbm: float = 43.0
    tx_height_m: float = 30.0
    rx_height_m: float = 1.5
    r2: float = 0.0
    sample_count: int = 0
    last_calibration: Optional[float] = None
    _raw_points: List[RSSICalibrationPoint] = field(default_factory=list, repr=False, compare=False)

    def to_dict(self) -> Dict:
        return {
            "operator": self.operator,
            "zone": self.zone,
            "environment": self.environment,
            "frequency_mhz": round(self.frequency_mhz, 2),
            "slope": round(self.slope, 4),
            "intercept": round(self.intercept, 4),
            "tx_power_dbm": round(self.tx_power_dbm, 1),
            "tx_height_m": round(self.tx_height_m, 1),
            "rx_height_m": round(self.rx_height_m, 1),
            "r2": round(self.r2, 4),
            "sample_count": self.sample_count,
            "last_calibration": self.last_calibration,
        }


class RSSICalibrator:
    """
    Calibration automatique RSSI par operateur et zone.

    Utilise une regression lineaire en espace logarithmique:
      log10(distance_km) = slope * rssi_dbm + intercept

    Ce modele est approxime mais fonctionne bien avec des donnees connues.
    """

    def __init__(self, min_samples: int = 5):
        self.min_samples = min_samples
        self.models: Dict[Tuple[str, str], RSSICalibrationModel] = {}
        self.global_model: Optional[RSSICalibrationModel] = None

    def add_calibration_point(self, point) -> None:
        """Ajoute un point de calibration. Accepte RSSICalibrationPoint ou dict."""
        if hasattr(point, 'operator'):
            operator = point.operator
            zone = point.zone
            environment = point.environment
            freq = point.frequency_mhz
            tx_power = point.tx_power_dbm
            tx_height = point.tx_height_m
            rx_height = point.rx_height_m
            raw = point
        else:
            operator = point.get('operator', 'default')
            zone = point.get('zone', 'default')
            environment = point.get('environment', 'urban')
            freq = float(point.get('frequency_mhz', 900))
            tx_power = float(point.get('tx_power_dbm', 43))
            tx_height = float(point.get('tx_height_m', 30))
            rx_height = float(point.get('rx_height_m', 1.5))
            raw = RSSICalibrationPoint(
                rssi_dbm=int(point.get('rssi_dbm', -100)),
                distance_km=float(point.get('distance_km', 1.0)),
                frequency_mhz=freq,
                environment=environment,
                tx_power_dbm=int(tx_power),
                tx_height_m=float(tx_height),
                rx_height_m=float(rx_height),
                operator=operator,
                zone=zone,
            )

        key = (operator, zone)
        if key not in self.models:
            self.models[key] = RSSICalibrationModel(
                operator=operator,
                zone=zone,
                environment=environment,
                frequency_mhz=freq,
                tx_power_dbm=tx_power,
                tx_height_m=tx_height,
                rx_height_m=rx_height,
            )
        model = self.models[key]
        model._raw_points.append(raw)
        model.sample_count += 1
        model.tx_power_dbm = (model.tx_power_dbm * (model.sample_count - 1) + tx_power) / model.sample_count
        model.tx_height_m = (model.tx_height_m * (model.sample_count - 1) + tx_height) / model.sample_count
        model.rx_height_m = (model.rx_height_m * (model.sample_count - 1) + rx_height) / model.sample_count
        model.frequency_mhz = (model.frequency_mhz * (model.sample_count - 1) + freq) / model.sample_count

    def load_model(self, model: RSSICalibrationModel) -> None:
        """Injecte directement un modele pre-entraine."""
        key = (model.operator, model.zone)
        self.models[key] = model

    def calibrate(self, key: Tuple[str, str]) -> Optional[RSSICalibrationModel]:
        """
        Lance la regression lineaire sur les points enregistres.

        Utilise train_rssi_calibration sur les points bruts accumules.
        """
        model = self.models.get(key)
        if not model or model.sample_count < self.min_samples:
            return model
        trained = train_rssi_calibration(model._raw_points, model.operator, model.zone, model.environment)
        if trained is not None:
            trained._raw_points = model._raw_points
            self.models[key] = trained
            return trained
        return model

    def predict_calibrated_distance(
        self,
        rssi_dbm: int,
        operator: str = "default",
        zone: str = "default",
        environment: str = "urban",
        frequency_mhz: float = 900,
        tx_power_dbm: float = 43,
        tx_height_m: float = 30,
        rx_height_m: float = 1.5,
    ) -> float:
        """
        Predis la distance avec calibration si disponible, sinon COST-231.
        """
        key = (operator, zone)
        model = self.models.get(key)

        if model and model.sample_count >= self.min_samples:
            # Modele calibre: log(d) = slope * rssi + intercept
            log_d = model.slope * rssi_dbm + model.intercept
            distance_km = 10 ** log_d
            return max(0.1, min(35.0, distance_km))

        # Fallback COST-231
        return cost231_hata_distance(
            rssi_dbm=rssi_dbm,
            frequency_mhz=frequency_mhz,
            tx_power_dbm=int(tx_power_dbm),
            tx_height_m=tx_height_m,
            rx_height_m=rx_height_m,
            environment=environment,
            city_size="medium",
        )


def train_rssi_calibration(
    calibration_points: List[RSSICalibrationPoint],
    operator: str = "default",
    zone: str = "default",
    environment: str = "urban",
) -> Optional[RSSICalibrationModel]:
    """
    Entraine un modele de calibration RSSI par regression lineaire
    sur des donnees connues (RSSI mesure -> distance connue).

    Hypothese: log10(distance_km) est lineaire en rssi_dbm.
    """
    if len(calibration_points) < 2:
        return None

    rssi = np.array([p.rssi_dbm for p in calibration_points], dtype=float)
    dist = np.array([p.distance_km for p in calibration_points], dtype=float)
    dist = np.clip(dist, 0.001, None)
    log_d = np.log10(dist)

    # Regression lineaire: log_d = slope * rssi + intercept
    A = np.vstack([rssi, np.ones_like(rssi)]).T
    try:
        slope, intercept = np.linalg.lstsq(A, log_d, rcond=None)[0]
    except np.linalg.LinAlgError:
        return None

    # Coefficient de determination
    y_mean = np.mean(log_d)
    ss_tot = np.sum((log_d - y_mean) ** 2)
    y_pred = slope * rssi + intercept
    ss_res = np.sum((log_d - y_pred) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # Moyenne des parametres de la BTS
    avg_tx_power = np.mean([p.tx_power_dbm for p in calibration_points])
    avg_tx_height = np.mean([p.tx_height_m for p in calibration_points])
    avg_rx_height = np.mean([p.rx_height_m for p in calibration_points])
    avg_freq = np.mean([p.frequency_mhz for p in calibration_points])

    model = RSSICalibrationModel(
        operator=operator,
        zone=zone,
        environment=environment,
        frequency_mhz=avg_freq,
        slope=float(slope),
        intercept=float(intercept),
        tx_power_dbm=float(avg_tx_power),
        tx_height_m=float(avg_tx_height),
        rx_height_m=float(avg_rx_height),
        r2=float(r2),
        sample_count=len(calibration_points),
    )
    return model


# ---------------------------------------------------------------------------
# 4. Integration avec CellTower / triangulation existante
# ---------------------------------------------------------------------------
try:
    from core.triangulation import CellTower

    def cell_tower_distance_cost231(self: "CellTower") -> float:
        """Distance via COST-231 Hata selon le type de mesure dispo."""
        if self.signal_dbm is not None:
            freq = _radio_frequency_mhz(self.radio)
            return cost231_hata_distance(
                rssi_dbm=self.signal_dbm,
                frequency_mhz=freq,
                tx_power_dbm=43,
                tx_height_m=30,
                rx_height_m=1.5,
                environment="urban",
                city_size="medium",
            )
        if self.rsrp is not None:
            freq = _radio_frequency_mhz(self.radio)
            return cost231_distance_rsrp(
                rsrp_dbm=self.rsrp,
                frequency_mhz=freq,
                tx_power_dbm=46,
                tx_height_m=30,
                rx_height_m=1.5,
                environment="urban",
                city_size="medium",
            )
        if self.timing_advance is not None:
            return ta_to_distance_improved(self.timing_advance, self.radio)
        return 0.0

    def cell_tower_distance_ta_improved(self: "CellTower") -> float:
        """Distance TA multi-techno."""
        if self.timing_advance is None:
            return 0.0
        return ta_to_distance_improved(self.timing_advance, self.radio)

    # Monkey-patch non intrusif: ajout de methodes sur CellTower
    CellTower.distance_km_cost231 = property(cell_tower_distance_cost231)
    CellTower.distance_km_ta_improved = property(cell_tower_distance_ta_improved)

except Exception as exc:  # pragma: no cover
    logger.debug(f"CellTower non disponible pour monkey-patch: {exc}")


def _radio_frequency_mhz(radio: str) -> float:
    """Frequence representative par techno."""
    radio_up = (radio or "GSM").upper()
    mapping = {
        "GSM": 900.0,
        "UMTS": 2100.0,
        "LTE": 1800.0,
        "LTE_TDD": 2600.0,
        "NR": 3500.0,
        "NR_TDD": 3500.0,
    }
    return mapping.get(radio_up, 900.0)


def best_distance_advanced(tower: Dict, environment: str = "urban") -> float:
    """
    Wrapper integrable avec triangulation.estimate_location.

    Choisit la meilleure distance disponible:
      1. COST-231 Hata via RSSI
      2. COST-231 via RSRP
      3. TA ameliore
      4. RTT
      5. radius_km
    """
    signal_dbm = tower.get("signal_dbm")
    rsrp = tower.get("rsrp")
    ta = tower.get("ta") or tower.get("timing_advance")
    rtt = tower.get("rtt")
    radio = tower.get("radio", "GSM")
    freq = _radio_frequency_mhz(radio)

    if signal_dbm is not None:
        d = cost231_hata_distance(
            rssi_dbm=int(signal_dbm),
            frequency_mhz=float(freq),
            tx_power_dbm=43,
            tx_height_m=30,
            rx_height_m=1.5,
            environment=environment,
            city_size="medium",
        )
        if d and d > 0:
            return d

    if rsrp is not None:
        d = cost231_distance_rsrp(
            rsrp_dbm=int(rsrp),
            frequency_mhz=float(freq),
            tx_power_dbm=46,
            tx_height_m=30,
            rx_height_m=1.5,
            environment=environment,
            city_size="medium",
        )
        if d and d > 0:
            return d

    if ta is not None:
        d = ta_to_distance_improved(ta, radio)
        if d and d > 0:
            return d

    if rtt is not None:
        d = (rtt / 1000.0) * SPEED_OF_LIGHT / (2 * 1000.0)
        if d and d > 0:
            return d

    return tower.get("radius_km") or 0.0


def estimate_location_advanced(
    towers: List[dict],
    method: str = "auto",
    environment: str = "urban",
    operator: str = "default",
    zone: str = "default",
    previous_location: Optional[Tuple[float, float]] = None,
    previous_covariance: Optional[Tuple] = None,
    calibrator: Optional[RSSICalibrator] = None,
) -> Dict:
    """
    Point d'entree avance, compatible avec estimate_location existant.

    Ajoute:
      - COST-231 Hata au lieu de Okumura-Hata
      - TDOA comme option de methode
      - Calibration RSSI par operateur/zone
      - TA amelioré multi-techno
    """
    n = len(towers)
    if n == 0:
        return {
            "latitude": 44.7866,
            "longitude": 20.4489,
            "accuracy_km": 50.0,
            "accuracy_meters": 50000,
            "method": "fallback_no_towers",
            "confidence": "poor",
            "towers_used": 0,
        }

    selected_method = method
    if method == "auto":
        if n >= 5:
            selected_method = "advanced_least_squares"
        elif n >= 3:
            selected_method = "advanced_linear"
        elif n >= 2:
            selected_method = "tdoa"
        else:
            selected_method = "advanced_weighted_centroid"

    # Construire des objets compatibles
    try:
        from core.triangulation import CellTower

        cell_towers = []
        for t in towers:
            ct = CellTower(
                mcc=t.get("mcc", 220),
                mnc=t.get("mnc", 0),
                lac=t.get("lac", 0),
                cell_id=t.get("cell_id", 0),
                lat=t.get("lat", 0.0),
                lon=t.get("lon", 0.0),
                radius_km=t.get("radius_km") or best_distance_advanced(t, environment),
                samples=t.get("samples", 0),
                radio=t.get("radio", "GSM"),
                signal_dbm=t.get("signal_dbm"),
                timing_advance=t.get("ta") or t.get("timing_advance"),
                rtt=t.get("rtt"),
                rsrp=t.get("rsrp"),
                rsrq=t.get("rsrq"),
            )
            cell_towers.append(ct)
    except Exception:
        cell_towers = []

    # Choix de distance
    def get_distance_km(t: dict) -> float:
        if calibrator:
            return calibrator.predict_calibrated_distance(
                rssi_dbm=t.get("signal_dbm") or -100,
                operator=operator,
                zone=zone,
                environment=environment,
                frequency_mhz=_radio_frequency_mhz(t.get("radio", "GSM")),
            )
        return best_distance_advanced(t, environment)

    lat, lon, error_km = 44.7866, 20.4489, 50.0
    method_used = "fallback"

    if selected_method == "tdoa" and n >= 2:
        try:
            res = tdoa_trilateration(towers, reference_index=0, weighting="ta")
            lat = res["latitude"]
            lon = res["longitude"]
            error_km = res["accuracy_km"]
            method_used = res["method"]
        except Exception as exc:
            logger.warning(f"TDOA echoue: {exc}, fallback")
            lat, lon, error_km = _advanced_weighted_centroid(towers, get_distance_km)
            method_used = "advanced_weighted_centroid_fallback"
    elif selected_method in ("advanced_linear", "advanced_least_squares") and n >= 3:
        try:
            if selected_method == "advanced_least_squares":
                lat, lon, error_km = _advanced_least_squares(cell_towers)
            else:
                lat, lon, error_km = _advanced_linear(cell_towers)
            method_used = selected_method
        except Exception as exc:
            logger.warning(f"Trilateration {selected_method} echouee: {exc}")
            lat, lon, error_km = _advanced_weighted_centroid(towers, get_distance_km)
            method_used = "advanced_weighted_centroid_fallback"
    else:
        lat, lon, error_km = _advanced_weighted_centroid(towers, get_distance_km)
        method_used = "advanced_weighted_centroid"

    if previous_location:
        lat, lon = _advanced_kalman_step(previous_location, (lat, lon), error_km)
        method_used += "_kalman"

    confidence = "excellent" if error_km < 0.5 else ("good" if error_km < 2 else ("moderate" if error_km < 5 else "low"))

    return {
        "latitude": round(lat, 6),
        "longitude": round(lon, 6),
        "accuracy_km": round(error_km, 3),
        "accuracy_meters": round(error_km * 1000, 0),
        "method": method_used,
        "confidence": confidence,
        "towers_used": n,
        "environment": environment,
        "operator": operator,
        "zone": zone,
    }


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------
def _advanced_weighted_centroid(towers: List[dict], distance_fn) -> Tuple[float, float, float]:
    if not towers:
        return 44.7866, 20.4489, 50.0

    weights = []
    lats = []
    lons = []
    for t in towers:
        d = distance_fn(t)
        w = max(0.05, min(1.0, 1.0 / max(0.1, d)))
        weights.append(w)
        lats.append(t.get("lat", 0.0))
        lons.append(t.get("lon", 0.0))

    total = sum(weights)
    if total == 0:
        return lats[0], lons[0], 10.0

    lat = sum(l * w for l, w in zip(lats, weights)) / total
    lon = sum(l * w for l, w in zip(lons, weights)) / total

    lat_std = math.sqrt(sum(w * (l - lat) ** 2 for l, w in zip(lats, weights)) / total)
    lon_std = math.sqrt(sum(w * (l - lon) ** 2 for l, w in zip(lons, weights)) / total)
    error_km = math.sqrt((lat_std * 111.32) ** 2 + (lon_std * 111.32 * math.cos(math.radians(lat))) ** 2)
    error_km = min(error_km * 10, 20.0)

    return lat, lon, error_km


def _advanced_linear(cell_towers: List) -> Tuple[float, float, float]:
    if len(cell_towers) < 3:
        raise ValueError("Minimum 3 antennes requises")

    ref = cell_towers[0]
    d_ref = ref.distance_km_cost231 or ref.distance_km_ta_improved or ref.radius_km
    lat0 = ref.lat
    lon0 = ref.lon

    A = []
    b = []
    for tower in cell_towers[1:]:
        d_i = tower.distance_km_cost231 or tower.distance_km_ta_improved or tower.radius_km
        if d_ref <= 0 or d_i <= 0:
            continue

        lat_ref_rad = math.radians(lat0)
        deg_lat_to_km = 111.32
        deg_lon_to_km = 111.32 * math.cos(lat_ref_rad)

        x_ref = ref.lat * deg_lat_to_km
        y_ref = ref.lon * deg_lon_to_km
        x_i = tower.lat * deg_lat_to_km
        y_i = tower.lon * deg_lon_to_km

        A.append([2 * (x_i - x_ref), 2 * (y_i - y_ref)])
        b.append((d_ref ** 2 - d_i ** 2) - (x_ref ** 2 + y_ref ** 2) + (x_i ** 2 + y_i ** 2))

    if len(A) < 2:
        avg_lat = sum(t.lat for t in cell_towers) / len(cell_towers)
        avg_lon = sum(t.lon for t in cell_towers) / len(cell_towers)
        return avg_lat, avg_lon, 5.0

    A = np.array(A)
    b = np.array(b)
    x, residuals, rank, s = np.linalg.lstsq(A, b, rcond=None)
    deg_lat_to_km = 111.32
    deg_lon_to_km = 111.32 * math.cos(math.radians(lat0))
    lat = x[0] / deg_lat_to_km
    lon = x[1] / deg_lon_to_km
    error_km = float(np.sqrt(residuals[0])) if len(residuals) > 0 else 1.0
    return lat, lon, error_km


def _advanced_least_squares(cell_towers: List) -> Tuple[float, float, float]:
    if len(cell_towers) < 3:
        raise ValueError("Minimum 3 antennes requises")

    weights = [max(0.05, min(1.0, 1.0 / max(0.1, (t.distance_km_cost231 or t.distance_km_ta_improved or t.radius_km)))) for t in cell_towers]
    total_weight = sum(weights) or 1.0
    x0 = sum(t.lat * w for t, w in zip(cell_towers, weights)) / total_weight
    y0 = sum(t.lon * w for t, w in zip(cell_towers, weights)) / total_weight

    def residuals(pos):
        lat, lon = pos
        res = []
        for tower in cell_towers:
            d_calc = geodesic_km((lat, lon), (tower.lat, tower.lon))
            d_obs = tower.distance_km_cost231 or tower.distance_km_ta_improved or tower.radius_km
            if d_obs > 0:
                res.append((d_calc - d_obs) * (tower.signal_dbm or 1))
        return res

    result = least_squares(residuals, [x0, y0], method="lm", max_nfev=100)
    lat, lon = result.x
    final_res = residuals([lat, lon])
    error_km = float(np.sqrt(np.mean(np.array(final_res) ** 2))) if final_res else 999.0
    return lat, lon, min(error_km, 35.0)


def _advanced_kalman_step(prev_loc, obs_loc, obs_var, dt: float = 1.0):
    prev_lat, prev_lon = prev_loc
    obs_lat, obs_lon = obs_loc

    P = np.eye(4) * 100
    x = np.array([prev_lat, prev_lon, 0.0, 0.0])
    F = np.array([[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]])
    Q = np.eye(4) * 0.01
    x_pred = F @ x
    P_pred = F @ P @ F.T + Q

    H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]])
    R = np.eye(2) * max(obs_var ** 2, 0.1)
    y = np.array([obs_lat, obs_lon]) - H @ x_pred
    S = H @ P_pred @ H.T + R
    K = P_pred @ H.T @ np.linalg.inv(S)
    x_upd = x_pred + K @ y
    return float(x_upd[0]), float(x_upd[1])
