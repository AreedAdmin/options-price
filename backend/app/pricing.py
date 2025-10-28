import math
from dataclasses import dataclass
from typing import Literal

# === Normal distribution helpers ===
def normal_pdf(x: float) -> float:
    """Standard normal probability density function n(x)."""
    return (1.0 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * x**2)

def normal_cdf(x: float) -> float:
    """Standard normal cumulative distribution function N(x)."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# === Black-Scholes Class ===
@dataclass
class BlackScholes:
    S: float      # Spot price
    K: float      # Strike price
    T: float      # Time to maturity (years)
    r: float      # Risk-free rate
    sigma: float  # Volatility

    def d1(self) -> float:
        if self.T <= 0 or self.sigma <= 0:
            raise ValueError("T and sigma must be positive nonzero values.")
        return (math.log(self.S / self.K) + (self.r + 0.5 * self.sigma**2) * self.T) / (self.sigma * math.sqrt(self.T))

    def d2(self) -> float:
        return self.d1() - self.sigma * math.sqrt(self.T)

    def call_price(self) -> float:
        """Black-Scholes call price."""
        return self.S * normal_cdf(self.d1()) - self.K * math.exp(-self.r * self.T) * normal_cdf(self.d2())

    def put_price(self) -> float:
        """Black-Scholes put price."""
        return self.K * math.exp(-self.r * self.T) * normal_cdf(-self.d2()) - self.S * normal_cdf(-self.d1())


# === Greeks Class ===
@dataclass
class Greeks:
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float

    @classmethod
    def from_bs(cls, bs: BlackScholes, option_type: Literal["call", "put"]) -> "Greeks":
        d1, d2 = bs.d1(), bs.d2()

        # Common terms
        pdf_d1 = normal_pdf(d1)
        sqrtT = math.sqrt(bs.T)

        # Delta
        delta = normal_cdf(d1) if option_type == "call" else normal_cdf(d1) - 1.0

        # Gamma
        gamma = pdf_d1 / (bs.S * bs.sigma * sqrtT)

        # Vega
        vega = bs.S * pdf_d1 * sqrtT

        # Theta
        first_term = -(bs.S * pdf_d1 * bs.sigma) / (2 * sqrtT)
        if option_type == "call":
            theta = first_term - bs.r * bs.K * math.exp(-bs.r * bs.T) * normal_cdf(d2)
            rho = bs.K * bs.T * math.exp(-bs.r * bs.T) * normal_cdf(d2)
        else:
            theta = first_term + bs.r * bs.K * math.exp(-bs.r * bs.T) * normal_cdf(-d2)
            rho = -bs.K * bs.T * math.exp(-bs.r * bs.T) * normal_cdf(-d2)

        # Convert theta to per-day decay
        theta = theta / 365.0

        return cls(delta, gamma, vega, theta, rho)


# === Functional wrappers (for FastAPI usage) ===
def black_scholes_price(
    S: float, K: float, r: float, sigma: float, T: float, option_type: Literal["call", "put"]
) -> float:
    """Stateless pricing wrapper."""
    bs = BlackScholes(S, K, T, r, sigma)
    return bs.call_price() if option_type == "call" else bs.put_price()


def black_scholes_greeks(
    S: float, K: float, r: float, sigma: float, T: float, option_type: Literal["call", "put"]
) -> Greeks:
    """Stateless Greeks wrapper."""
    bs = BlackScholes(S, K, T, r, sigma)
    return Greeks.from_bs(bs, option_type)