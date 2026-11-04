"""
Weather tool backed by Open-Meteo (https://open-meteo.com).

Open-Meteo is free and needs no API key. Two requests are made:
1. Geocoding: place name -> coordinates.
2. Forecast: current conditions and a 7-day daily forecast.

Only the place name (and its coordinates) is sent. If the service
can't be reached the tool says so; it never guesses the weather.
"""

import logging
from datetime import date

import httpx

from aurora.config import settings
from aurora.kernel.permissions import Permission
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool

logger = logging.getLogger(__name__)

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes used by Open-Meteo.
WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


class WeatherError(Exception):
    """Raised for problems that should be explained to the user."""


def describe_code(code) -> str:
    return WEATHER_CODES.get(code, "unknown conditions")


class WeatherTool(BaseTool):

    required_permissions = (Permission.NETWORK,)

    def __init__(self, client: httpx.Client | None = None):
        super().__init__("Weather")
        self._client = client

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=settings.WEATHER_TIMEOUT_SECONDS,
                headers={"User-Agent": f"{settings.APP_NAME}/{settings.VERSION}"},
            )
        return self._client

    # --- HTTP -------------------------------------------------------------

    def _get(self, url: str, params: dict) -> dict:
        try:
            response = self.client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            raise WeatherError("The weather service took too long to respond.") from None
        except httpx.HTTPStatusError as error:
            raise WeatherError(
                f"The weather service returned an error ({error.response.status_code})."
            ) from None
        except httpx.HTTPError:
            raise WeatherError("I couldn't reach the weather service.") from None
        except ValueError:
            raise WeatherError("The weather service sent a response I couldn't read.") from None

    def geocode(self, location: str) -> dict:
        data = self._get(
            GEOCODING_URL,
            {"name": location, "count": 1, "language": "en", "format": "json"},
        )
        results = data.get("results") or []
        if not results:
            raise WeatherError(f"I couldn't find a place called \"{location}\".")
        return results[0]

    def forecast(self, place: dict) -> dict:
        return self._get(
            FORECAST_URL,
            {
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                           "weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                         "precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": 7,
            },
        )

    # --- Formatting -------------------------------------------------------

    @staticmethod
    def place_name(place: dict) -> str:
        parts = [place.get("name"), place.get("admin1"), place.get("country")]
        unique = []
        for part in parts:
            if part and part not in unique:
                unique.append(part)
        return ", ".join(unique)

    @staticmethod
    def _day(daily: dict, index: int) -> str:
        low = round(daily["temperature_2m_min"][index])
        high = round(daily["temperature_2m_max"][index])
        text = f"{low}–{high}°C, {describe_code(daily['weather_code'][index])}"
        rain = (daily.get("precipitation_probability_max") or [None] * 7)[index]
        if rain is not None:
            text += f", {rain}% chance of rain"
        return text

    def format(self, place: dict, data: dict, when: str) -> str:
        name = self.place_name(place)
        daily = data["daily"]

        if when == "tomorrow":
            return f"{name}, tomorrow: {self._day(daily, 1)}."

        if when in ("this week", "next week", "this weekend"):
            days = list(enumerate(daily["time"]))
            if when == "this weekend":
                days = [(i, d) for i, d in days if date.fromisoformat(d).weekday() >= 5]
            lines = [
                f"  {date.fromisoformat(d).strftime('%a %d %b')}: {self._day(daily, i)}"
                for i, d in days
            ]
            return f"{name}, {when}:\n" + "\n".join(lines)

        current = data["current"]
        return (
            f"{name}, now: {round(current['temperature_2m'])}°C "
            f"(feels like {round(current['apparent_temperature'])}°C), "
            f"{describe_code(current['weather_code'])}, "
            f"humidity {current['relative_humidity_2m']}%, "
            f"wind {round(current['wind_speed_10m'])} km/h.\n"
            f"Today: {self._day(daily, 0)}."
        )

    # --- Tool -------------------------------------------------------------

    def execute(self, location: str | None = None, when: str = "now", **_) -> ToolResult:

        location = (location or settings.DEFAULT_LOCATION or "").strip()
        if not location:
            return ToolResult(
                False,
                "Which place? For example: \"weather in Pune\". "
                "(You can set DEFAULT_LOCATION in settings.py.)",
            )

        try:
            place = self.geocode(location)
            data = self.forecast(place)
            message = self.format(place, data, when)
        except WeatherError as error:
            # The user already sees the reason in the reply; keep it in the log file only.
            logger.info("Weather lookup failed for %r: %s", location, error)
            return ToolResult(False, f"{error} I won't guess the weather.")
        except (KeyError, IndexError, TypeError):
            logger.exception("Unexpected weather response for %r", location)
            return ToolResult(
                False,
                "The weather service sent data I didn't expect. I won't guess the weather.",
            )

        return ToolResult(
            True,
            f"{message}\n(Source: Open-Meteo)",
            {"place": place, "forecast": data, "when": when},
        )
