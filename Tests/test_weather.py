import httpx
import pytest

from aurora.tool_plugins.weather import WeatherTool

PUNE = {
    "name": "Pune", "admin1": "Maharashtra", "country": "India",
    "latitude": 18.52, "longitude": 73.86,
}

FORECAST = {
    "current": {
        "temperature_2m": 27.4, "apparent_temperature": 29.1,
        "relative_humidity_2m": 60, "weather_code": 2, "wind_speed_10m": 8.2,
    },
    "daily": {
        # 2026-11-07 is a Saturday, 2026-11-08 a Sunday.
        "time": ["2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07",
                 "2026-11-08", "2026-11-09", "2026-11-10"],
        "weather_code": [2, 61, 0, 1, 3, 0, 0],
        "temperature_2m_max": [31.2, 29.6, 30, 30, 29, 31, 32],
        "temperature_2m_min": [19.1, 18.4, 18, 18, 17, 19, 20],
        "precipitation_probability_max": [10, 70, 0, 5, 20, 0, 0],
    },
}


def client_returning(geocode=None, forecast=None, status=200, error=None):
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        if error:
            raise error
        if "geocoding" in request.url.host:
            return httpx.Response(status, json=geocode if geocode is not None else {"results": [PUNE]})
        return httpx.Response(status, json=forecast if forecast is not None else FORECAST)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.requests = requests
    return client


def test_current_weather():
    result = WeatherTool(client_returning()).execute(location="Pune", when="now")

    assert result.success
    assert result.message == (
        "Pune, Maharashtra, India, now: 27°C (feels like 29°C), partly cloudy, "
        "humidity 60%, wind 8 km/h.\n"
        "Today: 19–31°C, partly cloudy, 10% chance of rain.\n"
        "(Source: Open-Meteo)"
    )


def test_only_the_place_name_is_sent():
    client = client_returning()
    WeatherTool(client).execute(location="Pune")

    geocode_request = client.requests[0]
    assert geocode_request.url.params["name"] == "Pune"
    assert client.requests[1].url.params["latitude"] == "18.52"


def test_tomorrow():
    result = WeatherTool(client_returning()).execute(location="Pune", when="tomorrow")

    assert result.message.startswith(
        "Pune, Maharashtra, India, tomorrow: 18–30°C, light rain, 70% chance of rain."
    )


def test_this_weekend_lists_only_saturday_and_sunday():
    result = WeatherTool(client_returning()).execute(location="Pune", when="this weekend")

    lines = result.message.splitlines()
    assert lines[1].startswith("  Sat 07 Nov")
    assert lines[2].startswith("  Sun 08 Nov")
    assert lines[3] == "(Source: Open-Meteo)"


def test_unknown_place():
    result = WeatherTool(client_returning(geocode={})).execute(location="Atlantis")

    assert result.success is False
    assert "couldn't find a place called \"Atlantis\"" in result.message


@pytest.mark.parametrize(
    "client",
    [
        lambda: client_returning(error=httpx.ConnectError("offline")),
        lambda: client_returning(error=httpx.ReadTimeout("slow")),
        lambda: client_returning(status=503),
        lambda: client_returning(forecast={"unexpected": True}),
    ],
)
def test_failures_never_guess(client):
    result = WeatherTool(client()).execute(location="Pune")

    assert result.success is False
    assert "I won't guess the weather" in result.message


def test_missing_location_asks_which_place(monkeypatch):
    from aurora.config import settings

    monkeypatch.setattr(settings, "DEFAULT_LOCATION", None)

    result = WeatherTool(client_returning()).execute(location=None)

    assert result.success is False
    assert result.message.startswith("Which place?")


def test_default_location_is_used(monkeypatch):
    from aurora.config import settings

    monkeypatch.setattr(settings, "DEFAULT_LOCATION", "Pune")
    client = client_returning()

    WeatherTool(client).execute()

    assert client.requests[0].url.params["name"] == "Pune"
