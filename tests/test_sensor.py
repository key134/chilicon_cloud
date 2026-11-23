"""Exercise Chilicon discovery and polling with a mocked HTTP session."""

import sys
from contextlib import nullcontext
from datetime import timedelta
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from homeassistant import loader
from homeassistant.bootstrap import async_load_base_functionality
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry
from homeassistant.setup import async_setup_component

DOMAIN = "chilicon_cloud"
ENTITY_ID = "sensor.chilicon_solar_energy_daily_generated"
CONFIG = {
    DOMAIN: {
        "username": "test-user",
        "password": "test-password",
        "installation_hash": "test-installation",
    }
}
DAILY_KWH = 1.0185814291177082


def response(status, *, payload=None, headers=None):
    """Mock an aiohttp request context manager."""
    request = MagicMock()
    request.__aenter__.return_value = MagicMock(
        status=status, headers=headers or {}, json=AsyncMock(return_value=payload)
    )
    return request


class TestChiliconSensor(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        config_dir = Path(self.enterContext(TemporaryDirectory()))
        components = config_dir / "custom_components"
        components.mkdir()
        (components / DOMAIN).symlink_to(Path(__file__).resolve().parents[1])
        self.enterContext(patch.object(sys, "path", [str(config_dir), *sys.path]))
        self.config_dir = str(config_dir)
        self.sensor = import_module(f"custom_components.{DOMAIN}.sensor")
        self.session = MagicMock()
        self.session.post.side_effect = lambda *args, **kwargs: response(
            302, headers={"Location": "/installation/test-installation"}
        )

    async def test_legacy_entity_becomes_manageable_and_keeps_identity(self):
        results = []
        for legacy, watts in [(True, 0), (False, 6000), (False, 12000)]:
            with self.subTest(legacy=legacy, watts=watts):
                self.session.get.return_value = response(
                    200, payload=[[watts, -1], 48361.43982580944, 0.0]
                )
                hass = HomeAssistant(self.config_dir)
                loader.async_setup(hass)
                hass.config_entries = ConfigEntries(hass, CONFIG)
                try:
                    self.assertTrue(await async_load_base_functionality(hass))
                    identity = (
                        patch.object(
                            self.sensor.ChiliconDailySensor,
                            "unique_id",
                            new_callable=PropertyMock,
                            return_value=None,
                        )
                        if legacy
                        else nullcontext()
                    )
                    with identity, patch.object(
                        self.sensor, "async_get_clientsession", return_value=self.session
                    ):
                        self.assertTrue(await async_setup_component(hass, DOMAIN, CONFIG))
                        await hass.async_block_till_done()

                    self.assertEqual(hass.states.async_entity_ids("sensor"), [ENTITY_ID])
                    state = hass.states.get(ENTITY_ID)
                    self.assertEqual(float(state.state), watts / 12000)
                    self.assertEqual(
                        state.attributes["friendly_name"],
                        "Chilicon Solar Energy Daily Generated",
                    )
                    self.assertEqual(state.attributes["state_class"], "total_increasing")
                    self.assertEqual(state.attributes["unit_of_measurement"], "kWh")
                    entry = entity_registry.async_get(hass).async_get(ENTITY_ID)
                    if legacy:
                        self.assertIsNone(entry)
                    else:
                        self.assertTrue(entry.unique_id)
                        results.append((entry.id, entry.unique_id))
                finally:
                    await hass.async_stop(force=True)

        self.assertEqual(len(results), 2)
        self.assertEqual(*results)
        self.assertEqual(self.sensor.SCAN_INTERVAL, timedelta(minutes=5))
        self.assertEqual(self.session.post.call_count, 3)
        self.assertEqual(self.session.get.call_count, 3)
        url = self.session.get.call_args.args[0]
        self.assertIn("/ajax/fetchOwnerUpdate?today=", url)

    async def test_daily_calculation(self):
        cloud = self.sensor.ChiliconCloud(self.session, "user", "password", "site")
        readings = [6000, -1, (DAILY_KWH - 0.5) * 12000, 0]
        self.assertAlmostEqual(cloud.calculate_today(readings), DAILY_KWH, places=14)
        self.assertEqual(cloud.calculate_today([-1, 0, -1]), 0.0)

    async def test_different_installations_have_different_unique_ids(self):
        first = self.sensor.ChiliconCloud(self.session, "user", "password", "site-a")
        second = self.sensor.ChiliconCloud(self.session, "user", "password", "site-b")
        self.assertNotEqual(
            self.sensor.ChiliconDailySensor(first).unique_id,
            self.sensor.ChiliconDailySensor(second).unique_id,
        )

    async def test_auth_retry_is_bounded_and_http_error_preserves_last_value(self):
        cloud = self.sensor.ChiliconCloud(
            self.session, "user", "password", "test-installation"
        )
        sensor = self.sensor.ChiliconDailySensor(cloud)
        self.session.get.side_effect = [
            response(503),
            response(401),
            response(200, payload=[[0, -1], 48361.43982580944, 0.0]),
            response(401),
            response(401),
        ]

        with self.assertLogs(self.sensor.__name__, level="WARNING"):
            await sensor.async_update()
            self.assertIsNone(sensor.native_value)
            await sensor.async_update()
            self.assertEqual(sensor.native_value, 0.0)
            await sensor.async_update()
        self.assertEqual(sensor.native_value, 0.0)
        self.assertEqual(self.session.post.call_count, 2)
        self.assertEqual(self.session.get.call_count, 5)
