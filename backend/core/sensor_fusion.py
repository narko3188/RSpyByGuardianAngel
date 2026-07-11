"""
SerbiaTracker - Sensor Fusion Core
Extended Kalman Filter multi-sensor fusion for:
- Cell Towers
- WiFi Access Points
- Barometer
- GPS

Features:
- Adaptive covariance based on sensor quality/HDOP/signal strength
- Track interpolation between sparse observations
- Altitude estimation from barometer + GPS fallback
"""
import math
import time
import asyncio
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
from geopy.distance import geodesic

from core.triangulation import CellTower, kalman_filter_step

logger = logging.getLogger(__name__)


class SensorType(str, Enum):
    GPS = "gps"
    CELL_TOWER = "cell_tower"
    WIFI = "wifi"
    BAROMETER = "barometer"


@dataclass
class SensorMeasurement:
    sensor_type: SensorType
    timestamp: float
    lat: Optional[float] = None
    lon: Optional[float] = None
    altitude_m: Optional[float] = None
    hdop: Optional[float] = None       # GPS horizontal dilution
    vdop: Optional[float] = None       # GPS vertical dilution
    accuracy_m: Optional[float] = None # meters
    signal_dbm: Optional[int] = None   # RSSI for cell/wifi
    rssi: Optional[int] = None         # alias
    distance_km: Optional[float] = None
    tower_lat: Optional[float] = None
    tower_lon: Optional[float] = None
    raw: Dict = field(default_factory=dict)


@dataclass
class FusedPosition:
    lat: float
    lon: float
    altitude_m: float
    accuracy_m: float
    hdop: Optional[float]
    vdop: Optional[float]
    speed_mps: Optional[float]
    bearing_deg: Optional[float]
    timestamp: float
    sensors_used: List[str]
    method: str
    confidence: str


class AdaptiveEKF:
    """
    Extended Kalman Filter for 2D/3D position fusion.
    State: [lat, lon, alt, v_lat, v_lon, v_alt]  (6D)
    
    We keep alt simple because we fuse barometer separately in practice.
    For this implementation we use a reduced state:
      [lat, lon, v_lat, v_lon]  and altitude is handled as an auxiliary output.
    """

    def __init__(self, process_noise_scale: float = 0.5):
        # State: [lat, lon, v_lat, v_lon]
        self.x = np.zeros(4)
        self.P = np.eye(4) * 100.0
        self.initialized = False
        self.process_noise_scale = process_noise_scale
        self.last_update_time: Optional[float] = None

    def _F(self, dt: float) -> np.ndarray:
        return np.array([
            [1, 0, dt, 0],
            [0, 1, 0, dt],
            [0, 0, 1, 0],
            [0, 0, 0, 1],
        ], dtype=float)

    def _Q(self, dt: float) -> np.ndarray:
        # Process noise grows with time since last update
        s = self.process_noise_scale
        q_pos = s * dt
        q_vel = s * dt ** 3 / 3.0
        q_cross = s * dt ** 2 / 2.0
        return np.array([
            [q_pos, 0, q_cross, 0],
            [0, q_pos, 0, q_cross],
            [q_cross, 0, q_vel, 0],
            [0, q_cross, 0, q_vel],
        ], dtype=float)

    def _R_gps(self, m: SensorMeasurement) -> np.ndarray:
        # Adaptive: worse HDOP/signal => larger uncertainty
        base = 3.0  # meters baseline GPS noise
        if m.hdop is not None:
            base *= max(0.5, m.hdop)
        if m.signal_dbm is not None:
            # weaker signal -> more noise
            base *= max(0.5, 1.0 + (-m.signal_dbm - 40) / 80.0)
        base = max(base, 1.0)
        var = base ** 2
        return np.diag([var, var])

    def _R_cell(self, m: SensorMeasurement) -> np.ndarray:
        # Use provided accuracy or derive from signal/radius
        if m.accuracy_m and m.accuracy_m > 0:
            base = m.accuracy_m
        elif m.signal_dbm is not None:
            base = 250.0 * max(0.3, 1.0 + (-m.signal_dbm - 60) / 60.0)
        else:
            base = 500.0
        base = max(base, 50.0)
        return np.diag([base ** 2, base ** 2])

    def _R_wifi(self, m: SensorMeasurement) -> np.ndarray:
        if m.accuracy_m and m.accuracy_m > 0:
            base = m.accuracy_m
        elif m.signal_dbm is not None or m.rssi is not None:
            rssi = m.signal_dbm or m.rssi or -80
            base = 20.0 * max(0.5, 1.0 + (-rssi - 40) / 60.0)
        else:
            base = 30.0
        base = max(base, 5.0)
        return np.diag([base ** 2, base ** 2])

    def _H(self, sensor_type: SensorType) -> Optional[np.ndarray]:
        # Measurement model: observe [lat, lon]
        if sensor_type in (SensorType.GPS, SensorType.CELL_TOWER, SensorType.WIFI):
            return np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=float)
        return None

    def _z(self, m: SensorMeasurement) -> Optional[np.ndarray]:
        if m.lat is None or m.lon is None:
            return None
        return np.array([m.lat, m.lon], dtype=float)

    def init(self, lat: float, lon: float, alt_m: float = 0.0) -> None:
        self.x = np.array([lat, lon, 0.0, 0.0], dtype=float)
        self.P = np.eye(4) * 10.0
        self.initialized = True
        self.last_update_time = time.time()

    def predict(self, dt: Optional[float] = None) -> None:
        if not self.initialized:
            return
        if dt is None:
            now = time.time()
            dt = max(0.01, min(now - (self.last_update_time or now), 5.0))
        self.last_update_time = time.time()
        F = self._F(dt)
        Q = self._Q(dt)
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q

    def update_gps(self, m: SensorMeasurement) -> None:
        self._update_common(m, self._R_gps(m))

    def update_cell(self, m: SensorMeasurement) -> None:
        self._update_common(m, self._R_cell(m))

    def update_wifi(self, m: SensorMeasurement) -> None:
        self._update_common(m, self._R_wifi(m))

    def _update_common(self, m: SensorMeasurement, R: np.ndarray) -> None:
        if not self.initialized:
            raise RuntimeError("EKF not initialized")
        z = self._z(m)
        if z is None:
            return
        H = self._H(m.sensor_type)
        if H is None:
            return
        y = z - (H @ self.x)
        S = H @ self.P @ H.T + R
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ y
        self.P = (np.eye(4) - K @ H) @ self.P
        self.last_update_time = time.time()

    def get_position(self) -> Tuple[float, float]:
        if not self.initialized:
            raise RuntimeError("EKF not initialized")
        return float(self.x[0]), float(self.x[1])

    def get_covariance_2x2(self) -> np.ndarray:
        return self.P[:2, :2]

    def get_accuracy_m(self) -> float:
        return float(np.sqrt(np.trace(self.get_covariance_2x2()) / 2.0)) * 111_320.0


class SensorFusionEngine:
    """
    High-level fusion engine that:
    - Maintains per-phone EKF state
    - Accepts heterogeneous measurements
    - Fuses GPS + cell + WiFi + barometer
    - Returns interpolated positions on demand
    """

    def __init__(self):
        # phone -> AdaptiveEKF
        self._filters: Dict[str, AdaptiveEKF] = {}
        # phone -> latest fused position
        self._last_position: Dict[str, FusedPosition] = {}
        # phone -> last altitude baro reference (optional offset tuning)
        self._baro_offsets: Dict[str, float] = {}
        # phone -> last measurement timestamp
        self._last_ts: Dict[str, float] = {}

    def _filter(self, phone: str) -> AdaptiveEKF:
        if phone not in self._filters:
            self._filters[phone] = AdaptiveEKF()
        return self._filters[phone]

    async def add_measurement(self, phone: str, m: SensorMeasurement) -> FusedPosition:
        ekf = self._filter(phone)
        if not ekf.initialized:
            # bootstrap from first meaningful measurement
            lat = m.lat or 44.7866
            lon = m.lon or 20.4489
            alt = m.altitude_m or 0.0
            ekf.init(lat, lon, alt)

        ekf.predict()
        sensors_used: List[SensorType] = []

        # Barometer is special: it affects altitude estimate, not direct EKF update.
        # We handle it by adjusting an estimated altitude bias or by trusting baro when GPS VDOP is poor.
        if m.sensor_type == SensorType.BAROMETER and m.altitude_m is not None:
            # Simple baro fusion: treat as additional observation on altitude.
            # Since EKF state is [lat, lon, v_lat, v_lon], we only use baro to enrich
            # returned FusedPosition. For full 3D EKF we'd extend state.
            sensors_used.append(SensorType.BAROMETER)

        if m.sensor_type == SensorType.GPS:
            ekf.update_gps(m)
            sensors_used.append(SensorType.GPS)
        elif m.sensor_type == SensorType.CELL_TOWER:
            ekf.update_cell(m)
            sensors_used.append(SensorType.CELL_TOWER)
        elif m.sensor_type == SensorType.WIFI:
            ekf.update_wifi(m)
            sensors_used.append(SensorType.WIFI)

        lat, lon = ekf.get_position()
        accuracy_m = ekf.get_accuracy_m()
        now = time.time()
        self._last_ts[phone] = now

        # Speed / bearing if we had previous position
        speed_mps = None
        bearing_deg = None
        prev = self._last_position.get(phone)
        if prev is not None and prev.timestamp < now:
            dt = now - prev.timestamp
            if dt > 0:
                d_m = geodesic((prev.lat, prev.lon), (lat, lon)).meters
                speed_mps = d_m / dt
                bearing_deg = self._bearing(prev.lat, prev.lon, lat, lon)

        # Altitude heuristic: favor GPS if good, otherwise keep last baro/gps best estimate
        altitude_m = m.altitude_m if m.altitude_m is not None else (prev.altitude_m if prev else 0.0)
        if m.sensor_type == SensorType.GPS and m.altitude_m is not None:
            altitude_m = m.altitude_m

        confidence = self._confidence_from_accuracy_m(accuracy_m)
        method = f"ekf_{m.sensor_type.value}"

        pos = FusedPosition(
            lat=round(lat, 6),
            lon=round(lon, 6),
            altitude_m=round(altitude_m, 1),
            accuracy_m=round(accuracy_m, 1),
            hdop=m.hdop,
            vdop=m.vdop,
            speed_mps=round(speed_mps, 1) if speed_mps is not None else None,
            bearing_deg=round(bearing_deg, 1) if bearing_deg is not None else None,
            timestamp=now,
            sensors_used=[s.value if isinstance(s, SensorType) else str(s) for s in sensors_used] or [m.sensor_type.value],
            method=method,
            confidence=confidence,
        )
        self._last_position[phone] = pos
        return pos

    async def interpolate(self, phone: str, target_ts: Optional[float] = None) -> Optional[FusedPosition]:
        """
        Interpolate position at a target timestamp using last known EKF prediction.
        If target_ts is None, returns latest position.
        """
        if phone not in self._filters:
            return self._last_position.get(phone)
        ekf = self._filter(phone)
        if not ekf.initialized:
            return self._last_position.get(phone)

        if target_ts is None:
            return self._last_position.get(phone)

        now = time.time()
        dt = target_ts - now
        ekf.predict(dt=max(dt, 0.0))
        lat, lon = ekf.get_position()
        accuracy_m = ekf.get_accuracy_m()
        base = self._last_position.get(phone)
        if base is None:
            return None
        sensors_used_interp = list(base.sensors_used) + ["interpolated"]
        return FusedPosition(
            lat=round(lat, 6),
            lon=round(lon, 6),
            altitude_m=base.altitude_m,
            accuracy_m=round(accuracy_m, 1),
            hdop=base.hdop,
            vdop=base.vdop,
            speed_mps=base.speed_mps,
            bearing_deg=base.bearing_deg,
            timestamp=target_ts,
            sensors_used=sensors_used_interp,
            method=base.method + "_interp",
            confidence=self._confidence_from_accuracy_m(accuracy_m),
        )

    def get_last(self, phone: str) -> Optional[FusedPosition]:
        return self._last_position.get(phone)

    def remove(self, phone: str) -> None:
        self._filters.pop(phone, None)
        self._last_position.pop(phone, None)
        self._baro_offsets.pop(phone, None)
        self._last_ts.pop(phone, None)

    @staticmethod
    def _confidence_from_accuracy_m(accuracy_m: float) -> str:
        if accuracy_m < 20:
            return "excellent"
        if accuracy_m < 100:
            return "good"
        if accuracy_m < 500:
            return "moderate"
        if accuracy_m < 2000:
            return "low"
        return "poor"

    @staticmethod
    def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dlambda = math.radians(lon2 - lon1)
        x = math.sin(dlambda) * math.cos(phi2)
        y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
        brng = (math.degrees(math.atan2(x, y)) + 360) % 360
        return brng


sensor_fusion = SensorFusionEngine()
