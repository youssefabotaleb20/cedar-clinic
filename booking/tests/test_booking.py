from datetime import time, timedelta
from unittest import mock

from django.core import mail
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from booking.models import Appointment
from booking.services import BookingError, book_appointment, cancel_appointment

from .factories import make_doctor, make_patient, tomorrow


class SlotTests(TestCase):
    def test_slots_follow_working_hours_and_slot_length(self):
        doctor = make_doctor(slot_minutes=45, start=time(9, 0), end=time(11, 0))
        self.assertEqual(doctor.available_slots(tomorrow()), [time(9, 0), time(9, 45)])

    def test_booked_slot_is_not_offered(self):
        doctor = make_doctor()
        book_appointment(patient=make_patient(), doctor=doctor, day=tomorrow(), start=time(9, 0))
        self.assertNotIn(time(9, 0), doctor.available_slots(tomorrow()))

    def test_past_slots_are_hidden(self):
        doctor = make_doctor(start=time(0, 0), end=time(23, 30))
        now = timezone.localtime()
        for slot in doctor.available_slots(now.date()):
            self.assertGreater(timezone.make_aware(timezone.datetime.combine(now.date(), slot)), now)

    def test_day_off_has_no_slots(self):
        doctor = make_doctor()
        doctor.working_hours.filter(weekday=tomorrow().weekday()).delete()
        self.assertEqual(doctor.available_slots(tomorrow()), [])


class BookingTests(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.patient = make_patient()

    def test_booking_sets_end_time_and_sends_confirmation(self):
        with self.captureOnCommitCallbacks(execute=True):
            appt = book_appointment(patient=self.patient, doctor=self.doctor, day=tomorrow(), start=time(10, 0))
        self.assertEqual(appt.end_time, time(10, 30))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("booked", mail.outbox[0].subject)

    def test_double_booking_is_rejected(self):
        book_appointment(patient=self.patient, doctor=self.doctor, day=tomorrow(), start=time(10, 0))
        with self.assertRaises(BookingError):
            book_appointment(patient=make_patient("omar", "omar@example.com"), doctor=self.doctor,
                             day=tomorrow(), start=time(10, 0))

    def test_database_constraint_blocks_double_booking(self):
        """Even if the service layer were bypassed, the DB refuses a second active booking."""
        common = dict(doctor=self.doctor, date=tomorrow(), start_time=time(10, 0), end_time=time(10, 30))
        Appointment.objects.create(patient=self.patient, **common)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Appointment.objects.create(patient=make_patient("omar", "omar@example.com"), **common)

    def test_cancelled_slot_can_be_booked_again(self):
        appt = book_appointment(patient=self.patient, doctor=self.doctor, day=tomorrow(), start=time(10, 0))
        cancel_appointment(appt)
        again = book_appointment(patient=make_patient("omar", "omar@example.com"), doctor=self.doctor,
                                 day=tomorrow(), start=time(10, 0))
        self.assertEqual(again.status, Appointment.Status.BOOKED)

    def test_patient_cannot_be_in_two_places_at_once(self):
        other = make_doctor("Other Doctor")
        book_appointment(patient=self.patient, doctor=self.doctor, day=tomorrow(), start=time(10, 0))
        with self.assertRaises(BookingError):
            book_appointment(patient=self.patient, doctor=other, day=tomorrow(), start=time(10, 0))

    def test_outside_window_is_rejected(self):
        with self.assertRaises(BookingError):
            book_appointment(patient=self.patient, doctor=self.doctor,
                             day=timezone.localdate() + timedelta(days=365), start=time(10, 0))

    def test_time_not_on_the_schedule_is_rejected(self):
        with self.assertRaises(BookingError):
            book_appointment(patient=self.patient, doctor=self.doctor, day=tomorrow(), start=time(10, 15))

    def test_cancel_sends_notice_and_frees_slot(self):
        appt = book_appointment(patient=self.patient, doctor=self.doctor, day=tomorrow(), start=time(10, 0))
        mail.outbox.clear()
        cancel_appointment(appt)
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CANCELLED)
        self.assertIsNone(appt.holds_slot)
        self.assertEqual(len(mail.outbox), 1)

    def test_mail_failure_does_not_break_booking(self):
        # assertLogs is outermost so it is still open when the on-commit email runs.
        with self.assertLogs("booking.emails", "ERROR"), \
                mock.patch("booking.emails.send_mail", side_effect=OSError("smtp down")), \
                self.captureOnCommitCallbacks(execute=True):
            appt = book_appointment(patient=self.patient, doctor=self.doctor, day=tomorrow(), start=time(10, 0))
        self.assertEqual(appt.status, Appointment.Status.BOOKED)
