from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import *

# The router automatically generates all the URL paths for our ViewSet
router = DefaultRouter()
router.register(r'tasks', TaskViewSet, basename='task')
router.register(r'projects', ProjectViewSet, basename='project')

urlpatterns = [
    path('', include(router.urls)),
    path('interface/', task_interface, name='task-ui'),
    path('register/', RegisterView.as_view(), name='register'),
    path('login/', Login2FAView.as_view(), name='login-2fa'),
    path('2fa/verify/', Verify2FAView.as_view(), name='verify-2fa'),
    path('2fa/resend/', Resend2FAView.as_view(), name='resend-2fa'),
    path("users/", UserList.as_view()),
    path("users/<int:pk>/", UserDetail.as_view()),
]