from datetime import datetime, timedelta,timezone
from typing import Literal, Optional
import os

def days_to_expiry(expiry_date_str: str) -> int:
    """Convert expiry date string to days to expiry."""
    expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
    today_utc = datetime.now(timezone.utc).date()
    delta = expiry_date - today_utc
    return delta.days

def time_to_expiry_years(expiry_date_str: str) -> float:
    dte = days_to_expiry(expiry_date_str)
    if dte <= 0:
        return 0.0
    return dte / 365.0

def mid_price(bid: Optional[float], ask: Optional[float], last: Optional[float]) -> Optional[float]:
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    if last is not None and last > 0:
        return last
    return None

def clean_option_type(raw: str) -> str:
    s = raw.strip().lower()
    if s in ["c", "call", "calls"]:
        return "call"
    if s in ["p", "put", "puts"]:
        return "put"
    raise ValueError(f"Unrecognized option type: {raw}")

