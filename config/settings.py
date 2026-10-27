from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "dev-only-change-me"
)

DEBUG = True

ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
    "panel.karion24.ru",
    "lk.karion24.ru",
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    "portal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Asia/Krasnoyarsk"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = "/opt/karion-portal/staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Customer cabinet authentication
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/cabinet/"
LOGOUT_REDIRECT_URL = "/login/"

# Expiry reminders and messenger contracts from the Karion dashboard.
KARION_CABINET_URL = os.getenv("KARION_CABINET_URL", "https://lk.karion24.ru/cabinet/")
KARION_REMINDER_LOCK_FILE = os.getenv("KARION_REMINDER_LOCK_FILE", "/tmp/karion-subscription-reminders.lock")
KARION_NOTIFICATION_BACKENDS = {
    "telegram": os.getenv("KARION_TELEGRAM_NOTIFICATION_BACKEND", "portal.notification_backends.send_telegram"),
    "max": os.getenv("KARION_MAX_NOTIFICATION_BACKEND", "portal.notification_backends.send_max"),
}
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.yandex.ru")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "465"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "info@karion24.ru")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "0") == "1"
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "1") == "1"
EMAIL_TIMEOUT = 15
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "Karion <info@karion24.ru>")

TELEGRAM_BOT_SERVER_URL = os.getenv("TELEGRAM_BOT_SERVER_URL", "")
TELEGRAM_BOT_SERVER_TOKEN = os.getenv("TELEGRAM_BOT_SERVER_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN", "")

# Confirm on the actual Karion gateway before enabling its response validator.
TELEGRAM_BOT_SERVER_RESPONSE_VALIDATOR = os.getenv("TELEGRAM_BOT_SERVER_RESPONSE_VALIDATOR", "")
