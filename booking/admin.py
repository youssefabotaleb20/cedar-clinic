from django.contrib import admin, messages

from .models import Appointment, Doctor, Specialty, WorkingHours
from .services import cancel_appointment


class WorkingHoursInline(admin.TabularInline):
    model = WorkingHours
    extra = 1


@admin.register(Specialty)
class SpecialtyAdmin(admin.ModelAdmin):
    search_fields = ["name"]


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ["name", "specialty", "slot_minutes", "is_active"]
    list_filter = ["specialty", "is_active"]
    search_fields = ["name"]
    inlines = [WorkingHoursInline]


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ["date", "start_time", "doctor", "patient", "status", "reminder_sent_at"]
    list_filter = ["status", "doctor", "date"]
    search_fields = ["patient__username", "patient__email", "patient__last_name", "doctor__name"]
    date_hierarchy = "date"
    list_select_related = ["doctor", "patient"]
    readonly_fields = ["created_at", "reminder_sent_at"]
    actions = ["mark_completed", "mark_no_show", "cancel_selected"]

    @admin.action(description="Mark selected as completed")
    def mark_completed(self, request, queryset):
        self._set_status(request, queryset, Appointment.Status.COMPLETED)

    @admin.action(description="Mark selected as no-show")
    def mark_no_show(self, request, queryset):
        self._set_status(request, queryset, Appointment.Status.NO_SHOW)

    @admin.action(description="Cancel selected and email the patients")
    def cancel_selected(self, request, queryset):
        booked = queryset.filter(status=Appointment.Status.BOOKED)
        for appointment in booked:
            cancel_appointment(appointment, by_staff=True)
        self.message_user(request, f"Cancelled {len(booked)} appointment(s).", messages.SUCCESS)

    def _set_status(self, request, queryset, status):
        # save() per row keeps holds_slot in sync with status.
        for appointment in queryset:
            appointment.status = status
            appointment.save(update_fields=["status", "holds_slot"])
        self.message_user(request, f"Updated {queryset.count()} appointment(s).", messages.SUCCESS)
