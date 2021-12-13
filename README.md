# Denoise sensor for Home Assistant

[![GitHub Release](https://img.shields.io/github/tag-date/isorin/ha-denoise?label=release&style=popout)](https://github.com/isorin/ha-denoise/releases)
![GitHub](https://img.shields.io/github/license/isorin/ha-denoise)

Credits: The Denoise sensor code is based on the [Average Sensor for Home Assistant](https://github.com/Limych/ha-average) which I used as a source and inspiration.

The Denoise sensor acts as a noise removal filter by filtering out small variations in the output of a source sensor.
Sometimes sensors can produce quite a bit of white noise which ends up in the HA recorder database or even the long term storage DB.

## Installation

### Manual installation

1. Copy the directory `custom_components/denoise` from this repository into HA `custom_components` directory
2. Add `denoise` sensor to your HA configuration. See configuration examples below.
3. Restart Home Assistant

### Configuration Examples

To create a new temperature denoise filter sensor based on an existing temperature sensor:
```yaml
# Example configuration entry
sensor:
  - platform: denoise
    name: "Multi Sensor 1 Temperature Avg"
    time_delta: "00:30:00"
    value_delta: 0.25
    precision: 1
    scan_interval: 600
    entity_id: sensor.multi_sensor1_temperature
```

To create a new humidity denoise filter sensor based on an existing humidity sensor:
```yaml
# Example configuration entry
sensor:
  - platform: denoise
    name: "Multi Sensor 1 Humidity Avg"
    time_delta: "00:30:00"
    value_delta: 0.5
    precision: 0
    scan_interval: 600
    entity_id: sensor.multi_sensor1_humidity
```

Replace source sensors entity IDs with your existing sensor.

### Configuration Variables

> **_Note_**:\
> You can use weather provider, climate and water heater entities as a data source. For that entities sensor use values of current temperature.

**name**:\
  _(string) (Optional)_\
  Name to use in the frontend.\
  _Default value: "Denoise sensor"_

**value_delta**:\
  _(number) (Optional)_\
  The minimum change in the source sensor value to be considered, smaller changes are ignored.\
  _Default value: 0_

**time_delta**:\
  _(time) (Optional)_\
  If a state change is detected (taking into account precision) for time_delta then update state regardless of value_delta.\
  _Default value: None_

**precision**:\
  _(number) (Optional)_\
  The number of decimals to use when rounding the sensor state.\
  _Default value: 1_

**update_interval**:\
  _(time) (Optional)_\
  If the sensor state is not updated in this time period, a new state is forced (even if equal to the previous state).\
  If update_interval is not specified then a new state is not forced.\
  The update_interval can help with big gaps in dashboards due to missing recorded changes for long periods of time.
  _Default value: None_

**scan_interval**:\
  _(number) (Optional)_\
  This is a standard HA variable and dictates the update frequency in seconds for this sensor.\
  The verification of the update_interval happens at this frequency so the actual forced update interval can be higher than the value of update_interval.\
  _Default value: None_

**entity_id**:\
  _(string) (Required)_\
  The entity ID of the sensor you want to use as source.
