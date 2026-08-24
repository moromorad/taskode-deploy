import pytest
from django.contrib.auth.models import User
from coresite.forms import CustomUserCreationForm


@pytest.mark.django_db
def test_custom_user_creation_form_valid():
    form_data = {
        "username": "formuser",
        "email": "formuser@example.com",
        "password": "Password123!",
        "password_confirm": "Password123!",
    }
    # Standard UserCreationForm requires password1 and password2
    form = CustomUserCreationForm(data={
        "username": "formuser",
        "email": "formuser@example.com",
        "password1": "ComplexPass123!@",
        "password2": "ComplexPass123!@",
    })
    assert form.is_valid(), form.errors
    user = form.save()
    assert user.email == "formuser@example.com"


@pytest.mark.django_db
def test_custom_user_creation_form_duplicate_email(test_user):
    form = CustomUserCreationForm(data={
        "username": "differentuser",
        "email": test_user.email,
        "password1": "ComplexPass123!@",
        "password2": "ComplexPass123!@",
    })
    assert not form.is_valid()
    assert "email" in form.errors
    assert "An account with this email already exists." in form.errors["email"]
