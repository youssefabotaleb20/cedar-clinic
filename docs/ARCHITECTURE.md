# Cedar Clinic architecture

This document covers what the README doesn't: how a request moves through the app, the data model, the security model, the main decisions (and the options that were rejected), known limits, and what would come next.

## Request flow

### Booking a slot

1. The patient opens `/doctors/<id>/`. `Doctor.available_slots(day)` builds the free start times from the doctor's `WorkingHours` for that weekday, stepping by `slot_minutes`, and drops slots that are already booked or in the past.
2. The patient submits the form (POST, CSRF-protected, login required). The view calls `booking.services.book_appointment`, which holds all the booking rules.
3. Inside one transaction, the service:
   - checks the date is inside the booking window (`BOOKING_WINDOW_DAYS`),
   - locks the doctor row with `SELECT ... FOR UPDATE`, so concurrent bookings for the same doctor run one after another,
   - checks the slot is still free and that the patient has no other booking at that time,
   - inserts the appointment. If the database's unique constraint rejects it, the `IntegrityError` becomes a friendly "that time was just taken" message.
4. The confirmation email is queued with `transaction.on_commit`, so no email is sent for a booking that rolled back.

### Reminders

`send_due_reminders` finds booked appointments that start within `REMINDER_HOURS_AHEAD` and have no `reminder_sent_at`. It sends each email, then marks the row with a conditional `UPDATE ... WHERE reminder_sent_at IS NULL`, so two overlapping runs can't both send. A failed send leaves the row unmarked and it is retried on the next run.

The same function is triggered three ways, depending on where the app runs:

| Where | Trigger |
|---|---|
| Docker Compose | a `reminders` service running a loop |
| Kubernetes | the `send-reminders` CronJob, every 10 minutes |
| Vercel | Vercel Cron calls `/internal/cron/send-reminders` once a day with `Authorization: Bearer $CRON_SECRET` |

### Front desk

Staff open `/staff/?date=...` for a day view with counts per status, and change a visit's status with a POST to `/staff/appointments/<id>/status/`. Cancelling from here goes through `cancel_appointment(by_staff=True)`, which skips the "only future visits" rule that applies to patients.

## Data model

```
Specialty 1───* Doctor 1───* WorkingHours (weekday, start_time, end_time)
                  │
                  1
                  │
                  * Appointment *───1 User (patient)
                     date, start_time, end_time, reason,
                     status: booked | cancelled | completed | no_show,
                     holds_slot (True or NULL), reminder_sent_at
```

- Slots are not stored. They are computed from `WorkingHours` and `slot_minutes`, so changing a doctor's hours takes effect at once and there is no slot table to keep in sync.
- `Appointment` has an index on `(status, date)` for the dashboard and the reminder query.
- Doctors use `on_delete=PROTECT`, so a doctor with appointment history can't be deleted by accident. Set `is_active` to false instead.

## Security model

- **Patients** must log in to book, and they only see and cancel their own appointments. A cancel request for someone else's appointment returns 404.
- **Staff** (`is_staff`) get the front-desk dashboard and the Django admin, protected by `staff_member_required`.
- All state changes are POST-only and CSRF-protected. Cookies are `Secure` when debug is off, and the app trusts `X-Forwarded-Proto` from the proxy in front of it.
- The cron endpoint is disabled (404) unless `CRON_SECRET` is set. When it is set, the bearer token is compared in constant time.
- Secrets come only from environment variables. Without `DJANGO_SECRET_KEY` the app refuses to start unless debug or test mode is on.
- Database traffic to hosted MySQL uses verified TLS (`ssl-mode=VERIFY_IDENTITY`, CA bundle from certifi).
- The Docker image runs as a non-root user.

## Key decisions

### Double booking is blocked by the database, not only by code

**Chosen:** a unique constraint on `(doctor, date, start_time, holds_slot)`. `holds_slot` is `True` while an appointment is booked and `NULL` otherwise. Unique indexes allow many `NULL`s, so any number of cancelled appointments can share a slot, but only one booked appointment can.

**Rejected:**
- *A partial unique index (`WHERE status = 'booked'`).* This is the obvious design, but MySQL doesn't support partial indexes. Django doesn't create conditional constraints on MySQL and only shows a system-check warning.
- *Code checks only.* Two requests can both see the slot as free before either one inserts. The row lock covers that in normal use, and the constraint is the guarantee for any path that doesn't lock, such as adding an appointment by hand in the Django admin.

### One service module for all business rules

Views, the admin's cancel action, the management commands, the cron endpoint and the tests all call `booking/services.py`. Cancelling from the admin sends the same email as cancelling from the site, because both use `cancel_appointment`.

### Email failures never break a booking

`emails._send` logs the failure and returns `False` instead of raising. A patient whose booking succeeded shouldn't see an error page because the SMTP server is down. Reminders use the return value to retry later.

### Two MySQL drivers

**Chosen:** `mysqlclient` (C extension, fastest) in the Docker image, and PyMySQL (pure Python) on Vercel, where there is no C toolchain at build time. `config/settings.py` picks whichever is installed. CI runs the full test suite on both: MySQL 8.4 with mysqlclient, and TiDB 8.5 with PyMySQL only, which matches production.

### Free hosting: Vercel Hobby + TiDB Cloud Starter

The demo must cost nothing and need no card. Options tried, in order:

| Option | Why it was dropped |
|---|---|
| Render | asks for a card even on the free plan |
| Aiven MySQL | no free MySQL for new accounts, only a 30-day trial |
| Hugging Face Docker Space | Docker Spaces are paid on this account |
| **Vercel + TiDB Cloud** | **chosen**: no card, $0 spending limit on TiDB, MySQL-compatible |

What changed to fit serverless:
- `api/index.py` exposes the WSGI app as a Python function, and `vercel.json` rewrites every path to it.
- Persistent DB connections are off (`conn_max_age=0`), because functions don't share a pool between requests.
- There is no `collectstatic` step. WhiteNoise serves static files from the app folders.
- Migrations can't run inside a build, so `migrate-production.yml` runs them from GitHub Actions after CI passes on `main`.
- The reminder loop became an authenticated cron endpoint.

Docker Compose and Kubernetes stay the main way to run the full stack. Vercel is only the free public demo.

### Kubernetes manifests with Kustomize, tested on kind

Plain manifests plus Kustomize keep it readable without Helm templating. CI deploys them to a real kind cluster on every push, waits for the migrate Job and the rollout, checks `/healthz` and runs the CronJob once, so the manifests can't quietly rot.

## Known limits

- **Reminders on Vercel run once a day.** The Hobby plan allows only daily cron jobs, so a patient gets the reminder somewhere between 0 and 24 hours before the visit, not at exactly 24 hours. Compose and Kubernetes run it every 10 minutes.
- **No real email on the live demo.** With no SMTP provider configured, emails go to the function logs. Setting the `EMAIL_*` variables for any SMTP service turns them on.
- **One clinic time zone.** All times use `CLINIC_TIME_ZONE`. Patients in other time zones see clinic time.
- **Fixed-length visits.** Every visit with a doctor is `slot_minutes` long. There are no visit types of different lengths.
- **Cold starts.** The first request after a quiet period on Vercel can take a few seconds.
- **Demo secrets in `k8s/`.** The Kustomize secret generator holds demo values and must be replaced for a real cluster.

## What I'd do next

- Visit types with their own length, and a buffer between visits.
- Doctor leave and holidays that hide slots for a date range.
- SMS or WhatsApp reminders next to email.
- A waiting list that offers a cancelled slot to the next patient.
- Calendar invites (`.ics`) attached to the confirmation email.
- Rate limiting on sign-up and login.
