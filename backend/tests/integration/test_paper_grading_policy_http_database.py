"""A fresh confirmed batch must accept its strict grading policy over JSON."""

import os

import httpx
import pytest
from tests.integration.test_paper_checking_execution_database import (
    execution_database as execution_database,  # noqa: PLC0414
)
from tests.integration.test_paper_checking_intake_database import policy
from tests.integration.test_scan_storage_retention_database import http_app

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required"),
]


async def test_freeze_policy_json_roundtrip_and_schedule_fresh_batch(execution_database):
    c = execution_database
    app, _ = http_app(c)
    base = f"/api/assessment-core/scan-batches/{c[2]['batch']}"
    schema = app.openapi()
    body = schema["paths"][
        "/api/assessment-core/scan-batches/{batch_id}/grading-policies/{variant_id}"
    ]["post"]["requestBody"]
    assert body["required"] is True
    assert body["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ScanGradingPolicy"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"Origin": "http://localhost:5173"},
    ) as client:
        for variant in ("a", "b"):
            supplied = policy(c[2], variant).model_dump(mode="json")
            path = f"{base}/grading-policies/{c[2]['variant_' + variant]}"
            frozen = await client.post(path, json=supplied)
            assert frozen.status_code == 200, frozen.text
            assert frozen.json()["policy"] == supplied
            replay = await client.post(path, json=frozen.json()["policy"])
            assert replay.status_code == 200, replay.text
            assert replay.json()["id"] == frozen.json()["id"]
        started = await client.post(base + "/checking/start")
        assert started.status_code == 200, started.text
        assert started.json()["pending"] == 2


@pytest.mark.parametrize("invalid", [{}, {"total_max_score": "NaN"}, {"unexpected": True}])
async def test_policy_http_still_rejects_invalid_contract(execution_database, invalid):
    c = execution_database
    app, _ = http_app(c)
    supplied = policy(c[2]).model_dump(mode="json")
    supplied = {**supplied, **invalid} if invalid else {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"Origin": "http://localhost:5173"},
    ) as client:
        response = await client.post(
            f"/api/assessment-core/scan-batches/{c[2]['batch']}/grading-policies/{c[2]['variant_a']}",
            json=supplied,
        )
        assert response.status_code == 422
