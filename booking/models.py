from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Specialty(models.Model):
    name = models.CharField(max_length=80, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "specialties"

    def __str__(self):
        return self.name


class Doctor(models.Model):
    name = models.CharField(max_length=120)
    specialty = models.ForeignKey(Specialty, on_delete=models.PROTECT, related_name="doctors")
    bio = models.TextField(blank=True)
    slot_minutes = models.PositiveSmallIntegerField(
        default=30, help_text="Length of one appointment in minutes."
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"Dr. {self.name}"

    def available_slots(self, day: date) -> list[time]:
        """Free start times for `day`, excluding booked slots and times already past."""
        return self.free_slots_by_day([day])[day]

    def free_slots_by_day(self, days, *, taken=None) -> dict[date, list[time]]:
        """Free start times for each of `days` in at most two queries.

        `taken` is an optional set of (date, start_time) pairs already booked, so a
        caller listing many doctors can load bookings for all of them at once.
        Working hours come from `working_hours.all()`, which uses a prefetch if present.
        """
        days = list(days)
        if taken is None:
            taken = set(
                self.appointments.filter(date__in=days, status=Appointment.Status.BOOKED).values_list(
                    "date", "start_time"
                )
            )
        hours = list(self.working_hours.all())
        slot = timedelta(minutes=self.slot_minutes)
        now = timezone.localtime()
        result = {}
        for day in days:
            slots = []
            for block in hours:
                if block.weekday != day.weekday():
                    continue
                cursor = datetime.combine(day, block.start_time)
                end = datetime.combine(day, block.end_time)
                while cursor + slot <= end:
                    if (day, cursor.time()) not in taken and timezone.make_aware(cursor) > now:
                        slots.append(cursor.time())
                    cursor += slot
            result[day] = sorted(slots)
        return result


class WorkingHours(models.Model):
    class Weekday(models.IntegerChoices):
        MONDAY = 0
        TUESDAY = 1
        WEDNESDAY = 2
        THURSDAY = 3
        FRIDAY = 4
        SATURDAY = 5
        SUNDAY = 6

    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="working_hours")
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ["doctor", "weekday", "start_time"]
        verbose_name_plural = "working hours"

    def __str__(self):
        return f"{self.get_weekday_display()} {self.start_time:%H:%M}-{self.end_time:%H:%M}"

    def clean(self):
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError("End time must be after start time.")


class Appointment(models.Model):
    class Status(models.TextChoices):
        BOOKED = "booked", "Booked"
        CANCELLED = "cancelled", "Cancelled"
        COMPLETED = "completed", "Completed"
        NO_SHOW = "no_show", "No-show"

    doctor = models.ForeignKey(Doctor, on_delete=models.PROTECT, related_name="appointments")
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="appointments"
    )
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.BOOKED)
    # True while the appointment holds its slot, NULL once it doesn't.
    # MySQL (and every other backend) allows many NULLs in a unique index, so the
    # constraint below stops double booking at the database level while still
    # letting a cancelled slot be booked again. MySQL has no partial indexes,
    # which is why this is not a conditional UniqueConstraint.
    holds_slot = models.BooleanField(null=True, default=True, editable=False)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "start_time"]
        constraints = [
            models.UniqueConstraint(
                fields=["doctor", "date", "start_time", "holds_slot"],
                name="one_active_booking_per_slot",
            )
        ]
        indexes = [models.Index(fields=["status", "date"])]

    def __str__(self):
        return f"{self.doctor} with {self.patient} on {self.date} {self.start_time:%H:%M}"

    def save(self, *args, **kwargs):
        self.holds_slot = True if self.status == self.Status.BOOKED else None
        super().save(*args, **kwargs)

    @property
    def starts_at(self):
        return timezone.make_aware(datetime.combine(self.date, self.start_time))

    @property
    def can_cancel(self):
        return self.status == self.Status.BOOKED and self.starts_at > timezone.now()

    def cancel(self):
        self.status = self.Status.CANCELLED
        self.save(update_fields=["status", "holds_slot"])
