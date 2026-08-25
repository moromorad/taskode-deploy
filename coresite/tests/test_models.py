from datetime import timedelta
import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from coresite.models import EmailOTP, Project, Task, UserProfile, Weather


@pytest.mark.django_db
def test_project_model_str(test_user):
    project = Project.objects.create(
        name="Beta Project",
        github_repo="owner/beta",
        owner=test_user,
    )
    assert str(project) == "Beta Project"
    assert project.is_indexed is False
    assert project.last_indexed_at is None
    assert project.collection_name == ""
    assert project.embedding_model == "gemini-embedding-2"


@pytest.mark.django_db
def test_task_model_str_and_delete_signal(test_user, sample_project):
    task = Task.objects.create(
        title="Sample Task Title",
        project=sample_project,
        owner=test_user,
    )
    assert str(task) == "Sample Task Title"

    # Trigger post_delete signal
    task.delete()


@pytest.mark.django_db
def test_weather_model_str():
    now = timezone.now()
    weather = Weather.objects.create(
        temp=25.5,
        time=now,
        weather="Mainly clear",
        weather_code=1,
    )
    expected_str = f"Mainly clear (25.5°C) at {now.strftime('%Y-%m-%d %H:%M')}"
    assert str(weather) == expected_str


@pytest.mark.django_db
def test_email_otp_model(test_user):
    # Generate creates a new OTP and invalidates prior unused ones
    otp1 = EmailOTP.generate(test_user, validity_minutes=5)
    assert not otp1.is_used
    assert not otp1.is_expired()
    assert str(otp1) == f"OTP for {test_user.username} (Active)"

    # Generate a second OTP -> first should be marked used
    otp2 = EmailOTP.generate(test_user, validity_minutes=5)
    otp1.refresh_from_db()
    assert otp1.is_used is True
    assert str(otp1) == f"OTP for {test_user.username} (Used)"

    # Test verify on already used OTP
    success, msg = otp1.verify(otp1.otp_code)
    assert success is False
    assert msg == "This code has already been used."

    # Test verify with wrong code
    success, msg = otp2.verify("000000" if otp2.otp_code != "000000" else "111111")
    assert success is False
    assert "remaining" in msg
    otp2.refresh_from_db()
    assert otp2.attempts == 1

    # Test verify max attempts
    otp2.attempts = 5
    otp2.save()
    success, msg = otp2.verify(otp2.otp_code)
    assert success is False
    assert "Too many failed attempts" in msg

    # Test verify max attempts reached after wrong attempt
    otp3 = EmailOTP.generate(test_user, validity_minutes=5)
    otp3.attempts = 4
    otp3.save()
    wrong_code = "999999" if otp3.otp_code != "999999" else "888888"
    success, msg = otp3.verify(wrong_code)
    assert success is False
    assert "Too many failed attempts" in msg

    # Test verify expired
    otp4 = EmailOTP.generate(test_user, validity_minutes=-10)
    success, msg = otp4.verify(otp4.otp_code)
    assert success is False
    assert "This code has expired" in msg

    # Test verify successful
    otp5 = EmailOTP.generate(test_user, validity_minutes=5)
    success, msg = otp5.verify(otp5.otp_code)
    assert success is True
    assert msg == "Code verified successfully."
    otp5.refresh_from_db()
    assert otp5.is_used is True


@pytest.mark.django_db
def test_user_profile_model(test_user):
    # Test UserProfile string representation
    profile = UserProfile.get_or_create_for_user(test_user)
    assert str(profile) == f"Profile for {test_user.username}"

    # Test get_or_create_for_user when profile already exists
    same_profile = UserProfile.get_or_create_for_user(test_user)
    assert same_profile.id == profile.id

    # Test get_or_create_for_user when profile does not exist
    profile.delete()
    new_profile = UserProfile.get_or_create_for_user(test_user)
    assert new_profile is not None

    # Test refresh_calendar_token
    old_token = new_profile.calendar_token
    new_token = new_profile.refresh_calendar_token()
    assert new_token != old_token
    new_profile.refresh_from_db()
    assert new_profile.calendar_token == new_token
