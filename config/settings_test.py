from .settings import *

# Тестовая база отделена от рабочей.
DATABASES["default"]["NAME"] = BASE_DIR / "karion_portal_test.sqlite3"
DATABASES["default"]["TEST"] = {
    "NAME": str(BASE_DIR / "karion_portal_test_runner.sqlite3"),
}

# Тесты не должны обращаться к рабочим внешним сервисам.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
