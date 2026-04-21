"""Human-friendly duration formatting and a simple ETA estimator."""

import time


def format_duration(seconds: float) -> str:
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m {total % 60}s"
    return f"{total // 3600}h {(total % 3600) // 60}m"


class EtaEstimator:
    """ETA based on completed batches. Skips the first `concurrency` completions
    since they all finish near-simultaneously and would skew the rate."""

    def __init__(self, total: int, concurrency: int, start: float) -> None:
        self.total = total
        self.concurrency = concurrency
        self.start = start
        self.done = 0
        self._baseline_time: float | None = None
        self._baseline_done = 0

    def record(self) -> None:
        self.done += 1
        elapsed = time.time() - self.start
        if self._baseline_time is None and self.done >= self.concurrency:
            self._baseline_time = elapsed
            self._baseline_done = self.done

    def remaining_secs(self) -> float | None:
        if self.done >= self.total or self._baseline_time is None:
            return None
        elapsed = time.time() - self.start
        since_baseline_done = self.done - self._baseline_done
        if since_baseline_done > 0:
            rate = (elapsed - self._baseline_time) / since_baseline_done
        else:
            rate = self._baseline_time / self._baseline_done
        return (self.total - self.done) * rate
