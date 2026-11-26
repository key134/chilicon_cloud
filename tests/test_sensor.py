"""Exercise Chilicon YAML discovery and polling with mocked HTTP responses."""

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from importlib import import_module
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from homeassistant import loader
from homeassistant.bootstrap import async_load_base_functionality
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_component, entity_registry
from homeassistant.setup import async_setup_component

DOMAIN = "chilicon_cloud"
DAILY = "sensor.chilicon_solar_energy_daily_generated"
POWER = "sensor.chilicon_solar_power"
LIFETIME = "sensor.chilicon_solar_lifetime_energy"
CONFIG = {
    DOMAIN: {
        "username": "test-user",
        "password": "test-password",
        "installation_hash": "test-installation",
    }
}
DIGEST = sha256(b"test-installation").hexdigest()
LIFETIME_KWH = 48362.461157238555
LOGIN_LOCATION = "/installation/test-installation"


def response(status=200, *, payload=None, location=None, content_type="application/json"):
    """Mock an aiohttp request context manager."""
    request = MagicMock()
    request.__aenter__.return_value = MagicMock(
        status=status,
        headers={"Location": location} if location else {},
        content_type=content_type,
        json=AsyncMock(return_value=payload),
    )
    return request


def production(samples=None, current=4.748639601744799):
    """Return a successful response with a deliberately constant lifetime value."""
    if samples is None:
        samples = [6000, -1, 6222.977149412498, 0, -0.47205075]
    return response(payload=[samples, LIFETIME_KWH, current])


class TestChiliconSensor(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        config_dir = Path(self.enterContext(TemporaryDirectory()))
        components = config_dir / "custom_components"
        components.mkdir()
        (components / DOMAIN).symlink_to(Path(__file__).resolve().parents[1])
        self.enterContext(patch.object(sys, "path", [str(config_dir), *sys.path]))
        self.sensor = import_module(f"custom_components.{DOMAIN}.sensor")
        self.coordinator = import_module(f"custom_components.{DOMAIN}.coordinator")
        self.session = MagicMock()
        self.session.post.return_value = response(302, location=LOGIN_LOCATION)
        self.session.get.return_value = production()
        self.session_factory = self.enterContext(
            patch.object(self.sensor, "async_create_clientsession", return_value=self.session)
        )
        self.hass = hass = HomeAssistant(str(config_dir))
        loader.async_setup(hass)
        hass.config_entries = ConfigEntries(hass, CONFIG)
        self.addAsyncCleanup(hass.async_stop, force=True)
        self.assertTrue(await async_load_base_functionality(hass))
        self.assertTrue(await async_setup_component(hass, "homeassistant", {}))
        self.registry = entity_registry.async_get(hass)

    async def setup_chilicon(self, *, enable_lifetime=False):
        """Load the platform using its usual YAML entry point."""
        if enable_lifetime:
            self.registry.async_get_or_create(
                "sensor", DOMAIN, f"{DIGEST}_lifetime_energy",
                suggested_object_id="chilicon_solar_lifetime_energy",
            )
        self.assertTrue(await async_setup_component(self.hass, DOMAIN, CONFIG))
        await self.hass.async_block_till_done()

    async def refresh(self, *entity_ids):
        """Use the standard Home Assistant action."""
        await self.hass.services.async_call(
            "homeassistant", "update_entity", {"entity_id": entity_ids}, blocking=True
        )

    def state(self, entity_id):
        return self.hass.states.get(entity_id)

    async def test_milestone_one_registry_entry_and_default_sensors(self):
        original = self.registry.async_get_or_create(
            "sensor", DOMAIN, f"{DIGEST}_daily_energy",
            suggested_object_id="chilicon_solar_energy_daily_generated",
        )
        await self.setup_chilicon()
        self.assertCountEqual(self.hass.states.async_entity_ids("sensor"), [DAILY, POWER])
        entry = self.registry.async_get(DAILY)
        self.assertEqual(entry.id, original.id)
        self.assertEqual(entry.unique_id, f"{DIGEST}_daily_energy")
        self.assertEqual(self.registry.async_get(POWER).unique_id, f"{DIGEST}_power")
        lifetime = self.registry.async_get(LIFETIME)
        self.assertEqual(lifetime.unique_id, f"{DIGEST}_lifetime_energy")
        self.assertEqual(lifetime.disabled_by, entity_registry.RegistryEntryDisabler.INTEGRATION)
        self.assertAlmostEqual(float(self.state(DAILY).state), 1.0185814291177082)
        self.assertAlmostEqual(float(self.state(POWER).state), 4748.639601744799)
        for entity_id, name, unit, device_class, state_class in (
            (DAILY, "Chilicon Solar Energy Daily Generated", "kWh", "energy", "total_increasing"),
            (POWER, "Chilicon Solar Power", "W", "power", "measurement"),
        ):
            attrs = self.state(entity_id).attributes
            self.assertEqual(attrs["friendly_name"], name)
            self.assertEqual(attrs["unit_of_measurement"], unit)
            self.assertEqual(attrs["device_class"], device_class)
            self.assertEqual(attrs["state_class"], state_class)
        self.session_factory.assert_called_once_with(self.hass)
        self.session.post.assert_called_once()
        self.session.get.assert_called_once()

    async def test_manual_refresh_from_each_sensor_updates_all_three(self):
        await self.setup_chilicon(enable_lifetime=True)
        self.assertCountEqual(
            self.hass.states.async_entity_ids("sensor"), [DAILY, POWER, LIFETIME]
        )
        for index, entity_id in enumerate((DAILY, POWER, LIFETIME), start=1):
            self.session.get.return_value = production([index * 12000], current=index)
            await self.refresh(entity_id)
            self.assertEqual(float(self.state(DAILY).state), index)
            self.assertEqual(float(self.state(POWER).state), index * 1000)
            self.assertAlmostEqual(float(self.state(LIFETIME).state), LIFETIME_KWH)
            entity = self.hass.data[entity_component.DATA_INSTANCES]["sensor"].get_entity(
                LIFETIME
            )
            self.assertEqual(entity.native_value, LIFETIME_KWH)
            self.assertNotIn("state_class", self.state(LIFETIME).attributes)
            self.assertEqual(self.session.get.call_count, index + 1)
            poll_times = {
                self.state(entity).attributes["last_successful_poll"]
                for entity in (DAILY, POWER, LIFETIME)
            }
            self.assertEqual(len(poll_times), 1)
        self.session.post.assert_called_once()

    async def test_concurrent_manual_requests_share_in_flight_poll(self):
        await self.setup_chilicon(enable_lifetime=True)
        entered = asyncio.Event()
        release = asyncio.Event()
        request = production([24000], current=2)
        result = request.__aenter__.return_value

        async def blocked_request():
            entered.set()
            await release.wait()
            return result

        request.__aenter__.side_effect = blocked_request
        self.session.get.return_value = request
        first = asyncio.create_task(self.refresh(DAILY))
        await asyncio.wait_for(entered.wait(), timeout=5)
        both_joining = asyncio.Event()
        refresh = self.coordinator.ChiliconCoordinator.async_request_refresh
        joiners = 0

        async def count_joiners(coordinator):
            nonlocal joiners
            joiners += 1
            if joiners == 2:
                both_joining.set()
            await refresh(coordinator)

        with patch.object(
            self.coordinator.ChiliconCoordinator, "async_request_refresh", count_joiners
        ):
            second = asyncio.create_task(self.refresh(POWER, LIFETIME))
            try:
                await asyncio.wait_for(both_joining.wait(), timeout=5)
                self.assertEqual(self.session.get.call_count, 2)
            finally:
                release.set()
                await asyncio.gather(first, second)
        self.assertEqual(self.session.get.call_count, 2)
        self.assertEqual(float(self.state(POWER).state), 2000)

    async def test_scheduled_poll_uses_fifteen_minute_timer(self):
        loop = self.hass.loop
        schedule = loop.call_at
        timers = []

        def capture_timer(when, callback, *args, **kwargs):
            handle = schedule(when, callback, *args, **kwargs)
            if args and getattr(args[0], "name", "") == "Chilicon Cloud refresh interval":
                timers.append((when, callback, args, handle))
            return handle

        with patch.object(loop, "call_at", side_effect=capture_timer):
            await self.setup_chilicon()
            when, callback, args, handle = timers[0]
            self.assertAlmostEqual(when - loop.time(), 900, delta=2)
            self.assertEqual(self.coordinator.UPDATE_INTERVAL, timedelta(minutes=15))
            self.session.get.return_value = production([24000], current=3)
            handle.cancel()
            callback(*args)
            await self.hass.async_block_till_done(wait_background_tasks=True)
            self.assertEqual(len(timers), 2)
        self.assertEqual(self.session.get.call_count, 2)
        self.assertEqual(float(self.state(DAILY).state), 2)
        self.assertEqual(float(self.state(POWER).state), 3000)

    async def test_expired_session_recovers_and_retry_is_bounded(self):
        await self.setup_chilicon(enable_lifetime=True)
        self.session.get.side_effect = [
            response(302, location="/login"),
            production([], current=0),
            response(401),
            response(401),
        ]
        await self.refresh(POWER)
        self.assertEqual(float(self.state(POWER).state), 0)
        self.assertEqual(self.session.post.call_count, 2)
        with self.assertLogs(self.coordinator.__name__, level="ERROR"):
            await self.refresh(DAILY)
        self.assertEqual(self.session.post.call_count, 3)
        self.assertEqual(self.session.get.call_count, 5)
        for entity_id in (DAILY, POWER, LIFETIME):
            self.assertEqual(self.state(entity_id).state, "unavailable")

    async def test_failed_login_at_startup_can_recover(self):
        self.session.post.return_value = response(302, location="/login")
        with self.assertLogs(self.coordinator.__name__, level="ERROR") as logs:
            await self.setup_chilicon()
        self.assertNotIn("test-password", str(logs.output))
        self.assertEqual(self.state(DAILY).state, "unavailable")
        self.session.get.assert_not_called()
        self.session.post.return_value = response(302, location=LOGIN_LOCATION)
        await self.refresh(POWER)
        self.assertAlmostEqual(float(self.state(DAILY).state), 1.0185814291177082)

    async def test_failed_requests_mark_all_sensors_unavailable_then_recover(self):
        await self.setup_chilicon(enable_lifetime=True)
        malformed = response()
        malformed.__aenter__.return_value.json.side_effect = ValueError("private body")
        failures = [
            response(503),
            malformed,
            response(payload=[[0], LIFETIME_KWH]),
            response(payload=[[None], LIFETIME_KWH, 0]),
            response(payload=[[0], LIFETIME_KWH, None]),
            response(payload=[[0], LIFETIME_KWH, float("nan")]),
            response(payload=[[0], LIFETIME_KWH, 1e308]),
            response(payload=[[0], -1, 0]),
            response(payload=[[0], True, 0]),
            response(payload=[[0], 10**400, 0]),
            aiohttp.ClientConnectionError("private headers"),
            TimeoutError("private URL"),
        ]
        for failure in failures:
            with self.subTest(failure=failure):
                self.session.get.side_effect = [failure, production([0], current=0)]
                with self.assertLogs(self.coordinator.__name__, level="ERROR") as logs:
                    await self.refresh(DAILY)
                self.assertNotIn("private", str(logs.output))
                for entity_id in (DAILY, POWER, LIFETIME):
                    self.assertEqual(self.state(entity_id).state, "unavailable")
                await self.refresh(POWER)
                self.assertEqual(float(self.state(DAILY).state), 0)
                self.assertEqual(float(self.state(POWER).state), 0)
                self.assertAlmostEqual(float(self.state(LIFETIME).state), LIFETIME_KWH)
        self.session.post.assert_called_once()

    async def test_midnight_reset_and_unchanged_nighttime_data(self):
        before = datetime(2026, 11, 23, 23, 55, tzinfo=timezone.utc)
        midnight = before + timedelta(minutes=5)
        self.session.get.return_value = production([24000], current=0)
        with patch.object(self.coordinator.dt, "now", return_value=before):
            await self.setup_chilicon(enable_lifetime=True)
        self.assertEqual(float(self.state(DAILY).state), 2)

        self.session.get.return_value = production([0, -1], current=0)
        with patch.object(self.coordinator.dt, "now", side_effect=[before, midnight]):
            with self.assertLogs(self.coordinator.__name__, level="ERROR"):
                await self.refresh(DAILY)
        self.assertEqual(self.state(DAILY).state, "unavailable")

        for when in (midnight, midnight + timedelta(minutes=15)):
            with patch.object(self.coordinator.dt, "now", return_value=when):
                await self.refresh(POWER)
            self.assertTrue(self.session.get.call_args.args[0].endswith("today=2026-11-24"))
            self.assertEqual(float(self.state(DAILY).state), 0)
            self.assertEqual(float(self.state(POWER).state), 0)
            self.assertAlmostEqual(float(self.state(LIFETIME).state), LIFETIME_KWH)
            self.assertEqual(
                self.state(DAILY).attributes["last_successful_poll"], when.isoformat()
            )

        self.session.get.return_value = production([6000], current=0.5)
        with patch.object(self.coordinator.dt, "now", return_value=midnight + timedelta(hours=8)):
            await self.refresh(LIFETIME)
        self.assertEqual(float(self.state(DAILY).state), 0.5)
        self.assertAlmostEqual(float(self.state(LIFETIME).state), LIFETIME_KWH)
