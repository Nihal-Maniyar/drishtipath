---
name: testing-pytest
description: Pytest testing standards, FastAPI TestClient isolation, deterministic simulator scenario validation, and CI regression gates for FleetFusion.
sources:
  - https://docs.pytest.org/en/stable/
  - pytest.ini
  - tests/README.md
---

# Pytest Testing & Quality Assurance

Standards for running, authoring, and maintaining automated tests across backend APIs, simulators, and integration pipelines.

## 1. Test Suite Organization & Execution
- The test suite is organized into modular packages:
  - `tests/`: Integration tests, database tests, AI pipeline tests, and GIS dashboard contract tests.
  - `backend/tests/`: FastAPI unit tests, endpoint validation, and contract schema tests.
  - `data-demo-simulator/tests/`: Deterministic scenario emission and SQLite WAL replay tests.
  - `bus-simulator/tests/`: Edge camera, YOLO detector, ByteTrack tracking, and IMU sensor tests.
- When running tests locally from repository root:
  - Standard command: `.venv/bin/python -m pytest`
  - Ensure all modules are on pythonpath: `pytest.ini` defines `pythonpath = . backend data-demo-simulator bus-simulator`.

## 2. Zero Test Deletion Rule
- Per `AGENTS.md`: "Add or update tests for behaviour changes. Never delete tests merely to pass CI."
- If an existing contract changes, update the test assertion to reflect the approved new schema rather than removing test coverage.

## 3. TestClient & Database Isolation
- Use FastAPI's `TestClient` for synchronous API endpoint testing.
- Isolate test databases using in-memory SQLite or temporary SQLite database files that are torn down in test fixtures (`setUp` / `tearDown`).
- Mock external MQTT brokers (`paho-mqtt`) and network sockets during unit tests to guarantee deterministic, offline execution.
- Maintain synthetic test fixtures (e.g. 1x1 pixel test JPEG images) in `runtime/` and clean them up after test execution.
