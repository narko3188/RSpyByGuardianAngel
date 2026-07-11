"""
SerbiaTracker - Numverify Integration Service
API gratuite 100 req/mois
"""
import httpx
import logging
from typing import Dict, Optional
from config.settings import settings

logger = logging.getLogger(__name__)


async def numverify_lookup(phone: str) -> Optional[Dict]:
    """
    Numverify: validation numero + operateur + localisation
    100 requetes/mois gratuites
    """
    if not settings.NUMVERIFY_API_KEY:
        return None
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "http://apilayer.net/api/validate",
                params={
                    "access_key": settings.NUMVERIFY_API_KEY,
                    "number": phone,
                    "country_code": "RS",
                    "format": 1,
                }
            )
            data = resp.json()
            
            if data.get("valid"):
                return {
                    "valid": True,
                    "number": data.get("international_format"),
                    "local_format": data.get("local_format"),
                    "country": data.get("country_name", "Serbia"),
                    "country_code": data.get("country_code", "RS"),
                    "carrier": data.get("carrier"),
                    "line_type": data.get("line_type"),  # mobile/landline/voip
                    "location": data.get("location"),  # Region geographique
                    "source": "numverify",
                }
    except Exception as e:
        logger.warning(f"Numverify error: {e}")
    
    return None
