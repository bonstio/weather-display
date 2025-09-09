import os

def loadConfig():
    """Loads all configuration from environment variables."""
    data = {}

    # --- API & Location Configuration ---
    data['API_KEY'] = os.getenv('openWeatherApiKey', 'key_not_set')
    data['LOCATION'] = os.getenv('location', 'London')
    data['OTHER_LOCATION'] = os.getenv('otherLocation', None)
    data['UNITS'] = os.getenv('units', 'metric')
    data['TEMP_UNIT'] = "°C" if data['UNITS'] == 'metric' else "°F"
    data['TZ'] = os.getenv('TZ', 'Europe/London')

    # --- Display & Hardware Configuration ---
    data['ROTATION'] = int(os.getenv('screenRotation', 0))
    data['DC_PIN'] = int(os.getenv('dcPin', 24))
    data['RST_PIN'] = int(os.getenv('rstPin', 25))
    data['DEBUG'] = os.getenv('debug', 'False').lower() == 'true'
    data['DEBUG_DATE'] = os.getenv('debugDate', None)
    data['FPS'] = int(os.getenv('fps', 40))
    data['UPDATE_INTERVAL_SECONDS'] = int(os.getenv('updateIntervalSeconds', 1800))  # 30 minutes
    data['API_ERROR_SLEEP_SECONDS'] = int(os.getenv('apiErrorSleepSeconds', 1800))
    data['LOG_INTERVAL_SECONDS'] = int(os.getenv('logIntervalSeconds', 1800))  # 30 minutes
    data['FORCE_REFRESH_ON_START'] = os.getenv('forceRefresh', 'False').lower() == 'true'
    data['OPERATING_HOURS'] = os.getenv('operatingHours', '8-22')

    # --- View & Animation Timing ---
    # Set to 0 to switch views after marquee completes, or > 0 to switch after N seconds.
    data['DISPLAY_DURATION'] = int(os.getenv('displayDuration', 0))
    data['SCROLL_PAUSE_SECONDS'] = int(os.getenv('scrollPauseSeconds', 2))
    data['SCROLL_OFF_SCREEN_WAIT_SECONDS'] = int(os.getenv('scrollOffScreenWaitSeconds', 1))
    data['TRANSITION_DURATION_SECONDS'] = float(os.getenv('transitionDurationSeconds', 0.2))
    # Available options: 'blink', 'wipe'
    data['TRANSITION_EFFECT'] = os.getenv('transitionEffect', 'wipe').lower()
    data['SHOW_UMBRELLA_ICON'] = os.getenv('showBrollyIcon', 'True').lower() == 'true'

    # --- Current Weather View Slots ---
    # Available options: "Desc", "Sun", "Humidity", "Last updated", "Pressure", "Location", "Wind speed", "Wind direction"
    data['WEATHER_SLOT_1'] = os.getenv('weatherSlot1', 'Desc')
    data['WEATHER_SLOT_2'] = os.getenv('weatherSlot2', 'Sun')
    data['WEATHER_SLOT_3'] = os.getenv('weatherSlot3', 'Humidity')
    data['WEATHER_SLOT_4'] = os.getenv('weatherSlot4', 'Last updated')

    return data