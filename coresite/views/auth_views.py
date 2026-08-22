# pyrefly: ignore [missing-import]
from django.conf import settings
from django.core.mail import send_mail
from django.http import HttpRequest
from rest_framework import status
from rest_framework.exceptions import Throttled
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from ..models import EmailOTP
from ..serializers import (
    Login2FASerializer,
    RegisterSerializer,
    Resend2FASerializer,
    Verify2FASerializer,
)


class LoginRateThrottle(AnonRateThrottle):
    rate: str = "5/minute"


class ThrottledTokenObtainPairView(TokenObtainPairView):
    throttle_classes = [LoginRateThrottle]

    def throttled(self, request: Request | HttpRequest, wait: int) -> None:
        raise Throttled(detail="There were too many failed login attempts. Please try again later.")


def mask_email(email: str) -> str:
    try:
        user_part, domain = email.split("@", 1)
        if len(user_part) <= 2:
            masked_user = user_part[0] + "*"
        else:
            masked_user = user_part[:2] + "*" * (len(user_part) - 2)
        return f"{masked_user}@{domain}"
    except Exception:
        return email


class RegisterView(APIView):
    authentication_classes = ()
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

    def post(self, request: Request) -> Response:
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            refresh = RefreshToken.for_user(user)
            return Response(
                {
                    "message": "User registered successfully",
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
                    "user": {
                        "id": user.id,
                        "username": user.username,
                        "email": user.email,
                    },
                },
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class Login2FAView(APIView):
    authentication_classes = ()
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

    def post(self, request: Request) -> Response:
        serializer = Login2FASerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = serializer.validated_data["user"]

        # Generate new 6-digit OTP and session token
        otp = EmailOTP.generate(user)

        # Send email with OTP code
        subject = "Your Two-Factor Authentication Code"
        message = (
            f"Hello {user.username},\n\n"
            f"Your verification code for logging in is: {otp.otp_code}\n\n"
            f"This code will expire in 5 minutes.\n\n"
            f"If you did not request this login attempt, please secure your account."
        )
        try:
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to send verification email: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "message": "Verification code sent to your email.",
                "2fa_required": True,
                "session_token": otp.session_token,
                "email": mask_email(user.email),
            },
            status=status.HTTP_200_OK,
        )


class Verify2FAView(APIView):
    authentication_classes = ()
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

    def post(self, request: Request) -> Response:
        serializer = Verify2FASerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        session_token = serializer.validated_data["session_token"]
        otp_code = serializer.validated_data["otp"]

        otp_record = EmailOTP.objects.filter(session_token=session_token).select_related("user").first()
        if not otp_record:
            return Response(
                {"error": "Invalid session or verification session expired. Please log in again."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        is_valid, error_message = otp_record.verify(otp_code)
        if not is_valid:
            return Response({"error": error_message}, status=status.HTTP_400_BAD_REQUEST)

        # Verification successful -> issue JWT tokens
        user = otp_record.user
        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "message": "Verification successful. Logged in.",
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                },
            },
            status=status.HTTP_200_OK,
        )


class Resend2FAView(APIView):
    authentication_classes = ()
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

    def post(self, request: Request) -> Response:
        serializer = Resend2FASerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        session_token = serializer.validated_data["session_token"]
        otp_record = EmailOTP.objects.filter(session_token=session_token).select_related("user").first()
        if not otp_record:
            return Response(
                {"error": "Invalid session or verification session expired. Please log in again."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = otp_record.user
        new_otp = EmailOTP.generate(user)

        subject = "Your New Two-Factor Authentication Code"
        message = (
            f"Hello {user.username},\n\n"
            f"Your new verification code is: {new_otp.otp_code}\n\n"
            f"This code will expire in 5 minutes.\n\n"
            f"If you did not request this code, please secure your account."
        )
        try:
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to send verification email: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "message": "A new verification code has been sent to your email.",
                "session_token": new_otp.session_token,
                "email": mask_email(user.email),
            },
            status=status.HTTP_200_OK,
        )
