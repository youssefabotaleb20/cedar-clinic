from django.contrib import admin
from django.urls import include, path

from booking import views

admin.site.site_header = "Clinic administration"
admin.site.site_title = "Clinic admin"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/signup/", views.signup, name="signup"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("healthz", views.healthz, name="healthz"),
    path("internal/cron/send-reminders", views.cron_send_reminders, name="cron_send_reminders"),
    path("", include("booking.urls")),
]
