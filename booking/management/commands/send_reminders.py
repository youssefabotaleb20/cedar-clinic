from django.core.management.base import BaseCommand

from booking.services import send_due_reminders


class Command(BaseCommand):
    help = "Email patients whose appointment starts within REMINDER_HOURS_AHEAD. Safe to run often."

    def handle(self, *args, **options):
        sent = send_due_reminders()
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} reminder(s)."))
