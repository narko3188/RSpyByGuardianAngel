#!/usr/bin/env python3
"""
Retrain XGBoost on EIA ground truth data.
Replaces random city coordinates with real regulatory GPS positions.
"""
import sys
sys.path.insert(0, "/root/serbia-tracker/backend")

import os, sqlite3, math, random
import numpy as np
from pathlib import Path
from ml.position_predictor import XGBoostPositionRegressor

DB_PATH = "/root/serbia-tracker/data/cell_towers.db"
MODEL_DIR = Path("/root/serbia-tracker/backend/ml/models")
MODEL_DIR.mkdir(exist_ok=True)


def load_eia_towers():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT mnc, lat, lon FROM cell_towers WHERE source='EIA' AND mcc=220"
    ).fetchall()
    conn.close()
    return [(int(r[0]), float(r[1]), float(r[2])) for r in rows]


def load_all_towers():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT mnc, lat, lon, radio FROM cell_towers "
        "WHERE mcc=220 AND lat BETWEEN 42 AND 46 AND lon BETWEEN 19 AND 23"
    ).fetchall()
    conn.close()
    return [{"mnc": int(r[0]), "lat": float(r[1]), "lon": float(r[2]), "radio": r[3] or "LTE"}
            for r in rows]


def rssi_cost231(dist_km, radio="LTE"):
    tx = 43 if radio == "LTE" else 46
    f = 1800 if radio == "LTE" else 900
    hb, hm = 30, 1.5
    d = max(dist_km, 0.01)
    lf = math.log10(f)
    ahm = (1.1 * lf - 0.7) * hm - (1.56 * lf - 0.8)
    pl = 46.3 + 33.9*lf - 13.82*math.log10(hb) - ahm + (44.9 - 6.55*math.log10(hb))*math.log10(d) + 3
    return round(tx - pl), round(d / 0.55) if radio == "LTE" else round(d / 1.1)


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) * math.sin(dlon/2)**2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def main():
    eia = load_eia_towers()
    all_t = load_all_towers()
    print(f"EIA ground truth: {len(eia)}  |  All towers: {len(all_t)}")

    X, y = [], []
    for mnc, elat, elon in eia:
        nearby = [t for t in all_t if t["mnc"] == mnc and haversine(elat, elon, t["lat"], t["lon"]) < 15]
        if len(nearby) < 3:
            continue
        
        for _ in range(50):
            jit = random.uniform(-0.0005, 0.0005)
            lat, lon = elat + jit, elon + jit
            obs = random.sample(nearby, min(random.randint(3, 8), len(nearby)))
            
            rssi, ta = [], []
            for t in obs:
                d = haversine(lat, lon, t["lat"], t["lon"])
                r, ta_v = rssi_cost231(d, t.get("radio", "LTE"))
                rssi.append(r)
                ta.append(ta_v)
            
            if rssi:
                X.append([
                    np.mean(rssi),           # rssi_mean
                    np.mean(rssi) - 20,      # rsrp_mean
                    np.std(rssi) if len(rssi) > 1 else 5.0,  # rssi_std
                    np.mean(ta),             # ta_mean
                    len(obs),                # n_towers
                    mnc,                     # mnc
                ])
                y.append([lat, lon])
    
    X, y = np.array(X), np.array(y)
    print(f"Training samples: {len(X)} (ground truth EIA positions)")

    # Train
    xgb = XGBoostPositionRegressor()
    xgb.train(X, y)
    
    # Eval directly (bypass predict's Observation requirement)
    preds_lat = xgb.model_lat.predict(X[:min(50, len(X))])
    preds_lon = xgb.model_lon.predict(X[:min(50, len(X))])
    preds = np.column_stack([preds_lat, preds_lon])
    errs = np.sqrt(((preds - y[:len(preds)])**2).sum(axis=1)) * 111320
    print(f"XGBoost train MAE: {np.mean(errs):.0f}m (±{np.std(errs):.0f}m) on {len(preds)} samples")

    save_path = MODEL_DIR / "xgb_position_eia.joblib"
    xgb.save(save_path)
    print(f"Saved: {save_path}")
    
    # Also save to the default path the ensemble uses
    default_path = MODEL_DIR / "xgb_position.joblib"
    try:
        import shutil
        shutil.copy(save_path, default_path)
        print(f"Also copied to: {default_path}")
    except Exception as e:
        print(f"Copy failed: {e}")


if __name__ == "__main__":
    main()
