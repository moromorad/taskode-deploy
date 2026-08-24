import pytest
from django.contrib import admin
from coresite.admin import (
    EmailOTPAdmin,
    ProjectAdmin,
    TaskAdmin,
    UserProfileAdmin,
    WeatherAdmin,
)
from coresite.models import EmailOTP, Project, Task, UserProfile, Weather


def test_admin_registrations():
    assert admin.site.is_registered(Task)
    assert admin.site.is_registered(Weather)
    assert admin.site.is_registered(Project)
    assert admin.site.is_registered(EmailOTP)
    assert admin.site.is_registered(UserProfile)

    assert isinstance(admin.site._registry[Task], TaskAdmin)
    assert isinstance(admin.site._registry[Weather], WeatherAdmin)
    assert isinstance(admin.site._registry[Project], ProjectAdmin)
    assert isinstance(admin.site._registry[EmailOTP], EmailOTPAdmin)
    assert isinstance(admin.site._registry[UserProfile], UserProfileAdmin)
