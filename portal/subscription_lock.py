"""
Межпроцессная блокировка синхронизации VPN-подписки.

Linux flock. Не использовать внутри открытой транзакции БД.
Все операции Remnawave для одного client_id должны
использовать одинаковый файл блокировки.
"""

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path


LOCK_DIR = Path("/run/lock/karion-portal")


class SubscriptionLockTimeout(TimeoutError):
    pass


@contextmanager
def subscription_lock(client_id, timeout=30):
    client_id = int(client_id)

    if client_id <= 0:
        raise ValueError("client_id должен быть положительным")

    if timeout < 0:
        raise ValueError("timeout не может быть отрицательным")

    LOCK_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)

    lock_path = LOCK_DIR / f"subscription_{client_id}.lock"

    fd = os.open(
        str(lock_path),
        os.O_CREAT | os.O_RDWR | os.O_CLOEXEC,
        0o600,
    )

    acquired = False

    try:
        deadline = time.monotonic() + timeout

        while True:
            try:
                fcntl.flock(
                    fd,
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise SubscriptionLockTimeout(
                        f"Не удалось заблокировать подписку "
                        f"#{client_id} за {timeout} секунд"
                    )

                time.sleep(
                    min(0.05, max(0, deadline - time.monotonic()))
                )

        yield

    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)

        os.close(fd)
