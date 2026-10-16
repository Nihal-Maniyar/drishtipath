"""MQTT transport publisher for live bus sensing events.

Publishes to topic 'beyonders/events/v1' according to docs/api-contract.md.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

try:
    from events.schema import Event
    from transport.outbox import SQLiteOutbox
except ImportError:
    from ..events.schema import Event
    from .outbox import SQLiteOutbox

logger = logging.getLogger("actual_bus_simulator.transport.mqtt")


class MQTTTransport:
    """Publishes sensing events to MQTT broker with automatic outbox replay."""

    DEFAULT_TOPIC = "beyonders/events/v1"

    def __init__(
        self,
        host: str = "localhost",
        port: int = 1883,
        client_id: str = "bus-sensing-publisher",
        topic: str = DEFAULT_TOPIC,
        keepalive: int = 60,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.topic = topic
        self.keepalive = keepalive
        self.client = None
        self.is_connected = False
        self._advisory_callback = None
        self._route_id = None
        self.seen_advisories: set[str] = set()
        self.received_advisories: list[dict] = []
        self._init_client()

    def _init_client(self) -> None:
        """Initialize Paho MQTT client if library is installed."""
        try:
            import paho.mqtt.client as mqtt

            # Compatibility across paho-mqtt v1 and v2
            try:
                self.client = mqtt.Client(
                    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                    client_id=self.client_id,
                )
            except AttributeError:
                self.client = mqtt.Client(client_id=self.client_id)

            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message

        except ImportError:
            logger.warning("paho-mqtt not available; MQTT transport will operate in offline mock mode")
            self.client = None

    def _on_connect(self, client, userdata, flags, rc, *args) -> None:
        if rc == 0:
            self.is_connected = True
            logger.info("Connected to MQTT broker at %s:%d", self.host, self.port)
            if self._advisory_callback:
                self._resubscribe_advisories()
        else:
            self.is_connected = False
            logger.warning("MQTT connection failed with code %s", rc)

    def _on_disconnect(self, client, userdata, rc, *args) -> None:
        self.is_connected = False
        logger.info("Disconnected from MQTT broker")

    def _on_message(self, client, userdata, message) -> None:
        """Handle incoming MQTT messages, deduplicating QoS 1 advisories."""
        try:
            payload_str = message.payload.decode("utf-8")
            data = json.loads(payload_str)
            if message.topic.startswith("beyonders/advisories/"):
                adv_id = data.get("advisory_id")
                if adv_id:
                    if adv_id in self.seen_advisories:
                        logger.debug("Dropped duplicate advisory %s", adv_id)
                        return
                    self.seen_advisories.add(adv_id)
                    self.received_advisories.append(data)
                    if self._advisory_callback:
                        self._advisory_callback(data)
        except Exception as e:
            logger.warning("Error processing incoming MQTT message: %s", e)

    def subscribe_advisories(self, callback, route_id: Optional[str] = None) -> None:
        """Subscribe to hazard advisories (global and route-specific) with QoS 1."""
        self._advisory_callback = callback
        self._route_id = route_id
        if self.is_connected and self.client:
            self._resubscribe_advisories()

    def _resubscribe_advisories(self) -> None:
        """Helper to subscribe to advisory topics."""
        try:
            self.client.subscribe("beyonders/advisories/v1", qos=1)
            if self._route_id:
                self.client.subscribe(f"beyonders/advisories/v1/{self._route_id}", qos=1)
            logger.info("Subscribed to hazard advisories (route: %s)", self._route_id)
        except Exception as e:
            logger.warning("Failed to subscribe to advisories: %s", e)

    def connect(self) -> bool:
        """Connect to broker asynchronously."""
        if not self.client:
            return False
        try:
            self.client.connect(self.host, self.port, self.keepalive)
            self.client.loop_start()
            self.is_connected = True
            return True
        except Exception as e:
            logger.warning("Could not connect to MQTT broker %s:%d: %s", self.host, self.port, e)
            self.is_connected = False
            return False

    def disconnect(self) -> None:
        """Disconnect and stop loop."""
        if self.client and self.is_connected:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
            self.is_connected = False

    def publish_event(self, event: Event) -> bool:
        """Publish event to beyonders/events/v1 with QoS 1."""
        if not self.is_connected or not self.client:
            return False

        try:
            payload = event.to_json()
            info = self.client.publish(self.topic, payload, qos=1)
            info.wait_for_publish(timeout=2.0)
            logger.info("Published event %s to topic %s", event.event_id, self.topic)
            return True
        except Exception as e:
            logger.error("Error publishing event %s to MQTT: %s", event.event_id, e)
            return False

    def replay_outbox(self, outbox: SQLiteOutbox) -> int:
        """Replay all pending outbox events across MQTT preserving stable event IDs.

        Returns:
            Number of successfully replayed events.
        """
        if not self.is_connected:
            logger.warning("Cannot replay outbox: MQTT not connected")
            return 0

        pending_events = outbox.get_pending()
        replayed_count = 0

        for evt in pending_events:
            if self.publish_event(evt):
                outbox.mark_replayed(evt.event_id)
                replayed_count += 1
            else:
                break

        logger.info("Replayed %d events from outbox over MQTT", replayed_count)
        return replayed_count
