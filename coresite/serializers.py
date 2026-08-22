from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from rest_framework import serializers
from .models import Task, Weather, Project, EmailOTP


class TaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        # '__all__' tells Django to automatically translate every column we made in the database
        fields = '__all__'
        read_only_fields = ['owner'] 

class ProjectSerializer(serializers.ModelSerializer):
    tasks = TaskSerializer(many=True, read_only=True)
    github_token = serializers.CharField(
        write_only=True, required=False, allow_blank=True, default=""
    )

    class Meta:
        model = Project
        fields = '__all__'
        read_only_fields = ["owner", "ast_outline"]


class WeatherSerializer(serializers.ModelSerializer):
    class Meta:
        model = Weather
        fields = '__all__'

class UserSerializer(serializers.ModelSerializer):

    tasks = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Task.objects.all()
    )

    class Meta:
        model = User
        fields = ["id", "username", "tasks", "owner"]

    owner = serializers.ReadOnlyField(source="owner.username")


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(required=True)
    email = serializers.EmailField(required=True)
    password = serializers.CharField(write_only=True, required=True)

    def validate_username(self, value: str) -> str:
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("A user with that username already exists.")
        return value

    def validate_email(self, value: str) -> str:
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def create(self, validated_data: dict) -> User:
        return User.objects.create_user(
            username=validated_data["username"],
            email=validated_data["email"],
            password=validated_data["password"],
        )


class Login2FASerializer(serializers.Serializer):
    username = serializers.CharField(required=True)
    password = serializers.CharField(write_only=True, required=True)

    def validate(self, attrs: dict) -> dict:
        username = attrs.get("username", "").strip()
        password = attrs.get("password", "")

        user = authenticate(username=username, password=password)
        if not user:
            raise serializers.ValidationError("Invalid username or password.")
        if not user.is_active:
            raise serializers.ValidationError("This user account is inactive.")
        
        email = getattr(user, "email", None)
        if not email:
            raise serializers.ValidationError("This account does not have an email address configured for 2FA.")

        attrs["user"] = user
        return attrs


class Verify2FASerializer(serializers.Serializer):
    session_token = serializers.CharField(required=True)
    otp = serializers.CharField(required=True, min_length=6, max_length=6)

    def validate_otp(self, value: str) -> str:
        val = value.strip()
        if not val.isdigit() or len(val) != 6:
            raise serializers.ValidationError("The OTP code must be a 6-digit number.")
        return val


class Resend2FASerializer(serializers.Serializer):
    session_token = serializers.CharField(required=True)


