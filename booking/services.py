"""Booking rules live here so views, admin and tests share one code path."""

from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .emails import send_booking_confirmation, send_cancellation_notice, send_reminder
from .models import Appointment, Doctor


class BookingError(Exception):
    """Raised when a slot can't be booked; the message is safe to show patients."""


def booking_window():
    today = timezone.localdate()
    return today, today + timedelta(days=settings.BOOKING_WINDOW_DAYS)


@transaction.atomic
def book_appointment(*, patient, doctor: Doctor, day: date, start: time, reason: str = "") -> Appointment:
    first_day, last_day = booking_window()
    if not first_day <= day <= last_day:
        raise BookingError("That date is outside the booking window.")

    # Lock the doctor row so two requests for the same doctor are serialised.
    # The unique constraint on Appointment is the final guard if this is bypassed.
    doctor = Doctor.objects.select_for_update().get(pk=doctor.pk, is_active=True)
    if start not in doctor.available_slots(day):
        raise BookingError("Sorry, that time is no longer available. Please pick another slot.")
    if Appointment.objects.filter(
        patient=patient, date=day, start_time=start, status=Appointment.Status.BOOKED
    ).exists():
        raise BookingError("You already have another appointment at that time.")

    end = (datetime.combine(day, start) + timedelta(minutes=doctor.slot_minutes)).time()
    try:
        with transaction.atomic():
            appointment = Appointment.objects.create(
                doctor=doctor, patient=patient, date=day, start_time=start, end_time=end, reason=reason
            )
    except IntegrityError as exc:
        raise BookingError("Sorry, that time was just taken. Please pick another slot.") from exc

    transaction.on_commit(lambda: send_booking_confirmation(appointment))
    return appointment


def cancel_appointment(appointment: Appointment, *, by_staff: bool = False) -> None:
    if not by_staff and not appointment.can_cancel:
        raise BookingError("This appointment can no longer be cancelled.")
    appointment.cancel()
    send_cancellation_notice(appointment)


def appointments_due_for_reminder(now=None):
    """Booked appointments starting within REMINDER_HOURS_AHEAD that haven't had a reminder."""
    now = timezone.localtime(now or timezone.now())
    horizon = now + timedelta(hours=settings.REMINDER_HOURS_AHEAD)
    candidates = Appointment.objects.filter(
        status=Appointment.Status.BOOKED,
        reminder_sent_at__isnull=True,
        date__gte=now.date(),
        date__lte=horizon.date(),
    ).select_related("doctor", "patient")
    return [a for a in candidates if now < a.starts_at <= horizon]


def send_due_reminders(now=None) -> int:
    sent = 0
    for appointment in appointments_due_for_reminder(now):
        if not send_reminder(appointment):
            continue  # No email address or SMTP failure: retried on the next run.
        # Conditional update so two overlapping runs can't both mark and resend.
        Appointment.objects.filter(pk=appointment.pk, reminder_sent_at__isnull=True).update(
            reminder_sent_at=timezone.now()
        )
        sent += 1
    return sent
