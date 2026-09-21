"""
Django admin for the casestudy app.

The Django admin is a GUI for viewing and managing the database models like 'Security'.
Models registered with the Django admin will be accessible at http://localhost:8000/admin/.

https://docs.djangoproject.com/en/4.2/ref/contrib/admin/
"""
from django.contrib import admin
from casestudy.models import Security


# Create an and admin class for each model you want to be able to access in the Django admin, and register it with
# the admin.site.register() decorator.
@admin.register(Security)
class EmailAdmin(admin.ModelAdmin):

    # Fields in the list_display list will appear in the Django admin list view.
    # https://docs.djangoproject.com/en/4.2/ref/contrib/admin/#django.contrib.admin.ModelAdmin.list_display
    list_display = [
        'ticker',
        'name',
        'last_price',
    ]



