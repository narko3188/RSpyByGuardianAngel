"""
SerbiaTracker - Configuration centrale
Application de geolocalisation par numero de telephone pour la Serbie (+381)
"""
from pydantic_settings import BaseSettings
from typing import Optional
import os

class Settings(BaseSettings):
    # Application
    APP_NAME: str = "SerbiaTracker"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    
    # Serveur
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 4
    
    # APIs Externes - Geolocalisation Cellulaire
    UNWIRED_LABS_TOKEN: Optional[str] = None        # LocationAPI - geoloc par antennes
    GOOGLE_GEOLOCATION_API_KEY: Optional[str] = None  # Google Geolocation API backup
    
    # APIs Externes - Lookup Numero
    NUMVERIFY_API_KEY: Optional[str] = None          # Numverify - carrier + localisation
    TWILIO_ACCOUNT_SID: Optional[str] = None         # Twilio Lookup v2
    TWILIO_AUTH_TOKEN: Optional[str] = None
    
    # Infobip (base en Serbie - acces direct aux operateurs serbes)
    INFOBIP_API_KEY: Optional[str] = None
    INFOBIP_BASE_URL: str = "https://api.infobip.com"
    
    # OpenCellID
    OPENCELLID_API_KEY: Optional[str] = None
    
    # Base de donnees
    DATABASE_URL: str = "sqlite+aiosqlite:///data/serbia_tracker.db"
    
    # Redis (cache + temps reel)
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # Cache
    CACHE_TTL_HLR: int = 300       # 5 min
    CACHE_TTL_LOCATION: int = 60   # 1 min
    CACHE_TTL_CARRIER: int = 3600  # 1 heure
    
    # Serbie
    SERBIA_MCC: str = "220"
    SERBIA_OPERATORS: dict = {
        "220-01": {"name": "Yettel (ex Telenor)", "brand": "Yettel", "color": "#00AEEF"},
        "220-03": {"name": "mt:s (Telekom Srbija)", "brand": "mt:s", "color": "#E6007E"},
        "220-05": {"name": "A1 Srbija (ex VIP)", "brand": "A1", "color": "#E2001A"},
        "220-07": {"name": "Orion Telekom", "brand": "Orion", "color": "#FF6600"},
        "220-11": {"name": "MUNDIO MOBILE", "brand": "Mundio", "color": "#0099CC"},
    }
    
    # Triangulation
    TRIANGULATION_MIN_TOWERS: int = 3
    TRIANGULATION_MAX_RADIUS_KM: float = 35.0
    SIGNAL_PROPAGATION_MODEL: str = "okumura_hata"  # okumura_hata | cost231 | free_space
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
