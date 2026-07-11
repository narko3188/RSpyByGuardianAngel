"""
SerbiaTracker - Service de lookup telephonique
Integration APIs externes pour detection operateur et HLR
"""
import httpx
import logging
from typing import Dict, Optional
from config.settings import settings

logger = logging.getLogger(__name__)


class PhoneLookupService:
    """Services de lookup telephonique (operateur, portabilite, HLR)"""
    
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=15.0)
    
    async def numverify_lookup(self, phone_number: str) -> Optional[Dict]:
        """Numverify: numero + operateur + pays"""
        if not settings.NUMVERIFY_API_KEY:
            return None
        
        try:
            resp = await self.client.get(
                "http://apilayer.net/api/validate",
                params={
                    "access_key": settings.NUMVERIFY_API_KEY,
                    "number": phone_number,
                    "country_code": "RS",
                    "format": 1
                }
            )
            data = resp.json()
            
            if data.get("valid"):
                return {
                    "valid": True,
                    "number": data.get("international_format"),
                    "country": data.get("country_name"),
                    "carrier": data.get("carrier"),
                    "line_type": data.get("line_type"),
                    "location": data.get("location"),
                    "source": "numverify"
                }
        except Exception as e:
            logger.error(f"Numverify error: {e}")
        return None
    
    async def twilio_lookup(self, phone_number: str) -> Optional[Dict]:
        """Twilio Lookup v2 avec Line Type Intelligence"""
        if not all([settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN]):
            return None
        
        try:
            from base64 import b64encode
            auth = b64encode(f"{settings.TWILIO_ACCOUNT_SID}:{settings.TWILIO_AUTH_TOKEN}".encode()).decode()
            
            resp = await self.client.get(
                f"https://lookups.twilio.com/v2/PhoneNumbers/{phone_number}",
                params={"Fields": "line_type_intelligence,caller_name,sim_swap"},
                headers={"Authorization": f"Basic {auth}"}
            )
            data = resp.json()
            
            if resp.status_code == 200:
                return {
                    "valid": True,
                    "number": data.get("phone_number"),
                    "country": data.get("country_code"),
                    "carrier": data.get("line_type_intelligence", {}).get("carrier_name"),
                    "line_type": data.get("line_type_intelligence", {}).get("type"),
                    "mobile_country_code": data.get("line_type_intelligence", {}).get("mobile_country_code"),
                    "mobile_network_code": data.get("line_type_intelligence", {}).get("mobile_network_code"),
                    "source": "twilio"
                }
        except Exception as e:
            logger.error(f"Twilio error: {e}")
        return None
    
    async def infobip_hlr_lookup(self, phone_number: str) -> Optional[Dict]:
        """
        Infobip HLR Lookup - le plus pertinent pour la Serbie
        Infobip est base en Serbie (Vodovodska) et a acces direct aux operateurs
        """
        if not settings.INFOBIP_API_KEY:
            return None
        
        try:
            resp = await self.client.post(
                f"{settings.INFOBIP_BASE_URL}/hlr/1/query",
                json={"destinations": [{"to": phone_number}]},
                headers={
                    "Authorization": f"App {settings.INFOBIP_API_KEY}",
                    "Content-Type": "application/json"
                }
            )
            data = resp.json()
            
            results = data.get("results", [])
            if results:
                result = results[0]
                return {
                    "valid": result.get("status", {}).get("id") == 200,
                    "number": phone_number,
                    "carrier": result.get("servingMsc"),
                    "network": result.get("network", {}).get("networkName"),
                    "country": result.get("network", {}).get("countryName"),
                    "mcc": result.get("network", {}).get("mcc"),
                    "mnc": result.get("network", {}).get("mnc"),
                    "ported": result.get("ported"),
                    "roaming": result.get("roaming"),
                    "original_network": result.get("originalNetwork", {}).get("networkName"),
                    "source": "infobip_hlr"
                }
        except Exception as e:
            logger.error(f"Infobip HLR error: {e}")
        return None
    
    async def infobip_number_lookup(self, phone_number: str) -> Optional[Dict]:
        """Infobip Number Lookup (validation + format)"""
        if not settings.INFOBIP_API_KEY:
            return None
        
        try:
            resp = await self.client.get(
                f"{settings.INFOBIP_BASE_URL}/number/1/query",
                params={"number": phone_number},
                headers={"Authorization": f"App {settings.INFOBIP_API_KEY}"}
            )
            data = resp.json()
            
            return {
                "valid": True,
                "number": data.get("msisdn"),
                "country": data.get("countryName"),
                "country_code": data.get("countryCode"),
                "carrier": data.get("networkName"),
                "mcc": data.get("mcc"),
                "mnc": data.get("mnc"),
                "source": "infobip_number"
            }
        except Exception as e:
            logger.error(f"Infobip Number error: {e}")
        return None
    
    async def full_lookup(self, phone_number: str) -> Dict:
        """Lookup complet avec fallback en cascade"""
        result = {
            "number": phone_number,
            "valid": False,
            "carrier": None,
            "mnc": None,
            "mcc": "220",
            "country": "Serbia",
            "source": "local"
        }
        
        # Essayer les APIs dans l'ordre de preference
        # 1. Infobip HLR (meilleur pour Serbie)
        hlr = await self.infobip_hlr_lookup(phone_number)
        if hlr and hlr.get("valid"):
            result.update(hlr)
            return result
        
        # 2. Twilio Lookup
        twilio = await self.twilio_lookup(phone_number)
        if twilio and twilio.get("valid"):
            result.update(twilio)
            return result
        
        # 3. Infobip Number (moins precis)
        num_lookup = await self.infobip_number_lookup(phone_number)
        if num_lookup and num_lookup.get("valid"):
            result.update(num_lookup)
            return result
        
        # 4. Numverify (dernier recours)
        numverify = await self.numverify_lookup(phone_number)
        if numverify and numverify.get("valid"):
            result.update(numverify)
            return result
        
        # 5. Detection locale par prefix
        return await self._local_prefix_detection(phone_number)
    
    async def _local_prefix_detection(self, phone_number: str) -> Dict:
        """Detection operateur par prefix local serbe"""
        # Nettoyer le numero
        clean = phone_number.replace("+", "").replace(" ", "").replace("-", "")
        if clean.startswith("381"):
            clean = "0" + clean[3:]
        
        # Prefixes operateurs serbes (source: RATEL)
        # mt:s (Telekom Srbija) - 064, 065, 066 (partiel)
        # Yettel (Telenor) - 062, 063, 069
        # A1 (VIP) - 060, 061, 068
        
        prefix_map = {
            "064": ("mt:s (Telekom Srbija)", "03"),
            "065": ("mt:s (Telekom Srbija)", "03"),
            "066": ("mt:s (Telekom Srbija)", "03"),
            "069": ("Yettel", "01"),
            "062": ("Yettel", "01"),
            "063": ("Yettel", "01"),
            "060": ("A1 Srbija", "05"),
            "061": ("A1 Srbija", "05"),
            "068": ("A1 Srbija", "05"),
            "067": ("Orion Telekom", "07"),
            "070": ("MUNDIO MOBILE", "11"),
        }
        
        for prefix, (carrier, mnc) in prefix_map.items():
            if clean.startswith(prefix):
                return {
                    "number": phone_number,
                    "valid": True,
                    "carrier": carrier,
                    "mnc": mnc,
                    "mcc": "220",
                    "country": "Serbia",
                    "source": "prefix_detection"
                }
        
        return {
            "number": phone_number,
            "valid": True,
            "carrier": "Serbian Operator (unknown)",
            "mnc": None,
            "mcc": "220",
            "country": "Serbia",
            "source": "prefix_detection",
            "note": "Prefix non identifie"
        }
    
    async def close(self):
        await self.client.aclose()


# Instance singleton
phone_lookup = PhoneLookupService()
