"""
SerbiaTracker - WebSocket Tracking Continu
Endpoint: /ws/track/{phone}

Features:
- Push position every 5s
- Kalman interpolation between sparse observations
- Disconnect / timeout management
- Cleanup on disconnect
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional, Set
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from pydantic import BaseModel, Field

from config.settings import settings
from core.sensor_fusion import SensorFusionEngine, SensorMeasurement, SensorType
from services.reverse_geocoding import reverse_geocoding
from services.tower_database import tower_db

logger = logging.getLogger(__name__)

router = APIRouter()


class TrackSessionRequest(BaseModel):
    phone: str = Field(..., pattern=r'^\+381[0-9]{7,9}$', examples=["+381641234567"])
    push_interval_s: int = Field(5, ge=2, le=60)
    include_reverse_geocode: bool = True
    mock_observations: bool = Field(False, description="Inject mock sensor payloads for demo")


class SensorPayload(BaseModel):
    sensor_type: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    altitude_m: Optional[float] = None
    hdop: Optional[float] = None
    vdop: Optional[float] = None
    accuracy_m: Optional[float] = None
    signal_dbm: Optional[int] = None
    rssi: Optional[int] = None
    distance_km: Optional[float] = None
    tower_lat: Optional[float] = None
    tower_lon: Optional[float] = None


class ConnectionManager:
    """
    Manage multiple live tracking sessions.
    For production you'd likely use Redis pub/sub or a dedicated WS gateway.
    """

    def __init__(self, engine: SensorFusionEngine):
        self.engine = engine
        # phone -> set of websockets
        self._subscribers: Dict[str, Set[WebSocket]] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, phone: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._subscribers.setdefault(phone, set()).add(websocket)
            if phone not in self._tasks or self._tasks[phone].done():
                task = asyncio.create_task(self._pusher(phone))
                self._tasks[phone] = task

    async def unsubscribe(self, phone: str, websocket: WebSocket) -> None:
        async with self._lock:
            subs = self._subscribers.get(phone)
            if subs:
                subs.discard(websocket)
                if not subs:
                    self._subscribers.pop(phone, None)
                    task = self._tasks.pop(phone, None)
                    if task and not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass

    async def handle_client_message(self, phone: str, message: dict) -> None:
        sensor_type_str = message.get("sensor_type")
        if not sensor_type_str:
            return
        try:
            sensor_type = SensorType(sensor_type_str)
        except ValueError:
            logger.debug("Unknown sensor_type: %s", sensor_type_str)
            return

        payload = SensorPayload(**message)
        m = SensorMeasurement(
            sensor_type=sensor_type,
            timestamp=time.time(),
            lat=payload.lat,
            lon=payload.lon,
            altitude_m=payload.altitude_m,
            hdop=payload.hdop,
            vdop=payload.vdop,
            accuracy_m=payload.accuracy_m,
            signal_dbm=payload.signal_dbm or payload.rssi,
            rssi=payload.rssi,
            distance_km=payload.distance_km,
            tower_lat=payload.tower_lat,
            tower_lon=payload.tower_lon,
            raw=message,
        )
        await self.engine.add_measurement(phone, m)

    async def _pusher(self, phone: str) -> None:
        try:
            while True:
                await asyncio.sleep(5)
                pos = self.engine.get_last(phone)
                if pos is None:
                    payload = {
                        "type": "no_fix",
                        "phone": phone,
                        "timestamp": time.time(),
                    }
                else:
                    payload = self._position_payload(phone, pos)

                subs = list(self._subscribers.get(phone, set()))
                dead: Set[WebSocket] = set()
                for ws in subs:
                    try:
                        await ws.send_json(payload)
                    except Exception:
                        dead.add(ws)

                if dead:
                    async with self._lock:
                        current = self._subscribers.get(phone, set())
                        current -= dead
                        if not current:
                            self._subscribers.pop(phone, None)
                            task = self._tasks.pop(phone, None)
                            if task and not task.done():
                                task.cancel()
                                try:
                                    await task
                                except asyncio.CancelledError:
                                    pass
                            break
                        self._subscribers[phone] = current
        except asyncio.CancelledError:
            raise

    async def _maybe_enrich_reverse(self, phone: str, pos) -> Dict:
        try:
            rg = await reverse_geocoding.reverse(pos.lat, pos.lon)
            return {
                "reverse_geocode": {
                    "formatted": rg.get("formatted"),
                    "area_type": rg.get("area_type"),
                    "city": rg.get("city"),
                    "road": rg.get("road"),
                    "country_code": rg.get("country_code"),
                }
            }
        except Exception:
            return {"reverse_geocode": None}

    async def _position_payload(self, phone: str, pos) -> Dict:
        base = {
            "type": "position",
            "phone": phone,
            "lat": pos.lat,
            "lon": pos.lon,
            "altitude_m": pos.altitude_m,
            "accuracy_m": pos.accuracy_m,
            "hdop": pos.hdop,
            "vdop": pos.vdop,
            "speed_mps": pos.speed_mps,
            "bearing_deg": pos.bearing_deg,
            "timestamp": pos.timestamp,
            "method": pos.method,
            "confidence": pos.confidence,
            "sensors_used": pos.sensors_used,
        }
        # Async enrich reverse geocode without blocking push loop when not needed.
        # If session requested enrich, do it.
        return base


manager = ConnectionManager(engine=SensorFusionEngine())


@router.websocket("/ws/track/{phone}")
async def ws_track(websocket: WebSocket, phone: str):
    await websocket.accept()
    await manager.subscribe(phone, websocket)
    logger.info("WS track connected: %s", phone)

    try:
        # initial config handshake from client
        init_msg = await websocket.receive_json()
        push_interval = int(init_msg.get("push_interval_s", 5))
        include_reverse = bool(init_msg.get("include_reverse_geocode", True))

        # If client wants an immediate synthetic fix, send last known position now.
        last = manager.engine.get_last(phone)
        if last:
            await websocket.send_json({
                "type": "position",
                "phone": phone,
                **last.__dict__,
            })

        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_json(), timeout=max(push_interval, 5) * 1.5)
                if isinstance(msg, dict):
                    await manager.handle_client_message(phone, msg)
            except asyncio.TimeoutError:
                # Client may be idle; keep session alive and continue push loop.
                continue
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WS track error for %s: %s", phone, e)
    finally:
        await manager.unsubscribe(phone, websocket)
        logger.info("WS track disconnected: %s", phone)
