"""Delivery sinks for alert events.

A *sink* is the boundary between the platform's alert pipeline and the outside
world (email provider, webhook endpoint, etc.). The default implementations in
this module deliberately avoid any real network call or paid service:

  * :class:`InMemorySink` — collects every dispatched event in a list so tests
    can assert exactly what was emitted, in order.
  * :class:`LoggingSink` — production-safe default that simply logs the event;
    it never throws, never blocks, and never reaches the network.

Real email/webhook providers are intentionally out of scope: they are an
operational concern wired in by the deployment, not a contract-level behaviour.
A sink that respects :class:`MessageSink` can be plugged in without changing
any service code.
"""

from __future__ import annotations

import logging
from typing import Protocol

from apps.dataviz_service.platform.contracts import AlertEvent, Subscription

logger = logging.getLogger("dataviz_service.platform.sinks")


class MessageSink(Protocol):
    """Delivery boundary for an :class:`AlertEvent` addressed to a subscription."""

    def send(self, event: AlertEvent, subscription: Subscription) -> None:
        """Deliver ``event`` to ``subscription.destination`` via the channel."""
        ...


class InMemorySink:
    """Test-only sink that records every dispatched event for later assertion."""

    def __init__(self) -> None:
        self.events: list[tuple[AlertEvent, Subscription]] = []

    def send(self, event: AlertEvent, subscription: Subscription) -> None:
        self.events.append((event, subscription))

    @property
    def count(self) -> int:
        return len(self.events)

    def reset(self) -> None:
        self.events.clear()


class LoggingSink:
    """Production-safe sink that records alerts via the stdlib logger.

    Suitable as a default because it never performs I/O against a paid service
    and never raises — a sink failure must never break the alert pipeline.
    """

    def send(self, event: AlertEvent, subscription: Subscription) -> None:
        logger.info(
            "alert dispatched tenant=%s subscription=%s channel=%s destination=%s severity=%s "
            "target=%s/%s message=%s",
            event.tenant_id,
            event.subscription_id,
            subscription.channel.value,
            subscription.destination,
            event.severity.value,
            event.target_type.value,
            event.target_id,
            event.message,
        )
