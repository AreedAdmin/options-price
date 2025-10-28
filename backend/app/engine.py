from typing import Optional, Literal, Dict, Any, Tuple
import os
import httpx
from .pricing import black_scholes_price, black_scholes_greeks
from .vol import get_implied_volatility


#Fetch latest risk-free rate directly from Supabase REST API
def get_latest_risk_free_rate_from_supabase() -> Tuple[float, str]:
    """
    Returns (rate, source)

    rate: float annualized risk-free rate
    source: "supabase" if fetched successfully, "fallback" otherwise
    """
    try:
        supabase_url = os.getenv("SUPABASE_URL", "").strip().strip('"').strip("'")
        supabase_key = os.getenv("SUPABASE_ANON_KEY", "").strip().strip('"').strip("'")

        if not supabase_url or not supabase_key:
            print("⚠️ Missing Supabase credentials; using default r=0.05")
            return 0.05, "fallback_no_creds"

        #/rest/v1 endpoint
        if not supabase_url.rstrip("/").endswith("/rest/v1"):
            rest_url = supabase_url.rstrip("/") + "/rest/v1"
        else:
            rest_url = supabase_url.rstrip("/")

        url = (
            rest_url
            + "/risk_free_rates"
            + "?select=rate_annual,fetched_at"
            + "&order=fetched_at.desc"
            + "&limit=1"
        )

        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
        }

        resp = httpx.get(url, headers=headers, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()

        if isinstance(data, list) and len(data) > 0:
            latest_row = data[0]
            rate_val = float(latest_row.get("rate_annual", 0.05))
            return rate_val, "supabase"
        else:
            print("⚠️ No rate rows found; fallback 0.05")
            return 0.05, "fallback_empty_table"

    except Exception as e:
        print(f"⚠️ Error fetching rate from Supabase: {e}")
        return 0.05, "fallback_error"


def _mid_price(
    bid: Optional[float],
    ask: Optional[float],
    last_price: Optional[float]
) -> Optional[float]:
    """Decide the 'market price' of the option."""
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    if last_price is not None and last_price > 0:
        return last_price
    return None


def _classify_signal(mispricing_pct: Optional[float]) -> Optional[str]:
    """Turn mispricing % into a trading signal."""
    if mispricing_pct is None:
        return None
    if mispricing_pct > 0.10:
        return "BUY"
    if mispricing_pct < -0.10:
        return "OVERPRICED"
    return "FAIR"


def evaluate_contract(
    *,
    spot_price: float,
    strike: float,
    expiry_T_years: float,
    option_type: Literal["call", "put"],
    bid: Optional[float],
    ask: Optional[float],
    last_price: Optional[float],
    iv_override: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Core pricing pipeline.
    """

    # 1. Get latest r + where it came from
    r, r_source = get_latest_risk_free_rate_from_supabase()

    # 2. Handle edge case: expired option
    if expiry_T_years <= 0:
        intrinsic_val = (
            max(spot_price - strike, 0.0)
            if option_type == "call"
            else max(strike - spot_price, 0.0)
        )
        return {
            "status": "expired_or_intrinsic_only",
            "r": r,
            "r_source": r_source,
            "T": expiry_T_years,
            "intrinsic_value": intrinsic_val,
        }

    # 3. Determine market price
    market_price = _mid_price(bid, ask, last_price)

    # 4. Volatility
    if iv_override is not None:
        sigma = iv_override
        iv_source = "override"
    else:
        if market_price is None:
            sigma = None
            iv_source = "none"
        else:
            sigma = get_implied_volatility(
                market_price=market_price,
                S=spot_price,
                K=strike,
                T=expiry_T_years,
                r=r,
                option_type=option_type,
            )
            iv_source = "implied_from_market"

    if sigma is None:
        return {
            "status": "no_sigma",
            "r": r,
            "r_source": r_source,
            "T": expiry_T_years,
            "note": "Could not determine implied volatility.",
        }

    # 5. Compute model price & Greeks
    model_price = black_scholes_price(
        S=spot_price, K=strike, r=r, sigma=sigma, T=expiry_T_years, option_type=option_type
    )
    greeks = black_scholes_greeks(
        S=spot_price, K=strike, r=r, sigma=sigma, T=expiry_T_years, option_type=option_type
    )

    # 6. Mispricing and signal
    mispricing_pct = (
        (model_price - market_price) / market_price
        if (market_price is not None and market_price > 0)
        else None
    )
    signal = _classify_signal(mispricing_pct)

    # 7. Package result
    return {
        "status": "ok",
        "inputs": {
            "spot_price": spot_price,
            "strike": strike,
            "T_years": expiry_T_years,
            "option_type": option_type,
            "bid": bid,
            "ask": ask,
            "last_price": last_price,
            "market_price": market_price,
            "iv_source": iv_source,
        },
        "model": {
            "r": r,
            "r_source": r_source,
            "sigma": sigma,
            "model_price": model_price,
            "greeks": {
                "delta": greeks.delta,
                "gamma": greeks.gamma,
                "vega": greeks.vega,
                "theta": greeks.theta,
                "rho": greeks.rho,
            },
        },
        "market_comparison": {
            "market_price": market_price,
            "mispricing_pct": mispricing_pct,
            "signal": signal,
        },
    }