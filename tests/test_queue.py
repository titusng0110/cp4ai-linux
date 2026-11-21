import threading
import unittest

from cp4ai.core import _pdf_position, _round_percent
from cp4ai.jobs import JobQueue


class JobQueueTests(unittest.TestCase):
    def test_dynamic_jobs_finish_before_drain_returns(self):
        queue = JobQueue(max_concurrent=2)
        seen: list[int] = []
        lock = threading.Lock()

        def add(value: int) -> None:
            with lock:
                seen.append(value)

        def extraction() -> None:
            add(1)
            queue.add(lambda: add(3))
            queue.add(lambda: add(4))

        queue.add(extraction)
        queue.add(lambda: add(2))
        queue.drain()

        self.assertEqual(sorted(seen), [1, 2, 3, 4])

    def test_jobs_can_enqueue_after_the_first_wave(self):
        queue = JobQueue(max_concurrent=1)
        seen: list[int] = []

        def first() -> None:
            seen.append(1)
            queue.add(lambda: seen.append(2))

        queue.add(first)
        queue.drain()
        self.assertEqual(seen, [1, 2])


class PDFPositionTests(unittest.TestCase):
    def test_rounds_half_away_from_zero(self):
        self.assertEqual(_round_percent(7.5), 8)
        self.assertEqual(_round_percent(7.4), 7)

    def test_page_relative_percentages(self):
        self.assertEqual(
            _pdf_position((60, 60, 300, 300), 600, 800),
            "[top: 8%, left: 10%, width: 40%, height: 30%]",
        )

    def test_missing_page_size_has_no_position(self):
        self.assertEqual(_pdf_position((0, 0, 10, 10), 0, 800), "")


if __name__ == "__main__":
    unittest.main()
