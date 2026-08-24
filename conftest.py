from datetime import timedelta
import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIClient

from coresite.models import Project, Task


@pytest.fixture
def api_client():
    """Unauthenticated DRF APIClient fixture."""
    return APIClient()


@pytest.fixture
def test_user(db):
    """Test user fixture."""
    return User.objects.create_user(
        username="testuser",
        email="test@example.com",
        password="SecurePassword123!",
    )


@pytest.fixture
def auth_client(api_client, test_user):
    """DRF APIClient authenticated with test_user."""
    api_client.force_authenticate(user=test_user)
    return api_client


@pytest.fixture
def sample_project(db, test_user):
    """Sample project fixture owned by test_user."""
    return Project.objects.create(
        name="Alpha Project",
        owner=test_user,
    )


@pytest.fixture
def sample_task(db, test_user, sample_project):
    """Sample task fixture with due date and subtasks."""
    return Task.objects.create(
        title="Implement OAuth authentication",
        description="Add Google Calendar and Apple Reminders sync support.",
        due_date=timezone.now() + timedelta(days=2),
        ticket_type=Task.TicketType.FEATURE,
        subtasks=[
            {"title": "Create UserProfile model", "completed": True},
            {"title": "Implement iCal generator", "completed": False},
        ],
        project=sample_project,
        owner=test_user,
    )


@pytest.fixture
def completed_task(db, test_user):
    """Sample completed task fixture."""
    return Task.objects.create(
        title="Completed Task",
        due_date=timezone.now() + timedelta(days=1),
        completed=True,
        owner=test_user,
    )
