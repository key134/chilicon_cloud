# Chilicon Cloud Integration

This integration creates a sensor that exposes solar power data from [Chilicon](https://chiliconpower.com/) microinverters.

This is an integration that loads its platforms from its own set up, based on [this](https://github.com/home-assistant/example-custom-config/tree/master/custom_components/example_load_platform/) example. It also draws heavily from [this](https://aarongodfrey.dev/home%20automation/building_a_home_assistant_custom_component_part_1/) blog post by Aaron Godfrey. It does not use the config flow which is the preferred method to setup integrations.

### Installation

Copy this folder to `<config_dir>/custom_components/chilicon_cloud/`.

Add the following entry in your `configuration.yaml`:

```yaml
chilicon_cloud:
  username: me@example.com
  password: !secret chilicon_password
  installation_hash: abcd...123
```

### Upgrading an existing installation

On a full Home Assistant restart, the daily sensor registers a unique ID derived
from your configured installation hash. Home Assistant uses the existing name to
generate `sensor.chilicon_solar_energy_daily_generated` when that ID is free.
The previous sensor had no registry entry, so Home Assistant creates the entry
through its normal setup process. Keeping the same entity ID, kWh unit, and
`total_increasing` state class lets Recorder continue the existing statistics
series. Changing the installation hash changes the unique ID.

After installing the update and restarting Home Assistant:

1. In **Settings → Devices & services → Entities**, open the Chilicon daily
   sensor. Confirm its settings are editable and its entity ID is
   `sensor.chilicon_solar_energy_daily_generated`.
2. In **Developer tools → States**, check for a single daily sensor and confirm
   its unit is `kWh` and its state class is `total_increasing`. If you see a
   suffixed ID, check what owns the original ID before changing dashboard or
   automation references.
3. Within a five-minute polling interval, check the reading against Chilicon
   Cloud. Check **Developer tools → Statistics** for the original statistics
   series and verify continuity in the Energy dashboard if you use it.
4. Restart again and confirm the entity keeps the same ID and remains editable.
   Enable debug logging for `custom_components.chilicon_cloud` if you want to
   inspect the lifetime and current readings.

### Tests

Run the suite in a Python 3.14 virtual environment with Home Assistant Core
2026.11.2 installed. The tests use a temporary Home Assistant configuration
directory and mocked Chilicon HTTP responses.

```sh
python -m pip install homeassistant==2026.11.2
python -m unittest discover -s tests -v
```
