import pytest
from coresite.services.utils import get_weather_category


def test_get_weather_category_known_codes():
    assert get_weather_category(0) == "Clear sky"
    assert get_weather_category(1) == "Mainly clear"
    assert get_weather_category(2) == "Partly cloudy"
    assert get_weather_category(3) == "Overcast"
    assert get_weather_category(45) == "Fog"
    assert get_weather_category(48) == "Depositing rime fog"
    assert get_weather_category(51) == "Drizzle: Light intensity"
    assert get_weather_category(53) == "Drizzle: Moderate intensity"
    assert get_weather_category(55) == "Drizzle: Dense intensity"
    assert get_weather_category(56) == "Freezing Drizzle: Light intensity"
    assert get_weather_category(57) == "Freezing Drizzle: Dense intensity"
    assert get_weather_category(61) == "Rain: Slight intensity"
    assert get_weather_category(63) == "Rain: Moderate intensity"
    assert get_weather_category(65) == "Rain: Heavy intensity"
    assert get_weather_category(66) == "Freezing Rain: Light intensity"
    assert get_weather_category(67) == "Freezing Rain: Heavy intensity"
    assert get_weather_category(71) == "Snow fall: Slight intensity"
    assert get_weather_category(73) == "Snow fall: Moderate intensity"
    assert get_weather_category(75) == "Snow fall: Heavy intensity"
    assert get_weather_category(77) == "Snow grains"
    assert get_weather_category(80) == "Rain showers: Slight"
    assert get_weather_category(81) == "Rain showers: Moderate"
    assert get_weather_category(82) == "Rain showers: Violent"
    assert get_weather_category(85) == "Snow showers: Slight"
    assert get_weather_category(86) == "Snow showers: Heavy"
    assert get_weather_category(95) == "Thunderstorm: Slight or moderate"
    assert get_weather_category(96) == "Thunderstorm with slight hail"
    assert get_weather_category(99) == "Thunderstorm with heavy hail"


def test_get_weather_category_unknown_code():
    assert get_weather_category(999) == "Unknown weather code"
    assert get_weather_category(-1) == "Unknown weather code"
