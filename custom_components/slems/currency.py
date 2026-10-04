"""Currency of the prices: the one set in Home Assistant (Settings → System → General).

Tariff prices are entered in hundredths per kWh (cent, Rappen) and whole
units per year; only the shown symbols follow the currency. Market prices
come from European exchanges in €/MWh and are not converted: in another
currency, amounts of tariffs that follow the market price are not computed.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

# Currency of all market prices (sources and official monthly values).
MARKET_CURRENCY = "EUR"

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


def market_prices_usable(hass: HomeAssistant | None) -> bool:
    """Whether the market prices are in the currency of the tariffs."""
    return currency_code(hass) == MARKET_CURRENCY
