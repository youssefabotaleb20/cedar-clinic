from datetime import time

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from booking.models import Appointment
from booking.services import book_appointment

from .factories import make_doctor, make_patient, tomorrow


class PatientFlowTests(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.patient = make_patient()

    def test_home_lists_doctors(self):
        response = self.client.get(reverse("booking:home"))
        self.assertContains(response, self.doctor.name)

    def test_doctor_page_shows_slots(self):
        response = self.client.get(reverse("booking:doctor_detail", args=[self.doctor.pk]),
                                   {"date": tomorrow().isoformat()})
        self.assertContains(response, 'value="09:00"')

    def test_anonymous_booking_redirects_to_login(self):
        url = reverse("booking:doctor_detail", args=[self.doctor.pk])
        response = self.client.post(url, {"date": tomorrow().isoformat(), "start_time": "09:00"})
        self.assertRedirects(response, f"{reverse('login')}?next={url}", fetch_redirect_response=False)
        self.assertFalse(Appointment.objects.exists())

    def test_logged_in_patient_can_book_and_see_it(self):
        self.client.force_login(self.patient)
        url = reverse("booking:doctor_detail", args=[self.doctor.pk])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, {"date": tomorrow().isoformat(), "start_time": "09:00", "reason": "Flu"})
        self.assertRedirects(response, reverse("booking:my_appointments"))
        self.assertContains(self.client.get(reverse("booking:my_appointments")), "Flu")
        self.assertEqual(len(mail.outbox), 1)

    def test_patient_cannot_cancel_someone_elses_appointment(self):
        appt = book_appointment(patient=self.patient, doctor=self.doctor, day=tomorrow(), start=time(9, 0))
        self.client.force_login(make_patient("omar", "omar@example.com"))
        response = self.client.post(reverse("booking:cancel", args=[appt.pk]))
        self.assertEqual(response.status_code, 404)

    def test_patient_can_cancel(self):
        appt = book_appointment(patient=self.patient, doctor=self.doctor, day=tomorrow(), start=time(9, 0))
        self.client.force_login(self.patient)
        self.client.post(reverse("booking:cancel", args=[appt.pk]))
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CANCELLED)

    def test_signup_logs_in(self):
        response = self.client.post(reverse("signup"), {
            "username": "newbie", "first_name": "New", "last_name": "Patient", "email": "new@example.com",
            "password1": "a-Long-pass-987", "password2": "a-Long-pass-987",
        })
        self.assertRedirects(response, reverse("booking:home"))
        self.assertTrue(User.objects.filter(username="newbie").exists())

    def test_signup_rejects_duplicate_email(self):
        response = self.client.post(reverse("signup"), {
            "username": "dup", "first_name": "D", "last_name": "U", "email": "SARA@example.com",
            "password1": "a-Long-pass-987", "password2": "a-Long-pass-987",
        })
        self.assertContains(response, "already exists")


class StaffTests(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.appt = book_appointment(patient=make_patient(), doctor=self.doctor, day=tomorrow(), start=time(9, 0))

    def test_dashboard_requires_staff(self):
        self.client.force_login(make_patient("omar", "omar@example.com"))
        response = self.client.get(reverse("booking:dashboard"))
        self.assertEqual(response.status_code, 302)

    def test_staff_sees_day_and_marks_completed(self):
        staff = User.objects.create_user("desk", password="x", is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(reverse("booking:dashboard"), {"date": tomorrow().isoformat()})
        self.assertContains(response, "Sara")
        self.client.post(reverse("booking:set_status", args=[self.appt.pk]), {"status": "completed"})
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, Appointment.Status.COMPLETED)


class OpsTests(TestCase):
    def test_healthz(self):
        self.assertJSONEqual(self.client.get("/healthz").content, {"status": "ok"})

    def test_seed_demo_is_idempotent(self):
        call_command("seed_demo", stdout=open("/dev/null", "w"))
        call_command("seed_demo", stdout=open("/dev/null", "w"))
        self.assertEqual(User.objects.filter(username="patient").count(), 1)


class CronEndpointTests(TestCase):
    url = "/internal/cron/send-reminders"

    def test_disabled_without_secret(self):
        with self.settings(CRON_SECRET=""):
            self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_rejects_wrong_secret(self):
        with self.settings(CRON_SECRET="s3cret"):
            response = self.client.get(self.url, HTTP_AUTHORIZATION="Bearer nope")
        self.assertEqual(response.status_code, 401)

    def test_sends_with_right_secret(self):
        with self.settings(CRON_SECRET="s3cret"):
            response = self.client.get(self.url, HTTP_AUTHORIZATION="Bearer s3cret")
        self.assertJSONEqual(response.content, {"sent": 0})
