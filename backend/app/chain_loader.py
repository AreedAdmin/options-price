import os
from datetime import datetime, timezone
from typing import List, Dict, Any
import httpx
import yfinance as yf
from .engine import evaluate_contract
from .utils import time_to_expiry_years, clean_option_type


def _get_supabase_rest_info():
    """
    Build the REST base URL and auth headers for Supabase.
    Returns (rest_url, headers)
    """
    supabase_url = os.getenv("SUPABASE_URL", "").strip().strip('"').strip("'")
    supabase_key = os.getenv("SUPABASE_ANON_KEY", "").strip().strip('"').strip("'")

    if not supabase_url or not supabase_key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY in environment")

    # Ensure /rest/v1 on the end
    if not supabase_url.rstrip("/").endswith("/rest/v1"):
        rest_url = supabase_url.rstrip("/") + "/rest/v1"
    else:
        rest_url = supabase_url.rstrip("/")

    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        # Prefer lets us get rows back or at least have deterministic behavior
        "Prefer": "return=representation",
        "Content-Type": "application/json",
    }

    return rest_url, headers


def _get_spot_price(ticker: str) -> float:
    """
    Get the current underlying spot price for the ticker.
    We'll try fast_info.first then fall back to last close.
    """
    t = yf.Ticker(ticker)

    # Try fast_info for live-ish price
    spot = None
    try:
        fast = getattr(t, "fast_info", None)
        if fast and "lastPrice" in fast:
            spot = fast["lastPrice"]
        elif fast and "last_price" in fast:
            # some yfinance versions camel/snake differently
            spot = fast["last_price"]
    except Exception as e:
        print(f"⚠️ fast_info spot failed for {ticker}: {e}")

    # Fallback: most recent close
    if spot is None:
        hist = t.history(period="1d")
        if len(hist) == 0:
            raise RuntimeError(f"Could not get spot price for {ticker}")
        spot = float(hist["Close"].iloc[-1])

    return float(spot)


def _fetch_option_chain(ticker: str, expiry_date: str):
    """
    Ask yfinance for the option chain for this (ticker, expiry_date).
    Returns (calls_df, puts_df, expirations)

    On success:
        calls_df, puts_df = DataFrames
        expirations = list[str] of all valid expirations
    On failure:
        raises ValueError("bad_expiry", { ...context... })
    """
    t = yf.Ticker(ticker)

    # grab all expirations first
    expirations = []
    try:
        expirations = list(getattr(t, "options", []))
    except Exception as e:
        print(f"⚠️ couldn't fetch expirations for {ticker}: {e}")

    try:
        chain = t.option_chain(expiry_date)
        calls_df = chain.calls.copy()
        puts_df = chain.puts.copy()
        return calls_df, puts_df, expirations

    except Exception as e:
        # Instead of RuntimeError we raise a structured ValueError
        raise ValueError("bad_expiry", {
            "ticker": ticker,
            "requested_expiry": expiry_date,
            "available_expirations": expirations,
            "root_error": str(e),
        })


def _build_option_chain_rows(
    *,
    ticker: str,
    expiry_date: str,
    option_type: str,  # "call" or "put"
    df,
    spot_price: float,
    now_iso: str,
) -> List[Dict[str, Any]]:
    """
    Turn a yfinance chain df (calls or puts) into rows for option_chains table.
    """
    rows = []
    for _, row in df.iterrows():
        try:
            strike = float(row["strike"])
            bid = float(row["bid"]) if not (row["bid"] is None) else None
            ask = float(row["ask"]) if not (row["ask"] is None) else None
            last_price = float(row["lastPrice"]) if "lastPrice" in row else None
            iv = float(row["impliedVolatility"]) if "impliedVolatility" in row else None
            oi = int(row["openInterest"]) if "openInterest" in row and not (row["openInterest"] is None) else None
        except Exception as e:
            print(f"⚠️ failed to parse raw row for {ticker} {option_type} {e}")
            continue

        rows.append(
            {
                "ticker": ticker,
                "expiry_date": expiry_date,
                "strike": strike,
                "option_type": option_type,
                "bid": bid,
                "ask": ask,
                "last_price": last_price,
                "implied_volatility": iv,
                "open_interest": oi,
                "spot_price": spot_price,
                "timestamp": now_iso,  # if your DB column has default now(), you could skip this
                "source": "yfinance",
            }
        )
    return rows


def _build_prediction_rows(
    *,
    ticker: str,
    expiry_date: str,
    option_type: str,
    df,
    spot_price: float,
    T_years: float,
    now_iso: str,
) -> List[Dict[str, Any]]:
    """
    For each contract, run evaluate_contract() and return rows ready for predictions table.
    """
    rows = []
    for _, row in df.iterrows():
        try:
            strike = float(row["strike"])
            bid = float(row["bid"]) if not (row["bid"] is None) else None
            ask = float(row["ask"]) if not (row["ask"] is None) else None
            last_price = float(row["lastPrice"]) if "lastPrice" in row else None
        except Exception as e:
            print(f"⚠️ could not parse pricing inputs for {ticker} {option_type}: {e}")
            continue

        # Run the pricing engine
        result = evaluate_contract(
            spot_price=spot_price,
            strike=strike,
            expiry_T_years=T_years,
            option_type=option_type,
            bid=bid,
            ask=ask,
            last_price=last_price,
            iv_override=None,
        )

        if result["status"] != "ok":
            # skip rows we couldn't price
            continue

        model_block = result["model"]
        market_block = result["market_comparison"]

        greeks = model_block["greeks"]

        rows.append(
            {
                "ticker": ticker,
                "expiry_date": expiry_date,
                "strike": strike,
                "option_type": option_type,
                "model_price": model_block["model_price"],
                "market_price": market_block["market_price"],
                "mispricing_pct": market_block["mispricing_pct"],
                "signal": market_block["signal"],
                "delta": greeks["delta"],
                "gamma": greeks["gamma"],
                "vega": greeks["vega"],
                "theta": greeks["theta"],
                "rho": greeks["rho"],
                "created_at": now_iso,  # if DB default now(), you could omit
            }
        )
    return rows


def _batched_insert(table: str, rows: List[Dict[str, Any]]):
    """
    Inserts a list of dict rows into a Supabase table via REST.
    We'll do one request with the entire list.
    """
    if not rows:
        print(f"ℹ️ No rows to insert into {table}")
        return {"status": "empty", "count": 0}

    rest_url, headers = _get_supabase_rest_info()
    url = f"{rest_url}/{table}"

    try:
        resp = httpx.post(url, headers=headers, json=rows, timeout=10.0)
        # For debugging:
        print(f"SUPABASE INSERT {table} status={resp.status_code} len={len(rows)}")
        if resp.status_code >= 300:
            print("Body:", resp.text)
        resp.raise_for_status()
    except Exception as e:
        print(f"❌ error inserting into {table}: {e}")
        return {"status": "error", "count": 0, "error": str(e)}

    return {"status": "ok", "count": len(rows)}


def load_and_store_chain(ticker: str, expiry_date: str) -> Dict[str, Any]:
    """
    1. Pull call/put chain from Yahoo for that expiry.
    2. Build rows for option_chains and predictions.
    3. Insert both into Supabase.
    4. Return a summary dictionary for FastAPI response.
    """

    # normalize ticker just in case
    ticker = ticker.upper().strip()

    # mild validation: option_type is always "call"/"put" internally
    # (clean_option_type is for e.g. "C"/"P" inputs from /predict)
    # here we already know which side we're looping.

    print(f"➡️ Loading option chain for {ticker} @ {expiry_date}")

    # Snapshot time (UTC ISO)
    now_iso = datetime.now(timezone.utc).isoformat()

    # Get underlying spot now
    spot_price = _get_spot_price(ticker)
    print(f"   spot_price={spot_price}")

    # Get time to expiry in years (used for pricing model)
    T_years = time_to_expiry_years(expiry_date)
    print(f"   T_years={T_years}")

    # Fetch from Yahoo
    calls_df, puts_df, expirations = _fetch_option_chain(ticker, expiry_date)
    print(f"   fetched {len(calls_df)} calls, {len(puts_df)} puts from yfinance with expirations {expirations}")

    # Build rows for option_chains
    option_rows_calls = _build_option_chain_rows(
        ticker=ticker,
        expiry_date=expiry_date,
        option_type="call",
        df=calls_df,
        spot_price=spot_price,
        now_iso=now_iso,
    )
    option_rows_puts = _build_option_chain_rows(
        ticker=ticker,
        expiry_date=expiry_date,
        option_type="put",
        df=puts_df,
        spot_price=spot_price,
        now_iso=now_iso,
    )
    option_rows_all = option_rows_calls + option_rows_puts
    print(f"   built {len(option_rows_all)} option_chains rows")

    # Build rows for predictions (model output)
    prediction_rows_calls = _build_prediction_rows(
        ticker=ticker,
        expiry_date=expiry_date,
        option_type="call",
        df=calls_df,
        spot_price=spot_price,
        T_years=T_years,
        now_iso=now_iso,
    )
    prediction_rows_puts = _build_prediction_rows(
        ticker=ticker,
        expiry_date=expiry_date,
        option_type="put",
        df=puts_df,
        spot_price=spot_price,
        T_years=T_years,
        now_iso=now_iso,
    )
    prediction_rows_all = prediction_rows_calls + prediction_rows_puts
    print(f"   built {len(prediction_rows_all)} predictions rows")

    # Insert into Supabase
    insert_option_result = _batched_insert("option_chains", option_rows_all)
    insert_prediction_result = _batched_insert("predictions", prediction_rows_all)

    # Build summary for API response
    summary = {
        "ticker": ticker,
        "expiry_date": expiry_date,
        "spot_price": spot_price,
        "T_years": T_years,
        "counts": {
            "yfinance_calls": len(calls_df),
            "yfinance_puts": len(puts_df),
            "option_chain_rows": insert_option_result.get("count", 0),
            "prediction_rows": insert_prediction_result.get("count", 0),
        },
        "option_insert_status": insert_option_result.get("status"),
        "prediction_insert_status": insert_prediction_result.get("status"),
        "valid_expirations": expirations,
    }

    print("✅ load_and_store_chain summary:", summary)
    return summary