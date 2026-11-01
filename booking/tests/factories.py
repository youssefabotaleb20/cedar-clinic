from datetime import time, timedelta

from django.contrib.auth.models import User
from django.utils import timezone

from booking.models import Doctor, Specialty, WorkingHours


def make_doctor(name="Test Doctor", slot_minutes=30, start=time(9, 0), end=time(12, 0)):
    specialty, _ = Specialty.objects.get_or_create(name="General Practice")
    doctor = Doctor.objects.create(name=name, specialty=specialty, slot_minutes=slot_minutes)
    for weekday in range(7):
        WorkingHours.objects.create(doctor=doctor, weekday=weekday, start_time=start, end_time=end)
    return doctor


def make_patient(username="sara", email="sara@example.com"):
    return User.objects.create_user(username, email, "pw-123456!", first_name="Sara")


def tomorrow():
    return timezone.localdate() + timedelta(days=1)
