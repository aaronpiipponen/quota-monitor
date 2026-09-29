"""DeepSeek provider: prepaid balance via the documented /user/balance endpoint."""

from __future__ import annotations

from typing import Any

from ..http import HttpError
from ..models import KIND_BALANCE, Meter
from .base import Provider, ProviderError, to_float

BALANCE_URL = "https://api.deepseek.com/user/balance"

# Maps response fields to meter names. Suffixed with the currency at runtime,
# because an account can hold separate CNY and USD balances.
_BALANCE_FIELDS = (
    ("total_balance", "balance", "Balance"),
    ("granted_balance", "granted", "Granted credit"),
    ("topped_up_balance", "topped_up", "Topped up"),
)


class DeepSeekProvider(Provider):
    name = "deepseek"

    def fetch(self) -> list[Meter]:
        api_key = self.require_secret("api_key")
        try:
            data = self.http_get(BALANCE_URL, headers={"Authorization": f"Bearer {api_key}"})
        except HttpError as exc:
            if exc.status == 401:
                raise ProviderError("API key was rejected (HTTP 401).") from exc
            raise ProviderError(str(exc)) from exc
        return parse_balance(data)


def parse_balance(data: Any) -> list[Meter]:
    """Convert a /user/balance response into meters.

    The documentation shows amounts both as strings ("110.00") and as numbers,
    so both forms are accepted.
    """
    if not isinstance(data, dict) or not isinstance(data.get("balance_infos"), list):
        raise ProviderError("Unexpected response shape (missing 'balance_infos').")

    meters: list[Meter] = []
    for info in data["balance_infos"]:
        if not isinstance(info, dict):
            continue
        currency = str(info.get("currency", "UNKNOWN")).upper()
        for field_name, meter_name, label in _BALANCE_FIELDS:
            if field_name in info:
                meters.append(
                    Meter(
                        provider="deepseek",
                        name=f"{meter_name}_{currency.lower()}",
                        kind=KIND_BALANCE,
                        value=to_float(info[field_name], field_name),
                        unit=currency,
                        label=label,
                    )
                )
    return meters
