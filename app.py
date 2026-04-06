"""
QuantRead Position Sizer — Free ATR-Based Position Size Calculator
Matches the exact risk math from the live trading system (risk.py).
"""
from flask import Flask, render_template, jsonify, request
import yfinance as yf
import math

app = Flask(__name__)


def get_stock_data(ticker: str) -> dict:
    """Fetch live price, ATR(14), and company name via yfinance."""
    try:
        stock = yf.Ticker(ticker.upper())
        hist = yf.download(ticker.upper(), period="30d", interval="1d", progress=False)

        if hist.empty or len(hist) < 14:
            return {"error": f"Not enough data for {ticker.upper()}"}

        close = hist["Close"]
        high = hist["High"]
        low = hist["Low"]

        # Flatten any MultiIndex columns from yfinance
        if hasattr(close, 'columns'):
            close = close.iloc[:, 0]
        if hasattr(high, 'columns'):
            high = high.iloc[:, 0]
        if hasattr(low, 'columns'):
            low = low.iloc[:, 0]

        # Calculate ATR(14) — True Range method
        tr_list = []
        for i in range(1, len(hist)):
            h = float(high.iloc[i])
            l = float(low.iloc[i])
            pc = float(close.iloc[i - 1])
            tr = max(h - l, abs(h - pc), abs(l - pc))
            tr_list.append(tr)

        atr_14 = sum(tr_list[-14:]) / 14 if len(tr_list) >= 14 else sum(tr_list) / len(tr_list)
        current_price = float(close.iloc[-1])

        # Get company name (info can be None on rate-limited servers)
        try:
            info = stock.info
            if info and isinstance(info, dict):
                name = info.get("shortName", info.get("longName", ticker.upper()))
            else:
                name = ticker.upper()
        except Exception:
            name = ticker.upper()

        # Volume data for context
        vol = hist["Volume"]
        if hasattr(vol, 'columns'):
            vol = vol.iloc[:, 0]
        current_vol = int(vol.iloc[-1])
        avg_vol = int(vol.iloc[-20:].mean()) if len(vol) >= 20 else int(vol.mean())

        return {
            "ticker": ticker.upper(),
            "name": name,
            "price": round(current_price, 2),
            "atr_14": round(atr_14, 4),
            "atr_pct": round((atr_14 / current_price) * 100, 2),
            "current_vol": current_vol,
            "avg_vol": avg_vol,
        }
    except Exception as e:
        return {"error": str(e)}


def calculate_position(
    account_size: float,
    price: float,
    atr: float,
    risk_pct: float = 0.015,
    stop_mult: float = 1.5,
    rr_ratio: float = 2.0,
) -> dict:
    """
    Exact replica of RiskManager.calculate_size() from risk.py.
    """
    if atr <= 0 or price <= 0:
        return {"error": "Invalid market data"}

    # 5% safety buffer (matches live system)
    safe_equity = account_size * 0.95
    risk_amount = safe_equity * risk_pct

    # Stop distance with 2% floor (matches live system)
    stop_distance = stop_mult * atr
    min_stop_distance = price * 0.02
    stop_floor_active = False
    if stop_distance < min_stop_distance:
        stop_distance = min_stop_distance
        stop_floor_active = True

    # Target distance
    target_distance = stop_distance * rr_ratio

    # Share calculation
    if stop_distance == 0:
        return {"error": "Zero stop distance"}

    raw_shares = risk_amount / stop_distance
    shares = int(raw_shares)

    # Buying power cap
    max_shares = int(safe_equity / price)
    shares = min(shares, max_shares)

    if shares < 1:
        return {"error": "Account too small for this stock at current risk settings"}

    # Penny stock cap (matches live system)
    if price < 5.0:
        penny_cap = 1500.0
        max_penny_shares = int(penny_cap / price)
        shares = min(shares, max_penny_shares)

    # Calculate all outputs
    position_value = shares * price
    stop_loss_price = round(price - stop_distance, 2)
    take_profit_price = round(price + target_distance, 2)
    max_loss = round(shares * stop_distance, 2)
    max_gain = round(shares * target_distance, 2)
    risk_of_account = round((max_loss / account_size) * 100, 2)
    position_pct = round((position_value / account_size) * 100, 1)

    return {
        "shares": shares,
        "position_value": round(position_value, 2),
        "position_pct": position_pct,
        "stop_loss_price": stop_loss_price,
        "stop_distance": round(stop_distance, 4),
        "stop_pct": round((stop_distance / price) * 100, 2),
        "take_profit_price": take_profit_price,
        "target_distance": round(target_distance, 4),
        "target_pct": round((target_distance / price) * 100, 2),
        "max_loss": max_loss,
        "max_gain": max_gain,
        "risk_of_account": risk_of_account,
        "reward_risk_ratio": rr_ratio,
        "risk_amount_budget": round(risk_amount, 2),
        "stop_floor_active": stop_floor_active,
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/calculate", methods=["POST"])
def api_calculate():
    data = request.json
    if not data:
        return jsonify({"error": "Invalid request body"}), 400
    ticker = data.get("ticker", "").strip().upper()
    account_size = float(data.get("account_size", 10000))
    risk_pct = float(data.get("risk_pct", 1.5)) / 100.0
    stop_mult = float(data.get("stop_mult", 1.5))
    rr_ratio = float(data.get("rr_ratio", 2.0))

    if not ticker:
        return jsonify({"error": "No ticker provided"}), 400

    stock_data = get_stock_data(ticker)
    if "error" in stock_data:
        return jsonify(stock_data), 400

    position = calculate_position(
        account_size=account_size,
        price=stock_data["price"],
        atr=stock_data["atr_14"],
        risk_pct=risk_pct,
        stop_mult=stop_mult,
        rr_ratio=rr_ratio,
    )

    if "error" in position:
        return jsonify(position), 400

    return jsonify({**stock_data, **position})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
