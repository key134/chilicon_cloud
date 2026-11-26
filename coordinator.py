"""Shared Chilicon Cloud client and polling coordinator."""

from __future__ import annotations

from asyncio import Event
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from sys import float_info

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt

from .const import CHILICON_URL

_LOGGER = logging.getLogger(__name__)
UPDATE_INTERVAL = timedelta(minutes=15)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20, connect=10)


@dataclass(frozen=True)
class Production:
    """Validated production readings and the local poll completion time."""

    daily_energy: float
    power: float
    lifetime_energy: float
    polled_at: datetime


class CloudError(Exception):
    """A cloud request failed with a diagnostic safe to log."""


class SessionExpired(CloudError):
    """The update endpoint rejected the session."""


def _nonnegative(value: object, field: str) -> float:
    """Validate an API number without accepting booleans or numeric strings."""
    if type(value) in (int, float) and 0 <= value <= float_info.max:
        return float(value)
    raise CloudError(f"Invalid {field} in Chilicon response")


class ChiliconCloud:
    """Access the cloud using a dedicated cookie session."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        installation_hash: str,
    ) -> None:
        self.session = session
        self.username = username
        self.password = password
        self.installation_hash = installation_hash
        self._authenticated = False

    async def _login(self) -> None:
        async with self.session.post(
            f"{CHILICON_URL}/login",
            data={
                "username": self.username,
                "password": self.password,
                "Login": "Login",
            },
            allow_redirects=False,
            timeout=REQUEST_TIMEOUT,
        ) as response:
            if response.status >= 500 or response.status == 429:
                raise CloudError(f"Chilicon login returned HTTP {response.status}")
            if (
                response.status not in (301, 302, 303, 307, 308)
                or response.headers.get("Location")
                != f"/installation/{self.installation_hash}"
            ):
                raise CloudError(
                    "Chilicon login did not authorize the configured installation"
                )
        self._authenticated = True

    async def _fetch(self, today: str) -> Production:
        async with self.session.get(
            f"{CHILICON_URL}/ajax/fetchOwnerUpdate?today={today}",
            headers={"Referer": f"{CHILICON_URL}/installation/{self.installation_hash}"},
            allow_redirects=False,
            timeout=REQUEST_TIMEOUT,
        ) as response:
            if (
                response.status in (400, 401, 403)
                or 300 <= response.status < 400
                or (response.status == 200 and response.content_type == "text/html")
            ):
                raise SessionExpired(
                    f"Chilicon update rejected the session (HTTP {response.status})"
                )
            if response.status != 200:
                raise CloudError(f"Chilicon update returned HTTP {response.status}")
            try:
                payload = await response.json()
            except (aiohttp.ContentTypeError, ValueError):
                raise CloudError("Chilicon update did not return valid JSON") from None

        if not isinstance(payload, list) or len(payload) != 3:
            raise CloudError("Chilicon update did not return three production fields")
        day_data, lifetime, current = payload
        if not isinstance(day_data, list):
            raise CloudError("Invalid day_data in Chilicon response")

        # Each sample is average watts over 1/12 hour. -1 marks an unused slot.
        daily_energy = 0.0
        for index, sample in enumerate(day_data):
            if type(sample) in (int, float) and sample == -1:
                continue
            try:
                daily_energy += _nonnegative(sample, "day_data sample") / 12000
            except CloudError:
                detail = type(sample).__name__
                if type(sample) in (int, float) and -float_info.max <= sample < 0:
                    detail = f"negative {detail}: {sample:.8g}"
                raise CloudError(
                    f"Invalid day_data sample at index {index} in Chilicon response "
                    f"({detail})"
                ) from None

        power = _nonnegative(current, "current") * 1000
        polled_at = dt.now()
        if polled_at.date().isoformat() != today:
            raise CloudError("Chilicon update crossed midnight for the requested day")
        return Production(
            _nonnegative(daily_energy, "daily energy"),
            _nonnegative(power, "power"),
            _nonnegative(lifetime, "lifetime"),
            polled_at,
        )

    async def fetch_data(self) -> Production:
        """Fetch one snapshot, logging in again at most once per update."""
        try:
            if not self._authenticated:
                await self._login()
            try:
                return await self._fetch(dt.now().date().isoformat())
            except SessionExpired:
                self._authenticated = False
                _LOGGER.debug("Refreshing the Chilicon session after an update rejection")
                await self._login()
                try:
                    return await self._fetch(dt.now().date().isoformat())
                except SessionExpired:
                    self._authenticated = False
                    raise
        except (aiohttp.ClientError, TimeoutError) as err:
            # aiohttp exceptions may contain the request URL or headers.
            raise CloudError(
                f"Chilicon cloud communication failed ({type(err).__name__})"
            ) from None


class ChiliconCoordinator(DataUpdateCoordinator[Production]):
    """Coordinate all production entities for a YAML installation."""

    def __init__(self, hass: HomeAssistant, cloud: ChiliconCloud) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=None,
            name="Chilicon Cloud",
            update_interval=UPDATE_INTERVAL,
        )
        self._cloud = cloud
        self._poll_finished = Event()
        self._poll_finished.set()

    async def async_request_refresh(self) -> None:
        """Refresh immediately or join a poll already in progress."""
        if not self._poll_finished.is_set():
            await self._poll_finished.wait()
            return
        await self.async_refresh()

    async def _async_update_data(self) -> Production:
        self._poll_finished.clear()
        try:
            return await self._cloud.fetch_data()
        except CloudError as err:
            raise UpdateFailed(str(err)) from err
        finally:
            self._poll_finished.set()
