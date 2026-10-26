import io
import multiprocessing
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from app.core import model_api as api
from app.core import model_request_guard as guard


def _competing_process(path, queue, max_concurrency=1):
    try:
        guard.ModelRequestCoordinator(
            Path(path), clock=lambda: 1000, max_concurrency=max_concurrency
        ).acquire("scope", duration=10)
        queue.put("admitted")
    except guard.ModelAdmissionError as exc:
        queue.put(exc.reason)


class ModelRequestGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="niuone-model-guard-")
        self.addCleanup(self.temp.cleanup)
        self.now = 1000.0
        self.path = Path(self.temp.name) / "guard.sqlite3"
        self.a = guard.ModelRequestCoordinator(self.path, clock=lambda: self.now)
        self.b = guard.ModelRequestCoordinator(self.path, clock=lambda: self.now)

    def _run_competing_process(self, *, max_concurrency=1):
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        process = context.Process(
            target=_competing_process,
            args=(str(self.path), queue, max_concurrency),
        )
        process.start()
        try:
            result = queue.get(timeout=10)
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)
            return result
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            queue.close()

    def test_processes_share_leases_and_crashes_expire(self):
        # A single-lease process must still observe an exclusive scope, so a
        # pre-multi-lease deployment cannot overlap its in-flight request.
        solo = guard.ModelRequestCoordinator(
            self.path, clock=lambda: self.now, max_concurrency=1
        )
        lease = solo.acquire("scope", duration=10)
        self.assertEqual(self._run_competing_process(max_concurrency=1), "busy")
        self.now += 16
        replacement = self.b.acquire("scope", duration=10)
        solo.release("scope", lease)  # A stale owner cannot release the new lease.
        self.now += 2  # Clear the spacing gate the new acquisition set.
        with self.assertRaisesRegex(guard.ModelAdmissionError, "busy"):
            solo.acquire("scope", duration=10)
        self.b.release("scope", replacement)

    def test_independent_consumers_share_the_configured_slots(self):
        # Up to the configured cap may be in flight at once; beyond it the scope
        # is busy and reports the wait for the lease that frees first.
        limited = guard.ModelRequestCoordinator(
            self.path, clock=lambda: self.now, max_concurrency=2
        )
        leases = [limited.acquire("scope", duration=10)]
        self.now += 2  # Clear the spacing gate between acquisitions.
        leases.append(limited.acquire("scope", duration=10))
        self.now += 2
        try:
            limited.acquire("scope", duration=10)
            self.fail("expected the third concurrent request to be rejected")
        except guard.ModelAdmissionError as exc:
            self.assertEqual(exc.reason, "busy")
            # The oldest lease is still 10s from the wall clock, plus the 5s
            # grace the row carries, less the 4s already advanced.
            self.assertAlmostEqual(exc.retry_after, 11.0, places=1)
        # A separate process sees the same full scope across the database.
        self.assertEqual(self._run_competing_process(max_concurrency=2), "busy")
        for held in leases:
            limited.release("scope", held)
        self.now += 2
        self.assertNotEqual(limited.acquire("scope", duration=10), "")

    def test_spacing_backoff_header_and_success_reset(self):
        lease = self.a.acquire("scope", duration=10)
        self.assertEqual(self.a.rate_limited("scope", lease, None), 60)
        self.a.release("scope", lease)
        with self.assertRaisesRegex(guard.ModelAdmissionError, "rate_limited"):
            self.b.acquire("scope", duration=10)
        self.now += 60
        lease = self.b.acquire("scope", duration=10)
        self.assertEqual(self.b.rate_limited("scope", lease, "600"), 600)
        self.b.release("scope", lease)
        self.now += 600
        lease = self.a.acquire("scope", duration=10)
        self.a.release("scope", lease, success=True)
        with self.assertRaisesRegex(guard.ModelAdmissionError, "spacing"):
            self.b.acquire("scope", duration=10)
        self.now += 2
        lease = self.b.acquire("scope", duration=10)
        self.assertEqual(self.b.rate_limited("scope", lease, None), 60)

    def _admission_probe(self, coordinator, *, monotonic, sleep=None):
        """Drive _model_response through admission with no network transport."""

        def opener(req, **kwargs):
            return io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')

        request = urllib.request.Request("https://api.example/v1/chat/completions")
        patches = [
            patch.object(api, "_STANDARD_OPENER", opener),
            patch.object(api, "shared_model_coordinator", return_value=coordinator),
            patch.object(api.time, "monotonic", side_effect=monotonic),
        ]
        if sleep is not None:
            patches.append(patch.object(api.time, "sleep", side_effect=sleep))
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return api._model_response(request, "key", opener, timeout=30.0)

    def test_queue_waits_until_the_budget_can_still_be_used(self):
        # Queuing must wait for a slot while the result is still usable, instead
        # of failing after MAX_QUEUE_SECONDS and skipping a later cycle.
        now = [1000.0]

        class BusyThenFree(guard.ModelRequestCoordinator):
            def __init__(self, path):  # noqa: D107 - test double
                super().__init__(path, clock=lambda: now[0], max_concurrency=1)
                self.attempts = 0

            def acquire(self, scope, *, duration, rate_scope=None):
                self.attempts += 1
                if self.attempts < 3:
                    # The oldest lease frees well inside this caller's budget.
                    raise guard.ModelAdmissionError("busy", 1.5)
                return super().acquire(scope, duration=duration, rate_scope=rate_scope)

        coordinator = BusyThenFree(self.path)
        slept: list[float] = []
        with patch.object(guard, "max_concurrent_requests", return_value=1):
            with self._admission_probe(
                coordinator,
                monotonic=lambda: now[0],
                sleep=lambda value: slept.append(value),
            ):
                pass
        self.assertEqual(coordinator.attempts, 3)
        self.assertTrue(slept)
        self.assertTrue(all(0 < value <= 0.1 for value in slept))

    def test_queue_abandons_a_slot_that_arrives_without_budget(self):
        # Waiting for a slot that is admitted too late to finish must fail now
        # rather than burn the caller's deadline on an unusable round trip.
        now = [1000.0]

        class BusyForever(guard.ModelRequestCoordinator):
            def __init__(self, path):  # noqa: D107 - test double
                super().__init__(path, clock=lambda: now[0], max_concurrency=1)

            def acquire(self, scope, *, duration, rate_scope=None):
                raise guard.ModelAdmissionError("busy", 40.0)

        with patch.object(guard, "max_concurrent_requests", return_value=1):
            with self.assertRaisesRegex(guard.ModelAdmissionError, "busy"):
                with self._admission_probe(
                    BusyForever(self.path),
                    monotonic=lambda: now[0],
                    sleep=lambda value: now.__setitem__(0, now[0] + value),
                ):
                    pass

    def test_spacing_shrinks_as_the_shared_cap_grows(self):
        # Raising concurrency alone must not leave throughput pinned at one
        # request per spacing interval, and an explicit override still wins.
        cases = {1: 2.0, 2: 1.0, 3: 2.0 / 3, 5: 0.4}
        for cap, expected in cases.items():
            with patch.dict(os.environ, {"DASHBOARD_MODEL_MAX_CONCURRENCY": str(cap)}):
                self.assertAlmostEqual(
                    guard.request_spacing_seconds(cap), expected, places=2
                )
                coordinator = guard.ModelRequestCoordinator(
                    self.path, clock=lambda: self.now, max_concurrency=cap
                )
                self.assertAlmostEqual(coordinator.interval, expected, places=2)
        with patch.dict(os.environ, {"DASHBOARD_MODEL_REQUEST_SPACING_SECONDS": "1.5"}):
            self.assertEqual(guard.request_spacing_seconds(3), 1.5)
        for bad in ("", "nan", "abc"):
            with patch.dict(os.environ, {"DASHBOARD_MODEL_REQUEST_SPACING_SECONDS": bad}):
                self.assertGreater(guard.request_spacing_seconds(3), 0.0)
        with patch.dict(os.environ, {"DASHBOARD_MODEL_REQUEST_SPACING_SECONDS": "99"}):
            self.assertEqual(guard.request_spacing_seconds(3), guard.MIN_REQUEST_INTERVAL)
        # An explicit interval keeps its historical meaning.
        pinned = guard.ModelRequestCoordinator(
            self.path, clock=lambda: self.now, interval=5.0
        )
        self.assertEqual(pinned.interval, 5.0)

    def test_model_cooldown_does_not_block_other_models(self):
        # A per-model quota 429 must not take every model on the credential
        # offline for the whole Retry-After window.
        first = guard.rate_limit_scope("scope", "model-a")
        second = guard.rate_limit_scope("scope", "model-b")
        self.assertNotEqual(first, second)
        lease = self.a.acquire("scope", duration=10, rate_scope=first)
        self.assertEqual(
            self.a.rate_limited("scope", lease, "1800", rate_scope=first), 1800
        )
        self.a.release("scope", lease, rate_scope=first)
        with self.assertRaisesRegex(guard.ModelAdmissionError, "rate_limited"):
            self.b.acquire("scope", duration=10, rate_scope=first)
        self.now += 2  # Clear the spacing gate from the first acquisition.
        other = self.b.acquire("scope", duration=10, rate_scope=second)
        self.b.release("scope", other, rate_scope=second)
        self.now += 1800
        self.now += 2
        recovered = self.a.acquire("scope", duration=10, rate_scope=first)
        self.a.release("scope", recovered, rate_scope=first)

    def test_leases_stay_credential_wide_across_models(self):
        # The in-flight cap is still one credential budget; only the cooldown
        # is per model.
        limited = guard.ModelRequestCoordinator(
            self.path, clock=lambda: self.now, max_concurrency=1
        )
        held = limited.acquire(
            "scope", duration=10, rate_scope=guard.rate_limit_scope("scope", "model-a")
        )
        with self.assertRaisesRegex(guard.ModelAdmissionError, "busy"):
            limited.acquire(
                "scope", duration=10, rate_scope=guard.rate_limit_scope("scope", "model-b")
            )
        limited.release(
            "scope", held, rate_scope=guard.rate_limit_scope("scope", "model-a")
        )

    def test_legacy_credential_cooldown_still_blocks_models(self):
        # A pre-upgrade process writes its cooldown on the credential row; keep
        # honouring it so a mixed deployment still backs off.
        lease = self.a.acquire("scope", duration=10)
        self.a.rate_limited("scope", lease, None)
        self.a.release("scope", lease)
        self.now += 2
        with self.assertRaisesRegex(guard.ModelAdmissionError, "rate_limited"):
            self.b.acquire(
                "scope", duration=10,
                rate_scope=guard.rate_limit_scope("scope", "model-a"),
            )

    def test_success_resets_the_model_backoff_streak(self):
        scoped = guard.rate_limit_scope("scope", "model-a")
        lease = self.a.acquire("scope", duration=10, rate_scope=scoped)
        self.assertEqual(
            self.a.rate_limited("scope", lease, None, rate_scope=scoped), 60
        )
        self.a.release("scope", lease, rate_scope=scoped)
        self.now += 62
        lease = self.a.acquire("scope", duration=10, rate_scope=scoped)
        self.a.release("scope", lease, success=True, rate_scope=scoped)
        self.now += 2
        lease = self.b.acquire("scope", duration=10, rate_scope=scoped)
        self.assertEqual(
            self.b.rate_limited("scope", lease, None, rate_scope=scoped), 60
        )
        self.b.release("scope", lease, rate_scope=scoped)

    def test_transport_uses_the_model_scoped_cooldown(self):
        recorded: list[str | None] = []

        class Recorder:
            def acquire(self, scope, *, duration, rate_scope=None):
                recorded.append(rate_scope)
                return "lease"

            def release(self, scope, lease, *, success=False, rate_scope=None):
                return None

            def rate_limited(self, scope, lease, retry_after, *, rate_scope=None):
                return 0.0

        def opener(req, **kwargs):
            return io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')

        request = api.ModelRequest(
            "https://api.example/v1/chat/completions",
            {"model": "model-a", "messages": []},
            "chat",
        )
        with patch.object(api, "_STANDARD_OPENER", opener), patch.object(
            api, "shared_model_coordinator", return_value=Recorder()
        ):
            api._request_once(request, "key", timeout=10, opener=opener)
        expected = guard.rate_limit_scope(
            guard.request_scope(request.endpoint, "key"), "model-a"
        )
        self.assertEqual(recorded, [expected])

    def test_unavailable_store_fails_closed(self):
        bad = guard.ModelRequestCoordinator(self.path / "missing.db")
        self.path.write_text("file", encoding="utf-8")
        with self.assertRaisesRegex(guard.ModelAdmissionError, "coordination_unavailable"):
            bad.acquire("scope", duration=10)

    def test_scopes_share_paths_and_models_but_not_credentials(self):
        one = guard.request_scope("https://api.example/v1/chat/completions", "secret")
        self.assertEqual(one, guard.request_scope("https://api.example/v1/responses", "secret"))
        self.assertNotEqual(one, guard.request_scope("https://api.example/v1/responses", "other"))
        self.assertNotIn("secret", one)
        self.assertEqual(guard.retry_after_seconds("Thu, 01 Jan 1970 00:17:00 GMT", 1000), 20)
        for value in ("bad", "nan", "inf"):
            self.assertIsNone(guard.retry_after_seconds(value, 1000))

    def test_positional_timeout_and_nested_retries_share_deadline(self):
        clock = [0.0]

        @guard.budgeted_model_call
        def nested(timeout=60):
            clock[0] += 2
            return "late"

        @guard.budgeted_model_call
        def outer(timeout=60):
            clock[0] += 2
            return nested(timeout=20)

        with patch.object(guard.time, "monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(guard.ModelRequestExpired):
                outer(3)
            self.assertEqual(guard.remaining_model_seconds(10), 10)  # Context reset.

    def test_http_429_stops_other_transport_before_network(self):
        calls = []
        def opener(*args, **kwargs):
            calls.append(1)
            raise urllib.error.HTTPError("https://api.example", 429, "limit", {"Retry-After": "120"}, io.BytesIO(b"private body"))
        request = api.build_model_request("https://api.example/v1", "test", [{"role": "user", "content": "x"}])
        with patch.object(api, "_STANDARD_OPENER", opener), patch.object(api, "shared_model_coordinator", return_value=self.a):
            with self.assertRaisesRegex(guard.ModelAdmissionError, "upstream_http_429"):
                api.request_model_complete(request, "key", timeout=10, opener=opener)
            with self.assertRaisesRegex(guard.ModelAdmissionError, "rate_limited"):
                api.request_model_complete(request, "key", timeout=10, opener=opener)
        self.assertEqual(len(calls), 1)

    def test_slow_stream_is_bounded_across_individually_fast_reads(self):
        clock = [0.0]
        class SlowBody:
            def read1(self, size):
                clock[0] += 1
                return b"data: x\n"
        response = api._DeadlineResponse(SlowBody(), 2.5)
        with patch.object(api.time, "monotonic", side_effect=lambda: clock[0]):
            self.assertEqual(response.readline(), b"data: x\n")
            self.assertEqual(response.readline(), b"data: x\n")
            with self.assertRaises(guard.ModelRequestExpired):
                response.readline()


if __name__ == "__main__":
    unittest.main()
