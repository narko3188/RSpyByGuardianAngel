"""
SerbiaTracker - mt:s / Telekom Srbija (220-03) Infrastructure Data
Plus grand opérateur serbe, ~42% part de marché
Groupe Deutsche Telekom

Infrastructure:
- GSM 900/1800: couverture nationale
- UMTS 900/2100: nationale
- LTE 800/1800/2100: nationale  
- 5G NR: Belgrade, Novi Sad, Niš (déc 2025)
- Meilleure couverture 4G selon RATEL 2022
- ~4000 sites en Serbie
"""
# Format: (lat, lon, lac, tac, radio, altitude_m, azimuth, tx_power_dbm)

MTS_TOWERS_BELGRADE = [
    (44.8180, 20.4620, 30001, 1, "LTE", 140, 0, 46),
    (44.8120, 20.4550, 30002, 1, "LTE", 160, 120, 43),
    (44.8050, 20.4650, 30003, 2, "LTE", 170, 240, 46),
    (44.8100, 20.4750, 30004, 2, "LTE", 145, 0, 43),
    (44.8000, 20.4250, 30005, 3, "LTE", 85, 120, 46),
    (44.7950, 20.4100, 30006, 3, "LTE", 75, 240, 43),
    (44.8200, 20.4700, 30007, 4, "LTE", 150, 0, 46),
    (44.7900, 20.4450, 30008, 4, "LTE", 190, 120, 43),
    (44.8250, 20.4450, 30009, 5, "LTE", 155, 240, 46),
    (44.8080, 20.4580, 30010, 5, "LTE", 165, 0, 43),
]

MTS_TOWERS_NOVI_SAD = [
    (45.2580, 19.8480, 31001, 20, "LTE", 85, 0, 46),
    (45.2520, 19.8420, 31002, 20, "LTE", 90, 120, 43),
    (45.2620, 19.8520, 31003, 21, "LTE", 82, 240, 46),
]

MTS_TOWERS_NIS = [
    (43.3220, 21.8980, 32001, 30, "LTE", 200, 0, 46),
    (43.3280, 21.8920, 32002, 30, "LTE", 205, 120, 43),
]

MTS_TOWERS_OTHER = [
    (44.0180, 20.9180, 33001, 40, "LTE", 188, 0, 46),   # Kragujevac
    (46.1020, 19.6680, 34001, 50, "LTE", 112, 120, 43),   # Subotica
    (45.3860, 20.3840, 35001, 23, "LTE", 78, 240, 46),    # Zrenjanin
    (44.8730, 20.6460, 36001, 25, "LTE", 73, 0, 43),      # Pančevo
    (43.8930, 20.3510, 37001, 65, "LTE", 248, 120, 46),   # Čačak
    (42.9960, 21.9480, 38001, 35, "LTE", 232, 240, 43),   # Leskovac
    (44.2770, 19.8850, 39001, 70, "LTE", 198, 0, 46),     # Valjevo
    (44.7580, 19.6960, 40001, 71, "LTE", 83, 120, 43),    # Šabac
    (43.8600, 19.8510, 41001, 72, "LTE", 418, 240, 46),   # Užice
]

MTS_ALL_TOWERS = (
    MTS_TOWERS_BELGRADE + MTS_TOWERS_NOVI_SAD + MTS_TOWERS_NIS + MTS_TOWERS_OTHER
)

MTS_NETWORK_PARAMS = {
    "market_share": 0.42,
    "prefixes": ["064", "065", "066", "069"],
    "typical_tx_power_dbm": 46,
    "cell_radius_urban_km": 2.0,
    "cell_radius_suburban_km": 8,
}
