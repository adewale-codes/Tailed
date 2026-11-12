"""Companies House lookup.

Public Data API: https://developer-specs.company-information.service.gov.uk/
Auth is HTTP basic with the API key as the username and a blank password.
Any failure (no key, network, 4xx/5xx) raises LookupUnavailable so the check reports
not_checked, never a pass.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Protocol

import httpx
from pydantic import BaseModel

from app.checks.util import company_tokens

log = logging.getLogger(__name__)

BASE_URL = "https://api.company-information.service.gov.uk"
TIMEOUT = httpx.Timeout(8.0, connect=4.0)


class CompanyRecord(BaseModel):
    number: str
    name: str
    status: str | None = None
    date_of_creation: date | None = None
    last_accounts_period_end: date | None = None

    @property
    def has_filed_accounts(self) -> bool:
        return self.last_accounts_period_end is not None


class LookupUnavailable(Exception):
    """Companies House couldn't be asked (no key, outage, rate limit...)."""


class CompanyLookup(Protocol):
    def find(self, name: str) -> CompanyRecord | None:
        """Best exact match for a company name, None if there is none."""


def same_company_name(a: str, b: str) -> bool:
    return company_tokens(a) == company_tokens(b)


class CompaniesHouseClient:
    def __init__(self, api_key: str, base_url: str = BASE_URL,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.transport = transport

    def _get(self, client: httpx.Client, path: str, params: dict | None = None) -> dict | None:
        try:
            res = client.get(path, params=params)
        except httpx.HTTPError as exc:
            log.warning("companies_house.error type=%s", type(exc).__name__)
            raise LookupUnavailable(type(exc).__name__) from None
        if res.status_code == 404:
            return None
        if res.status_code != 200:
            log.warning("companies_house.error status=%s", res.status_code)
            raise LookupUnavailable(f"status {res.status_code}")
        try:
            return res.json()
        except ValueError:
            log.warning("companies_house.error type=invalid_json")
            raise LookupUnavailable("invalid json") from None

    def find(self, name: str) -> CompanyRecord | None:
        if not self.api_key:
            raise LookupUnavailable("no api key")
        with httpx.Client(base_url=self.base_url, auth=(self.api_key, ""), timeout=TIMEOUT,
                          transport=self.transport) as client:
            results = self._get(client, "/search/companies", {"q": name, "items_per_page": 20}) or {}
            match = next((item for item in results.get("items") or []
                          if same_company_name(item.get("title", ""), name)), None)
            if match is None:
                return None
            number = match.get("company_number", "")
            profile = self._get(client, f"/company/{number}") or {}
        last = ((profile.get("accounts") or {}).get("last_accounts")) or {}
        period_end = last.get("period_end_on") or last.get("made_up_to")
        created = profile.get("date_of_creation") or match.get("date_of_creation")
        return CompanyRecord(
            number=number,
            name=profile.get("company_name") or match.get("title", ""),
            status=profile.get("company_status") or match.get("company_status"),
            date_of_creation=date.fromisoformat(created) if created else None,
            last_accounts_period_end=date.fromisoformat(period_end) if period_end else None,
        )


class StaticCompanies:
    """In-memory registry for tests and fixture runs. Never used in production."""

    def __init__(self, records: list[CompanyRecord]) -> None:
        self.records = records

    def find(self, name: str) -> CompanyRecord | None:
        return next((r for r in self.records if same_company_name(r.name, name)), None)


class UnavailableCompanies:
    """Behaves like Companies House being down or unconfigured."""

    def find(self, name: str) -> CompanyRecord | None:
        raise LookupUnavailable("unavailable")
