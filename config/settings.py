"""Django settings for the clinic booking system.

Everything environment-specific comes from environment variables so the same
code runs locally, in Docker Compose, on Kubernetes and on Vercel.
"""

import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# Vercel sets VERCEL=1 in builds and functions.
ON_VERCEL = bool(os.environ.get("VERCEL"))

# MySQL driver: mysqlclient (C extension) in the Docker image, PyMySQL (pure
# Python) where a C toolchain isn't available, such as Vercel.
try:
    import MySQLdb  # noqa: F401

    MYSQL_DRIVER = "mysqlclient"
except ImportError:
    import pymysql

    pymysql.version_info = (2, 2, 7, "final", 0)  # Django checks for mysqlclient >= 2.2.1
    pymysql.install_as_MySQLdb()
    MYSQL_DRIVER = "pymysql"


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG or os.environ.get("DJANGO_TESTING"):
        SECRET_KEY = "dev-only-insecure-key"
    else:
        raise RuntimeError("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

if ON_VERCEL:
    ALLOWED_HOSTS.append(".vercel.app")
    CSRF_TRUSTED_ORIGINS.append("https://*.vercel.app")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "booking",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "booking.context_processors.clinic",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# DATABASE_URL, e.g. mysql://clinic:secret@db:3306/clinic
# Falls back to SQLite so tests and a quick local run need no server.
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        # Serverless functions can't share pooled connections between requests.
        conn_max_age=0 if ON_VERCEL else 60,
    )
}
if DATABASES["default"]["ENGINE"] == "django.db.backends.mysql":
    db_options = DATABASES["default"].setdefault("OPTIONS", {})
    db_options.update({"charset": "utf8mb4", "init_command": "SET sql_mode='STRICT_TRANS_TABLES'"})
    # Hosted MySQL (TiDB Cloud, Aiven) hands out URIs ending in ?ssl-mode=REQUIRED
    # or VERIFY_IDENTITY. Translate that into each driver's own options.
    ssl_mode = (db_options.pop("ssl-mode", None) or os.environ.get("DB_SSL_MODE") or "").upper()
    ssl_ca = os.environ.get("DB_SSL_CA")
    if ssl_mode.startswith("VERIFY") or ssl_ca:
        import certifi

        # Verified TLS: CA bundle plus hostname check (the default for both drivers).
        db_options["ssl"] = {"ca": ssl_ca or certifi.where()}
        if MYSQL_DRIVER == "mysqlclient":
            db_options["ssl_mode"] = ssl_mode or "VERIFY_IDENTITY"
    elif ssl_mode == "REQUIRED":
        if MYSQL_DRIVER == "mysqlclient":
            db_options["ssl_mode"] = "REQUIRED"
        else:
            db_options["ssl"] = {"check_hostname": False}  # encrypted, unverified

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("CLINIC_TIME_ZONE", "Africa/Cairo")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
if os.environ.get("DJANGO_TESTING") or ON_VERCEL:
    STORAGES["staticfiles"]["BACKEND"] = "django.contrib.staticfiles.storage.StaticFilesStorage"
if ON_VERCEL:
    # No collectstatic step on Vercel: WhiteNoise serves straight from the app
    # and package static folders.
    WHITENOISE_USE_FINDERS = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "booking:my_appointments"
LOGOUT_REDIRECT_URL = "booking:home"

# Email: console in development, SMTP when EMAIL_HOST is set (Mailpit in Compose).
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
if EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", False)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "Cedar Clinic <no-reply@cedarclinic.local>")

CLINIC_NAME = os.environ.get("CLINIC_NAME", "Cedar Clinic")
# How far ahead patients may book, and how early reminders go out.
BOOKING_WINDOW_DAYS = int(os.environ.get("BOOKING_WINDOW_DAYS", "30"))
REMINDER_HOURS_AHEAD = int(os.environ.get("REMINDER_HOURS_AHEAD", "24"))
# Shared secret for the reminders endpoint called by Vercel Cron.
CRON_SECRET = os.environ.get("CRON_SECRET", "")

if not DEBUG:
    SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", True)
    CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}
