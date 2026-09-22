"""Persisted replay plus wakeups; clients never own job lifetimes."""
import threading

from .registry import JobRegistry


class EventBroker:
    def __init__(self, registry: JobRegistry):
        self.registry = registry
        self.condition = threading.Condition()
        self.closed = False

    def publish(self, job_id: str, event: dict):
        with self.condition:
            result = self.registry.append(job_id, event)
            self.condition.notify_all()
            return result

    def read(self, after: int, *, timeout: float = 10, **filters) -> list[dict]:
        with self.condition:
            if self.closed:
                return []
            events = self.registry.events(after, **filters)
            if not events and not self.closed:
                self.condition.wait(timeout)
                if self.closed:
                    return []
                events = self.registry.events(after, **filters)
            return events

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
