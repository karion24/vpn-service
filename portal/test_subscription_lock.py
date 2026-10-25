import multiprocessing
import time
from unittest import TestCase

from portal.subscription_lock import (
    SubscriptionLockTimeout,
    subscription_lock,
)


def hold_lock(client_id, ready, release):
    with subscription_lock(client_id, timeout=3):
        ready.set()
        release.wait(timeout=5)


class SubscriptionLockTests(TestCase):

    def test_basic_acquire_and_release(self):
        with subscription_lock(910001, timeout=1):
            pass

        with subscription_lock(910001, timeout=1):
            pass

    def test_invalid_client_id(self):
        with self.assertRaises(ValueError):
            with subscription_lock(0):
                pass

    def test_different_clients_do_not_block(self):
        with subscription_lock(910002, timeout=1):
            with subscription_lock(910003, timeout=1):
                pass

    def test_cross_process_timeout_and_release(self):
        ctx = multiprocessing.get_context("spawn")
        ready = ctx.Event()
        release = ctx.Event()

        process = ctx.Process(
            target=hold_lock,
            args=(910004, ready, release),
        )
        process.start()

        try:
            self.assertTrue(
                ready.wait(timeout=5),
                "Дочерний процесс не захватил блокировку",
            )

            start = time.monotonic()

            with self.assertRaises(SubscriptionLockTimeout):
                with subscription_lock(910004, timeout=0.2):
                    pass

            self.assertGreaterEqual(
                time.monotonic() - start,
                0.18,
            )

        finally:
            release.set()
            process.join(timeout=5)

            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

        self.assertEqual(process.exitcode, 0)

        with subscription_lock(910004, timeout=1):
            pass
