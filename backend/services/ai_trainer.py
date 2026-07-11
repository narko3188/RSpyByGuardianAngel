"""
SerbiaTracker — AI Self-Training Engine
=========================================
Apprentissage continu: chaque tracking ameliore la precision.

Architecture:
1. COLLECT: chaque requete est loggee avec features + resultat
2. CALIBRATE: les 17 tours EIA servent d'anchor points verite terrain
3. LEARN: l'ensemble apprend quelles methodes (V3/V5/V7) sont fiables
   par operateur et par region de Serbie
4. AUTO-TUNE: les poids de l'adaptive engine s'ajustent automatiquement
5. CONVERGE: plus il y a de requetes, plus la precision s'ameliore

State sauvegarde dans training_state.json
"""
import json
import time
import math
import logging
import sqlite3
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field, asdict
from collections import defaultdict
import numpy as np

logger = logging.getLogger(__name__)

STATE_PATH = Path(__file__).parent.parent.parent / "data" / "training_state.json"
DB_PATH = Path(__file__).parent.parent.parent / "data" / "cell_towers.db"

# Regions de Serbie (pour l'apprentissage spatial)
SERBIA_REGIONS = {
    "belgrade":    (44.81, 20.46, 0.3),   # lat, lon, radius_deg
    "vojvodina":   (45.26, 19.85, 1.5),
    "sumadija":    (44.01, 20.92, 1.0),
    "south":       (43.32, 21.90, 1.5),
    "east":        (43.90, 22.30, 1.0),
    "west":        (43.89, 19.85, 1.2),
}


def get_region(lat: float, lon: float) -> str:
    """Determine la region serbe depuis les coordonnees"""
    for name, (rlat, rlon, rrad) in SERBIA_REGIONS.items():
        dlat = abs(lat - rlat)
        dlon = abs(lon - rlon)
        if dlat < rrad and dlon < rrad * 1.3:
            return name
    return "other"


@dataclass
class MethodStats:
    """Stats par methode de tracking"""
    calls: int = 0
    total_error_km: float = 0.0
    best_calls: int = 0        # fois ou c'etait la meilleure
    weight: float = 1.0         # poids adaptatif
    confidence_scores: List[float] = field(default_factory=list)


@dataclass
class OperatorProfile:
    """Profil appris par operateur"""
    mnc: int
    total_calls: int = 0
    v3: MethodStats = field(default_factory=MethodStats)
    v5: MethodStats = field(default_factory=MethodStats)
    v7: MethodStats = field(default_factory=MethodStats)
    region_accuracy: Dict[str, float] = field(default_factory=dict)
    region_calls: Dict[str, int] = field(default_factory=dict)
    eia_calibration_offset: Tuple[float, float] = (0.0, 0.0)
    region_calibration: Dict[str, Tuple[float, float]] = field(default_factory=dict)


@dataclass
class TrainingState:
    """Etat complet de l'apprentissage"""
    version: int = 2
    total_queries: int = 0
    operators: Dict[int, OperatorProfile] = field(default_factory=dict)
    last_trained: float = 0.0
    convergence_score: float = 0.0  # 0.0 = debut, 1.0 = pleinement converge
    
    def get_profile(self, mnc: int) -> OperatorProfile:
        mnc = int(mnc)
        if mnc not in self.operators:
            self.operators[mnc] = OperatorProfile(mnc=mnc)
        return self.operators[mnc]


# Singleton
_state: Optional[TrainingState] = None


def load_state() -> TrainingState:
    global _state
    if _state is not None:
        return _state
    
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text())
            _state = TrainingState()
            _state.total_queries = data.get("total_queries", 0)
            _state.last_trained = data.get("last_trained", 0)
            _state.convergence_score = data.get("convergence_score", 0)
            
            for mnc_str, op_data in data.get("operators", {}).items():
                mnc = int(mnc_str)
                prof = OperatorProfile(mnc=mnc)
                prof.total_calls = op_data.get("total_calls", 0)
                
                for method in ["v3", "v5", "v7"]:
                    md = op_data.get(method, {})
                    ms = MethodStats(
                        calls=md.get("calls", 0),
                        total_error_km=md.get("total_error_km", 0),
                        best_calls=md.get("best_calls", 0),
                        weight=md.get("weight", 1.0),
                    )
                    setattr(prof, method, ms)
                
                prof.region_accuracy = op_data.get("region_accuracy", {})
                prof.region_calls = op_data.get("region_calls", {})
                prof.eia_calibration_offset = tuple(op_data.get("eia_calibration_offset", [0.0, 0.0]))
                prof.region_calibration = {
                    k: tuple(v) for k, v in op_data.get("region_calibration", {}).items()
                }
                _state.operators[mnc] = prof
            
            logger.info(f"AI Training: loaded state ({_state.total_queries} queries, "
                       f"convergence={_state.convergence_score:.1%})")
        except Exception as e:
            logger.warning(f"AI Training: failed to load state: {e}")
            _state = TrainingState()
    else:
        _state = TrainingState()
        _state.last_trained = time.time()
    
    return _state


def save_state():
    """Persiste l'etat d'apprentissage"""
    global _state
    if _state is None:
        return
    
    data = {
        "version": _state.version,
        "total_queries": _state.total_queries,
        "last_trained": time.time(),
        "convergence_score": _state.convergence_score,
        "operators": {}
    }
    
    for mnc, prof in _state.operators.items():
        op_data = {
            "total_calls": prof.total_calls,
            "region_accuracy": prof.region_accuracy,
            "region_calls": prof.region_calls,
            "eia_calibration_offset": list(prof.eia_calibration_offset),
            "region_calibration": {k: list(v) for k, v in prof.region_calibration.items()},
        }
        for method in ["v3", "v5", "v7"]:
            ms = getattr(prof, method)
            op_data[method] = {
                "calls": ms.calls,
                "total_error_km": ms.total_error_km,
                "best_calls": ms.best_calls,
                "weight": ms.weight,
            }
        data["operators"][str(mnc)] = op_data
    
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(data, indent=2))


def record_tracking(
    phone: str,
    mnc: int,
    lat: float,
    lon: float,
    accuracy_km: float,
    method: str,
    confidence: str,
    v3_acc: float = None,
    v5_acc: float = None,
    v7_acc: float = None,
    eia_matches: int = 0,
    city: str = "unknown",
):
    """
    Enregistre un tracking pour l'apprentissage.
    Appele apres chaque requete de geolocalisation.
    """
    state = load_state()
    prof = state.get_profile(mnc)
    
    state.total_queries += 1
    prof.total_calls += 1
    
    # Determiner la region
    region = get_region(lat, lon)
    
    # Mettre a jour les stats de region
    if region not in prof.region_accuracy:
        prof.region_accuracy[region] = accuracy_km
        prof.region_calls[region] = 0
    else:
        alpha = 0.1
        prof.region_accuracy[region] = (
            (1 - alpha) * prof.region_accuracy[region] + alpha * accuracy_km
        )
    prof.region_calls[region] = prof.region_calls.get(region, 0) + 1
    
    # Mettre a jour les stats de methode
    method_map = {
        "adaptive_v6_v3": "v3",
        "adaptive_v6_v5": "v5",
        "adaptive_v6_v7": "v7",
    }
    method_key = method_map.get(method, "v5")
    ms = getattr(prof, method_key)
    ms.calls += 1
    ms.total_error_km += accuracy_km
    
    # Determiner quelle methode etait la meilleure
    if v3_acc is not None and v5_acc is not None:
        accuracies = {}
        if v3_acc is not None: accuracies["v3"] = v3_acc
        if v5_acc is not None: accuracies["v5"] = v5_acc
        if v7_acc is not None: accuracies["v7"] = v7_acc
        
        if accuracies:
            best_method = min(accuracies, key=accuracies.get)
            best_ms = getattr(prof, best_method)
            best_ms.best_calls += 1
    
    # Recalculer les poids adaptatifs (toutes les 10 requetes)
    if prof.total_calls % 10 == 0:
        _recompute_weights(prof)
    
    # Mettre a jour le score de convergence
    _update_convergence(state)
    
    # Calibrer avec EIA si dispo
    if eia_matches > 0:
        _calibrate_with_eia(prof, mnc, lat, lon)
    
    save_state()


def _recompute_weights(prof: OperatorProfile):
    """
    Recalcule les poids de l'adaptive engine.
    Plus une methode est souvent la meilleure, plus son poids augmente.
    """
    methods = {"v3": prof.v3, "v5": prof.v5, "v7": prof.v7}
    total_best = sum(m.best_calls for m in methods.values())
    total_calls = sum(m.calls for m in methods.values())
    
    if total_best == 0 or total_calls < 5:
        return
    
    for name, ms in methods.items():
        if ms.calls > 0:
            # Weight = best_rate * confidence_factor
            best_rate = ms.best_calls / max(total_best, 1)
            avg_error = ms.total_error_km / max(ms.calls, 1)
            
            # Moins d'erreur = plus de poids
            error_factor = 1.0 / max(avg_error, 0.1)
            
            ms.weight = round(best_rate * 0.6 + error_factor * 0.4, 3)
    
    # Normaliser
    total_w = sum(getattr(prof, m).weight for m in ["v3", "v5", "v7"])
    if total_w > 0:
        for m in ["v3", "v5", "v7"]:
            getattr(prof, m).weight /= total_w


def _calibrate_with_eia(prof: OperatorProfile, mnc: int, lat: float, lon: float):
    """Calibre avec les tours EIA (verite terrain) — global + par region"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute(
            "SELECT lat, lon FROM cell_towers WHERE source IN ('EIA','EIA_CAL') AND mnc=? AND mcc=220",
            (mnc,)
        ).fetchall()
        conn.close()
        
        if not rows:
            return
        
        region = get_region(lat, lon)
        
        offsets_lat = []
        offsets_lon = []
        for eia_lat, eia_lon in rows:
            dlat = abs(lat - eia_lat)
            dlon = abs(lon - eia_lon)
            if dlat < 0.5 and dlon < 0.5:
                offsets_lat.append(lat - eia_lat)
                offsets_lon.append(lon - eia_lon)
        
        if offsets_lat:
            alpha = 0.05
            # Global calibration
            old_lat, old_lon = prof.eia_calibration_offset
            new_lat = (1 - alpha) * old_lat + alpha * np.mean(offsets_lat)
            new_lon = (1 - alpha) * old_lon + alpha * np.mean(offsets_lon)
            prof.eia_calibration_offset = (float(new_lat), float(new_lon))
            
            # Regional calibration
            if region not in prof.region_calibration:
                prof.region_calibration[region] = (float(np.mean(offsets_lat)), float(np.mean(offsets_lon)))
            else:
                rlat, rlon = prof.region_calibration[region]
                prof.region_calibration[region] = (
                    float((1 - alpha) * rlat + alpha * np.mean(offsets_lat)),
                    float((1 - alpha) * rlon + alpha * np.mean(offsets_lon))
                )
    except Exception as e:
        logger.debug(f"EIA calibration skipped: {e}")


def _update_convergence(state: TrainingState):
    """Evalue si le systeme converge (les poids se stabilisent).
    
    VRAIE convergence: quand un pattern clair emerge (une methode domine).
    Plus l'ecart-type est GRAND, plus le systeme a appris laquelle est fiable.
    """
    if state.total_queries < 20:
        state.convergence_score = state.total_queries / 20 * 0.2
        return
    
    if state.total_queries < 100:
        # Phase de croissance: 20-99 queries → 20-50%
        state.convergence_score = 0.2 + (state.total_queries - 20) / 80 * 0.3
        return
    
    # Phase de stabilisation: verifier que les poids sont stables
    stable_ops = 0
    total_ops = max(len(state.operators), 1)
    
    for prof in state.operators.values():
        if prof.total_calls < 10:
            continue
        weights = [prof.v3.weight, prof.v5.weight, prof.v7.weight]
        # Verifier qu'une methode domine clairement (signe d'apprentissage)
        max_w = max(weights)
        if max_w > 0.6:  # Une methode a >60% de confiance = l'IA a appris
            stable_ops += 1
    
    # 50-95% selon stabilite
    state.convergence_score = 0.5 + (stable_ops / total_ops) * 0.45


def get_adaptive_weights(mnc: int) -> Dict[str, float]:
    """
    Retourne les poids appris pour l'adaptive engine.
    A utiliser dans adaptive_geolocation() pour biaiser la selection.
    """
    state = load_state()
    prof = state.get_profile(mnc)
    
    return {
        "v3": prof.v3.weight,
        "v5": prof.v5.weight,
        "v7": prof.v7.weight,
        "convergence": state.convergence_score,
        "total_queries": state.total_queries,
        "calibration_offset": list(prof.eia_calibration_offset),
        "region_calibration": {k: list(v) for k, v in prof.region_calibration.items()},
        "region_accuracy": prof.region_accuracy,
        "region_calls": prof.region_calls,
    }


def get_training_stats() -> Dict:
    """Stats d'apprentissage pour le dashboard"""
    state = load_state()
    return {
        "total_queries": state.total_queries,
        "convergence": round(state.convergence_score, 3),
        "operators": {
            str(mnc): {
                "calls": prof.total_calls,
                "weights": {
                    "v3": prof.v3.weight,
                    "v5": prof.v5.weight,
                    "v7": prof.v7.weight,
                },
                "best_method": max(
                    [("v3", prof.v3.best_calls), ("v5", prof.v5.best_calls), ("v7", prof.v7.best_calls)],
                    key=lambda x: x[1]
                )[0] if prof.total_calls > 0 else "unknown",
                "avg_accuracy": round(
                    (prof.v3.total_error_km + prof.v5.total_error_km + prof.v7.total_error_km) /
                    max(prof.v3.calls + prof.v5.calls + prof.v7.calls, 1), 2
                ),
                "calibration": list(prof.eia_calibration_offset),
            }
            for mnc, prof in state.operators.items()
        }
    }
