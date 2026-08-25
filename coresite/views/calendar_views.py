import uuid
from datetime import timedelta
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from icalendar import Calendar, Event
from rest_framework import permissions, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Task, UserProfile


def generate_ical_feed(profile: UserProfile) -> bytes:
    """
    Generates an iCalendar (RFC 5545) byte string for a user's active tasks.
    """
    tasks = (
        Task.objects.filter(owner=profile.user, due_date__isnull=False, completed=False)
        .select_related("project")
        .order_by("due_date")
    )

    cal = Calendar()
    cal.add("prodid", "-//TasKode//Tasks Feed//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", f"{profile.user.username}'s TasKode Tasks")
    cal.add("x-wr-caldesc", "Active tasks and deadlines from TasKode")
    cal.add("x-published-ttl", "PT1H")
    cal.add("refresh-interval;value=duration", "PT1H")

    now = timezone.now()

    for task in tasks:
        event = Event()
        
        # Summary with ticket type badge
        type_prefix = f"[{task.ticket_type.upper()}] " if task.ticket_type else ""
        event.add("summary", f"{type_prefix}{task.title}")

        # Build comprehensive description
        desc_parts = []
        if task.project:
            desc_parts.append(f"Project: {task.project.name}")
        if task.description:
            desc_parts.append(f"Description: {task.description}")
        if task.subtasks:
            desc_parts.append("Subtasks:")
            for st in task.subtasks:
                status_mark = "[x]" if st.get("completed") else "[ ]"
                desc_parts.append(f"  {status_mark} {st.get('title', '')}")

        event.add("description", "\n".join(desc_parts))
        event.add("dtstart", task.due_date)
        # Default 1-hour block for calendar visualization
        event.add("dtend", task.due_date + timedelta(hours=1))
        event.add("dtstamp", now)
        event.add("created", task.created_at)
        event.add("uid", f"task-{task.id}-{profile.user.id}@taskapp.local")
        event.add("status", "CONFIRMED")
        event.add("categories", [task.ticket_type.upper(), "TASK"])

        cal.add_component(event)

    return cal.to_ical()


def user_calendar_feed(request: HttpRequest, token: str) -> HttpResponse:
    """
    Public iCal feed endpoint accessed by calendar clients (Google Calendar, Apple Calendar/Reminders).
    Token-based authentication via URL.
    """
    # Clean token if .ics extension was provided
    clean_token = token.replace(".ics", "")
    profile = get_object_or_404(UserProfile, calendar_token=clean_token)

    ical_content = generate_ical_feed(profile)
    response = HttpResponse(ical_content, content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = f'inline; filename="{profile.user.username}_tasks.ics"'
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


class CalendarTokenView(APIView):
    """
    Returns the authenticated user's calendar feed subscription URLs.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Calendar Sync"],
        summary="Get Calendar Subscription URLs",
        description="Returns the authenticated user's private iCal/Webcal feed subscription URLs for Google Calendar and Apple Reminders/Calendar.",
        responses={
            200: inline_serializer(
                name="CalendarTokenResponse",
                fields={
                    "calendar_token": serializers.CharField(),
                    "feed_url": serializers.CharField(help_text="Standard HTTPS .ics feed URL"),
                    "webcal_url": serializers.CharField(help_text="Webcal protocol URL for direct calendar subscription"),
                },
            )
        },
    )
    def get(self, request: Request) -> Response:
        profile = UserProfile.get_or_create_for_user(request.user)
        base_url = request.build_absolute_uri("/api/calendar/feed/")
        feed_url = f"{base_url}{profile.calendar_token}.ics"
        webcal_url = feed_url.replace("http://", "webcal://").replace("https://", "webcal://")

        return Response(
            {
                "calendar_token": profile.calendar_token,
                "feed_url": feed_url,
                "webcal_url": webcal_url,
            },
            status=status.HTTP_200_OK,
        )


class RefreshCalendarTokenView(APIView):
    """
    Regenerates the user's calendar token to revoke previous subscription links.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Calendar Sync"],
        summary="Regenerate Calendar Token",
        description="Revokes the existing calendar subscription URL and issues a new one.",
        request=None,
        responses={
            200: inline_serializer(
                name="RefreshCalendarTokenResponse",
                fields={
                    "message": serializers.CharField(),
                    "calendar_token": serializers.CharField(),
                    "feed_url": serializers.CharField(),
                    "webcal_url": serializers.CharField(),
                },
            )
        },
    )
    def post(self, request: Request) -> Response:
        profile = UserProfile.get_or_create_for_user(request.user)
        new_token = profile.refresh_calendar_token()
        base_url = request.build_absolute_uri("/api/calendar/feed/")
        feed_url = f"{base_url}{new_token}.ics"
        webcal_url = feed_url.replace("http://", "webcal://").replace("https://", "webcal://")

        return Response(
            {
                "message": "Calendar token refreshed successfully. Previous subscription URLs have been invalidated.",
                "calendar_token": new_token,
                "feed_url": feed_url,
                "webcal_url": webcal_url,
            },
            status=status.HTTP_200_OK,
        )
