from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional, Literal
from .utils import time_to_expiry_years, clean_option_type
from .engine import evaluate_contract
from .chain_loader import load_and_store_chain

app = FastAPI()

class PredictRequest(BaseModel):
    ticker: str
    spot_price: float
    strike: float
    expiry_date: str   # "YYYY-MM-DD"
    option_type: str   # "call" / "put" / "C" / "P"
    bid: Optional[float] = None
    ask: Optional[float] = None
    last_price: Optional[float] = None
    iv_override: Optional[float] = None  # let user force sigma if we want

@app.post("/predict")
def predict(req: PredictRequest):
    opt_type = clean_option_type(req.option_type)
    T = time_to_expiry_years(req.expiry_date)

    result = evaluate_contract(
        spot_price=req.spot_price,
        strike=req.strike,
        expiry_T_years=T,
        option_type=opt_type,
        bid=req.bid,
        ask=req.ask,
        last_price=req.last_price,
        iv_override=req.iv_override,
    )
    return {
        "input": req.model_dump(),
        "result": result
    }

class ChainRequest(BaseModel):
    ticker: str
    expiry_date: str

@app.post("/load-chain")
def load_chain(req: ChainRequest):
    """
    Fetch option chain for ticker+expiry,
    write to option_chains + predictions,
    return summary.
    """
    summary = load_and_store_chain(req.ticker, req.expiry_date)
    return summary