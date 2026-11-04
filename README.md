# Cedar Clinic: clinic booking system (`cedar-clinic`)

A clinic appointment system built with **Django and MySQL**, packaged with **Docker Compose** and deployable to **Kubernetes**.

Patients pick a doctor, choose a free time slot and get email confirmations and reminders. Front-desk staff see each day's schedule and mark visits as completed or no-show. Admins manage doctors, working hours and appointments in the Django admin.

| Pick a slot | Front desk | Mobile |
|---|---|---|
| ![Booking](docs/screenshots/book-slot.png) | ![Front desk](docs/screenshots/front-desk.png) | ![Mobile](docs/screenshots/mobile.png) |

## Features

- **Slot booking.** Free times come from each doctor's weekly working hours and visit length. Slots that are already booked or in the past are hidden.
- **No double booking, enforced by MySQL.** A unique index on `(doctor, date, start_time, holds_slot)` blocks two active bookings for one slot even under concurrent requests. When an appointment is cancelled, `holds_slot` becomes `NULL`, so the slot can be booked again. MySQL has no partial indexes, which is why the design uses a nullable column instead. The booking service also locks the doctor row with `SELECT ... FOR UPDATE`.
- **Email flow.** Confirmation on booking, a notice on cancellation, and a reminder about 24 hours before the visit. `send_reminders` is idempotent, and a failed send is retried on the next run.
- **Patient accounts.** Sign up, log in, see upcoming visits and history, and cancel a visit.
- **Front desk dashboard.** A day view with booked, completed, no-show and cancelled counts, plus one-click status changes.
- **Django admin.** Doctors with inline working hours, appointment search and filters, and bulk actions (complete, no-show, cancel and notify).
- **Ops.** `/healthz` checks the database for probes. The image is a multi-stage build that runs as a non-root user with gunicorn, and static files are served by WhiteNoise.

## Architecture

```
                 ┌──────────────┐
 browser ──────▶ │  web (x2)    │──┐   Django + gunicorn
                 └──────────────┘  │
                 ┌──────────────┐  ├──▶ MySQL 8.4 (StatefulSet + PVC)
 every 10 min ─▶ │ send-reminders│─┤
   (CronJob)     └──────────────┘  └──▶ SMTP (Mailpit locally)
                 ┌──────────────┐
 on deploy ────▶ │ migrate Job  │  migrations + demo data
                 └──────────────┘
```

Code layout:

- `booking/models.py`: `Specialty`, `Doctor`, `WorkingHours`, `Appointment`, plus slot generation.
- `booking/services.py`: booking, cancelling and reminder rules, the single code path used by the views, the admin and the tests.
- `booking/emails.py`: email sending. A mail outage is logged and never breaks a booking.
- `booking/management/commands/`: `seed_demo` and `send_reminders`.
- `k8s/`: Kustomize manifests.

## Run it

Demo logins: `patient` / `demo-pass-123` (patient) and `staff` / `demo-pass-123` (front desk and admin).

### Docker Compose (MySQL + Mailpit)

```bash
cp .env.example .env
docker compose up --build
```

- App: http://localhost:8000
- Caught emails: http://localhost:8025

### Kubernetes (kind)

```bash
kind create cluster --name clinic --config k8s/kind-config.yaml
docker build -t cedar-clinic:local .
kind load docker-image cedar-clinic:local --name clinic
kubectl apply -k k8s/
kubectl -n clinic wait --for=condition=complete job/migrate --timeout=300s
kubectl -n clinic rollout status deployment/web
kubectl -n clinic port-forward svc/web 8000:80      # http://localhost:8000
kubectl -n clinic port-forward svc/mailpit 8025:8025
```

To use the Ingress instead of port-forwarding, install ingress-nginx for kind and open http://clinic.localtest.me:8080:

```bash
kubectl apply -f https://kind.sigs.k8s.io/examples/ingress/deploy-ingress-nginx.yaml
```

What gets deployed: a namespace; MySQL as a StatefulSet with a persistent volume; a two-replica web Deployment with readiness and liveness probes, resource limits and a zero-downtime rolling update; an init container that waits for migrations; a migrate Job; a reminders CronJob; Mailpit; and an Ingress. Config and secrets come from Kustomize generators. The secrets are demo values only, so replace them for a real cluster.

To trigger reminders by hand:

```bash
kubectl -n clinic create job reminders-now --from=cronjob/send-reminders
```

### Without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # mysqlclient needs libmysqlclient-dev and pkg-config
export DJANGO_DEBUG=1             # SQLite is used when DATABASE_URL is unset
python manage.py migrate && python manage.py seed_demo && python manage.py runserver
```

## Tests and CI

```bash
DJANGO_TESTING=1 python manage.py test
```

30 tests cover slot generation, double booking (service and database constraint), cancelling and re-booking, the booking window, the reminder window and idempotency, retry after a mail failure, permissions, and the views.

GitHub Actions (`.github/workflows/ci.yml`) runs three jobs on every push:

1. The test suite against a real **MySQL 8.4** service, with a check for missing migrations.
2. A **Docker Compose** smoke test that builds, starts the stack and checks the app.
3. A **Kubernetes** deploy to a kind cluster that waits for migrations and the rollout, checks `/healthz` through the Service and runs the reminders CronJob once.

## Free live demo: Hugging Face Space + Aiven MySQL

The demo runs at no cost and needs no credit card:

- **App:** a [Hugging Face Docker Space](https://huggingface.co/docs/hub/spaces-sdks-docker) built from this repo's `Dockerfile`.
- **Database:** [Aiven's free MySQL plan](https://aiven.io/free-mysql-database).

A Space runs one container, so it switches on two extras in `docker/entrypoint.sh`: migrations and demo data on start, and a background loop that sends reminders.

Setup, done once:

1. Create a free MySQL service on Aiven and copy its **Service URI** (`mysql://avnadmin:...@...aivencloud.com:PORT/defaultdb?ssl-mode=REQUIRED`).
2. Create a Space with the **Docker** SDK and the blank template. In its **Settings**, add:
   - Secrets: `DATABASE_URL` (the Aiven URI) and `DJANGO_SECRET_KEY` (any long random string).
   - Variables: `RUN_MIGRATIONS_ON_START=1`, `RUN_REMINDER_LOOP=1`, `DJANGO_ALLOW_HF_EMBED=1`, `WEB_CONCURRENCY=2`.
3. On GitHub, go to **Settings > Secrets and variables > Actions** and add:
   - Secret `HF_TOKEN`: a Hugging Face token with write access.
   - Variable `HF_SPACE`: for example `youssefabotaleb20/cedar-clinic`.

From then on, every push to `main` that passes CI is deployed to the Space by `.github/workflows/deploy-space.yml`.

Free Spaces go to sleep after about two days without visitors and wake up on the next visit. Emails go to the console unless you set the `EMAIL_*` variables for an SMTP provider.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | SQLite file | e.g. `mysql://user:pass@host:3306/clinic`; `?ssl-mode=REQUIRED` is honoured |
| `DJANGO_SECRET_KEY` | required unless debug | |
| `DJANGO_DEBUG` | `0` | |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | comma separated |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` | console backend | SMTP settings |
| `CLINIC_NAME` | `Cedar Clinic` | shown in the UI and emails |
| `CLINIC_TIME_ZONE` | `Africa/Cairo` | |
| `BOOKING_WINDOW_DAYS` | `30` | how far ahead patients can book |
| `REMINDER_HOURS_AHEAD` | `24` | when reminders go out |
| `RUN_MIGRATIONS_ON_START` | `0` | single-container hosts: migrate and seed on start |
| `RUN_REMINDER_LOOP` | `0` | single-container hosts: send reminders every `REMINDER_INTERVAL_SECONDS` (600) |
| `DJANGO_ALLOW_HF_EMBED` | `0` | allow the huggingface.co iframe (cross-site cookies, frame-ancestors) |
