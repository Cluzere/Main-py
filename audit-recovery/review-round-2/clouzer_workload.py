"""Central, bounded workload admission for CLOUZER.

This scheduler limits aggregate expensive work while allowing short/light AI turns
to move ahead of queued heavy jobs. It is intentionally conservative: it does not
claim to control the hosting provider's CPU quota, only the work admitted by the bot.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager


class WorkloadQueueFull(RuntimeError):
    """Raised when the global GWM waiting room is full."""


class WorkloadManager:
    def __init__(
        self,
        max_active: int = 3,
        max_pending: int = 24,
        queue_timeout: float = 300.0,
        heavy_max_active: int = 1,
    ) -> None:
        self.max_active = max(1, int(max_active))
        self.max_pending = max(1, int(max_pending))
        self.queue_timeout = max(1.0, float(queue_timeout))
        self.heavy_max_active = max(1, min(int(heavy_max_active), self.max_active))
        self._condition = asyncio.Condition()
        self._waiters: list[dict] = []
        self._sequence = 0
        self._active = 0
        self._active_heavy = 0
        self._active_by_kind: dict[str, int] = {}
        self._light_streak = 0
        self._host_pressure = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._kind_limits = {
            "ai": self.max_active,
            "attachment": min(2, self.max_active),
            "image": 1,
            "audio": 1,
        }

    def set_host_pressure(self, high: bool) -> None:
        high = bool(high)
        if high == self._host_pressure:
            return
        self._host_pressure = high
        logging.warning(
            "[GWM] stage=host_pressure state=%s action=%s",
            "high" if high else "normal",
            "pause_new_heavy_admissions" if high else "resume_heavy_admissions",
        )
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(lambda: asyncio.create_task(self._notify_waiters()))

    async def _notify_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    def _kind_has_capacity(self, kind: str) -> bool:
        return self._active_by_kind.get(kind, 0) < self._kind_limits.get(kind, self.max_active)

    def _select_next(self) -> dict | None:
        if self._active >= self.max_active:
            return None
        eligible = [
            item for item in self._waiters
            if self._kind_has_capacity(item["kind"])
            and (not item["heavy"] or self._active_heavy < self.heavy_max_active)
            and (not self._host_pressure or not item["heavy"])
        ]
        if not eligible:
            return None
        light = [item for item in eligible if not item["heavy"]]
        heavy = [item for item in eligible if item["heavy"]]
        # Let at most two light admissions pass a waiting heavy job, then yield
        # one slot to the oldest eligible heavy job to prevent starvation.
        if heavy and light and self._light_streak >= 2:
            return min(heavy, key=lambda item: item["sequence"])
        if light:
            return min(light, key=lambda item: item["sequence"])
        return min(heavy, key=lambda item: item["sequence"])

    async def _wait_for_admission(self, waiter: dict) -> None:
        while True:
            selected = self._select_next()
            if selected is waiter:
                self._waiters.remove(waiter)
                waiter["admitted"] = True
                self._active += 1
                self._active_by_kind[waiter["kind"]] = self._active_by_kind.get(waiter["kind"], 0) + 1
                if waiter["heavy"]:
                    self._active_heavy += 1
                    self._light_streak = 0
                else:
                    self._light_streak += 1
                return
            await self._condition.wait()

    @asynccontextmanager
    async def slot(self, kind: str, *, heavy: bool = False, user_id: int | None = None):
        kind = str(kind or "other").strip().lower()[:24] or "other"
        self._loop = asyncio.get_running_loop()
        started = time.monotonic()
        waiter = None
        async with self._condition:
            if len(self._waiters) >= self.max_pending:
                raise WorkloadQueueFull(
                    "Antrean kerja global CLOUZER penuh. Coba lagi setelah pekerjaan yang berjalan selesai."
                )
            self._sequence += 1
            waiter = {
                "sequence": self._sequence,
                "kind": kind,
                "heavy": bool(heavy),
                "user_id": int(user_id) if user_id is not None else None,
                "admitted": False,
            }
            self._waiters.append(waiter)
            try:
                await asyncio.wait_for(
                    self._wait_for_admission(waiter),
                    timeout=self.queue_timeout,
                )
            except asyncio.TimeoutError as exc:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
                elif waiter.get("admitted"):
                    # A timeout can land immediately after admission. Roll back
                    # the reserved counters just like cancellation does.
                    waiter["admitted"] = False
                    self._active = max(0, self._active - 1)
                    self._active_by_kind[kind] = max(0, self._active_by_kind.get(kind, 1) - 1)
                    if self._active_by_kind[kind] == 0:
                        self._active_by_kind.pop(kind, None)
                    if heavy:
                        self._active_heavy = max(0, self._active_heavy - 1)
                self._condition.notify_all()
                raise WorkloadQueueFull(
                    "Waktu tunggu antrean kerja global habis. Coba lagi beberapa saat."
                ) from exc
            except BaseException:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
                elif waiter.get("admitted"):
                    # wait_for() can be cancelled at the admission boundary.
                    # Roll back the reservation so cancellation cannot leak capacity.
                    waiter["admitted"] = False
                    self._active = max(0, self._active - 1)
                    self._active_by_kind[kind] = max(0, self._active_by_kind.get(kind, 1) - 1)
                    if self._active_by_kind[kind] == 0:
                        self._active_by_kind.pop(kind, None)
                    if heavy:
                        self._active_heavy = max(0, self._active_heavy - 1)
                self._condition.notify_all()
                raise

        admitted_at = time.monotonic()
        queue_wait = admitted_at - started
        logging.info(
            "[GWM] stage=admitted kind=%s class=%s queue_wait=%.3fs active=%d/%d pending=%d",
            kind, "heavy" if heavy else "light", queue_wait, self._active,
            self.max_active, len(self._waiters),
        )
        try:
            yield
        finally:
            runtime = time.monotonic() - admitted_at
            async with self._condition:
                self._active = max(0, self._active - 1)
                self._active_by_kind[kind] = max(0, self._active_by_kind.get(kind, 1) - 1)
                if self._active_by_kind[kind] == 0:
                    self._active_by_kind.pop(kind, None)
                if heavy:
                    self._active_heavy = max(0, self._active_heavy - 1)
                self._condition.notify_all()
            logging.info(
                "[GWM] stage=released kind=%s class=%s runtime=%.3fs active=%d pending=%d",
                kind, "heavy" if heavy else "light", runtime, self._active, len(self._waiters),
            )
