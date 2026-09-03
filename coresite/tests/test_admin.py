import pytest
from django.contrib import admin
from coresite.admin import (
    CodeChunkAdmin,
    EmailOTPAdmin,
    ProjectAdmin,
    TaskAdmin,
    UserProfileAdmin,
)
from coresite.models import CodeChunk, EmailOTP, Project, Task, UserProfile


def test_admin_registrations():
    assert admin.site.is_registered(Task)
    assert admin.site.is_registered(Project)
    assert admin.site.is_registered(EmailOTP)
    assert admin.site.is_registered(UserProfile)
    assert admin.site.is_registered(CodeChunk)

    assert isinstance(admin.site._registry[Task], TaskAdmin)
    assert isinstance(admin.site._registry[Project], ProjectAdmin)
    assert isinstance(admin.site._registry[EmailOTP], EmailOTPAdmin)
    assert isinstance(admin.site._registry[UserProfile], UserProfileAdmin)
    assert isinstance(admin.site._registry[CodeChunk], CodeChunkAdmin)

