"""
SerbiaTracker - A1 Srbija (220-05) Infrastructure Data
Anciennement VIP mobile, rebrandé A1 en 2020
Groupe A1 Telekom Austria

Infrastructure technique:
- GSM 900/1800: couverture urbaine
- UMTS 900/2100: nationale  
- LTE 800/1800/2100: nationale
- 5G NR: Belgrade, Novi Sad, Niš (depuis déc 2025)
- 4CA carrier aggregation jusqu'à 50MHz
- ~2500 sites en Serbie
"""
# A1 Srbija - Positions d'antennes (source: CellMapper + données publiques)
# Format: (lat, lon, lac, tac, radio, altitude_m, azimuth, tx_power_dbm)

A1_TOWERS_BELGRADE = [
    (44.8200, 20.4550, 60001, 1, "LTE", 130, 120, 46),
    (44.8100, 20.4650, 60002, 1, "LTE", 145, 240, 43),
    (44.8050, 20.4500, 60003, 2, "LTE", 180, 0, 46),
    (44.8150, 20.4700, 60004, 2, "LTE", 140, 120, 43),
    (44.8000, 20.4200, 60005, 3, "LTE", 80, 240, 46),
    (44.7950, 20.4150, 60006, 3, "LTE", 75, 0, 43),
    (44.8120, 20.4800, 60007, 4, "LTE", 135, 120, 46),
    (44.8250, 20.4400, 60008, 4, "LTE", 150, 240, 43),
    (44.7900, 20.4300, 60009, 5, "LTE", 80, 0, 46),
    (44.8080, 20.4600, 60010, 5, "LTE", 175, 120, 43),
]

A1_TOWERS_NOVI_SAD = [
    (45.2600, 19.8450, 61001, 20, "LTE", 85, 120, 46),
    (45.2550, 19.8400, 61002, 20, "LTE", 88, 240, 43),
    (45.2500, 19.8500, 61003, 21, "LTE", 80, 0, 46),
    (45.2650, 19.8550, 61004, 21, "LTE", 82, 120, 43),
]

A1_TOWERS_NIS = [
    (43.3250, 21.8950, 62001, 30, "LTE", 200, 120, 46),
    (43.3200, 21.9000, 62002, 30, "LTE", 195, 240, 43),
    (43.3300, 21.8900, 62003, 31, "LTE", 210, 0, 46),
]

A1_TOWERS_OTHER = [
    # Kragujevac
    (44.0150, 20.9200, 63001, 40, "LTE", 185, 120, 46),
    (44.0200, 20.9150, 63002, 40, "LTE", 190, 240, 43),
    # Subotica
    (46.1000, 19.6650, 64001, 50, "LTE", 115, 120, 43),
    # Zrenjanin
    (45.3836, 20.3819, 65001, 23, "LTE", 80, 120, 43),
    # Pančevo
    (44.8713, 20.6443, 66001, 25, "LTE", 75, 240, 46),
    # Čačak
    (43.8914, 20.3497, 67001, 65, "LTE", 250, 120, 43),
    # Leskovac
    (42.9983, 21.9461, 68001, 35, "LTE", 230, 0, 46),
    # Valjevo
    (44.2750, 19.8833, 69001, 70, "LTE", 200, 120, 43),
    # Šabac
    (44.7558, 19.6939, 70001, 71, "LTE", 85, 240, 43),
    # Užice
    (43.8586, 19.8489, 71001, 72, "LTE", 420, 120, 46),
]

A1_ALL_TOWERS = (
    A1_TOWERS_BELGRADE + A1_TOWERS_NOVI_SAD + A1_TOWERS_NIS + A1_TOWERS_OTHER
)

# Paramètres réseau A1
A1_NETWORK_PARAMS = {
    "market_share": 0.18,
    "prefixes": ["060", "061", "068"],
    "bands": {
        "LTE_800": {"freq_mhz": 800},
        "LTE_1800": {"freq_mhz": 1800},
        "LTE_2100": {"freq_mhz": 2100},
        "NR_3500": {"freq_mhz": 3500},
        "NR_3600": {"freq_mhz": 3600},
    },
    "typical_tx_power_dbm": 46,
    "cell_radius_urban_km": 2.5,
    "cell_radius_suburban_km": 10,
}
