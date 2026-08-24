from unittest.mock import MagicMock, patch
import pytest
from django.utils import timezone

from coresite.models import Weather
from coresite.tasks import fetch_weather_and_cleanup


@pytest.mark.django_db
def test_fetch_weather_and_cleanup_creates_record():
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "current": {
            "temperature_2m": 28.5,
            "weather_code": 0,
            "time": "2026-08-24T12:00:00Z",
        }
    }

    with patch("coresite.tasks.requests.get", return_value=mock_response):
        fetch_weather_and_cleanup()

    latest = Weather.objects.latest("id")
    assert latest.temp == 28.5
    assert latest.weather == "Clear sky"
    assert latest.weather_code == 0


@pytest.mark.django_db
def test_fetch_weather_and_cleanup_deletes_excess_records():
    now = timezone.now()
    # Create 1005 weather records in bulk
    records = [
        Weather(temp=20.0, weather="Clear sky", time=now, weather_code=0)
        for _ in range(1005)
    ]
    Weather.objects.bulk_create(records)

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "current": {
            "temperature_2m": 22.0,
            "weather_code": 1,
            "time": "2026-08-24T13:00:00Z",
        }
    }

    with patch("coresite.tasks.requests.get", return_value=mock_response):
        fetch_weather_and_cleanup()

    # Total should be capped at MAX_RECORDS (1000) + 1 newly added record before cleanup -> exactly 1000
    assert Weather.objects.count() <= 1000
