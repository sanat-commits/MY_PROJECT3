"""Integration test suite for end-to-end task types against live server."""

import json
import os
import sys
import time
import urllib.error
import urllib.request
import pytest

BASE_URL = os.environ.get("SOVEREIGN_SERVER_URL", "http://127.0.0.1:8080")


def is_server_reachable() -> bool:
    """Check if the live test server is responding."""
    try:
        req = urllib.request.Request(f"{BASE_URL}/api/v1/sovereignty/status")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            return resp.status == 200
    except Exception:
        return False


@pytest.fixture(scope="module")
def require_live_server():
    """Skip test if live server is not running on BASE_URL."""
    if not is_server_reachable():
        pytest.skip(
            f"Live workbench server is not reachable at {BASE_URL}. "
            "To run live integration tests, start the server: "
            "python -m uvicorn backend.app.api.server:app --port 8080"
        )


def post_json(endpoint: str, data: dict):
    req = urllib.request.Request(
        f"{BASE_URL}{endpoint}",
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_json(endpoint: str):
    req = urllib.request.Request(f"{BASE_URL}{endpoint}")
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_text_output(result: dict) -> str:
    if not isinstance(result, dict):
        return ""
    if result.get("summary"):
        return result["summary"]
    outs = result.get("outputs") or result.get("partial_outputs") or {}
    if isinstance(outs, dict):
        return outs.get("answer") or outs.get("content") or outs.get("analysis") or str(outs.get("value", ""))
    return str(outs)


def test_pdf_document_summarization(require_live_server):
    """Test 1: PDF Document Summarization."""
    file_p = "data/uploads/5700b716_industrial_spec_format.pdf"
    if not os.path.exists(file_p):
        from tests.fixtures.multimodal_fixtures import create_text_pdf
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.close()
        create_text_pdf(tmp.name, "Confidential Industrial Specification Report\nOperating Pressure: 12.4 MPa.")
        file_p = tmp.name
    pdf_task = post_json("/api/v1/tasks", {
        "instruction": "Summarize this industrial specification document",
        "data_sensitivity": "INTERNAL",
        "file_path": file_p,
    })
    pdf_result = pdf_task.get("final_result") or {}
    pdf_summary = get_text_output(pdf_result)
    assert len(pdf_summary) > 50, "PDF summary too short or empty!"
    assert "Step 1: Gather Information" not in pdf_summary, "PDF summary is still generic placeholder!"


def test_visual_inspection(require_live_server):
    """Test 2: Image Understanding / Visual Inspection."""
    file_p = "data/uploads/sample_inspection.png"
    if not os.path.exists(file_p):
        from tests.fixtures.phase_9_fixtures import create_industrial_equipment_photo
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        create_industrial_equipment_photo(tmp.name)
        file_p = tmp.name
    img_task = post_json("/api/v1/tasks", {
        "instruction": "Inspect this engineering component for surface defects and structural integrity",
        "data_sensitivity": "INTERNAL",
        "file_path": file_p,
    })
    img_result = img_task.get("final_result") or {}
    img_summary = get_text_output(img_result)
    assert len(img_summary) > 20, "Visual summary empty!"


def test_tabular_data_analysis(require_live_server):
    """Test 3: Tabular Data Analysis & Calculation."""
    file_p = "data/uploads/c6c22098_sample_sensors.csv"
    if not os.path.exists(file_p):
        from tests.fixtures.phase_9_fixtures import create_sample_csv_file
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        tmp.close()
        create_sample_csv_file(tmp.name)
        file_p = tmp.name
    csv_task = post_json("/api/v1/tasks", {
        "instruction": "Analyze sensor telemetry: calculate mean temperature and maximum pressure",
        "data_sensitivity": "INTERNAL",
        "file_path": file_p,
    })
    csv_result = csv_task.get("final_result") or {}
    csv_summary = get_text_output(csv_result)
    assert len(csv_summary) > 10, "CSV calculation summary empty!"


def test_coding_and_approval_flow(require_live_server):
    """Test 4: Code Execution in Sandbox with Approval Flow."""
    code_task = post_json("/api/v1/tasks", {
        "instruction": "Write a python script to compute the first 10 Fibonacci numbers and execute it",
        "data_sensitivity": "INTERNAL",
    })
    if code_task.get("status") == "WAITING_APPROVAL":
        app_res = post_json(f"/api/v1/tasks/{code_task.get('task_id')}/approval", {
            "action": "APPROVED",
            "comments": "Proceed with local sandbox execution",
        })
        assert app_res.get("status") == "COMPLETED", f"Expected COMPLETED, got {app_res.get('status')}"
    else:
        assert code_task.get("status") in ("COMPLETED", "SUCCESS")


def test_sovereign_knowledge_reasoning(require_live_server):
    """Test 5: Sovereign Knowledge / Reasoning."""
    rag_task = post_json("/api/v1/tasks", {
        "instruction": "Outline the sovereign safety policies and air-gapped guidelines for Indian PSU AI infrastructure",
        "data_sensitivity": "RESTRICTED",
    })
    if rag_task.get("status") == "WAITING_APPROVAL":
        rag_task = post_json(f"/api/v1/tasks/{rag_task.get('task_id')}/approval", {
            "action": "APPROVED",
            "comments": "Approved for restricted sovereign knowledge review",
        })
    rag_result = rag_task.get("final_result") or {}
    rag_summary = get_text_output(rag_result)
    assert "INSUFFICIENT_EVIDENCE" not in rag_summary, "Knowledge query failed with INSUFFICIENT_EVIDENCE!"
    assert len(rag_summary) > 30, "Knowledge summary too short!"


def test_audit_logs_and_events(require_live_server):
    """Test 6: Audit Logs & Events Verification."""
    audit_logs = get_json("/api/v1/audit/logs?limit=50")
    assert isinstance(audit_logs, list)
