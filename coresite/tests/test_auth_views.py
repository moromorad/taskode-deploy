from unittest.mock import patch
import pytest
from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.exceptions import Throttled

from coresite.models import EmailOTP
from coresite.views.auth_views import (
    ThrottledTokenObtainPairView,
    mask_email,
)


def test_mask_email():
    assert mask_email("ab@domain.com") == "a*@domain.com"
    assert mask_email("a@domain.com") == "a*@domain.com"
    assert mask_email("testuser@domain.com") == "te******@domain.com"
    assert mask_email("invalid-email") == "invalid-email"


def test_throttled_view_custom_handler():
    view = ThrottledTokenObtainPairView()
    with pytest.raises(Throttled) as exc_info:
        view.throttled(None, 60)
    assert "There were too many failed login attempts" in str(exc_info.value)


@pytest.mark.django_db
def test_register_view_success(api_client):
    data = {
        "username": "registered_user",
        "email": "registered@example.com",
        "password": "Password123!",
    }
    response = api_client.post("/api/register/", data)
    assert response.status_code == status.HTTP_201_CREATED
    assert "access" in response.json()
    assert "refresh" in response.json()
    assert response.json()["user"]["username"] == "registered_user"


@pytest.mark.django_db
def test_register_view_invalid(api_client):
    data = {"username": "incomplete"}
    response = api_client.post("/api/register/", data)
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_login_2fa_view_success(api_client, test_user):
    data = {
        "username": test_user.username,
        "password": "SecurePassword123!",
    }
    with patch("coresite.views.auth_views.send_mail") as mock_send:
        response = api_client.post("/api/login/", data)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["2fa_required"] is True
        assert "session_token" in response.json()
        assert mock_send.called


@pytest.mark.django_db
def test_login_2fa_view_invalid_credentials(api_client, test_user):
    data = {
        "username": test_user.username,
        "password": "WrongPassword!",
    }
    response = api_client.post("/api/login/", data)
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_login_2fa_view_mail_exception(api_client, test_user):
    data = {
        "username": test_user.username,
        "password": "SecurePassword123!",
    }
    with patch("coresite.views.auth_views.send_mail", side_effect=Exception("SMTP error")):
        response = api_client.post("/api/login/", data)
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Failed to send verification email" in response.json()["error"]


@pytest.mark.django_db
def test_verify_2fa_view_success(api_client, test_user):
    otp = EmailOTP.generate(test_user)
    data = {
        "session_token": otp.session_token,
        "otp": otp.otp_code,
    }
    response = api_client.post("/api/2fa/verify/", data)
    assert response.status_code == status.HTTP_200_OK
    assert "access" in response.json()
    assert "refresh" in response.json()
    assert response.json()["user"]["username"] == test_user.username


@pytest.mark.django_db
def test_verify_2fa_view_invalid_data(api_client):
    response = api_client.post("/api/2fa/verify/", {})
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_verify_2fa_view_nonexistent_session(api_client):
    data = {
        "session_token": "nonexistent_session",
        "otp": "123456",
    }
    response = api_client.post("/api/2fa/verify/", data)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid session" in response.json()["error"]


@pytest.mark.django_db
def test_verify_2fa_view_wrong_otp(api_client, test_user):
    otp = EmailOTP.generate(test_user)
    wrong_code = "000000" if otp.otp_code != "000000" else "111111"
    data = {
        "session_token": otp.session_token,
        "otp": wrong_code,
    }
    response = api_client.post("/api/2fa/verify/", data)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Incorrect code" in response.json()["error"]


@pytest.mark.django_db
def test_resend_2fa_view_success(api_client, test_user):
    otp = EmailOTP.generate(test_user)
    data = {"session_token": otp.session_token}

    with patch("coresite.views.auth_views.send_mail") as mock_send:
        response = api_client.post("/api/2fa/resend/", data)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "A new verification code has been sent to your email."
        assert "session_token" in response.json()
        assert mock_send.called


@pytest.mark.django_db
def test_resend_2fa_view_invalid_data(api_client):
    response = api_client.post("/api/2fa/resend/", {})
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_resend_2fa_view_nonexistent_session(api_client):
    data = {"session_token": "unknown_token"}
    response = api_client.post("/api/2fa/resend/", data)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid session" in response.json()["error"]


@pytest.mark.django_db
def test_resend_2fa_view_mail_exception(api_client, test_user):
    otp = EmailOTP.generate(test_user)
    data = {"session_token": otp.session_token}

    with patch("coresite.views.auth_views.send_mail", side_effect=Exception("SMTP down")):
        response = api_client.post("/api/2fa/resend/", data)
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Failed to send verification email" in response.json()["error"]
