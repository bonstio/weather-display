import os
import time
from datetime import datetime
import requests
import io
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from PIL import Image, ImageDraw, ImageFont
from luma.core.sprite_system import framerate_regulator
from luma.core.interface.serial import spi
from luma.core.render import canvas
from luma.oled.device import ssd1322
from config import loadConfig

# --- Configuration ---
config = loadConfig()

# Log all environment variables at startup
print("--- Configuration Loaded ---")
for key, value in config.items():
    print(f"ENV: {key} = {value}")
print("--------------------------\n")

# --- Timezone Setup ---
try:
    TIMEZONE = ZoneInfo(config.get("TZ", "UTC"))
    print(f"Timezone set to: {TIMEZONE}")
except ZoneInfoNotFoundError:
    print(f"WARNING: Timezone '{config.get('TZ')}' not found. Defaulting to UTC.")
    TIMEZONE = ZoneInfo("UTC")

# --- Day Abbreviation Mapping ---
DAY_ABBREVIATIONS = {
    "Monday": "Mon",
    "Tuesday": "Tues",
    "Wednesday": "Weds",
    "Thursday": "Thur",
    "Friday": "Fri",
    "Saturday": "Sat",
    "Sunday": "Sun"
}

# --- AI Prompt Configuration ---
_location_text = "around {location}"
if config.get('OTHER_LOCATION'):
    _location_text = "between {location} and {other_location}"

AI_PROMPT_TEMPLATE = "I'm travelling " + _location_text + " today. " + (
    "Give me a concise, single-line weather tip under 100 characters which covers the weather in the "
    "aforementioned location(s). Focus on significant changes in temperature or wind. If the response contains the "
    "word 'umbrella' I will inform the users of the chance of precipitation so include that word ONLY if it will rain."
    "If it will be especially colder or warmer than the previous day, mention that. Always mention the location(s) in the "
    "response."
)

# --- Cache Configuration ---
CACHE_DIR = "cache"
WEATHER_CACHE_FILE = os.path.join(CACHE_DIR, "weather_data.json")
FORECAST_CACHE_FILE = os.path.join(CACHE_DIR, "forecast_data.json")
os.makedirs(CACHE_DIR, exist_ok=True) # Ensure cache directory exists

# Global variables to store the timestamp and cached data
last_update_time = 0
current_weather_bg = None
forecast_bg = None
weather_data_cache = None
forecast_data_cache = None
forecast_icons_cache = []
bitmapRenderCache = {}
ai_tip_cache = "Fetching weather tip..."

pixelsUp = 0
hasElevated = 0
scroll_x = 0
animation_pause_timer = 0
scroll_completion_event_fired = False
transition_state = None  # Can be None, 'out', or 'in'
transition_start_time = 0
umbrella_icon = None


# --- Caching Functions ---
def save_cache(filepath, data):
    """Saves data to a JSON cache file."""
    try:
        with open(filepath, 'w') as f:
            json.dump(data, f)
        if config.get('DEBUG', False):
            print(f"Saved data to cache: {filepath}")
    except IOError as e:
        print(f"Error saving cache file {filepath}: {e}")

def load_cache(filepath):
    """Loads data from a JSON cache file."""
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
            if config.get('DEBUG', False):
                print(f"Loaded data from cache: {filepath}")
            return data
    except (IOError, json.JSONDecodeError) as e:
        print(f"Error loading or parsing cache file {filepath}: {e}")
        return None

def cachedBitmapText(text, font):
    """Caches and returns the bitmap representation of a text string."""
    # cache the bitmap representation of the stations string
    nameTuple = font.getname()
    fontKey = ''
    for item in nameTuple:
        fontKey = fontKey + item
    key = text + fontKey
    if key in bitmapRenderCache:
        # found in cache; re-use it
        pre = bitmapRenderCache[key]
        bitmap = pre['bitmap']
        txt_width = pre['txt_width']
        txt_height = pre['txt_height']
    else:
        # not cached; create a new image containing the string as a monochrome bitmap
        _, _, txt_width, txt_height = font.getbbox(text)
        bitmap = Image.new('L', [txt_width, txt_height], color=0)
        pre_render_draw = ImageDraw.Draw(bitmap)
        pre_render_draw.text((0, 0), text=text, font=font, fill=255)
        # save to render cache
        bitmapRenderCache[key] = {'bitmap': bitmap, 'txt_width': txt_width, 'txt_height': txt_height}
    return txt_width, txt_height, bitmap

# --- Display Setup ---
try:
    serial = spi(port=0, device=0, gpio_DC=config.get('DC_PIN', 24), gpio_RST=config.get('RST_PIN', 25))
    device = ssd1322(serial, rotate=config.get('ROTATION', 0))
except Exception as e:
    print(f"Fatal Error initializing display: {e}")
    # Exit if the display cannot be initialized.
    exit()


# --- Font Setup ---
def make_font(name, size):
    """Helper function to load a font file from the 'fonts' directory."""
    font_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), 'fonts', name)
    )
    return ImageFont.truetype(font_path, size, layout_engine=ImageFont.Layout.BASIC)

try:
    font_small = make_font("Dot Matrix Regular4.ttf", 10)
    font_medium = make_font("Dot Matrix Bold.ttf", 10)
    font_medium_tall = make_font("Dot Matrix Bold.ttf", 10)
    font_large = make_font("Dot Matrix Bold.ttf", 16)
    font_numeric = make_font("Dot Matrix Bold Tall3.ttf", 22)
except IOError:
    print("Custom fonts not found, falling back to default.")
    font_large = ImageFont.load_default()
    font_medium = ImageFont.load_default()
    font_small = ImageFont.load_default()

# --- Pre-calculate maximum clock width for stable layout ---
hm_width_max, _, _ = cachedBitmapText("23:59", font_large)
sec_width_max, _, _ = cachedBitmapText(":59", font_medium)
MAX_CLOCK_WIDTH = hm_width_max + sec_width_max

def get_current_time():
    """Returns the current time, or a debug time if specified in the config."""
    is_debug_mode = config.get('DEBUG') is True
    debug_date_str = config.get('DEBUG_DATE')

    if is_debug_mode and debug_date_str:
        try:
            # This log will now appear once to confirm debug mode is active.
            if not hasattr(get_current_time, "has_logged_debug"):
                print(f"DEBUG MODE ACTIVE: Using fixed date from config: {debug_date_str}")
                get_current_time.has_logged_debug = True
            return datetime.fromisoformat(debug_date_str).replace(tzinfo=TIMEZONE)
        except (ValueError, TypeError):
            print(f"ERROR: Could not parse DEBUG_DATE '{debug_date_str}'. Using current time.")
    else:
        # This new block will explain why debug mode is off.
        if not hasattr(get_current_time, "has_logged_normal"):
            print("DEBUG MODE OFF. Using real time.")
            if not is_debug_mode:
                print("-> Reason: 'DEBUG' flag is not set to True in config.")
            if not debug_date_str:
                print("-> Reason: 'DEBUG_DATE' is not set in config.")
            get_current_time.has_logged_normal = True

    # Fallback to the real current time.
    return datetime.now(tz=TIMEZONE)

def get_current_timestamp():
    """Returns the current timestamp, or a debug timestamp if specified in the config."""
    if config.get('DEBUG') and config.get('DEBUG_DATE'):
        return get_current_time().timestamp()
    return time.time()

def get_weather():
    """Fetches current weather data from OpenWeatherMap API."""
    global last_update_time
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={config['LOCATION']}&appid={config['API_KEY']}&units={config['UNITS']}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        last_update_time = get_current_timestamp()  # Record the time of successful update
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching current weather: {e}")
        return None


def get_forecast():
    """Fetches 5-day forecast data from OpenWeatherMap API."""
    try:
        url = f"http://api.openweathermap.org/data/2.5/forecast?q={config['LOCATION']}&appid={config['API_KEY']}&units={config['UNITS']}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching forecast: {e}")
        return None


def get_ai_weather_tip():
    """Fetches a weather tip from the OpenWeatherMap AI Assistant."""
    if not config.get('API_KEY') or config.get('API_KEY') == 'key_not_set':
        return "Weather tip currently unavailable." # Don't make request if API key isn't set.

    prompt = AI_PROMPT_TEMPLATE.format(location=config['LOCATION'], other_location=config.get('OTHER_LOCATION'))

    try:
        url = "https://api.openweathermap.org/assistant/session"
        headers = {"Content-Type": "application/json", "X-Api-Key": config['API_KEY']}
        json_payload = {"prompt": prompt}

        if config.get('DEBUG', False):
            print(f"Sending AI Prompt: {prompt}")

        response = requests.post(url, headers=headers, json=json_payload, timeout=20)
        response.raise_for_status()
        data = response.json()

        if 'answer' in data:
            tip = data['answer'].strip()
            print(f"AI Weather Tip: {tip}")
            return tip
        else:
            print("AI Weather Tip: 'answer' field not found in the response.")
            return "Weather tip currently unavailable."

    except requests.exceptions.RequestException as e:
        print(f"Error fetching AI weather tip: {e}")
        return "Weather tip currently unavailable."


def get_icon_from_url(url):
    """Downloads an image from a URL and returns a PIL Image object."""
    try:
        response = requests.get(url, stream=True, timeout=10)
        response.raise_for_status()
        image_bytes = io.BytesIO(response.content)
        return Image.open(image_bytes).convert("RGBA")
    except requests.exceptions.RequestException as e:
        print(f"Error downloading icon: {e}")
        return None


def get_umbrella_icon():
    """Loads and prepares the umbrella icon from a local file."""
    try:
        # Construct the path to the icon in the 'icons' directory
        icon_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), 'icons', 'brolly.png')
        )
        icon_image = Image.open(icon_path).convert("RGBA")
        if icon_image:
            # Resize the icon to fit the display layout
            icon_image.thumbnail((16, 16), Image.LANCZOS)
            return icon_image
        return None
    except Exception as e:
        print(f"Error getting umbrella icon: {e}")
        return None


def format_time_ago(timestamp):
    """Formats a timestamp into a compact 'time ago' string."""
    if timestamp == 0:
        return ""
    diff = get_current_timestamp() - timestamp
    if diff < 60: return "Just updated"
    if diff < 3600: return f"Updated {int(diff / 60)}m ago"
    if diff < 86400: return f"Updated {int(diff / 3600)}h ago"
    return f"Updated {int(diff / 86400)}d ago"


def get_ordinal_suffix(day):
    """Returns the ordinal suffix for a given day (st, nd, rd, th)."""
    return "th" if 11 <= day <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')

def is_within_operating_hours(operating_hours_str):
    """
    Checks if the current time is within the specified operating hours.
    Handles normal (e.g., 8-22) and overnight (e.g., 22-6) schedules.
    """
    try:
        start_hour, end_hour = map(int, operating_hours_str.split('-'))
        now = get_current_time()

        if start_hour <= end_hour:
            # Normal day schedule
            return start_hour <= now.hour < end_hour
        else:
            # Overnight schedule (e.g., 22:00 to 06:00)
            return now.hour >= start_hour or now.hour < end_hour
    except (ValueError, AttributeError):
        # Default to running if the format is incorrect or not set
        print("[Operating Hours] ERROR: Invalid format for OPERATING_HOURS. Defaulting to ON.")
        return True

def render_current_weather_bg(weather_data_to_render):
    """Pre-renders the background image for the current weather view."""
    background = Image.new("1", device.size, "black")
    draw = ImageDraw.Draw(background)
    icon_code = weather_data_to_render.get('weather', [{}])[0].get('icon')
    if icon_code:
        icon_url = f"https://openweathermap.org/img/wn/{icon_code}@2x.png"
        icon_image = get_icon_from_url(icon_url)
        if icon_image:
            # Reverted icon size and position to original
            icon_image.thumbnail((64, 64), Image.LANCZOS)
            icon_x = 4 + (device.width - icon_image.width) // 2
            icon_y = 9 + (device.height - icon_image.height) // 2
            background.paste(icon_image, (icon_x, icon_y), icon_image)
    draw_current_weather_info(draw, weather_data_to_render)
    return background


def get_weather_slot_data(slot_name, weather_data):
    """Returns the formatted string for a given weather data slot."""
    if slot_name == "Desc":
        return weather_data.get('weather', [{}])[0].get('description', 'N/A').title()
    if slot_name == "Sun":
        now_ts = get_current_timestamp()
        sunrise_ts = weather_data.get('sys', {}).get('sunrise', now_ts)
        sunset_ts = weather_data.get('sys', {}).get('sunset', now_ts)
        next_event_label = "Sunset:" if now_ts < sunset_ts else "Sunrise:"
        next_event_ts = sunset_ts if now_ts < sunset_ts else sunrise_ts
        next_event_time = datetime.fromtimestamp(next_event_ts).strftime('%H:%M')
        return f"{next_event_label} {next_event_time}"
    if slot_name == "Humidity":
        return f"Humidity: {weather_data.get('main', {}).get('humidity', 0)}%"
    if slot_name == "Last updated":
        return format_time_ago(last_update_time)
    if slot_name == "Pressure":
        return f"Pressure: {weather_data.get('main', {}).get('pressure', 0)} hPa"
    if slot_name == "Location":
        return weather_data.get('name', 'N/A')
    if slot_name == "Wind speed":
        speed = weather_data.get('wind', {}).get('speed', 0)
        speed_unit = "m/s" if config['UNITS'] == 'metric' else "mph"
        return f"Wind: {speed} {speed_unit}"
    if slot_name == "Wind direction":
        return f"Wind Dir: {weather_data.get('wind', {}).get('deg', 0)}°"
    return ""


def render_forecast_bg(forecast_data_to_render):
    """Pre-renders the background image for the forecast view."""
    background = Image.new("1", device.size, "black")
    draw = ImageDraw.Draw(background)

    forecast_icons_cache.clear()
    forecast_indices = [0, 2, 4, 7]  # Get forecasts for now, 6h, 12h, and 21h
    for index in forecast_indices:
        if index < len(forecast_data_to_render['list']):
            forecast = forecast_data_to_render['list'][index]
            icon_code = forecast.get('weather', [{}])[0].get('icon')
            if icon_code:
                icon_url = f"https://openweathermap.org/img/wn/{icon_code}.png"
                icon = get_icon_from_url(icon_url)
                if icon:
                    icon.thumbnail((28, 28), Image.LANCZOS)
                    forecast_icons_cache.append(icon)

    draw_forecast_weather_info(draw, forecast_data_to_render, forecast_icons_cache)
    return background


def draw_current_weather_info(draw, weather_data):
    """Extracts and draws current weather info to the provided image draw object."""
    temperature = f"{weather_data.get('main', {}).get('temp', 0):.0f}{config['TEMP_UNIT']}"
    temp_min = f"{weather_data.get('main', {}).get('temp_min', 0):.0f}{config['TEMP_UNIT']}"
    temp_max = f"{weather_data.get('main', {}).get('temp_max', 0):.0f}{config['TEMP_UNIT']}"

    # --- Drawing Static Info ---
    temp_width, temp_height, temp_bitmap = cachedBitmapText(temperature, font_numeric)
    draw.bitmap((0, 26), temp_bitmap, fill="yellow")

    _, _, temp_max_bitmap = cachedBitmapText(temp_max, font_small)
    draw.bitmap((temp_width + 5, 26), temp_max_bitmap, fill="yellow")

    _, temp_min_height, temp_min_bitmap = cachedBitmapText(temp_min, font_small)
    draw.bitmap((temp_width + 5, 26 + temp_height - temp_min_height), temp_min_bitmap, fill="yellow")

    # --- Drawing Configurable Slots ---
    slot_1_str = get_weather_slot_data(config['WEATHER_SLOT_1'], weather_data)
    if slot_1_str:
        _, _, slot_1_bitmap = cachedBitmapText(slot_1_str, font_small)
        draw.bitmap((0, 26 + temp_height + 4), slot_1_bitmap, fill="yellow")

    slot_2_str = get_weather_slot_data(config['WEATHER_SLOT_2'], weather_data)
    if slot_2_str:
        slot_2_width, _, slot_2_bitmap = cachedBitmapText(slot_2_str, font_small)
        draw.bitmap((device.width - slot_2_width, 26), slot_2_bitmap, fill="yellow")

    slot_3_str = get_weather_slot_data(config['WEATHER_SLOT_3'], weather_data)
    if slot_3_str:
        slot_3_width, _, slot_3_bitmap = cachedBitmapText(slot_3_str, font_small)
        draw.bitmap((device.width - slot_3_width, 39), slot_3_bitmap, fill="yellow")

    slot_4_str = get_weather_slot_data(config['WEATHER_SLOT_4'], weather_data)
    if slot_4_str:
        slot_4_width, _, slot_4_bitmap = cachedBitmapText(slot_4_str, font_small)
        draw.bitmap((device.width - slot_4_width, 26 + temp_height + 4), slot_4_bitmap, fill="yellow")


def draw_forecast_weather_info(draw, forecast_data, icons):
    """Extracts and draws forecast info in a horizontally centered layout."""
    if not forecast_data or 'list' not in forecast_data or len(forecast_data['list']) < 8:
        _, _, unavailable_bitmap = cachedBitmapText("Forecast data unavailable", font_small)
        draw.bitmap((10, 25), unavailable_bitmap, fill="yellow")
        return

    forecast_indices = [0, 2, 4, 7]
    column_centers = [20, 91, 161, 232] # Pre-defined centers for 4 columns

    for i, forecast_index in enumerate(forecast_indices):
        forecast = forecast_data['list'][forecast_index]

        dt = datetime.fromtimestamp(forecast['dt'])
        time_str = dt.strftime("%H:%M")
        icon = icons[i] if i < len(icons) else None
        temp_str = f"{forecast['main']['temp']:.0f}{config['TEMP_UNIT']}"

        slot_center_x = column_centers[i]

        forecast_icon_size = (28, 28)
        forecast_icon_y = 34

        time_width, _, time_bitmap = cachedBitmapText(time_str, font_small)
        time_x = slot_center_x - (time_width // 2)
        draw.bitmap((time_x, 26), time_bitmap, fill="yellow")

        icon_width = icon.width if icon else 0
        padding = 2
        temp_width, temp_height, temp_bitmap = cachedBitmapText(temp_str, font_small)

        block_width = icon_width + padding + temp_width
        block_start_x = slot_center_x - (block_width // 2)

        if icon:
            draw.bitmap((block_start_x, forecast_icon_y), icon, fill="yellow")

        temp_y = forecast_icon_y + (forecast_icon_size[1] // 2) - (temp_height // 2)
        temp_x = block_start_x + icon_width + padding
        draw.bitmap((temp_x, temp_y), temp_bitmap, fill="yellow")


def display_dynamic_elements(background):
    """Draws only the fast-changing elements on top of a pre-rendered background."""
    global pixelsUp, hasElevated, scroll_x, animation_pause_timer, forecast_bg, scroll_completion_event_fired
    is_forecast_view = background is forecast_bg
    now = get_current_time()

    with canvas(device) as draw:
        if background:
            draw.bitmap((0, 0), background, fill="yellow")

        # --- Draw Clock ---
        hm_str = now.strftime("%H:%M")
        sec_str = now.strftime(":%S")
        hm_width, hm_height, hm_bitmap = cachedBitmapText(hm_str, font_large)
        sec_width, _, sec_bitmap = cachedBitmapText(sec_str, font_medium)
        draw.bitmap((0, 0), hm_bitmap, fill="yellow")
        draw.bitmap((hm_width, 1), sec_bitmap, fill="yellow")


        # --- Draw Header Right Side (Date/Title, Icon) and AI Tip ---
        right_content_str = ""
        right_content_start_x = device.width
        is_umbrella_visible = config.get('SHOW_UMBRELLA_ICON', False) and umbrella_icon and ('umbrella' in ai_tip_cache.lower() or config.get('DEBUG', False))

        if is_forecast_view:
            title_str = f"{config['LOCATION']} 24h forecast"
            title_width, _, title_bitmap = cachedBitmapText(title_str, font_medium_tall)
            title_x = device.width - title_width
            draw.bitmap((title_x, 1), title_bitmap, fill="yellow")

            right_content_start_x = title_x
            right_content_str = title_str

            if is_umbrella_visible:
                available_gap = title_x - MAX_CLOCK_WIDTH
                icon_x = MAX_CLOCK_WIDTH + (available_gap - umbrella_icon.width) // 2
                icon_y = (hm_height - umbrella_icon.height) // 2
                draw.bitmap((icon_x, icon_y), umbrella_icon, fill="yellow")
        else:
            # --- Current Weather View: Adaptive Date and Centered Icon ---
            day = now.day
            suffix = get_ordinal_suffix(day)
            month_name = now.strftime('%B')
            year_str = now.strftime('%Y')
            full_day_name = now.strftime('%A')
            short_day_name = DAY_ABBREVIATIONS.get(full_day_name, '----')

            date_formats = [
                f"{full_day_name}, {day}{suffix} {month_name} {year_str}",
                f"{full_day_name}, {day}{suffix} {month_name}",
                f"{short_day_name}, {day}{suffix} {month_name}"
            ]
            final_date_str = date_formats[-1]
            icon_padding = 10

            for date_format in date_formats:
                date_width, _, _ = cachedBitmapText(date_format, font_medium_tall)
                date_start_x = device.width - date_width
                available_gap = date_start_x - MAX_CLOCK_WIDTH
                icon_space_needed = (umbrella_icon.width + icon_padding) if is_umbrella_visible else 0
                if available_gap >= icon_space_needed:
                    final_date_str = date_format
                    break

            date_width, date_height, date_bitmap = cachedBitmapText(final_date_str, font_medium_tall)
            date_x = device.width - date_width
            draw.bitmap((date_x, 1), date_bitmap, fill="yellow")

            right_content_start_x = date_x
            right_content_str = final_date_str

            if is_umbrella_visible:
                available_gap = date_x - MAX_CLOCK_WIDTH
                icon_x = MAX_CLOCK_WIDTH + (available_gap - umbrella_icon.width) // 2
                icon_y = (hm_height - umbrella_icon.height) // 2
                draw.bitmap((icon_x, icon_y), umbrella_icon, fill="yellow")

        # --- Animate and Draw AI Tip (on BOTH views) ---
        if ai_tip_cache:
            tip_width, tip_height, tip_bitmap = cachedBitmapText(ai_tip_cache, font_small)
            tip_x = right_content_start_x
            _, right_content_height, _ = cachedBitmapText(right_content_str, font_medium_tall)
            final_tip_y = 1 + right_content_height + 2
            travel_distance = tip_height
            animation_complete = (pixelsUp >= travel_distance)

            if not hasElevated and not animation_complete:
                if pixelsUp == 0:
                    print("LOG: Starting reveal animation for AI tip.")
                pixelsUp += 1
            elif animation_complete:
                if not hasElevated:
                    hasElevated = 1
                    animation_pause_timer = time.time()
                pixelsUp = travel_distance

            if hasElevated and time.time() - animation_pause_timer > config.get('SCROLL_PAUSE_SECONDS', 2) and not scroll_completion_event_fired:
                scroll_x += 1
                if scroll_x > tip_width:
                    scroll_completion_event_fired = True
                    print("LOG: Marquee scroll completed. Firing event.")

            viewport = Image.new("1", (tip_width, tip_height), "black")
            bitmap_y_in_viewport = tip_height - pixelsUp
            bitmap_x_in_viewport = -scroll_x
            viewport.paste(tip_bitmap, (bitmap_x_in_viewport, bitmap_y_in_viewport))
            draw.bitmap((tip_x, final_tip_y), viewport, fill="yellow")


def main():
    """Main function to run the weather display loop."""
    print("Starting weather display...")
    if config.get('API_KEY') == 'key_not_set' or not config.get('API_KEY'):
        print("ERROR: openWeatherApiKey is not set. Please set it in your environment.")
        with canvas(device) as draw:
            _, _, error_bitmap = cachedBitmapText("API Key Not Set!", font_medium)
            draw.bitmap((10, 20), error_bitmap, fill="yellow")
        time.sleep(config.get('API_ERROR_SLEEP_SECONDS', 30))
        return

    global weather_data_cache, forecast_data_cache, last_update_time, current_weather_bg, forecast_bg, ai_tip_cache, pixelsUp, hasElevated, scroll_x, animation_pause_timer, scroll_completion_event_fired, transition_state, transition_start_time, umbrella_icon

    umbrella_icon = get_umbrella_icon()

    print("Attempting to load data from cache...")
    cached_weather_info = load_cache(WEATHER_CACHE_FILE)
    if cached_weather_info:
        weather_data_cache = cached_weather_info.get('data')
        last_update_time = cached_weather_info.get('timestamp', 0)
        ai_tip_cache = cached_weather_info.get('ai_tip', 'Weather tip unavailable.')

        # --- Logging loaded cache data ---
        print(f"Loaded AI Tip from cache: {ai_tip_cache}")
        last_updated_str = format_time_ago(last_update_time)
        print(f"Last updated string will be: '{last_updated_str}'")

    if config.get('FORCE_REFRESH_ON_START', False):
        print("Force refresh is enabled. Ignoring cache timer for initial fetch.")
        last_update_time = 0

    forecast_data_cache = load_cache(FORECAST_CACHE_FILE)

    if weather_data_cache:
        print("Pre-rendering current weather from cache...")
        current_weather_bg = render_current_weather_bg(weather_data_cache)

    if forecast_data_cache:
        print("Pre-rendering forecast from cache...")
        forecast_bg = render_forecast_bg(forecast_data_cache)

    regulator = framerate_regulator(fps=config.get('FPS', 40))
    log_timer = time.time()
    is_showing_forecast = False
    scroll_off_event_time = 0
    last_view_switch_time = time.time()

    # --- Operating Hours Setup ---
    operating_hours = config.get('OPERATING_HOURS', '0-24')
    print(f"[Operating Hours] Service started. Display will be managed according to schedule: '{operating_hours}'.")
    is_active = None # Use None to force initial state log


    while True:
        # --- Operating Hours Check ---
        should_be_active = is_within_operating_hours(operating_hours)

        if should_be_active != is_active:
            if should_be_active:
                now_str = get_current_time().strftime('%H:%M')
                print(f"[Operating Hours] Current time ({now_str}) is within schedule. Turning display ON.")
            else:
                now_str = get_current_time().strftime('%H:%M')
                print(f"[Operating Hours] Current time ({now_str}) is outside schedule. Turning display OFF.")
                with canvas(device) as draw:
                    draw.rectangle(device.bounding_box, outline="black", fill="black")
            is_active = should_be_active

        if not is_active:
            time.sleep(60)
            continue

        with regulator:
            if get_current_timestamp() - last_update_time > config.get('UPDATE_INTERVAL_SECONDS', 1800):
                print("Fetching new weather and forecast data...")
                weather_data = get_weather()
                forecast_data = get_forecast()

                if weather_data:
                    weather_data_cache = weather_data

                    # Get a new tip.
                    new_ai_tip = get_ai_weather_tip()

                    # --- Animation Reset Logic ---
                    # Only reset the animation if the tip's text has actually changed.
                    if new_ai_tip and new_ai_tip != ai_tip_cache and new_ai_tip != "Weather tip currently unavailable.":
                        print("New AI tip received. Resetting animation.")
                        ai_tip_cache = new_ai_tip

                        # Reset animation state for the new tip
                        pixelsUp = 0
                        hasElevated = 0
                        scroll_x = 0
                        scroll_completion_event_fired = False

                    # Prepare the data payload for caching.
                    data_to_save = {
                        'timestamp': last_update_time,
                        'data': weather_data
                    }

                    # Only add the AI tip to the cache file if it's a valid one.
                    # This prevents overwriting a good cached tip with an error message.
                    if ai_tip_cache != "Weather tip currently unavailable." and ai_tip_cache != "Fetching weather tip...":
                        data_to_save['ai_tip'] = ai_tip_cache

                    save_cache(WEATHER_CACHE_FILE, data_to_save)
                    current_weather_bg = render_current_weather_bg(weather_data)

                if forecast_data:
                    forecast_data_cache = forecast_data
                    save_cache(FORECAST_CACHE_FILE, forecast_data)
                    forecast_bg = render_forecast_bg(forecast_data)

            # --- View Switching Logic ---
            if config.get('DISPLAY_DURATION', 0) > 0:
                # Timer-based switching
                if time.time() - last_view_switch_time > config.get('DISPLAY_DURATION', 0):
                    if transition_state is None:
                        transition_state = 'out'
                        transition_start_time = time.time()
                        last_view_switch_time = time.time() # Reset timer
            else:
                # Marquee-based switching
                if scroll_completion_event_fired and scroll_off_event_time == 0:
                    scroll_off_event_time = time.time()

                if scroll_off_event_time != 0 and time.time() - scroll_off_event_time > config.get('SCROLL_OFF_SCREEN_WAIT_SECONDS', 1):
                    if transition_state is None:
                        transition_state = 'out'
                        transition_start_time = time.time()
                        # Consume the triggers for the transition immediately
                        scroll_off_event_time = 0
                        scroll_completion_event_fired = False

            # --- Drawing Logic ---
            if transition_state == 'out':
                elapsed = time.time() - transition_start_time
                if elapsed >= config.get('TRANSITION_DURATION_SECONDS', 0.2):
                    # Wipe-out is finished: flip the view, reset animation states, and start wipe-in.
                    is_showing_forecast = not is_showing_forecast
                    transition_state = 'in'
                    transition_start_time = time.time() # Reset timer for the next phase

                    print("View changed. Resetting animation for new view.")
                    pixelsUp = 0
                    hasElevated = 0
                    scroll_x = 0
                else:
                    # Draw the wipe-out transition frame
                    with canvas(device) as draw:
                        # Draw the outgoing view's background
                        outgoing_bg = forecast_bg if is_showing_forecast else current_weather_bg
                        if outgoing_bg:
                            draw.bitmap((0, 0), outgoing_bg, fill="yellow")

                        progress = elapsed / config.get('TRANSITION_DURATION_SECONDS', 0.2)
                        if config.get('TRANSITION_EFFECT') == 'wipe':
                            # Calculate and draw the black curtain sweeping left
                            curtain_width = int(device.width * progress)
                            draw.rectangle([(device.width - curtain_width, 0), device.size], fill="black")
                        elif config.get('TRANSITION_EFFECT') == 'blink':
                            # Calculate dimensions for the closing rectangle (iris effect)
                            center_x, center_y = device.width // 2, device.height // 2

                            # Invert progress for closing effect
                            inv_progress = 1.0 - progress

                            half_width = int(center_x * inv_progress)
                            half_height = int(center_y * inv_progress)

                            # Draw four black rectangles to create the closing effect without overlapping
                            draw.rectangle([(0, 0), (device.width, center_y - half_height)], fill="black") # Top
                            draw.rectangle([(0, center_y + half_height), (device.width, device.height)], fill="black") # Bottom
                            draw.rectangle([(0, center_y - half_height), (center_x - half_width, center_y + half_height)], fill="black") # Left
                            draw.rectangle([(center_x + half_width, center_y - half_height), (device.width, center_y + half_height)], fill="black") # Right


            elif transition_state == 'in':
                elapsed = time.time() - transition_start_time
                if elapsed >= config.get('TRANSITION_DURATION_SECONDS', 0.2):
                    # Wipe-in is finished, back to normal display
                    transition_state = None
                else:
                    # Draw the wipe-in transition frame
                    with canvas(device) as draw:
                        # Draw the incoming view's background
                        incoming_bg = forecast_bg if is_showing_forecast else current_weather_bg

                        progress = elapsed / config.get('TRANSITION_DURATION_SECONDS', 0.2)
                        if config.get('TRANSITION_EFFECT') == 'wipe':
                            if incoming_bg:
                                draw.bitmap((0, 0), incoming_bg, fill="yellow")
                            # Calculate and draw the black curtain wiping right
                            curtain_x = int(device.width * progress)
                            draw.rectangle([(curtain_x, 0), device.size], fill="black")
                        elif config.get('TRANSITION_EFFECT') == 'blink':
                            # For the 'in' transition, we create a mask with a growing transparent hole
                            mask = Image.new("1", device.size, "black")
                            mask_draw = ImageDraw.Draw(mask)
                            center_x, center_y = device.width // 2, device.height // 2

                            visible_half_width = int(center_x * progress)
                            visible_half_height = int(center_y * progress)

                            # Draw a white (transparent) rectangle on the black mask
                            mask_draw.rectangle([
                                (center_x - visible_half_width, center_y - visible_half_height),
                                (center_x + visible_half_width, center_y + visible_half_height)
                            ], fill="white")

                            # Create a temporary canvas for the final composition
                            transition_canvas = Image.new("1", device.size, "black")
                            if incoming_bg:
                                transition_canvas.paste(incoming_bg, (0,0), mask)

                            draw.bitmap((0,0), transition_canvas)

            else:
                # Not transitioning, so draw the appropriate view
                if is_showing_forecast:
                    display_dynamic_elements(forecast_bg)
                else:
                    display_dynamic_elements(current_weather_bg)

            if config['DEBUG'] and time.time() - log_timer > config['LOG_INTERVAL_SECONDS']:
                print(f"Framerate: {regulator.effective_FPS():.2f} FPS")
                log_timer = time.time()


if __name__ == "__main__":
    main()

