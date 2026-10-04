"""Client-side resource sampling (optional; requires the ``cli`` extra).

Runs a low-frequency daemon thread (default 1 s) so it never touches the
timing path. Gracefully degrades to no samples when psutil is absent - SDK
users without the CLI extra lose nothing that affects metrics.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

try:  # pragma: no cover - trivial import guard
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class ResourceSample:
    ts: float  # perf_counter()
    cpu_percent: float  # process CPU, can exceed 100 on multi-core
    rss_mb: float


class ResourceSampler:
    def __init__(self, interval_s: float = 1.0) -> None:
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.samples: list[ResourceSample] = []

    def start(self) -> None:
        if psutil is None or self._thread is not None:
            return
        proc = psutil.Process()

        def _loop() -> None:
            proc.cpu_percent()  # prime the counter
            while not self._stop.is_set():
                self._stop.wait(self._interval)
                if self._stop.is_set():
                    break
                try:
                    self.samples.append(
                        ResourceSample(
                            ts=time.perf_counter(),
                            cpu_percent=proc.cpu_percent(),
                            rss_mb=proc.memory_info().rss / (1024 * 1024),
                        )
                    )
                except (psutil.Error, OSError):  # pragma: no cover
                    break

        self._thread = threading.Thread(target=_loop, daemon=True, name="faster-sampler")
        self._thread.start()

    def stop(self) -> list[ResourceSample]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval * 2)
            self._thread = None
        return self.samples
