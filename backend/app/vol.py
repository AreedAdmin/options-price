#Compute sigmoid of realized volatility and implied volatility
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Optional
from .pricing import black_scholes_price, black_scholes_greeks

def get_historical_volatility(ticker: str, window: int = 252) -> float:
    """Compute annualized historic volatility for a given ticker over a specified window."""
    try:
        data = yf.download(ticker, period=f"{window}d", interval="1d", progress=False, auto_adjust=True)
        closing_prices = data['Close']

        log_returns = np.log(closing_prices / closing_prices.shift(1).dropna())

        # Use axis=0 for DataFrame.std to avoid deprecation warning
        daily_vol = np.std(log_returns, axis=0)
        
        # Convert to scalar if it's a Series/ndarray
        daily_vol_value = float(daily_vol.iloc[0]) if hasattr(daily_vol, 'iloc') else float(daily_vol)

        annual_vol = daily_vol_value * np.sqrt(252)
        return annual_vol
    except Exception as e:
        print(f"Error computing historic volatility for {ticker}: {e}")
        return None

def get_implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: str,
    initial_guess: float = 0.2,
    tol: float = 1e-6,
    max_iterations: int = 100
) -> Optional[float]:
    """
    Compute implied volatility using Newton-Raphson method.
    Returns:
        Implied volatility (float) or None if not converged / not solvable.
    """

    # guardrails
    if market_price is None or market_price <= 0:
        return None
    if T <= 0:
        return None

    sigma = initial_guess

    for _ in range(max_iterations):
        # theoretical price at current sigma
        price = black_scholes_price(S, K, r, sigma, T, option_type)

        # vega = dPrice/dSigma
        vega = black_scholes_greeks(S, K, r, sigma, T, option_type).vega

        price_difference = price - market_price

        # close enough? we're done
        if abs(price_difference) < tol:
            return sigma

        # if vega is ~0, we can't update safely -> bail out
        if vega is None or abs(vega) < 1e-8:
            # cannot refine; IV not solvable in a stable way
            return None

        # Newton-Raphson update
        sigma = sigma - price_difference / vega

        # sigma must stay positive and sane
        if sigma <= 0 or sigma > 5:  # cap at 500% just to ignore nonsense
            return None

    # did not converge
    return None