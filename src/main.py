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

# --- Debug Logging Function ---
def debug_log(message):
    if config.get('DEBUG', False):
        print(message)

# Log all environment variables at startup
debug_log("--- Configuration Loaded ---")
for key, value in config.items():
    debug_log(f"ENV: {key} = {value}")
debug_log("--------------------------\n")

# --- Timezone Setup ---
try:
    TIMEZONE = ZoneInfo(config.get("TZ", "UTC"))
    debug_log(f"Timezone set to: {TIMEZONE}")
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
        "response. Be sure to use " + ("metric" if config['UNITS'] == 'metric' else "imperial") + " and not Kelvins."
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

# --- Performance Optimization Globals ---
animation_viewport = None
animation_viewport_draw = None
cached_date_str = ""
cached_date_day = -1


# --- Caching Functions ---
def save_cache(filepath, data):
    """Saves data to a JSON cache file."""
    try:
        with open(filepath, 'w') as f:
            json.dump(data, f)
        debug_log(f"Saved data to cache: {filepath}")
    except IOError as e:
        print(f"Error saving cache file {filepath}: {e}")

def load_cache(filepath):
    """Loads data from a JSON cache file."""
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
            debug_log(f"Loaded data from cache: {filepath}")
            return data
    except (IOError, json.JSONDecodeError) as e:
        print(f"Error loading or parsing cache file {filepath}: {e}")
        return None

def cachedBitmapText(text, font):
    """Caches and returns the bitmap representation of a text string."""
    nameTuple = font.getname()
    fontKey = ''.join(nameTuple)
    key = text + fontKey
    if key in bitmapRenderCache:
        pre = bitmapRenderCache[key]
        return pre['txt_width'], pre['txt_height'], pre['bitmap']
    else:
        _, _, txt_width, txt_height = font.getbbox(text)
        bitmap = Image.new('L', [txt_width, txt_height], color=0)
        pre_render_draw = ImageDraw.Draw(bitmap)
        pre_render_draw.text((0, 0), text=text, font=font, fill=255)
        bitmapRenderCache[key] = {'bitmap': bitmap, 'txt_width': txt_width, 'txt_height': txt_height}
        return txt_width, txt_height, bitmap

# --- Display Setup ---
try:
    serial = spi(port=0, device=0, gpio_DC=config.get('DC_PIN', 24), gpio_RST=config.get('RST_PIN', 25))
    device = ssd1322(serial, rotate=config.get('ROTATION', 0))
except Exception as e:
    print(f"Fatal Error initializing display: {e}")
    exit()


# --- Font Setup ---
def make_font(name, size):
    """Helper function to load a font file from the 'fonts' directory."""
    font_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'fonts', name))
    return ImageFont.truetype(font_path, size, layout_engine=ImageFont.Layout.BASIC)

try:
    font_small = make_font("Dot Matrix Regular4.ttf", 10)
    font_medium = make_font("Dot Matrix Bold.ttf", 10)
    font_medium_tall = make_font("Dot Matrix Bold.ttf", 10)
    font_large = make_font("Dot Matrix Bold.ttf", 16)
    font_numeric = make_font("Dot Matrix Bold Tall3.ttf", 22)
except IOError:
    print("Custom fonts not found, falling back to default.")
    font_large, font_medium, font_small, font_medium_tall = [ImageFont.load_default()]*4

# --- Pre-calculate maximum clock width for stable layout ---
hm_width_max, _, _ = cachedBitmapText("23:59", font_large)
sec_width_max, _, _ = cachedBitmapText(":59", font_medium)
MAX_CLOCK_WIDTH = hm_width_max + sec_width_max

def get_current_time():
    """Returns the current time, or a debug time if specified in the config."""
    if config.get('DEBUG') and config.get('DEBUG_DATE'):
        try:
            return datetime.fromisoformat(config.get('DEBUG_DATE')).replace(tzinfo=TIMEZONE)
        except (ValueError, TypeError):
            print(f"ERROR: Could not parse DEBUG_DATE. Using current time.")
    return datetime.now(tz=TIMEZONE)

def get_current_timestamp():
    """Returns the current timestamp, or a debug timestamp."""
    if config.get('DEBUG') and config.get('DEBUG_DATE'):
        return get_current_time().timestamp()
    return time.time()

def _make_request_with_retries(url, method='GET', **kwargs):
    """Makes an HTTP request with a retry mechanism."""
    for attempt in range(config.get('MAX_RETRIES', 3)):
        try:
            response = requests.request(method, url, timeout=10, **kwargs)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            print(f"Request failed: {e}")
            if attempt < config.get('MAX_RETRIES', 3) - 1:
                time.sleep(config.get('RETRY_DELAY_SECONDS', 2))
    return None

def get_weather():
    """Fetches current weather data from OpenWeatherMap API."""
    global last_update_time
    url = f"http://api.openweathermap.org/data/2.5/weather?q={config['LOCATION']}&appid={config['API_KEY']}&units={config['UNITS']}"
    response = _make_request_with_retries(url)
    if response:
        last_update_time = get_current_timestamp()
        return response.json()
    return None


def get_forecast():
    """Fetches 5-day forecast data from OpenWeatherMap API."""
    url = f"http://api.openweathermap.org/data/2.5/forecast?q={config['LOCATION']}&appid={config['API_KEY']}&units={config['UNITS']}"
    response = _make_request_with_retries(url)
    return response.json() if response else None


def get_ai_weather_tip():
    """Fetches a weather tip from the OpenWeatherMap AI Assistant."""
    if not config.get('API_KEY') or config.get('API_KEY') == 'key_not_set':
        return "Weather tip currently unavailable."

    prompt = AI_PROMPT_TEMPLATE.format(location=config['LOCATION'], other_location=config.get('OTHER_LOCATION'))
    url = "https://api.openweathermap.org/assistant/session"
    headers = {"Content-Type": "application/json", "X-Api-Key": config['API_KEY']}
    response = _make_request_with_retries(url, method='POST', headers=headers, json={"prompt": prompt})

    return response.json()['answer'].strip() if response and 'answer' in response.json() else "Weather tip currently unavailable."


def get_icon_from_url(url):
    """Downloads an image from a URL and returns a PIL Image object."""
    response = _make_request_with_retries(url, stream=True)
    if response:
        try:
            return Image.open(io.BytesIO(response.content)).convert("RGBA")
        except:
            return None
    return None

def get_umbrella_icon():
    """Loads and prepares the umbrella icon from a local file."""
    try:
        icon_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'icons', 'brolly.png'))
        icon = Image.open(icon_path).convert("RGBA")
        icon.thumbnail((16, 16), Image.LANCZOS)
        return icon
    except:
        return None


def format_time_ago(timestamp):
    """Formats a timestamp into a compact 'time ago' string."""
    if timestamp == 0: return ""
    diff = get_current_timestamp() - timestamp
    if diff < 60: return "Just updated"
    if diff < 3600: return f"Updated {int(diff / 60)}m ago"
    if diff < 86400: return f"Updated {int(diff / 3600)}h ago"
    return f"Updated {int(diff / 86400)}d ago"


def get_ordinal_suffix(day):
    """Returns the ordinal suffix for a given day (st, nd, rd, th)."""
    return "th" if 11 <= day <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')

def is_within_operating_hours(operating_hours_str):
    """Checks if the current time is within the specified operating hours."""
    try:
        start_hour, end_hour = map(int, operating_hours_str.split('-'))
        now = get_current_time()
        if start_hour <= end_hour:
            return start_hour <= now.hour < end_hour
        else:
            return now.hour >= start_hour or now.hour < end_hour
    except:
        print("[Operating Hours] ERROR: Invalid format. Defaulting to ON.")
        return True

def render_current_weather_bg(weather_data):
    """Pre-renders the background image for the current weather view."""
    background = Image.new("1", device.size, "black")
    draw = ImageDraw.Draw(background)
    icon_code = weather_data.get('weather', [{}])[0].get('icon')
    if icon_code:
        icon_url = f"https://openweathermap.org/img/wn/{icon_code}@2x.png"
        icon_image = get_icon_from_url(icon_url)
        if icon_image:
            icon_image.thumbnail((64, 64), Image.LANCZOS)
            background.paste(icon_image, (4 + (device.width - 64) // 2, 9), icon_image)
    draw_current_weather_info(draw, weather_data)
    return background


def get_weather_slot_data(slot_name, weather_data):
    """Returns the formatted string for a given weather data slot."""
    if slot_name == "Desc": return weather_data.get('weather', [{}])[0].get('description', 'N/A').title()
    if slot_name == "Sun":
        now_ts = get_current_timestamp()
        sys = weather_data.get('sys', {})
        sunrise = sys.get('sunrise', now_ts)
        sunset = sys.get('sunset', now_ts)
        next_event = "Sunset" if now_ts < sunset else "Sunrise"
        next_ts = sunset if now_ts < sunset else sunrise
        return f"{next_event}: {datetime.fromtimestamp(next_ts).strftime('%H:%M')}"
    if slot_name == "Humidity": return f"Humidity: {weather_data.get('main', {}).get('humidity', 0)}%"
    if slot_name == "Last updated": return format_time_ago(last_update_time)
    if slot_name == "Pressure": return f"Pressure: {weather_data.get('main', {}).get('pressure', 0)} hPa"
    if slot_name == "Location": return weather_data.get('name', 'N/A')
    if slot_name == "Wind speed":
        unit = "m/s" if config['UNITS'] == 'metric' else "mph"
        return f"Wind: {weather_data.get('wind', {}).get('speed', 0)} {unit}"
    if slot_name == "Wind direction": return f"Wind Dir: {weather_data.get('wind', {}).get('deg', 0)}°"
    return ""


def render_forecast_bg(forecast_data):
    """Pre-renders the background image for the forecast view."""
    background = Image.new("1", device.size, "black")
    draw = ImageDraw.Draw(background)

    forecast_icons_cache.clear()
    for index in [0, 2, 4, 7]:
        if index < len(forecast_data['list']):
            forecast = forecast_data['list'][index]
            icon_code = forecast.get('weather', [{}])[0].get('icon')
            if icon_code:
                icon = get_icon_from_url(f"https://openweathermap.org/img/wn/{icon_code}.png")
                if icon:
                    icon.thumbnail((28, 28), Image.LANCZOS)
                    forecast_icons_cache.append(icon)

    draw_forecast_weather_info(draw, forecast_data, forecast_icons_cache)
    return background


def draw_current_weather_info(draw, weather_data):
    """Extracts and draws current weather info."""
    temp = f"{weather_data.get('main', {}).get('temp', 0):.0f}{config['TEMP_UNIT']}"
    temp_min = f"{weather_data.get('main', {}).get('temp_min', 0):.0f}{config['TEMP_UNIT']}"
    temp_max = f"{weather_data.get('main', {}).get('temp_max', 0):.0f}{config['TEMP_UNIT']}"

    temp_w, temp_h, temp_bmp = cachedBitmapText(temp, font_numeric)
    draw.bitmap((0, 26), temp_bmp, fill="white")
    _, _, temp_max_bmp = cachedBitmapText(temp_max, font_small)
    draw.bitmap((temp_w + 5, 26), temp_max_bmp, fill="white")
    _, min_h, temp_min_bmp = cachedBitmapText(temp_min, font_small)
    draw.bitmap((temp_w + 5, 26 + temp_h - min_h), temp_min_bmp, fill="white")

    slot_1_str = get_weather_slot_data(config['WEATHER_SLOT_1'], weather_data)
    if slot_1_str:
        _, _, bmp = cachedBitmapText(slot_1_str, font_small)
        draw.bitmap((0, 26 + temp_h + 4), bmp, fill="white")

    slot_2_str = get_weather_slot_data(config['WEATHER_SLOT_2'], weather_data)
    if slot_2_str:
        w, _, bmp = cachedBitmapText(slot_2_str, font_small)
        draw.bitmap((device.width - w, 26), bmp, fill="white")

    slot_3_str = get_weather_slot_data(config['WEATHER_SLOT_3'], weather_data)
    if slot_3_str:
        w, _, bmp = cachedBitmapText(slot_3_str, font_small)
        draw.bitmap((device.width - w, 39), bmp, fill="white")

    slot_4_str = get_weather_slot_data(config['WEATHER_SLOT_4'], weather_data)
    if slot_4_str:
        w, _, bmp = cachedBitmapText(slot_4_str, font_small)
        draw.bitmap((device.width - w, 26 + temp_h + 4), bmp, fill="white")


def draw_forecast_weather_info(draw, forecast_data, icons):
    """Extracts and draws forecast info."""
    if not forecast_data or 'list' not in forecast_data or len(forecast_data['list']) < 8:
        _, _, bmp = cachedBitmapText("Forecast data unavailable", font_small)
        draw.bitmap((10, 25), bmp, fill="white")
        return

    for i, index in enumerate([0, 2, 4, 7]):
        forecast = forecast_data['list'][index]
        center_x = [20, 91, 161, 232][i]

        time_str = datetime.fromtimestamp(forecast['dt']).strftime("%H:%M")
        w, _, bmp = cachedBitmapText(time_str, font_small)
        draw.bitmap((center_x - w // 2, 26), bmp, fill="white")

        icon = icons[i] if i < len(icons) else None
        temp_str = f"{forecast['main']['temp']:.0f}{config['TEMP_UNIT']}"
        temp_w, temp_h, temp_bmp = cachedBitmapText(temp_str, font_small)

        block_w = (icon.width if icon else 0) + 2 + temp_w
        block_x = center_x - block_w // 2

        if icon: draw.bitmap((block_x, 34), icon, fill="white")
        temp_y = 34 + (28 // 2) - (temp_h // 2)
        draw.bitmap((block_x + (icon.width if icon else 0) + 2, temp_y), temp_bmp, fill="white")

def get_display_date_str(now, is_umbrella_visible):
    """Calculates the longest possible date string that fits on the screen."""
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

    for date_format in date_formats:
        date_width, _, _ = cachedBitmapText(date_format, font_medium_tall)
        date_start_x = device.width - date_width
        available_gap = date_start_x - MAX_CLOCK_WIDTH
        icon_space_needed = (umbrella_icon.width + 10) if is_umbrella_visible else 0
        if available_gap >= icon_space_needed:
            final_date_str = date_format
            break

    return final_date_str


def draw_frame_content(draw, background, is_transitioning=False):
    """Draws all elements for a single frame onto the provided draw context."""
    global pixelsUp, hasElevated, scroll_x, animation_pause_timer, scroll_completion_event_fired
    global animation_viewport, animation_viewport_draw, cached_date_str, cached_date_day

    is_forecast_view = background is forecast_bg
    now = get_current_time()

    if background: draw.bitmap((0, 0), background, fill="white")

    # --- Draw Clock ---
    hm_width, hm_height, hm_bitmap = cachedBitmapText(now.strftime("%H:%M"), font_large)
    sec_width, _, sec_bitmap = cachedBitmapText(now.strftime(":%S"), font_medium)
    draw.bitmap((0, 0), hm_bitmap, fill="white")
    draw.bitmap((hm_width, 1), sec_bitmap, fill="white")

    # --- Draw Header Right Side (Date/Title, Icon) and AI Tip ---
    right_content_str = ""
    is_umbrella_visible = config.get('SHOW_UMBRELLA_ICON') and umbrella_icon and ('umbrella' in ai_tip_cache.lower() or config.get('DEBUG'))

    if is_forecast_view:
        right_content_str = f"{config['LOCATION']} 24h forecast"
    else:
        if now.day != cached_date_day:
            cached_date_str = get_display_date_str(now, is_umbrella_visible)
            cached_date_day = now.day
        right_content_str = cached_date_str

    content_width, content_height, content_bitmap = cachedBitmapText(right_content_str, font_medium_tall)
    content_x = device.width - content_width
    draw.bitmap((content_x, 1), content_bitmap, fill="white")

    if is_umbrella_visible:
        available_gap = content_x - MAX_CLOCK_WIDTH
        icon_x = MAX_CLOCK_WIDTH + (available_gap - umbrella_icon.width) // 2
        icon_y = (hm_height - umbrella_icon.height) // 2
        draw.bitmap((icon_x, icon_y), umbrella_icon, fill="white")

    # --- Animate and Draw AI Tip ---
    if ai_tip_cache:
        tip_width, tip_height, tip_bitmap = cachedBitmapText(ai_tip_cache, font_small)
        if animation_viewport is None or animation_viewport.size != (tip_width, tip_height):
            animation_viewport = Image.new("1", (tip_width, tip_height), "black")
            animation_viewport_draw = ImageDraw.Draw(animation_viewport)

        tip_x = content_x
        final_tip_y = 1 + content_height + 2

        if not is_transitioning:
            if pixelsUp < tip_height:
                pixelsUp += 1
            elif not hasElevated:
                hasElevated = 1
                animation_pause_timer = time.time()

            if hasElevated and time.time() - animation_pause_timer > config.get('SCROLL_PAUSE_SECONDS', 2) and not scroll_completion_event_fired:
                scroll_x += 1
                if scroll_x > tip_width: scroll_completion_event_fired = True

        animation_viewport_draw.rectangle(((0, 0), animation_viewport.size), fill="black")
        animation_viewport.paste(tip_bitmap, (-scroll_x, tip_height - pixelsUp))
        draw.bitmap((tip_x, final_tip_y), animation_viewport, fill="white")


def display_dynamic_elements(background):
    """Wrapper to draw a normal frame."""
    with canvas(device) as draw:
        draw_frame_content(draw, background, is_transitioning=False)


def main():
    """Main function to run the weather display loop."""
    print("Starting weather display...")
    if config.get('API_KEY') == 'key_not_set' or not config.get('API_KEY'):
        print("ERROR: openWeatherApiKey is not set.")
        with canvas(device) as draw:
            _, _, bmp = cachedBitmapText("API Key Not Set!", font_medium)
            draw.bitmap((10, 20), bmp, fill="white")
        time.sleep(1800)
        return

    global weather_data_cache, forecast_data_cache, last_update_time, current_weather_bg, forecast_bg, ai_tip_cache, pixelsUp, hasElevated, scroll_x, scroll_completion_event_fired, transition_state, transition_start_time, umbrella_icon

    umbrella_icon = get_umbrella_icon()
    cached_info = load_cache(WEATHER_CACHE_FILE)
    if cached_info:
        weather_data_cache, last_update_time, ai_tip_cache = cached_info.get('data'), cached_info.get('timestamp', 0), cached_info.get('ai_tip', 'Weather tip unavailable.')

    if config.get('FORCE_REFRESH_ON_START') or not cached_info:
        last_update_time = 0

    forecast_data_cache = load_cache(FORECAST_CACHE_FILE)
    if weather_data_cache: current_weather_bg = render_current_weather_bg(weather_data_cache)
    if forecast_data_cache: forecast_bg = render_forecast_bg(forecast_data_cache)

    regulator = framerate_regulator(fps=config.get('FPS', 80))
    log_timer, is_showing_forecast = time.time(), False
    scroll_off_event_time, last_view_switch_time = 0, time.time()
    is_active = None

    while True:
        should_be_active = is_within_operating_hours(config.get('OPERATING_HOURS', '0-24'))
        if should_be_active != is_active:
            is_active = should_be_active
            # Simplified on/off logic for now
        if not is_active:
            time.sleep(60)
            continue

        with regulator:
            if get_current_timestamp() - last_update_time > config.get('UPDATE_INTERVAL_SECONDS', 1800):
                weather_data, forecast_data = get_weather(), get_forecast()
                if weather_data:
                    weather_data_cache = weather_data
                    new_tip = get_ai_weather_tip()
                    if new_tip and new_tip != ai_tip_cache and "unavailable" not in new_tip:
                        ai_tip_cache = new_tip
                        pixelsUp, hasElevated, scroll_x, scroll_completion_event_fired = 0, 0, 0, False

                    data_to_save = {'timestamp': last_update_time, 'data': weather_data}
                    if "unavailable" not in ai_tip_cache and "Fetching" not in ai_tip_cache:
                        data_to_save['ai_tip'] = ai_tip_cache
                    save_cache(WEATHER_CACHE_FILE, data_to_save)
                    current_weather_bg = render_current_weather_bg(weather_data)

                if forecast_data:
                    forecast_data_cache = forecast_data
                    save_cache(FORECAST_CACHE_FILE, forecast_data)
                    forecast_bg = render_forecast_bg(forecast_data)

            duration = config.get('DISPLAY_DURATION', 0)
            if duration > 0 and time.time() - last_view_switch_time > duration and transition_state is None:
                transition_state, transition_start_time, last_view_switch_time = 'out', time.time(), time.time()
            elif duration == 0:
                if scroll_completion_event_fired and scroll_off_event_time == 0: scroll_off_event_time = time.time()
                if scroll_off_event_time != 0 and time.time() - scroll_off_event_time > config.get('SCROLL_OFF_SCREEN_WAIT_SECONDS', 1) and transition_state is None:
                    transition_state, transition_start_time = 'out', time.time()
                    scroll_off_event_time, scroll_completion_event_fired = 0, False

            if transition_state is not None:
                elapsed = time.time() - transition_start_time
                duration = config.get('TRANSITION_DURATION_SECONDS', 0.2)
                if elapsed >= duration:
                    if transition_state == 'out':
                        is_showing_forecast = not is_showing_forecast
                        pixelsUp, hasElevated, scroll_x, scroll_completion_event_fired = 0, 0, 0, False
                        transition_state = 'in' if config.get('TRANSITION_EFFECT') in ['wipe', 'blink'] else None
                        transition_start_time = time.time()
                    else:
                        transition_state = None
                else:
                    with canvas(device) as draw:
                        progress = elapsed / duration
                        effect = config.get('TRANSITION_EFFECT')
                        is_out = transition_state == 'out'

                        # Determine which background to draw for the transition frame
                        bg_to_draw = None
                        if is_out:
                            # Fading out the CURRENT view
                            bg_to_draw = forecast_bg if is_showing_forecast else current_weather_bg
                        else: # 'in'
                            # Fading in the NEW view
                            bg_to_draw = forecast_bg if is_showing_forecast else current_weather_bg

                        # Draw all content for the frame first
                        draw_frame_content(draw, bg_to_draw, is_transitioning=True)

                        # Then, draw the transition effect on top
                        center_x, center_y = device.width // 2, device.height // 2
                        if effect == 'wipe':
                            if is_out:
                                curtain_width = int(device.width * progress)
                                draw.rectangle([(device.width - curtain_width, 0), device.size], fill="black")
                            else: # 'in'
                                curtain_x = int(device.width * progress)
                                draw.rectangle([(curtain_x, 0), device.size], fill="black")
                        elif effect == 'blink':
                            p = 1.0 - progress if is_out else progress
                            w, h = int(center_x * p), int(center_y * p)
                            draw.rectangle([(0,0), (device.width, center_y - h)], fill="black")
                            draw.rectangle([(0, center_y + h), device.size], fill="black")
                            draw.rectangle([(0, center_y - h), (center_x - w, center_y + h)], fill="black")
                            draw.rectangle([(center_x + w, center_y-h), (device.width, center_y+h)], fill="black")
            else:
                display_dynamic_elements(forecast_bg if is_showing_forecast else current_weather_bg)

            if time.time() - log_timer > 30:
                print(f"Framerate: {regulator.effective_FPS():.2f} FPS")
                log_timer = time.time()

if __name__ == "__main__":
    main()

