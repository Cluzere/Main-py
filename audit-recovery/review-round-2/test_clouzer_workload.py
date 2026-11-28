"""Offline regression tests for the CLOUZER global workload manager."""
import asyncio
import unittest

from clouzer_workload import WorkloadManager, WorkloadQueueFull


class WorkloadManagerTests(unittest.IsolatedAsyncioTestCase):
    async def _wait_for(self, predicate, timeout=1.0):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            if loop.time() >= deadline:
                self.fail("condition was not reached before timeout")
            await asyncio.sleep(0.005)

    async def test_heavy_concurrency_is_limited(self):
        manager = WorkloadManager(max_active=3, max_pending=4, heavy_max_active=1)
        release_first = asyncio.Event()
        first_entered = asyncio.Event()
        second_entered = asyncio.Event()

        async def worker(entered, release):
            async with manager.slot("ai", heavy=True):
                entered.set()
                await release.wait()

        first = asyncio.create_task(worker(first_entered, release_first))
        await asyncio.wait_for(first_entered.wait(), 1)
        second = asyncio.create_task(worker(second_entered, asyncio.Event()))
        await asyncio.sleep(0.03)
        self.assertFalse(second_entered.is_set())
        release_first.set()
        await asyncio.wait_for(second_entered.wait(), 1)
        second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
        self.assertEqual(manager._active, 0)
        self.assertEqual(manager._active_heavy, 0)

    async def test_host_pressure_blocks_heavy_but_allows_light(self):
        manager = WorkloadManager(max_active=2, max_pending=4, heavy_max_active=1)
        manager.set_host_pressure(True)
        heavy_entered = asyncio.Event()
        light_entered = asyncio.Event()

        async def worker(heavy, entered):
            async with manager.slot("ai", heavy=heavy):
                entered.set()

        heavy = asyncio.create_task(worker(True, heavy_entered))
        light = asyncio.create_task(worker(False, light_entered))
        await asyncio.wait_for(light_entered.wait(), 1)
        await asyncio.sleep(0.03)
        self.assertFalse(heavy_entered.is_set())
        manager.set_host_pressure(False)
        await asyncio.wait_for(heavy_entered.wait(), 1)
        await asyncio.gather(heavy, light)

    async def test_cancelled_waiter_does_not_leak_capacity(self):
        manager = WorkloadManager(max_active=1, max_pending=2)
        release = asyncio.Event()
        entered = asyncio.Event()

        async def holder():
            async with manager.slot("ai"):
                entered.set()
                await release.wait()

        active = asyncio.create_task(holder())
        await asyncio.wait_for(entered.wait(), 1)

        async def waiter():
            async with manager.slot("ai"):
                pass

        waiting = asyncio.create_task(waiter())
        await self._wait_for(lambda: len(manager._waiters) == 1)
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        self.assertEqual(len(manager._waiters), 0)
        self.assertEqual(manager._active, 1)
        release.set()
        await active
        self.assertEqual(manager._active, 0)

    async def test_global_pending_limit_rejects_excess_waiter(self):
        manager = WorkloadManager(max_active=1, max_pending=1)
        release = asyncio.Event()
        entered = asyncio.Event()

        async def holder():
            async with manager.slot("ai"):
                entered.set()
                await release.wait()

        active = asyncio.create_task(holder())
        await asyncio.wait_for(entered.wait(), 1)

        async def waiter():
            async with manager.slot("ai"):
                pass

        waiting = asyncio.create_task(waiter())
        await self._wait_for(lambda: len(manager._waiters) == 1)
        with self.assertRaises(WorkloadQueueFull):
            async with manager.slot("ai"):
                pass
        release.set()
        await asyncio.gather(active, waiting)
        self.assertEqual(manager._active, 0)
        self.assertEqual(len(manager._waiters), 0)

    async def test_timeout_removes_waiter_and_preserves_active_slot(self):
        manager = WorkloadManager(max_active=1, max_pending=2, queue_timeout=0.03)
        release = asyncio.Event()
        entered = asyncio.Event()

        async def holder():
            async with manager.slot("ai"):
                entered.set()
                await release.wait()

        active = asyncio.create_task(holder())
        await asyncio.wait_for(entered.wait(), 1)
        with self.assertRaises(WorkloadQueueFull):
            async with manager.slot("ai"):
                pass
        self.assertEqual(len(manager._waiters), 0)
        self.assertEqual(manager._active, 1)
        release.set()
        await active
        self.assertEqual(manager._active, 0)


    async def test_heavy_waiter_is_not_starved_by_light_work(self):
        manager = WorkloadManager(max_active=1, max_pending=8, heavy_max_active=1)
        release = asyncio.Event()
        entered = asyncio.Event()
        order = []

        async def holder():
            async with manager.slot("ai", heavy=False):
                entered.set()
                await release.wait()

        async def worker(name, heavy):
            async with manager.slot("ai", heavy=heavy):
                order.append(name)

        active = asyncio.create_task(holder())
        await asyncio.wait_for(entered.wait(), 1)
        heavy = asyncio.create_task(worker("heavy", True))
        await self._wait_for(lambda: len(manager._waiters) == 1)
        light1 = asyncio.create_task(worker("light1", False))
        light2 = asyncio.create_task(worker("light2", False))
        light3 = asyncio.create_task(worker("light3", False))
        await self._wait_for(lambda: len(manager._waiters) == 4)
        release.set()
        await asyncio.gather(active, heavy, light1, light2, light3)
        self.assertEqual(order[0], "light1")
        self.assertEqual(order[1], "light2")
        self.assertEqual(order[2], "heavy")
        self.assertEqual(sorted(order), ["heavy", "light1", "light2", "light3"])
        self.assertEqual(manager._active, 0)
        self.assertEqual(len(manager._waiters), 0)

    async def test_cancellation_inside_admitted_slot_releases_capacity(self):
        manager = WorkloadManager(max_active=1, max_pending=2)
        entered = asyncio.Event()

        async def worker():
            async with manager.slot("ai", heavy=True):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(worker())
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(manager._active, 0)
        self.assertEqual(manager._active_heavy, 0)
        self.assertEqual(manager._active_by_kind, {})
        self.assertEqual(manager._waiters, [])

    async def test_attachment_kind_limit_is_enforced(self):
        manager = WorkloadManager(max_active=3, max_pending=4, heavy_max_active=3)
        release = asyncio.Event()
        entered = 0
        all_entered = asyncio.Event()

        async def worker():
            nonlocal entered
            async with manager.slot("attachment", heavy=False):
                entered += 1
                if entered == 2:
                    all_entered.set()
                await release.wait()

        first = asyncio.create_task(worker())
        second = asyncio.create_task(worker())
        third = asyncio.create_task(worker())
        await asyncio.wait_for(all_entered.wait(), 1)
        await asyncio.sleep(0.03)
        self.assertEqual(entered, 2)
        self.assertEqual(manager._active, 2)
        release.set()
        await asyncio.gather(first, second, third)
        self.assertEqual(manager._active, 0)


if __name__ == "__main__":
    unittest.main()
