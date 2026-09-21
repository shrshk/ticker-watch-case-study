"""
For more information on setting up DRF views see docs:
https://www.django-rest-framework.org/api-guide/views/#class-based-views
"""
import os

from rest_framework.views import APIView
from rest_framework import authentication, permissions
from rest_framework.response import Response
from django.contrib.auth.models import User


class LoginView(APIView):
    """
    Login view for the API.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request, format=None):
        """
        Login view for the API.
        """
        username = request.data['username']
        user = User.objects.get(username=username)
        user_data = {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
        }
        return Response(user_data)
