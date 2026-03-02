from flask import Flask, jsonify, request
from flask_cors import CORS
import json
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)

# ─── Load Dataset ─────────────────────────────────────────────────
BASE = os.path.dirname(__file__)
with open(os.path.join(BASE, "../data/dataset.json")) as f:
    DB = json.load(f)

def get_item(item_id):
    return next((i for i in DB["menu_items"] if i["id"] == item_id), None)

def get_user(user_id):
    return next((u for u in DB["users"] if u["id"] == user_id), None)


# ─── Core Recommendation Engine ───────────────────────────────────

def score_add_on(item, user, weather=None, time_of_day=None, cart_item_ids=None):
    
    score = 0.0
    reasons = []

    cart_item_ids = cart_item_ids or []

    # 1. Personalization signals (weight: 0.35)
    if user:
        history = user.get("order_history", [])
        # Item in user history → strong positive signal
        if item["id"] in history:
            freq = history.count(item["id"])
            score += min(0.35, freq * 0.10)
            reasons.append(f"You've ordered this {freq}x before")

        # Dietary compatibility
        dietary = user.get("dietary", "")
        if dietary == "veg" and "non-veg" in item.get("tags", []):
            score -= 0.5  # Heavy penalty
        elif dietary == "non-veg" and "non-veg" in item.get("tags", []):
            score += 0.05

        # Sweet lover preference
        if user.get("sweet_lover") and item["category"] == "dessert":
            score += 0.15
            reasons.append("Matches your sweet preference")

        # Removed items memory
        for removed in user.get("removed_items", []):
            if removed.lower() in item["name"].lower():
                score -= 0.8
                reasons.append(f"You avoid {removed}")

    # 2. Weather context (weight: 0.20)
    if weather:
        weather_items = DB["contextual_rules"]["weather"].get(weather, [])
        if item["id"] in weather_items:
            score += 0.20
            emoji_map = {"hot": "🌡️", "rainy": "🌧️", "cold": "❄️", "mild": "😊"}
            reasons.append(f"Perfect for {weather} weather {emoji_map.get(weather,'')}")

    # 3. Time of day context (weight: 0.15)
    if time_of_day:
        time_items = DB["contextual_rules"]["time_of_day"].get(time_of_day, [])
        if item["id"] in time_items:
            score += 0.15
            reasons.append(f"Popular at {time_of_day}")

    # 4. Combo pairing score (weight: 0.30)
    for rule in DB["combo_rules"]:
        if rule["add_on"] == item["id"] and rule["trigger_item"] in cart_item_ids:
            score += rule["pairing_score"] * 0.30
            reasons.append(f"Classic pair — {rule['pairing_score']*100:.0f}% match")
            break

    # 5. High margin bonus (Zomato's interest)
    if item.get("margin", 0) > 0.80:
        score += 0.05

    return round(score, 3), reasons


# ─── API Endpoints ─────────────────────────────────────────────────

@app.route("/api/recommendations", methods=["GET"])
def get_recommendations():
   
    user_id    = request.args.get("user_id", "U001")
    item_ids   = request.args.get("item_ids", "M001").split(",")
    weather    = request.args.get("weather", "mild")
    time_of_day = request.args.get("time", "evening")

    user = get_user(user_id)
    cart_items = [get_item(i) for i in item_ids if get_item(i)]

    # Score every non-cart item
    candidates = [
        item for item in DB["menu_items"]
        if item["id"] not in item_ids
        and item["category"] not in ["main"]  # Don't suggest more mains
    ]

    scored = []
    for item in candidates:
        score, reasons = score_add_on(item, user, weather, time_of_day, item_ids)
        scored.append({
            "item": item,
            "score": score,
            "reasons": reasons
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # Build response sections
    top_horizontal = scored[:6]
    super_cart_picks = [s for s in scored if s["score"] >= 0.3][:4]

    # Combo suggestion
    combo = None
    for rule in DB["combo_rules"]:
        if rule["trigger_item"] in item_ids:
            add_on_item = get_item(rule["add_on"])
            combo = {**rule, "add_on_item": add_on_item}
            break

    # Frequently bought together
    freq_ids = []
    for iid in item_ids:
        freq_ids.extend(DB["frequently_bought_together"].get(iid, []))
    freq_unique = list(dict.fromkeys(freq_ids))  # preserve order, dedupe
    freq_items = [get_item(i) for i in freq_unique if i not in item_ids and get_item(i)][:4]

    # Local favorite
    local_fav = None
    if user:
        city = user.get("city", "")
        area = user.get("area", "")
        city_data = DB["local_favorites"].get(city, {})
        area_data = city_data.get(area)
        if area_data:
            local_item = get_item(area_data["item"])
            local_fav = {"item": local_item, "label": area_data["label"]}

    # Promoted combo
    promoted = DB.get("promoted_combos", [])

    # Cart value calculation
    cart_total = sum(i["price"] for i in cart_items)
    add_on_opportunity = sum(s["item"]["price"] for s in super_cart_picks)

    # Unlock threshold (Free Delivery unlock)
    delivery_fee = 20
    free_delivery_at = 399
    gap_to_free = max(0, free_delivery_at - cart_total)

    # Combo Passport progress
    passport = None
    if user:
        progress = user.get("combo_passport_progress", 0)
        passport = {
            "goal": "Try 5 Biryani combos this month",
            "progress": progress,
            "total": 5,
            "reward": "Free Gulab Jamun on next order! 🎁",
            "pct": round(progress / 5 * 100)
        }

    return jsonify({
        "success": True,
        "cart": {
            "items": cart_items,
            "total": cart_total,
            "delivery_fee": delivery_fee,
            "gap_to_free_delivery": gap_to_free
        },
        "user": {
            "id": user["id"] if user else None,
            "name": user["name"] if user else "Guest",
            "dietary": user.get("dietary") if user else None,
            "loyalty_points": user.get("loyalty_points", 0) if user else 0
        },
        "context": {
            "weather": weather,
            "time_of_day": time_of_day
        },
        "recommendations": {
            "combo_suggestion": combo,
            "horizontal_scroll": [s["item"] for s in top_horizontal],
            "frequently_bought": freq_items,
            "super_cart": [
                {**s["item"], "reasons": s["reasons"], "score": s["score"]}
                for s in super_cart_picks
            ],
            "local_favorite": local_fav,
            "promoted": promoted
        },
        "gamification": {
            "combo_passport": passport,
            "add_on_potential_value": add_on_opportunity
        },
        "meta": {
            "timestamp": datetime.now().isoformat(),
            "engine_version": "1.0.0"
        }
    })


@app.route("/api/combo", methods=["GET"])
def get_combo():
    """Get combo suggestion for a specific item."""
    item_id = request.args.get("item_id", "M001")
    rule = next((r for r in DB["combo_rules"] if r["trigger_item"] == item_id), None)
    if not rule:
        return jsonify({"success": False, "message": "No combo found"}), 404
    add_on = get_item(rule["add_on"])
    return jsonify({"success": True, "combo": {**rule, "add_on_item": add_on}})


@app.route("/api/user-profile", methods=["GET"])
def user_profile():
    """Get user profile with preferences."""
    user_id = request.args.get("user_id", "U001")
    user = get_user(user_id)
    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404

    history_items = [get_item(i) for i in user["order_history"] if get_item(i)]
    categories = {}
    for item in history_items:
        cat = item["category"]
        categories[cat] = categories.get(cat, 0) + 1

    return jsonify({
        "success": True,
        "user": user,
        "insights": {
            "category_frequency": categories,
            "top_categories": sorted(categories, key=categories.get, reverse=True)[:3],
            "orders_count": len(user["order_history"])
        }
    })


@app.route("/api/frequently-bought", methods=["GET"])
def freq_bought():
    """Get frequently bought together items."""
    item_id = request.args.get("item_id", "M001")
    ids = DB["frequently_bought_together"].get(item_id, [])
    items = [get_item(i) for i in ids if get_item(i)]
    return jsonify({"success": True, "item_id": item_id, "frequently_bought": items})


@app.route("/api/metrics", methods=["GET"])
def metrics():
    """Business metrics overview."""
    total_combos = len(DB["combo_rules"])
    total_orders = sum(c["orders_together"] for c in DB["combo_rules"])
    avg_pairing  = sum(c["pairing_score"] for c in DB["combo_rules"]) / total_combos
    high_margin  = [i for i in DB["menu_items"] if i.get("margin", 0) > 0.80]

    return jsonify({
        "success": True,
        "metrics": {
            "total_menu_items": len(DB["menu_items"]),
            "total_combos": total_combos,
            "total_combo_orders": total_orders,
            "avg_pairing_score": round(avg_pairing, 2),
            "high_margin_items": len(high_margin),
            "avg_addon_margin": round(sum(i["margin"] for i in high_margin)/len(high_margin), 2),
            "projected_aov_lift_pct": 32,
            "projected_revenue_lift_pct": 18
        }
    })


@app.route("/api/all-items", methods=["GET"])
def all_items():
    return jsonify({"success": True, "items": DB["menu_items"]})


if __name__ == "__main__":
    print("🚀 Super Cart API running at http://localhost:5000")
    print("Try: /api/recommendations?user_id=U001&item_ids=M001&weather=hot&time=evening")
    app.run(debug=True, port=5000)
