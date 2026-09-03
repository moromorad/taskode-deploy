import pytest
from django.contrib.auth.models import User
from rest_framework.exceptions import ValidationError

from coresite.models import EmailOTP, Project, Task
from coresite.serializers import (
    Login2FASerializer,
    ProjectSerializer,
    RegisterSerializer,
    Resend2FASerializer,
    TaskSerializer,
    UserSerializer,
    Verify2FASerializer,
)


@pytest.mark.django_db
def test_task_serializer(sample_task):
    serializer = TaskSerializer(instance=sample_task)
    data = serializer.data
    assert data["id"] == sample_task.id
    assert data["title"] == sample_task.title
    assert data["ticket_type"] == "feature"
    assert len(data["subtasks"]) == 2


@pytest.mark.django_db
def test_project_serializer(sample_project, sample_task):
    serializer = ProjectSerializer(instance=sample_project)
    data = serializer.data
    assert data["id"] == sample_project.id
    assert data["name"] == sample_project.name
    assert len(data["tasks"]) == 1



@pytest.mark.django_db
def test_user_serializer(test_user, sample_task):
    serializer = UserSerializer(instance=test_user)
    data = serializer.data
    assert data["id"] == test_user.id
    assert data["username"] == test_user.username
    assert sample_task.id in data["tasks"]


@pytest.mark.django_db
def test_register_serializer_success():
    data = {
        "username": "newuser",
        "email": "newuser@example.com",
        "password": "Password123!",
    }
    serializer = RegisterSerializer(data=data)
    assert serializer.is_valid(), serializer.errors
    user = serializer.save()
    assert user.username == "newuser"
    assert user.email == "newuser@example.com"


@pytest.mark.django_db
def test_register_serializer_duplicate_username(test_user):
    data = {
        "username": test_user.username,
        "email": "unique@example.com",
        "password": "Password123!",
    }
    serializer = RegisterSerializer(data=data)
    assert not serializer.is_valid()
    assert "username" in serializer.errors


@pytest.mark.django_db
def test_register_serializer_duplicate_email(test_user):
    data = {
        "username": "uniquename",
        "email": test_user.email,
        "password": "Password123!",
    }
    serializer = RegisterSerializer(data=data)
    assert not serializer.is_valid()
    assert "email" in serializer.errors


@pytest.mark.django_db
def test_login_2fa_serializer_success(test_user):
    data = {
        "username": test_user.username,
        "password": "SecurePassword123!",
    }
    serializer = Login2FASerializer(data=data)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["user"] == test_user


@pytest.mark.django_db
def test_login_2fa_serializer_invalid_credentials(test_user):
    data = {
        "username": test_user.username,
        "password": "WrongPassword!",
    }
    serializer = Login2FASerializer(data=data)
    assert not serializer.is_valid()
    assert "non_field_errors" in serializer.errors


@pytest.mark.django_db
def test_login_2fa_serializer_inactive_user():
    from unittest.mock import patch
    inactive_user = User.objects.create_user(
        username="inactive",
        email="inactive@example.com",
        password="Password123!",
        is_active=False,
    )
    data = {
        "username": "inactive",
        "password": "Password123!",
    }
    with patch("coresite.serializers.authenticate", return_value=inactive_user):
        serializer = Login2FASerializer(data=data)
        assert not serializer.is_valid()
        assert "inactive" in str(serializer.errors)


@pytest.mark.django_db
def test_login_2fa_serializer_missing_email():
    no_email_user = User.objects.create_user(
        username="noemail",
        email="",
        password="Password123!",
    )
    data = {
        "username": "noemail",
        "password": "Password123!",
    }
    serializer = Login2FASerializer(data=data)
    assert not serializer.is_valid()
    assert "email address configured" in str(serializer.errors)


def test_verify_2fa_serializer_valid():
    serializer = Verify2FASerializer(data={"session_token": "abc", "otp": "123456"})
    assert serializer.is_valid()
    assert serializer.validated_data["otp"] == "123456"


def test_verify_2fa_serializer_invalid_otp():
    # Non-digit
    serializer1 = Verify2FASerializer(data={"session_token": "abc", "otp": "abcdef"})
    assert not serializer1.is_valid()
    assert "otp" in serializer1.errors

    # Length != 6
    serializer2 = Verify2FASerializer(data={"session_token": "abc", "otp": "123"})
    assert not serializer2.is_valid()
    assert "otp" in serializer2.errors


def test_resend_2fa_serializer():
    serializer = Resend2FASerializer(data={"session_token": "token123"})
    assert serializer.is_valid()
    assert serializer.validated_data["session_token"] == "token123"
