# Configuration 🛠️

The project is configured using environment variables. These can be set in your `docker-compose.yml` file for local development or in the balenaCloud dashboard for devices deployed in the fleet.

## API & Location Configuration

These variables control the connection to the OpenWeatherMap API, location settings, and units of measurement.

| Environment Variable | Description | Default Value |
| :--- | :--- | :--- |
| `openWeatherApiKey` | **Required.** Your API key from OpenWeatherMap. See the [Getting Started guide](/docs/01-getting-started.md). | `key_not_set` |
| `location` | **Required.** The primary location for the weather display, e.g., "City, Country Code". | `London` |
| `otherLocation` | A secondary location to monitor, useful for a commute. If set, the display will cycle between this and the primary `location`. | (None) |
| `units` | The unit system to use. Options are `metric` for Celsius or `imperial` for Fahrenheit. | `metric` |

## Hardware & System Configuration

These settings control the hardware, performance, and data refresh intervals.

| Environment Variable    | Description | Default Value    |
|:------------------------| :--- |:-----------------|
| `operatingHours`        |The hours between which the display is active, in 24-hour H-H format. | `8-22`           |
| `screenRotation`        | Rotates the display output. Set to `180` if your display is upside down. | `0`              |
| `dcPin`                 | The GPIO Data/Command pin number. | `24`             |
| `rstPin`                | The GPIO Reset pin number. | `25`             |
| `fps`                   | The target frames-per-second for display updates and animations. | `40`             |
| `updateIntervalSeconds` | How often (in seconds) to fetch new data from the weather API. | `1800` (30 mins) |
| `apiErrorSleepSeconds`  | How long (in seconds) to wait before retrying after an API connection error. | `30`             |
| `logIntervalSeconds`    | How often (in seconds) to log performance statistics. | `1800` (30 mins) |
| `forceRefresh`          | Set to `True` to force an API data refresh immediately on application start. | `False`          |
| `debug`                 | Set to `True` to enable detailed debug output in the logs. | `False`          |

## Display, Animation & Layout Configuration

Customize the look, feel, and content of the display.

| Environment Variable | Description | Default Value |
| :--- | :--- | :--- |
| `displayDuration` | Time in seconds to show a view before switching between current conditions and forecast weather. If `0`, it switches after scrolling text completes. | `0` |
| `scrollPauseSeconds` | How long (in seconds) to pause before and after scrolling long text. | `2` |
| `scrollOffScreenWaitSeconds` | How long (in seconds) to wait after text has scrolled completely off-screen before resetting. | `1` |
| `transitionDurationSeconds` | The duration (in seconds) of the screen transition effect. | `0.2` |
| `transitionEffect` | The animation effect used when changing views. Available options: `blink`, `wipe`. | `blink` |
| `showBrollyIcon` | Set to `True` to show an umbrella icon when rain is forecast. | `True` |
| `weatherSlot1`<br>`weatherSlot2`<br>`weatherSlot3`<br>`weatherSlot4` | Sets the data to be shown in each of the four customizable slots on the main weather screen. | `Desc`<br>`Sun`<br>`Humidity`<br>`Last updated` |

**Available Slot Options:**
The following values can be used for `weatherSlot1` through `weatherSlot4`:
- `Desc`
- `Sun`
- `Humidity`
- `Last updated`
- `Pressure`
- `Location`
- `Wind speed`
- `Wind direction`