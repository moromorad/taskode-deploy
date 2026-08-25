# pyrefly: ignore [missing-import]
import secrets
import uuid
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone


class Project(models.Model):
    name = models.CharField(max_length=100)
    github_repo = models.CharField(
        max_length=200, help_text="e.g. 'owner/repository'", blank=True, default=""
    )
    ast_outline = models.TextField(blank=True, default="")
    github_token = models.CharField(
        max_length=500, blank=True, default="",
        help_text="GitHub personal access token for private repos"
    )
    owner = models.ForeignKey(
        "auth.User", related_name="projects", on_delete=models.CASCADE
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_indexed = models.BooleanField(
        default=False,
        help_text="Indicates whether this project's code has been embedded into ChromaDB",
    )
    last_indexed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of when the repository was last embedded",
    )
    collection_name = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="ChromaDB collection identifier, e.g. 'project_1'",
    )
    embedding_model = models.CharField(
        max_length=50,
        blank=True,
        default="gemini-embedding-2",
        help_text="Model used to generate vectors (gemini-embedding-2 or gemini-embedding-001)",
    )

    def __str__(self):
        return self.name


class Task(models.Model):
    class TicketType(models.TextChoices):
        BUG = "bug", "Bug"
        FEATURE = "feature", "Feature"
        CHORE = "chore", "Chore"

    title = models.CharField(max_length=200)

    description = models.CharField(null=True, blank=True)

    # Is it done? Defaults to False when created.
    completed = models.BooleanField(default=False)

    # Automatically saves the exact timestamp when a task is first created
    created_at = models.DateTimeField(auto_now_add=True)

    due_date = models.DateTimeField(null=True, blank=True)

    owner = models.ForeignKey(
        "auth.User", related_name="tasks", on_delete=models.CASCADE
    )

    
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="tasks",
        null=True,
        blank=True,
    )
    ticket_type = models.CharField(
        max_length=10,
        choices=TicketType.choices,
        default=TicketType.FEATURE,
    )
    # Stores subtasks as a list of dicts, e.g. [{"title": "Check auth middleware", "done": False}]
    subtasks = models.JSONField(default=list, blank=True)

    def __str__(self):
        return self.title   
    
class Weather(models.Model):
    temp = models.FloatField()
    time = models.DateTimeField()
    weather = models.CharField(max_length=200)
    weather_code = models.IntegerField(default=0)
    class Meta:
        ordering = ['-time'] 
    def __str__(self):
        return f"{self.weather} ({self.temp}°C) at {self.time.strftime('%Y-%m-%d %H:%M')}"


class EmailOTP(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="email_otps"
    )
    otp_code = models.CharField(max_length=6)
    session_token = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.IntegerField(default=0)
    is_used = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"OTP for {self.user.username} ({'Used' if self.is_used else 'Active'})"

    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at

    MAX_ATTEMPTS = 5

    def verify(self, code: str) -> tuple[bool, str]:
        if self.is_used:
            return False, "This code has already been used."
        if self.attempts >= self.MAX_ATTEMPTS:
            return False, "Too many failed attempts. Please request a new code."
        if self.is_expired():
            return False, "This code has expired. Please request a new code."
        if self.otp_code != code.strip():
            self.attempts += 1
            self.save(update_fields=['attempts'])
            remaining = self.MAX_ATTEMPTS - self.attempts
            if remaining <= 0:
                return False, "Too many failed attempts. Please request a new code."
            return False, f"Incorrect code. {remaining} attempt{'s' if remaining > 1 else ''} remaining."
        
        self.is_used = True
        self.save(update_fields=['is_used'])
        return True, "Code verified successfully."

    @classmethod
    def generate(cls, user: User, validity_minutes: int = 5) -> "EmailOTP":
        # Invalidate previous unused active OTPs for this user
        cls.objects.filter(user=user, is_used=False).update(is_used=True)

        code = f"{secrets.randbelow(1000000):06d}"
        token = uuid.uuid4().hex
        expires = timezone.now() + timedelta(minutes=validity_minutes)

        return cls.objects.create(
            user=user,
            otp_code=code,
            session_token=token,
            expires_at=expires,
        )
    

class UserProfile(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="profile"
    )
    calendar_token = models.CharField(
        max_length=64, unique=True, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Profile for {self.user.username}"

    @classmethod
    def get_or_create_for_user(cls, user: User) -> "UserProfile":
        profile = cls.objects.filter(user=user).first()
        if not profile:
            profile = cls.objects.create(
                user=user,
                calendar_token=uuid.uuid4().hex,
            )
        return profile

    def refresh_calendar_token(self) -> str:
        self.calendar_token = uuid.uuid4().hex
        self.save(update_fields=["calendar_token"])
        return self.calendar_token


# Signal to auto-create a UserProfile with calendar_token on User creation
@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.get_or_create_for_user(instance)


# 1. We use the @receiver decorator to tell Django to listen for 'post_delete' on the 'Task' model
@receiver(post_delete, sender=Task)
def notify_task_deleted(sender, instance, **kwargs):
    # 2. 'instance' is the actual task object that was just deleted
    print("\n-------------------------------------------------------------")
    print(f"💥 SIGNAL ALARM: The task '{instance.title}' was just deleted!")
    print("-------------------------------------------------------------\n")

