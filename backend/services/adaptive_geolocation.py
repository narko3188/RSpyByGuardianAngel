"""
SerbiaTracker — Hybrid Adaptive Geolocation V6
Selection automatique entre V3 (multi-pass), V5 (consensus)
et V7 (consensus-first WkNN) selon la qualite des resultats
"""
import logging
from typing import Dict
from services.multi_pass_geolocation import multi_pass_geolocation
from services.consensus_geolocation import consensus_geolocation
from services.hybrid_wknn_geolocation import enhanced_wknn_geolocation
from services.eia_weight_booster import compute_eia_weight_boost, get_eia_stats
from services.ai_trainer import record_tracking, get_adaptive_weights

# HONESTY: confidence maximale car toutes les donnees RSSI/TA sont estimees
# COST-231 Hata remplace random.randint(), mais reste une estimation
MAX_HONEST_CONFIDENCE = "moderate"

logger = logging.getLogger(__name__)


def adaptive_geolocation(phone: str, mnc: str) -> Dict:
    """
    Geolocalisation adaptative V6++ avec V7 fallback

    Heuristique:
    1. V3 (multi-pass): bon si infra dense (Yettel), mauvais si sparse
    2. V5 (consensus DBSCAN): honnete, bon pour A1/mt:s
    3. V7 (consensus WkNN): elimine random city, ultra-stable
    """
    # Lancer les 3 methodes
    v3_result = multi_pass_geolocation(phone, mnc, passes=7)
    v5_result = consensus_geolocation(phone, mnc)

    v5_consensus = v5_result.get("cities_in_consensus", 0)
    v3_acc = v3_result["accuracy_km"]
    v5_acc = v5_result["accuracy_km"]

    # Heuristique de decision
    use_v3 = False
    use_v7 = False
    reason = ""

    if v3_acc < 0.1 and v5_acc < 5 and v5_consensus <= 3:
        # V3 tres bas (<100m) + V5 consensus serre → V3 est credible
        # (Yettel infra dense, les tours sont reellement proches)
        use_v3 = True
        reason = f"V3 credible ({v3_acc:.2f}km), V5 confirme (consensus {v5_consensus}, {v5_acc:.1f}km)"
    elif v5_consensus >= 5 and v3_acc < 3:
        # Beaucoup de villes dispersees = V3 menteur → V7
        use_v7 = True
        reason = f"villes dispersees ({v5_consensus}), V3 non fiable → V7"
    elif v3_acc < 0.1 and v5_acc > 2.0:
        # V3 trop optimiste, V5 realiste → V5
        use_v3 = False
        reason = f"V3 trop optimiste ({v3_acc:.2f}km), V5 honnete ({v5_acc:.1f}km)"
    elif v3_acc < 1.0 and v5_acc > 3.0:
        use_v3 = True
        reason = f"V3 excellent ({v3_acc:.1f}km), V5 degrade ({v5_acc:.1f}km)"
    elif v5_consensus <= 3 and v5_acc < 10 and v3_acc > v5_acc:
        use_v3 = False
        reason = f"consensus serre ({v5_consensus} villes, {v5_acc:.1f}km)"
    elif v5_consensus >= 6 and v3_acc < 5:
        use_v3 = True
        reason = f"infra dense, V3 credible ({v3_acc:.1f}km)"
    elif v3_acc < 2 and v5_acc > 10:
        use_v3 = True
        reason = f"V3 excellent ({v3_acc:.1f}km), V5 degrade"
    elif v5_acc < v3_acc * 0.5:
        use_v3 = False
        reason = f"V5 plus precis ({v5_acc:.1f}km vs {v3_acc:.1f}km)"
    else:
        use_v3 = v3_acc < v5_acc
        reason = f"meilleure precision (V3:{v3_acc:.1f}km vs V5:{v5_acc:.1f}km)"

    if use_v7:
        selected = enhanced_wknn_geolocation(phone, mnc)
        selected["method"] = "adaptive_v6_v7"
    elif use_v3:
        selected = v3_result
        selected["method"] = "adaptive_v6_v3"
    else:
        selected = v5_result
        selected["method"] = "adaptive_v6_v5"

    selected["adaptive_reason"] = reason
    selected["v3_accuracy"] = v3_acc
    selected["v5_accuracy"] = v5_acc
    selected["v3_city"] = v3_result.get("city_estimated", "?")
    selected["v5_city"] = v5_result.get("city_estimated", "?")
    selected["v5_consensus"] = v5_consensus
    
    # AI: utiliser les poids appris pour biaiser la decision
    try:
        ai_weights = get_adaptive_weights(mnc) if mnc else {}
        convergence = ai_weights.get("convergence", 0)
        
        if convergence > 0.5 and ai_weights.get("total_queries", 0) > 100:
            # AUTO-CALIBRATION: preferentiellement regionale, sinon globale
            region_cal = ai_weights.get("region_calibration", {})
            region = "belgrade"  # default
            try:
                from services.ai_trainer import get_region
                region = get_region(selected["latitude"], selected["longitude"])
            except:
                pass
            
            cal = region_cal.get(region, ai_weights.get("calibration_offset", [0.0, 0.0]))
            if abs(cal[0]) > 0.001 or abs(cal[1]) > 0.001:
                selected["latitude"] -= cal[0]
                selected["longitude"] -= cal[1]
                selected["ai_calibrated"] = True
                selected["calibration_applied"] = [round(cal[0], 5), round(cal[1], 5)]
                selected["calibration_region"] = region
                reason += f" + AI calibrated {region} ({cal[0]:+.4f}°, {cal[1]:+.4f}°)"
            
            w7 = ai_weights.get("v7", 0.33)
            if w7 > 0.5 and v3_acc < 0.5 and v5_consensus >= 5:
                use_v7 = True
                use_v3 = False
                reason += f" + AI override (V7 weight={w7:.2f})"
    except Exception:
        pass

    # EIA BOOST: appliquer les GPS exacts du regulateur serbe
    if mnc:
        try:
            mnc_int = int(mnc)
            eia_stats = get_eia_stats(mnc_int)
            if eia_stats["eia_towers_available"] > 0:
                # Appliquer EIA directement: les GPS exacts tirent la position
                pos = (selected["latitude"], selected["longitude"])
                # Passer la vraie liste de tours si disponible, sinon liste vide
                towers_list = selected.get("matched_towers_list", [])
                if not towers_list:
                    towers_list = [{"lat": selected["latitude"], "lon": selected["longitude"], "distance_km": 0.5}]
                merged_lat, merged_lon, eia_matches = compute_eia_weight_boost(mnc_int, pos, towers_list)
                if eia_matches > 0:
                    # Fusion: EIA tire la position vers la verite terrain (30% EIA, 70% triangulation)
                    selected["latitude"] = round(selected["latitude"] * 0.7 + merged_lat * 0.3, 6)
                    selected["longitude"] = round(selected["longitude"] * 0.7 + merged_lon * 0.3, 6)
                    selected["eia_matches_used"] = eia_matches
                    selected["eia_towers_available"] = eia_stats["eia_towers_available"]
                    selected["accuracy_source"] = "eia_weighted+triangulation"
        except Exception as e:
            logger.warning(f"EIA boost failed: {e}")

    # HONESTY CAP: sans mesures reelles, la confiance maximale est "moderate"
    conf_levels = {"excellent": 5, "good": 4, "moderate": 3, "low": 2, "poor": 1}
    if conf_levels.get(selected.get("confidence", "low"), 1) > conf_levels[MAX_HONEST_CONFIDENCE]:
        selected["confidence"] = MAX_HONEST_CONFIDENCE
        selected["confidence_note"] = "capped: no real RSSI/TA measurements"

    # AI TRAINING: enregistrer cette requete pour l'apprentissage
    try:
        if mnc:
            record_tracking(
                phone=phone,
                mnc=int(mnc),
                lat=selected["latitude"],
                lon=selected["longitude"],
                accuracy_km=selected["accuracy_km"],
                method=selected["method"],
                confidence=selected["confidence"],
                v3_acc=v3_acc,
                v5_acc=v5_acc,
                eia_matches=selected.get("eia_matches_used", 0),
                city=selected.get("city_estimated", "unknown"),
            )
    except Exception as e:
        logger.debug(f"AI training record skipped: {e}")

    return selected
