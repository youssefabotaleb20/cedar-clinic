from django.urls import path

from . import views

app_name = "booking"

urlpatterns = [
    path("", views.home, name="home"),
    path("doctors/<int:pk>/", views.doctor_detail, name="doctor_detail"),
    path("appointments/", views.my_appointments, name="my_appointments"),
    path("appointments/<int:pk>/cancel/", views.cancel, name="cancel"),
    path("staff/", views.dashboard, name="dashboard"),
    path("staff/appointments/<int:pk>/status/", views.set_status, name="set_status"),
]
