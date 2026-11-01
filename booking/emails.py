import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def _send(template: str, subject: str, appointment) -> bool:
    """Send one email to the patient. Returns False instead of raising, because a
    mail outage must never break a booking or a cancellation."""
    recipient = appointment.patient.email
    if not recipient:
        return False
    context = {"appointment": appointment, "clinic_name": settings.CLINIC_NAME}
    body = render_to_string(f"emails/{template}.txt", context)
    try:
        send_mail(f"{settings.CLINIC_NAME}: {subject}", body, None, [recipient])
    except Exception:
        logger.exception("Could not send %s email for appointment %s", template, appointment.pk)
        return False
    return True


def send_booking_confirmation(appointment) -> bool:
    return _send("confirmation", "your appointment is booked", appointment)


def send_reminder(appointment) -> bool:
    return _send("reminder", "reminder of your appointment", appointment)


def send_cancellation_notice(appointment) -> bool:
    return _send("cancellation", "your appointment was cancelled", appointment)
