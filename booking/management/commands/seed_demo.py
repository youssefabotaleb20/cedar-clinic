import os
from datetime import datetime, time, timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from booking.models import Appointment, Doctor, Specialty, WorkingHours

DOCTORS = [
    ("Mona Hassan", "General Practice", 20, "Family doctor for check-ups, vaccinations and everyday care."),
    ("Karim Fawzy", "Dentistry", 30, "Cleanings, fillings and cosmetic dentistry."),
    ("Laila Samir", "Dermatology", 30, "Skin, hair and nail conditions for adults and children."),
    ("Omar Nabil", "Pediatrics", 20, "Care for newborns, children and teenagers."),
    ("Nour El-Din Adel", "Cardiology", 40, "Heart health screening, ECG and follow-up."),
]

# Clinic week: Sunday to Thursday, morning and evening sessions.
SESSIONS = [(time(9, 0), time(13, 0)), (time(17, 0), time(20, 0))]
WORKDAYS = [
    WorkingHours.Weekday.SUNDAY,
    WorkingHours.Weekday.MONDAY,
    WorkingHours.Weekday.TUESDAY,
    WorkingHours.Weekday.WEDNESDAY,
    WorkingHours.Weekday.THURSDAY,
]


class Command(BaseCommand):
    help = "Create demo doctors, schedules and two demo accounts. Idempotent."

    @transaction.atomic
    def handle(self, *args, **options):
        for name, specialty_name, slot, bio in DOCTORS:
            specialty, _ = Specialty.objects.get_or_create(name=specialty_name)
            doctor, created = Doctor.objects.get_or_create(
                name=name, defaults={"specialty": specialty, "slot_minutes": slot, "bio": bio}
            )
            if created:
                WorkingHours.objects.bulk_create(
                    WorkingHours(doctor=doctor, weekday=day, start_time=start, end_time=end)
                    for day in WORKDAYS
                    for start, end in SESSIONS
                )

        password = os.environ.get("DEMO_PASSWORD", "demo-pass-123")
        patient = self._user("patient", "Sara", "Ali", "patient@example.com", password)
        self._user("staff", "Reception", "Desk", "staff@example.com", password, staff=True)

        if not patient.appointments.exists():
            self._sample_appointments(patient)

        self.stdout.write(self.style.SUCCESS(
            f"Demo data ready. Log in as patient / {password} or staff / {password}."
        ))

    def _user(self, username, first, last, email, password, staff=False):
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"first_name": first, "last_name": last, "email": email,
                      "is_staff": staff, "is_superuser": staff},
        )
        if created:
            user.set_password(password)
            user.save()
        return user

    def _sample_appointments(self, patient):
        day = timezone.localdate() + timedelta(days=1)
        while day.weekday() not in WORKDAYS:
            day += timedelta(days=1)
        for doctor, start in zip(Doctor.objects.all()[:2], [time(10, 0), time(18, 0)]):
            slots = doctor.available_slots(day)
            if start in slots:
                end = (datetime.combine(day, start) + timedelta(minutes=doctor.slot_minutes)).time()
                Appointment.objects.create(
                    doctor=doctor, patient=patient, date=day, start_time=start, end_time=end,
                    reason="Routine check-up",
                )
