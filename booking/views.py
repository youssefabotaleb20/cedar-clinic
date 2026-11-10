from collections import defaultdict
from datetime import time, timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.db import connection
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from .forms import BookingForm, SignUpForm
from .models import Appointment, Doctor, Specialty
from .services import BookingError, book_appointment, booking_window, cancel_appointment


# How many days ahead the doctors list and the booking calendar look for free times.
LOOKAHEAD_DAYS = 21


def _lookahead_days():
    first_day, last_day = booking_window()
    count = min(LOOKAHEAD_DAYS, (last_day - first_day).days + 1)
    return [first_day + timedelta(days=i) for i in range(count)]


def _part_of_day(slots):
    groups = [("Morning", []), ("Afternoon", []), ("Evening", [])]
    for slot in slots:
        index = 0 if slot < time(12) else 1 if slot < time(17) else 2
        groups[index][1].append(slot)
    return [(name, items) for name, items in groups if items]


def home(request):
    doctors = Doctor.objects.filter(is_active=True).select_related("specialty").prefetch_related("working_hours")
    specialty_id = request.GET.get("specialty")
    if specialty_id and specialty_id.isdigit():
        doctors = doctors.filter(specialty_id=specialty_id)
    doctors = list(doctors)

    # One query for every doctor's bookings, then the next few free times per doctor.
    days = _lookahead_days()
    taken = defaultdict(set)
    booked = Appointment.objects.filter(
        doctor__in=doctors, date__in=days, status=Appointment.Status.BOOKED
    ).values_list("doctor_id", "date", "start_time")
    for doctor_id, day, start in booked:
        taken[doctor_id].add((day, start))
    soonest = []
    for doctor in doctors:
        by_day = doctor.free_slots_by_day(days, taken=taken[doctor.pk])
        doctor.next_free = [(day, slot) for day in days for slot in by_day[day]][:3]
        if doctor.next_free:
            soonest.append((doctor.next_free[0], doctor))
    soonest.sort(key=lambda item: item[0])

    return render(
        request,
        "booking/home.html",
        {
            "doctors": doctors,
            "soonest": [(doctor, day, slot) for (day, slot), doctor in soonest[:3]],
            "specialties": Specialty.objects.filter(doctors__is_active=True).distinct(),
            "selected_specialty": specialty_id,
            "today": timezone.localdate(),
        },
    )


def doctor_detail(request, pk):
    doctor = get_object_or_404(
        Doctor.objects.select_related("specialty").prefetch_related("working_hours"), pk=pk, is_active=True
    )
    first_day, last_day = booking_window()
    window = [first_day + timedelta(days=i) for i in range((last_day - first_day).days + 1)]
    working_weekdays = {hours.weekday for hours in doctor.working_hours.all()}
    open_days = [d for d in window if d.weekday() in working_weekdays]

    days = _lookahead_days()
    free = doctor.free_slots_by_day(days)
    calendar = [
        {"day": d, "open": d.weekday() in working_weekdays, "free": len(free[d])} for d in days
    ]

    selected = parse_date(request.GET.get("date", "")) if request.GET.get("date") else None
    if selected not in open_days:
        selected = next((d for d in days if free[d]), None)

    form = BookingForm(request.POST or None)
    if request.method == "POST":
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if form.is_valid():
            try:
                appointment = book_appointment(
                    patient=request.user,
                    doctor=doctor,
                    day=form.cleaned_data["date"],
                    start=form.cleaned_data["start_time"],
                    reason=form.cleaned_data["reason"],
                )
            except BookingError as exc:
                messages.error(request, str(exc))
                selected = form.cleaned_data["date"]
            else:
                messages.success(
                    request,
                    f"Booked with {doctor} on {appointment.date:%A %d %B} at "
                    f"{appointment.start_time:%H:%M}. A confirmation email is on its way.",
                )
                return redirect("booking:my_appointments")
        else:
            messages.error(request, "Please pick a time slot.")

    if selected is None:
        slots = []
    elif selected in free:
        slots = doctor.available_slots(selected) if request.method == "POST" else free[selected]
    else:
        slots = doctor.available_slots(selected)

    return render(
        request,
        "booking/doctor_detail.html",
        {
            "doctor": doctor,
            "calendar": calendar,
            "selected": selected,
            "slots": slots,
            "slot_groups": _part_of_day(slots),
            "preselected": request.POST.get("start_time") or request.GET.get("time", ""),
            "form": form,
        },
    )


@login_required
def my_appointments(request):
    now = timezone.localtime()
    appointments = request.user.appointments.select_related("doctor", "doctor__specialty")
    upcoming = [a for a in appointments.filter(status=Appointment.Status.BOOKED) if a.starts_at > now]
    past = [a for a in appointments.order_by("-date", "-start_time") if a not in upcoming][:20]
    return render(request, "booking/my_appointments.html", {"upcoming": upcoming, "past": past})


@login_required
@require_POST
def cancel(request, pk):
    appointment = get_object_or_404(Appointment, pk=pk, patient=request.user)
    try:
        cancel_appointment(appointment)
    except BookingError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Your appointment was cancelled.")
    return redirect("booking:my_appointments")


def signup(request):
    if request.user.is_authenticated:
        return redirect("booking:home")
    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, f"Welcome, {user.first_name}. You can book an appointment now.")
        return redirect(request.GET.get("next") or "booking:home")
    return render(request, "registration/signup.html", {"form": form})


@staff_member_required
def dashboard(request):
    day = parse_date(request.GET.get("date", "")) or timezone.localdate()
    appointments = (
        Appointment.objects.filter(date=day)
        .exclude(status=Appointment.Status.CANCELLED)
        .select_related("doctor", "patient")
        .order_by("doctor__name", "start_time")
    )
    counts = {
        status: Appointment.objects.filter(date=day, status=status).count()
        for status in Appointment.Status.values
    }
    return render(
        request,
        "booking/dashboard.html",
        {
            "day": day,
            "previous_day": day - timedelta(days=1),
            "next_day": day + timedelta(days=1),
            "appointments": appointments,
            "counts": counts,
            "statuses": Appointment.Status,
        },
    )


@staff_member_required
@require_POST
def set_status(request, pk):
    appointment = get_object_or_404(Appointment, pk=pk)
    status = request.POST.get("status")
    if status == Appointment.Status.CANCELLED:
        cancel_appointment(appointment, by_staff=True)
    elif status in (Appointment.Status.COMPLETED, Appointment.Status.NO_SHOW):
        appointment.status = status
        appointment.save(update_fields=["status", "holds_slot"])
    else:
        raise Http404("Unknown status")
    patient = appointment.patient.get_full_name() or appointment.patient.username
    messages.success(request, f"{patient} marked {appointment.get_status_display().lower()}.")
    return redirect(f"{reverse('booking:dashboard')}?date={appointment.date:%Y-%m-%d}")


def healthz(request):
    """Liveness/readiness probe: the app is up and can reach the database."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "error", "database": "unreachable"}, status=503)
    return JsonResponse({"status": "ok"})


def cron_send_reminders(request):
    """Reminders endpoint for Vercel Cron, which has no shell to run the command.

    Vercel sends `Authorization: Bearer <CRON_SECRET>`; anything else is refused.
    """
    from django.conf import settings
    from django.utils.crypto import constant_time_compare

    from .services import send_due_reminders

    if not settings.CRON_SECRET:
        raise Http404("Cron endpoint is disabled")
    expected = f"Bearer {settings.CRON_SECRET}"
    if not constant_time_compare(request.headers.get("Authorization", ""), expected):
        return JsonResponse({"error": "unauthorized"}, status=401)
    return JsonResponse({"sent": send_due_reminders()})
