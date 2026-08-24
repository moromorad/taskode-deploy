import pytest
from rest_framework import status

from coresite.models import Task, UserProfile


@pytest.mark.django_db
def test_calendar_token_endpoint(auth_client):
    response = auth_client.get("/api/calendar/token/")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "calendar_token" in data
    assert "feed_url" in data
    assert "webcal_url" in data
    assert data["feed_url"].endswith(f"{data['calendar_token']}.ics")
    assert data["webcal_url"].startswith("webcal://")


@pytest.mark.django_db
def test_user_calendar_feed_with_tasks(api_client, test_user, sample_task, completed_task):
    profile = UserProfile.get_or_create_for_user(test_user)
    response = api_client.get(f"/api/calendar/feed/{profile.calendar_token}.ics")
    assert response.status_code == status.HTTP_200_OK
    assert "text/calendar" in response["Content-Type"]

    content = response.content.decode("utf-8")
    assert "BEGIN:VCALENDAR" in content
    assert "Implement OAuth authentication" in content
    assert "Alpha Project" in content
    assert "Create UserProfile model" in content
    # Completed task must not appear
    assert "Completed Task" not in content


@pytest.mark.django_db
def test_user_calendar_feed_empty_and_without_subtasks(api_client, test_user):
    profile = UserProfile.get_or_create_for_user(test_user)
    # Create task without project or subtasks
    from django.utils import timezone
    from datetime import timedelta
    Task.objects.create(
        title="Simple Solo Task",
        due_date=timezone.now() + timedelta(days=1),
        completed=False,
        owner=test_user,
    )
    response = api_client.get(f"/api/calendar/feed/{profile.calendar_token}")
    assert response.status_code == status.HTTP_200_OK
    content = response.content.decode("utf-8")
    assert "Simple Solo Task" in content


@pytest.mark.django_db
def test_user_calendar_feed_invalid_token(api_client):
    response = api_client.get("/api/calendar/feed/invalid_token_12345.ics")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_refresh_calendar_token_endpoint(auth_client, api_client, test_user):
    profile = UserProfile.get_or_create_for_user(test_user)
    old_token = profile.calendar_token

    response = auth_client.post("/api/calendar/token/refresh/")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    new_token = data["calendar_token"]
    assert new_token != old_token

    # Old token returns 404
    old_resp = api_client.get(f"/api/calendar/feed/{old_token}.ics")
    assert old_resp.status_code == status.HTTP_404_NOT_FOUND

    # New token works
    new_resp = api_client.get(f"/api/calendar/feed/{new_token}.ics")
    assert new_resp.status_code == status.HTTP_200_OK
