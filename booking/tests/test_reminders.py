from datetime import datetime, time, timedelta
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from booking.models import Appointment
from booking.services import appointments_due_for_reminder, send_due_reminders

from .factories import make_doctor, make_patient


def at(day, hh, mm=0):
    return timezone.make_aware(datetime.combine(day, time(hh, mm)))


@override_settings(REMINDER_HOURS_AHEAD=24)
class ReminderTests(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.patient = make_patient()
        self.day = timezone.localdate() + timedelta(days=3)

    def book(self, start=time(10, 0), patient=None, **extra):
        return Appointment.objects.create(
            doctor=self.doctor, patient=patient or self.patient, date=self.day,
            start_time=start, end_time=time(start.hour, 30), **extra,
        )

    def test_only_appointments_inside_the_window_are_due(self):
        inside = self.book(time(10, 0))
        self.book(time(11, 0))
        now = at(self.day - timedelta(days=1), 10, 30)  # 23.5h before 10:00, 24.5h before 11:00
        self.assertEqual(appointments_due_for_reminder(now), [inside])

    def test_reminder_is_sent_once(self):
        self.book()
        now = at(self.day - timedelta(days=1), 12)
        self.assertEqual(send_due_reminders(now), 1)
        self.assertEqual(send_due_reminders(now), 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("reminder", mail.outbox[0].subject)

    def test_cancelled_and_past_appointments_are_skipped(self):
        self.book(time(9, 0), status=Appointment.Status.CANCELLED)
        self.book(time(10, 0))
        self.assertEqual(send_due_reminders(at(self.day, 11)), 0)

    def test_failed_send_is_retried_next_run(self):
        appt = self.book()
        now = at(self.day - timedelta(days=1), 12)
        with mock.patch("booking.emails.send_mail", side_effect=OSError), self.assertLogs("booking.emails", "ERROR"):
            self.assertEqual(send_due_reminders(now), 0)
        appt.refresh_from_db()
        self.assertIsNone(appt.reminder_sent_at)
        self.assertEqual(send_due_reminders(now), 1)

    def test_management_command_reports_count(self):
        out = StringIO()
        call_command("send_reminders", stdout=out)
        self.assertIn("Sent 0 reminder(s).", out.getvalue())
