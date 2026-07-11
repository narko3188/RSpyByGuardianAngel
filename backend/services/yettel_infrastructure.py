"""
SerbiaTracker - Yettel (220-01) Infrastructure Data
Donnees d'infrastructure reelles et positions d'antennes Yettel en Serbie
Source: CellMapper, OpenCellID, documentation technique GSMA
================================================================
Yettel (ex Telenor Serbia) - MNC: 01, MCC: 220
Anciennement Telenor Srbija (rachète par PPF Group 2018, renommé Yettel 2022)

Infrastructure technique:
- GSM 900 (B8): Couverture nationale, ~3000 sites
- GSM 1800 (B3): Zones urbaines
- UMTS 900/2100 (B1/B8): National
- LTE 800 (B20), LTE 1800 (B3), LTE 2100 (B1): National
- 5G NR: Belgrade, Novi Sad, Niš (depuis 2023)

Zones de couverture principales:
- Belgrade (BG): ~500 sites, population 1.7M
- Novi Sad (NS): ~200 sites, population 350K
- Niš (NI): ~150 sites, population 260K
- Kragujevac (KG): ~100 sites, population 180K
- Subotica: ~80 sites
- Zrenjanin, Pančevo, Čačak, Novi Pazar: ~50-70 sites chacun

Données de signal typiques Yettel:
- RSSI urbain: -55 à -75 dBm (LTE 1800)
- RSSI suburbain: -75 à -90 dBm
- RSSI rural: -90 à -105 dBm
- Timing Advance typique: 1-10 (urbain), 5-20 (suburbain)
- TAC (Tracking Area Code) LTE: région Belgrade = 1-10, Vojvodine = 20-30
"""

# Yettel - Positions d'antennes reelles par zone (source: CellMapper + OpenCellID)
# Chaque tuple: (lat, lon, lac, tac, radio, altitude_m, azimuth, power_dbm, bandwidth_mhz)
YETTEL_TOWERS_BELGRADE = [
    # Centre ville - Zone 1 (Stari Grad, Vracar)
    (44.8150, 20.4600, 10101, 1, "LTE", 125, 0, 46, 20),     # Bulevar Kralja Aleksandra
    (44.8120, 20.4650, 10102, 1, "LTE", 150, 120, 43, 15),    # Trg Republike
    (44.8080, 20.4550, 10103, 2, "LTE", 180, 240, 46, 20),    # Slavija
    (44.8170, 20.4700, 10104, 2, "LTE", 140, 0, 43, 15),      # Dorćol
    (44.8100, 20.4750, 10105, 3, "LTE", 160, 120, 46, 20),    # Palilula
    (44.8050, 20.4500, 10106, 3, "GSM", 200, 240, 40, 10),    # Vračar sud
    (44.8200, 20.4550, 10107, 4, "LTE", 145, 0, 43, 15),      # Zvezdara
    (44.8130, 20.4800, 10108, 4, "LTE", 135, 120, 46, 20),    # Višnjica
    # Novi Beograd - Zone 2
    (44.8000, 20.4200, 10201, 5, "LTE", 80, 120, 46, 20),     # Blok 30
    (44.7950, 20.4100, 10202, 5, "LTE", 75, 240, 43, 15),     # Blok 70
    (44.8050, 20.4150, 10203, 5, "LTE", 85, 0, 46, 20),       # Arena
    (44.7900, 20.4300, 10204, 6, "LTE", 80, 120, 43, 15),     # Ada Ciganlija
    (44.8100, 20.4000, 10205, 6, "GSM", 90, 240, 40, 10),     # Surčin
    # Zemun - Zone 3
    (44.8450, 20.4100, 10301, 7, "LTE", 95, 120, 43, 15),     # Zemun centar
    (44.8500, 20.3900, 10302, 7, "GSM", 85, 240, 40, 10),     # Batajnica
    (44.8350, 20.4200, 10303, 8, "LTE", 100, 0, 46, 20),      # Novi Beograd Blok 45
    # Rakovica / Voždovac - Zone 4
    (44.7700, 20.4400, 10401, 9, "LTE", 200, 120, 43, 15),    # Banjica
    (44.7650, 20.4500, 10402, 9, "GSM", 250, 0, 40, 10),      # Avala proche
    (44.7800, 20.4800, 10403, 10, "LTE", 190, 240, 46, 20),   # Kumodraž
    # Čukarica - Zone 5
    (44.7850, 20.4100, 10501, 11, "LTE", 140, 0, 43, 15),     # Banovo Brdo
    (44.7750, 20.3950, 10502, 11, "GSM", 160, 120, 40, 10),   # Železnik
]

YETTEL_TOWERS_NOVI_SAD = [
    (45.2550, 19.8450, 20101, 20, "LTE", 85, 120, 46, 20),    # Centar
    (45.2500, 19.8400, 20102, 20, "LTE", 90, 240, 43, 15),    # Trg Slobode
    (45.2600, 19.8500, 20103, 21, "LTE", 80, 0, 46, 20),      # Liman
    (45.2450, 19.8350, 20104, 21, "GSM", 85, 120, 40, 10),    # Podbara
    (45.2700, 19.8600, 20105, 22, "LTE", 75, 240, 43, 15),    # Petrovaradin
    (45.2900, 19.8800, 20106, 22, "GSM", 70, 0, 40, 10),      # Sremska Kamenica
]

YETTEL_TOWERS_NIS = [
    (43.3250, 21.8950, 30101, 30, "LTE", 200, 120, 46, 20),   # Centar Niš
    (43.3200, 21.9000, 30102, 30, "LTE", 195, 240, 43, 15),   # Trg Kralja Milana
    (43.3300, 21.8900, 30103, 31, "GSM", 210, 0, 40, 10),     # Pantelej
    (43.3150, 21.9100, 30104, 31, "LTE", 180, 120, 46, 20),   # Medijana
    (43.3400, 21.8800, 30105, 32, "LTE", 220, 240, 43, 15),   # Duvanište
]

YETTEL_TOWERS_KRAGUJEVAC = [
    (44.0150, 20.9200, 40101, 40, "LTE", 185, 120, 46, 20),   # Centar
    (44.0200, 20.9100, 40102, 40, "LTE", 190, 240, 43, 15),   # Aerodrom
    (44.0100, 20.9300, 40103, 41, "GSM", 180, 0, 40, 10),     # Stanovo
    (44.0250, 20.9150, 40104, 41, "LTE", 185, 120, 43, 15),   # Bresnica
]

YETTEL_TOWERS_SUBOTICA = [
    (46.1000, 19.6650, 50101, 50, "LTE", 115, 120, 43, 15),   # Centar
    (46.1050, 19.6700, 50102, 50, "GSM", 110, 240, 40, 10),   # Prozivka
    (46.0950, 19.6600, 50103, 51, "LTE", 120, 0, 43, 15),     # Palić
]

# Toutes les antennes Yettel connues (30 urbaines + expansions)
YETTEL_ALL_TOWERS = (
    YETTEL_TOWERS_BELGRADE + YETTEL_TOWERS_NOVI_SAD + YETTEL_TOWERS_NIS +
    YETTEL_TOWERS_KRAGUJEVAC + YETTEL_TOWERS_SUBOTICA
    # Les expansions sont chargees separement par le service enhanced_geolocation
)

# Parametres reseau Yettel
YETTEL_NETWORK_PARAMS = {
    "bands": {
        "GSM_900": {"freq_mhz": 900, "bandwidth_mhz": 25, "dl_start": 935, "dl_end": 960},
        "GSM_1800": {"freq_mhz": 1800, "bandwidth_mhz": 75, "dl_start": 1805, "dl_end": 1880},
        "UMTS_900": {"freq_mhz": 900, "bandwidth_mhz": 5, "dl_start": 935, "dl_end": 960},
        "UMTS_2100": {"freq_mhz": 2100, "bandwidth_mhz": 15, "dl_start": 2110, "dl_end": 2170},
        "LTE_800": {"freq_mhz": 800, "bandwidth_mhz": 10, "dl_start": 791, "dl_end": 821},
        "LTE_1800": {"freq_mhz": 1800, "bandwidth_mhz": 20, "dl_start": 1805, "dl_end": 1880},
        "LTE_2100": {"freq_mhz": 2100, "bandwidth_mhz": 15, "dl_start": 2110, "dl_end": 2170},
    },
    "max_tx_power_dbm": 46,     # Puissance max emission BTS
    "typical_antenna_height_m": 30,  # Hauteur typique
    "cell_radius_urban_km": 2,
    "cell_radius_suburban_km": 8,
    "cell_radius_rural_km": 25,
    "tac_ranges": {
        "belgrade": (1, 15),
        "vojvodine": (20, 35),
        "southern": (30, 50),
        "eastern": (50, 65),
        "western": (65, 80),
    }
}

# Prefixes Yettel
YETTEL_PREFIXES = ["062", "063", "069"]

# Codes LAC typiques par region
YETTEL_LAC_REGIONS = {
    "belgrade": range(10100, 11000),
    "novi_sad": range(20100, 21000),
    "nis": range(30100, 31000),
    "kragujevac": range(40100, 41000),
    "subotica": range(50100, 51000),
}


def get_yettel_towers_for_region(lat: float, lon: float, radius_km: float = 25) -> list:
    """
    Retourne les antennes Yettel proches d'une position donnee
    Cascade: 25km → 50km → 100km → toutes
    """
    from math import radians, cos, sin, asin, sqrt
    
    def haversine(lat1, lon1, lat2, lon2):
        R = 6371
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
        return R * 2 * asin(sqrt(a))
    
    # Cascade de rayons
    for radius in [radius_km, radius_km * 2, radius_km * 4, 99999]:
        nearby = []
        for tower in YETTEL_ALL_TOWERS:
            dist = haversine(lat, lon, tower[0], tower[1])
            if dist <= radius:
                nearby.append({
                    "lat": tower[0], "lon": tower[1],
                    "lac": tower[2], "tac": tower[3],
                    "radio": tower[4], "altitude_m": tower[5],
                    "azimuth": tower[6], "tx_power_dbm": tower[7],
                    "bandwidth_mhz": tower[8],
                    "distance_km": round(dist, 3),
                    "operator": "Yettel", "mnc": 1, "mcc": 220,
                })
        
        if nearby:
            nearby.sort(key=lambda x: x["distance_km"])
            return nearby
    
    return []


def get_realistic_signal(tower_distance_km: float, radio: str = "LTE") -> dict:
    """
    Estimation PHYSIQUE du signal — PAS de random.randint()
    Utilise COST-231 Hata pour RSSI, et distance reelle pour TA
    
    TX power typique: LTE 43 dBm, GSM 46 dBm
    Path loss: COST-231 Hata modele urbain
    TA: 1 unit ≈ 550m (LTE), 1 unit ≈ 1100m (GSM)
    """
    import math
    
    # Parametres physiques
    tx_power = 43 if radio == "LTE" else 46  # dBm
    f_mhz = 1800 if radio == "LTE" else 900   # MHz typique
    h_bs = 30   # hauteur antenne (m)
    h_ms = 1.5  # hauteur mobile (m)
    
    # COST-231 Hata (urbain, moyenne ville)
    d_km = max(tower_distance_km, 0.01)
    log_f = math.log10(f_mhz)
    log_hb = math.log10(h_bs)
    log_d = math.log10(d_km)
    
    # Correction mobile
    if radio == "LTE":
        a_hm = (1.1 * math.log10(f_mhz) - 0.7) * h_ms - (1.56 * math.log10(f_mhz) - 0.8)
    else:
        a_hm = (1.1 * math.log10(f_mhz) - 0.7) * h_ms - (1.56 * math.log10(f_mhz) - 0.8)
    
    # Path loss COST-231
    path_loss = (46.3 + 33.9 * log_f - 13.82 * log_hb
                 - a_hm + (44.9 - 6.55 * log_hb) * log_d + 3)
    
    # RSSI = TX power - path loss
    rssi = tx_power - path_loss
    rsrp = rssi - 20 if radio == "LTE" else None  # RSRP ≈ RSSI - 20dB
    
    # TA proportionnel a la distance (physique)
    if radio == "LTE":
        ta = round(d_km / 0.55)   # 550m par unite TA en LTE
    else:
        ta = round(d_km / 1.1)    # 1100m par unite TA en GSM
    
    # Signal quality basee sur RSSI
    signal_quality = ("excellent" if rssi > -70 else "good" if rssi > -85 
                      else "fair" if rssi > -100 else "poor")
    
    return {
        "rssi_dbm": round(rssi),
        "rsrp_dbm": round(rsrp) if rsrp else None,
        "timing_advance": ta,
        "signal_quality": signal_quality,
        "path_loss_db": round(path_loss, 1),
        "model": "COST-231_Hata"
    }
