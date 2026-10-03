"""Currency of the prices: the one set in Home Assistant (Settings → System → General).

Tariff prices are entered in hundredths per kWh (cent, Rappen) and whole
units per year; only the shown symbols follow the currency. Market prices
come from European exchanges in €/MWh and stay in euro.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

# Currency code -> (symbol, symbol of a hundredth).
SYMBOLS = {
    "EUR": ("€", "ct"),
    "CHF": ("CHF", "Rp."),
    "GBP": ("£", "p"),
    "USD": ("$", "¢"),
}


def currency_code(hass: HomeAssistant | None) -> str:
    return (getattr(getattr(hass, "config", None), "currency", None) or "EUR").upper()


def symbols(code: str) -> tuple[str, str]:
    """(symbol, symbol of a hundredth) of a currency code."""
    return SYMBOLS.get(code.upper(), (code.upper(), "ct"))
