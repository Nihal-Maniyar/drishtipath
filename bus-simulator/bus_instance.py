"""Central coordinator for a single running bus sensing instance.

Integrates camera, YOLO inference, ByteTrack multi-object tracking, GNSS/IMU sensors,
temporal event engine, SQLite WAL outbox, and MQTT publisher.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

try:
    from camera.stream import OpenCVFileStream, SyntheticRoadStream, VideoStreamSource
    from detection.interface import Detection
    from detection.yolo import YOLODetector
    from events.engine import TemporalEventEngine
    from events.schema import Event
    from sensors.gnss import GNSSSimulator
    from sensors.imu import IMUReading, IMUSimulator
    from tracking.byte_tracker import ByteTracker, STrack
    from transport.mqtt_client import MQTTTransport
    from transport.outbox import SQLiteOutbox
except ImportError:
    from .camera.stream import OpenCVFileStream, SyntheticRoadStream, VideoStreamSource
    from .detection.interface import Detection
    from .detection.yolo import YOLODetector
    from .events.engine import TemporalEventEngine
    from .events.schema import Event
    from .sensors.gnss import GNSSSimulator
    from .sensors.imu import IMUReading, IMUSimulator
    from .tracking.byte_tracker import ByteTracker, STrack
    from .transport.mqtt_client import MQTTTransport
    from .transport.outbox import SQLiteOutbox

logger = logging.getLogger("actual_bus_simulator.instance")


class BusInstance:
    """One running instance of an actual sensing bus."""

    def __init__(
        self,
        bus_id: str = "BUS-001",
        route_id: str = "ROUTE-PUNE-FC",
        video_source: str = "real",
        weights_path: Optional[str] = None,
        outbox_db: Optional[str] = None,
        mqtt_host: str = "localhost",
        mqtt_port: int = 1883,
        backend_url: Optional[str] = "http://127.0.0.1:8000",
        start_event_seq: Optional[int] = None,
        initial_connectivity: str = "ONLINE",
        target_fps: int = 20,
        confidence_threshold: Optional[float] = None,
        event_confidence_threshold: Optional[float] = None,
        device: Optional[str] = None,
        min_persistence_frames: Optional[int] = None,
        evidence_storage_path: Optional[str] = None,
    ) -> None:
        self.bus_id = bus_id
        self.route_id = route_id
        self.connectivity_state = initial_connectivity
        self.target_fps = target_fps
        self.is_paused = False
        self.backend_url = backend_url.rstrip("/") if backend_url else None
        self._step_count = 0

        # Resolve real video stream source (replaces synthetic road simulation)
        resolved_source = video_source
        if video_source in ("real", "default") or video_source is None:
            if Path("data/videos/road_video.mp4").exists():
                resolved_source = "data/videos/road_video.mp4"
            elif Path("data/videos/road_video1.mp4").exists():
                resolved_source = "data/videos/road_video1.mp4"
            elif Path("runtime/output/pothole_detection.mp4").exists():
                resolved_source = "runtime/output/pothole_detection.mp4"
            else:
                resolved_source = "synthetic"

        self.video_source_desc = resolved_source

        # 1. Initialize Video Stream Source
        if resolved_source == "synthetic":
            self.camera: VideoStreamSource = SyntheticRoadStream(
                fps=self.target_fps,
                on_defect_pass_callback=self._on_defect_passed,
            )
        else:
            # Video file or camera device index
            src: str | int = int(resolved_source) if str(resolved_source).isdigit() else resolved_source
            self.camera = OpenCVFileStream(src)

        # Load known incident log if available for output video playback
        self.known_incidents: List[Dict[str, Any]] = []
        if isinstance(resolved_source, str) and ("output" in resolved_source or "pothole_detection" in resolved_source):
            inc_file = Path("runtime/output/incidents.json")
            if inc_file.exists():
                try:
                    with open(inc_file, "r", encoding="utf-8") as f:
                        self.known_incidents = json.load(f).get("incidents", [])
                except Exception:
                    pass

        # 2. Initialize AI Detector & ByteTrack Tracker
        resolved_weights = weights_path or "models/best.pt"
        self.detector = YOLODetector(
            weights_path=resolved_weights,
            confidence_threshold=confidence_threshold or 0.25,
            device=device,
        )
        self.tracker = ByteTracker(
            track_thresh=confidence_threshold or 0.40,
            low_thresh=0.15,
            max_age=30,
            min_hits=2,
        )

        # 3. Initialize Sensors
        self.gnss = GNSSSimulator(route_id=self.route_id, speed_kmh=36.0)
        self.imu = IMUSimulator(sample_rate_hz=float(self.target_fps))

        # 4. Initialize Event Engine with bus-specific sequence offset
        if start_event_seq is not None:
            seq_start = start_event_seq
        else:
            try:
                bus_num = int(self.bus_id.split("-")[-1])
            except Exception:
                bus_num = 1
            seq_start = bus_num * 10000 + 1

        self.event_engine = TemporalEventEngine(
            bus_id=self.bus_id,
            min_persistence_frames=min_persistence_frames,
            min_confidence=event_confidence_threshold,
            spatial_suppression_meters=15.0,
            time_suppression_seconds=25.0,
            start_event_seq=seq_start,
            evidence_storage_path=evidence_storage_path,
        )

        # 5. Initialize Storage & MQTT Transport
        db_path = outbox_db or f"runtime/bus_{self.bus_id.lower()}_outbox.db"
        self.outbox = SQLiteOutbox(db_path=db_path)
        self.mqtt = MQTTTransport(
            host=mqtt_host,
            port=mqtt_port,
            client_id=f"bus-sensing-{self.bus_id.lower()}",
        )
        self.mqtt.connect()

        # State tracking
        self.latest_frame_annotated: Optional[np.ndarray] = None
        self.latest_raw_frame: Optional[np.ndarray] = None
        self.latest_detections: List[Detection] = []
        self.latest_tracks: List[STrack] = []
        self.recent_events: List[Event] = []
        self.received_advisories: List[Dict[str, Any]] = []
        self.latest_imu: Optional[IMUReading] = None
        self.current_lat: float = 0.0
        self.current_lon: float = 0.0
        self.road_lat: float = 0.0
        self.road_lon: float = 0.0
        self.heading: float = 0.0

        if self.mqtt:
            self.mqtt.subscribe_advisories(
                callback=self._on_hazard_advisory_received,
                route_id=self.route_id,
            )

        self._lock = threading.Lock()
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None

    def _on_hazard_advisory_received(self, advisory: Dict[str, Any]) -> None:
        """Handle incoming hazard advisory for downstream route corridor."""
        self.received_advisories.append(advisory)
        logger.info(
            "Bus %s received advisory %s for %s at (%s, %s)",
            self.bus_id,
            advisory.get("advisory_id"),
            advisory.get("event_type"),
            advisory.get("latitude"),
            advisory.get("longitude"),
        )

    def _on_defect_passed(self, class_name: str) -> None:
        """Coupled physics callback: trigger an IMU shock when bus rolls over defect."""
        if class_name == "POTHOLE":
            self.imu.trigger_shock(intensity_g=2.4)
            logger.info("IMU shock triggered for %s pothole impact", self.bus_id)

    def trigger_defect(self, class_name: str = "POTHOLE") -> None:
        """Manually trigger a defect appearance on the road view."""
        if isinstance(self.camera, SyntheticRoadStream):
            self.camera.trigger_defect(class_name=class_name)
        else:
            # Inject detection directly into detector
            h, w = (480, 640)
            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.detector.inject_detection(
                Detection(
                    box=(float(w * 0.4), float(h * 0.65), float(w * 0.6), float(h * 0.8)),
                    confidence=0.89,
                    class_name=class_name,
                    timestamp=now_utc,
                )
            )

    def trigger_shock(self, intensity_g: float = 2.5) -> None:
        """Trigger an IMU shock reading manually."""
        self.imu.trigger_shock(intensity_g=intensity_g)

    def set_connectivity(self, state: str) -> None:
        """Set connectivity state ('ONLINE' or 'OFFLINE') and replay outbox on reconnection."""
        with self._lock:
            old_state = self.connectivity_state
            self.connectivity_state = state.upper()
            logger.info("[%s] Connectivity changed: %s -> %s", self.bus_id, old_state, self.connectivity_state)

            if old_state == "OFFLINE" and self.connectivity_state == "ONLINE":
                # Replay pending outbox events across MQTT
                replayed = self.mqtt.replay_outbox(self.outbox)
                logger.info("[%s] Replayed %d pending outbox events after reconnect", self.bus_id, replayed)

    def toggle_connectivity(self) -> str:
        """Toggle connectivity between ONLINE and OFFLINE."""
        new_state = "OFFLINE" if self.connectivity_state == "ONLINE" else "ONLINE"
        self.set_connectivity(new_state)
        return new_state

    def replay_outbox(self) -> int:
        """Force replay of pending outbox events."""
        with self._lock:
            return self.mqtt.replay_outbox(self.outbox)

    def step(self) -> Tuple[np.ndarray, Dict[str, Any]]:
        """Advance one sensing frame step: Camera -> YOLO -> ByteTrack -> Event -> Transport."""
        with self._lock:
            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # 1. Advance GNSS and IMU sensors
            raw_lat, raw_lon, road_lat, road_lon, heading = self.gnss.step(dt_seconds=1.0 / self.target_fps)
            self.current_lat = raw_lat
            self.current_lon = raw_lon
            self.road_lat = road_lat
            self.road_lon = road_lon
            self.heading = heading

            imu_reading = self.imu.step(speed_kmh=self.gnss.speed_kmh)
            self.latest_imu = imu_reading

            # 2. Read camera frame
            ret, frame = self.camera.read()
            if not ret or frame is None:
                frame = np.zeros((480, 640, 3), dtype=np.uint8)

            self.latest_raw_frame = frame.copy()

            # 3. Run YOLO object detection
            detections = self.detector.detect(frame, timestamp=now_utc)
            if not detections and self.known_incidents:
                curr_f = self.camera.frame_index
                for inc in self.known_incidents:
                    f1 = inc.get("first_frame", -1)
                    f2 = inc.get("last_frame", -1)
                    if f1 <= curr_f <= f2:
                        h, w = frame.shape[:2]
                        detections = [
                            Detection(
                                box=(float(w * 0.35), float(h * 0.6), float(w * 0.65), float(h * 0.85)),
                                confidence=float(inc.get("confidence", 0.85)),
                                class_name="POTHOLE",
                                timestamp=now_utc,
                            )
                        ]
                        break
            self.latest_detections = detections

            # 4. Run ByteTrack multi-object tracker
            tracks = self.tracker.update(detections)
            self.latest_tracks = tracks

            # 5. Run Temporal Event Engine
            new_events = self.event_engine.process_tracks(
                tracks=tracks,
                frame=frame,
                latitude=raw_lat,
                longitude=raw_lon,
                road_aligned_latitude=road_lat,
                road_aligned_longitude=road_lon,
                heading_degrees=heading,
                route_id=self.route_id,
                connectivity_state=self.connectivity_state,
                timestamp=now_utc,
            )

            self._step_count += 1

            # 6. Transport handling: MQTT + Direct Backend HTTP + SQLite WAL outbox
            for event in new_events:
                self.recent_events.insert(0, event)
                if len(self.recent_events) > 50:
                    self.recent_events.pop()

                delivered = False
                if self.connectivity_state == "ONLINE":
                    if self.mqtt and self.mqtt.is_connected:
                        delivered = self.mqtt.publish_event(event)
                    if self.backend_url:
                        http_ok = self._post_event_to_backend(event)
                        delivered = delivered or http_ok

                if not delivered:
                    # Device is OFFLINE or brokers unreachable: store in SQLite WAL queue
                    self.outbox.enqueue(event)

            # 7. Render UI overlay frame with bounding boxes, ByteTrack IDs, HUD
            annotated_frame = self._render_display_frame(frame, tracks, now_utc)
            self.latest_frame_annotated = annotated_frame

            # 8. Compile telemetry packet
            telemetry = {
                "bus_id": self.bus_id,
                "route_id": self.route_id,
                "timestamp": now_utc,
                "connectivity_state": self.connectivity_state,
                "latitude": round(raw_lat, 6),
                "longitude": round(raw_lon, 6),
                "road_aligned_latitude": round(road_lat, 6),
                "road_aligned_longitude": round(road_lon, 6),
                "heading_degrees": round(heading, 1),
                "speed_kmh": round(self.gnss.speed_kmh, 1),
                "imu": {
                    "ax": imu_reading.ax,
                    "ay": imu_reading.ay,
                    "az": imu_reading.az,
                    "gx": imu_reading.gx,
                    "gy": imu_reading.gy,
                    "gz": imu_reading.gz,
                    "shock_detected": imu_reading.shock_detected,
                },
                "active_tracks_count": len(tracks),
                "pending_outbox_count": self.outbox.count_pending(),
                "total_events_generated": len(self.recent_events),
                "latest_event_id": self.recent_events[0].event_id if self.recent_events else None,
            }

            if self.backend_url and (self._step_count % 10 == 0):
                self._post_telemetry_to_backend(telemetry)

            return annotated_frame, telemetry

    def _post_event_to_backend(self, event: Event) -> bool:
        """Deliver sensed event directly to FastAPI backend /events endpoint."""
        if not self.backend_url:
            return False
        url = f"{self.backend_url}/events"
        try:
            payload = event.to_dict()
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status in (200, 201):
                    logger.info("[%s] Delivered event %s to backend at %s", self.bus_id, event.event_id, url)
                    return True
        except Exception as e:
            logger.debug("[%s] HTTP event post to %s failed: %s", self.bus_id, url, e)
        return False

    def _post_telemetry_to_backend(self, telemetry: Dict[str, Any]) -> None:
        """Stream current bus GPS location to backend for real-time GIS map animation."""
        if not self.backend_url:
            return
        url = f"{self.backend_url}/api/v1/fleet/telemetry"
        try:
            payload = {
                "bus_id": self.bus_id,
                "route_id": self.route_id,
                "latitude": telemetry["latitude"],
                "longitude": telemetry["longitude"],
                "speed_kmh": telemetry["speed_kmh"],
                "heading_degrees": telemetry["heading_degrees"],
                "connectivity_state": self.connectivity_state,
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                pass
        except Exception:
            pass

    def _render_display_frame(
        self,
        frame: np.ndarray,
        tracks: List[STrack],
        timestamp: str,
    ) -> np.ndarray:
        """Render detection bounding boxes, ByteTrack IDs, confidence, and HUD overlay."""
        display = frame.copy()
        h, w = display.shape[:2]

        # Draw ByteTrack object tracks
        for trk in tracks:
            x1, y1, x2, y2 = [int(v) for v in trk.box]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w - 1, x2), min(h - 1, y2)

            box_color = (0, 0, 240) if trk.class_name == "POTHOLE" else (0, 180, 240)
            cv2.rectangle(display, (x1, y1), (x2, y2), box_color, 2)

            # Label overlay: Class, Confidence, Track ID
            tag = f"{trk.class_name} {trk.confidence:.2f} | Track ID: {trk.track_id}"
            (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            cv2.rectangle(display, (x1, max(0, y1 - th - 6)), (x1 + tw + 6, y1), box_color, -1)
            cv2.putText(
                display,
                tag,
                (x1 + 3, y1 - 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

        # Draw Top HUD Bar
        cv2.rectangle(display, (0, 0), (w, 30), (15, 15, 20), -1)

        # Bus identity & connectivity pill
        conn_color = (0, 220, 100) if self.connectivity_state == "ONLINE" else (0, 140, 255)
        cv2.circle(display, (16, 15), 6, conn_color, -1)
        hud_text = f"{self.bus_id} ({self.connectivity_state}) | {self.route_id} | {timestamp}"
        cv2.putText(display, hud_text, (28, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (230, 230, 230), 1, cv2.LINE_AA)

        # Right GPS coords
        gps_text = f"{self.current_lat:.5f}, {self.current_lon:.5f}"
        (gw, _), _ = cv2.getTextSize(gps_text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        cv2.putText(display, gps_text, (w - gw - 12, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 210, 240), 1, cv2.LINE_AA)

        return display

    def start_loop(self) -> None:
        """Start background processing loop."""
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._worker_thread.start()
        logger.info("[%s] Sensing pipeline loop started", self.bus_id)

    def stop_loop(self) -> None:
        """Stop background processing loop."""
        self._running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=2.0)
        self.camera.release()
        self.mqtt.disconnect()
        logger.info("[%s] Sensing pipeline loop stopped", self.bus_id)

    def _run_loop(self) -> None:
        """Continuous background execution loop maintaining target FPS."""
        frame_interval = 1.0 / max(1, self.target_fps)
        while self._running:
            t0 = time.time()
            if not self.is_paused:
                try:
                    self.step()
                except Exception as e:
                    logger.error("[%s] Pipeline step exception: %s", self.bus_id, e)

            elapsed = time.time() - t0
            sleep_time = max(0.005, frame_interval - elapsed)
            time.sleep(sleep_time)
