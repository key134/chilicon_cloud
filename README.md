# Chilicon Cloud Integration

This custom Home Assistant integration exposes production data from [Chilicon](https://chiliconpower.com/) microinverters. It uses one authenticated cloud client and one polling coordinator per YAML installation.

### Installation and upgrade

Copy `__init__.py`, `const.py`, `coordinator.py`, `sensor.py` and `manifest.json` into `<config_dir>/custom_components/chilicon_cloud/`. For an existing installation, keep the same YAML credentials and installation hash.

```yaml
chilicon_cloud:
  username: me@example.com
  password: !secret chilicon_password
  installation_hash: abcd...123
```

Check the configuration and restart Home Assistant. Then verify the following.

1. Under **Settings → Devices & services → Entities**, confirm there is one daily entity with the original entity ID and existing registry settings. Verify the power sensor is available and the lifetime sensor is registered but disabled by default.
2. Under **Developer tools → Actions**, run the action below with the daily or power entity. Confirm both report the same new `last_successful_poll`. Compare daytime power against the cloud dashboard after converting kW to W.
3. Under **Developer tools → Statistics**, confirm the original daily energy series continues. After midnight, check that the daily reading falls with the new day's samples and that yesterday's energy is counted once in the Energy dashboard.
4. To inspect lifetime energy, enable its entity in the registry and restart so YAML discovery loads it. Check the raw value across several daylight polls and day boundaries.

### Entities

| Name | Default entity ID | Unit | Device class | State class |
| --- | --- | --- | --- | --- |
| Chilicon Solar Energy Daily Generated | `sensor.chilicon_solar_energy_daily_generated` | kWh | `energy` | `total_increasing` |
| Chilicon Solar Power | `sensor.chilicon_solar_power` | W | `power` | `measurement` |
| Chilicon Solar Lifetime Energy | `sensor.chilicon_solar_lifetime_energy` | kWh | `energy` | None |

Unique IDs use the SHA-256 hex digest of the exact configured installation hash followed by `_daily_energy`, `_power`, or `_lifetime_energy`. The daily sensor retains its existing unique ID, name, unit and state class so Home Assistant can reuse its registry entry and Recorder series. An existing registry rename is retained. Changing the hash registers new entities.

The daily reading sums each valid five-minute sample in watts divided by 12,000, skipping `-1` slots. Each request uses the current date in Home Assistant's configured time zone. A response crossing that date boundary is rejected to keep the previous day's samples from being published as today's. Recorder detects a daily reset when it observes a sufficiently lower value, typically a nighttime zero. If polling misses the reset and the next reading is already close to or above the previous day's last recorded value, Recorder may miss the reset and undercount. Check that Home Assistant's time zone matches the installation.

The raw lifetime field agrees approximately with the dashboard in kWh but has been observed holding steady for hours as daily production increased. A plateau alone is compatible with a cumulative meter that updates in batches. The update cadence, reset behavior and treatment of downward corrections are unknown. Recorder could treat a large downward correction as a new meter cycle and overcount. Lifetime energy is therefore disabled by default and has no state class. Keep it out of the Energy dashboard until its behavior is verified.

### Refresh and availability

The coordinator polls every 15 minutes. The standard action requests an immediate refresh or joins a poll already in progress. One validated response updates all enabled sensors.

```yaml
action: homeassistant.update_entity
data:
  entity_id: sensor.chilicon_solar_energy_daily_generated
```

The client uses finite HTTP timeouts and retries a rejected session once after logging in again. A failed request or invalid snapshot makes the enabled sensors unavailable until a successful poll. A valid response with zero power or unchanged readings still counts as a successful poll.

The `last_successful_poll` attribute records when Home Assistant accepted the response. The inspected payload contains five-minute slots and two numeric values. It supplies no trustworthy timestamp for the latest underlying production measurement. A slot position describes its nominal period, and a `-1` slot can indicate either a future interval or no production. Repeated data alone cannot establish whether cloud readings are stale.

### Tests

Run in a Python 3.14 virtual environment with Home Assistant Core 2026.11.2. The tests use a temporary Home Assistant configuration and mock the Chilicon HTTP session.

```sh
python -m pip install homeassistant==2026.11.2
python -m unittest discover -s tests -v
```
