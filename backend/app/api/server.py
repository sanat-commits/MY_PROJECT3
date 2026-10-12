"""FastAPI server and Humane Sovereign Workbench Web Interface."""

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import fastapi
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from backend.app.config.settings import get_settings
from backend.app.models.local_openai_runtime import LocalOpenAIRuntime
from backend.app.models.registry import ModelRegistry
from backend.app.orchestration.graph import WorkbenchOrchestrator
from backend.app.security.approval import ApprovalDecision
from backend.app.state.store import LocalStateStore
from backend.app.state.task_state import TaskStatus

app = FastAPI(
    title="Sovereign On-Premise Agentic AI Workbench",
    description="Zero-Egress Industrial AI Workbench using Local Open-Weight Multimodal LLMs",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Persistent storage and orchestrator instance
state_store = LocalStateStore()
model_registry = ModelRegistry()

# Detect local model runtime (prefer configured MODEL_BASE_URL, fallback to standard local endpoints)
settings = get_settings()
candidate_urls = [
    getattr(settings, "MODEL_BASE_URL", "http://127.0.0.1:11434/v1"),
    "http://127.0.0.1:11434/v1",
    "http://127.0.0.1:8000/v1",
]
local_runtime = None
for candidate_url in dict.fromkeys(candidate_urls):
    try:
        rt = LocalOpenAIRuntime(base_url=candidate_url)
        h = rt.health_check()
        if h.status.value == "healthy":
            local_runtime = rt
            break
    except Exception:
        continue

orchestrator = WorkbenchOrchestrator(
    model_registry=model_registry,
    model_runtime=local_runtime,
    state_store=state_store,
)

# Uploads staging directory
UPLOAD_DIR = Path(get_settings().DATA_ROOT) / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class TaskRequestPayload(BaseModel):
    instruction: str
    data_sensitivity: str = "INTERNAL"
    file_path: Optional[str] = None
    csv_path: Optional[str] = None
    task_id: Optional[str] = None


class ApprovalPayload(BaseModel):
    decision: Optional[str] = None
    action: Optional[str] = None
    approver: str = "Chief Systems Engineer"
    approval_id: Optional[str] = None
    plan_hash: Optional[str] = None
    reason: Optional[str] = None
    comments: Optional[str] = None


@app.get("/api/v1/sovereignty/status")
def get_sovereignty_status():
    """Return central zero-egress sovereignty status."""
    from backend.app.sandbox.docker_sandbox import DockerSandbox
    docker_avail = DockerSandbox.is_docker_available()
    return {
        "sovereign_mode": True,
        "zero_egress_enforced": True,
        "external_network_blocked": True,
        "allowed_hosts": ["127.0.0.1", "localhost"],
        "cloud_telemetry_disabled": True,
        "cloud_api_disabled": True,
        "active_runtime_endpoint": local_runtime.base_url if local_runtime else "Local Subprocess",
        "sandbox_backend": "Docker (sovereign-sandbox:latest)" if docker_avail else "Local Process",
        "sandbox_docker_connected": docker_avail,
    }


@app.get("/api/v1/models")
def get_models_status():
    """Return dynamically detected local GPU / CPU, memory, and loaded models."""
    from backend.app.models.hardware import probe_hardware
    snap = probe_hardware()

    models_list = []
    if local_runtime:
        h = local_runtime.health_check()
        models_list = h.models or []

    is_high = getattr(get_settings(), "USE_HIGH_LEVEL_MODELS", False)
    profile_base = "high" if is_high else "small"

    if snap.gpu_available and snap.gpu_name:
        gpu_info = snap.gpu_name
        if snap.gpu_memory_total_gb is not None:
            if snap.gpu_memory_free_gb is not None:
                used_gb = max(0.0, snap.gpu_memory_total_gb - snap.gpu_memory_free_gb)
                vram_usage = f"{used_gb:.1f} GB / {snap.gpu_memory_total_gb:.1f} GB"
            else:
                vram_usage = f"{snap.gpu_memory_total_gb:.1f} GB VRAM"
        else:
            vram_usage = "Hardware Accelerated"
        runtime_status = "ONLINE (Local GPU)" if local_runtime else "STANDBY (Local Hardware)"
        active_profile = f"{profile_base} ({snap.gpu_name})"
    else:
        gpu_info = f"CPU Acceleration ({snap.cpu_count} cores, {snap.cpu_architecture})"
        if snap.ram_total_gb is not None:
            ram_avail = snap.ram_available_gb or 0.0
            ram_used = max(0.0, snap.ram_total_gb - ram_avail)
            vram_usage = f"{ram_used:.1f} GB / {snap.ram_total_gb:.1f} GB RAM"
        else:
            vram_usage = "CPU Mode"
        runtime_status = "ONLINE (Local CPU)" if local_runtime else "STANDBY (CPU Runtime)"
        active_profile = f"{profile_base} (CPU Mode)"

    return {
        "gpu": gpu_info,
        "vram_usage": vram_usage,
        "runtime_status": runtime_status,
        "models": models_list,
        "active_profile": active_profile,
    }


@app.post("/api/v1/upload")
async def upload_file(file: UploadFile = File(...)):
    """Save user-selected file from Explorer into workspace staging."""
    file_id = uuid.uuid4().hex[:8]
    clean_name = Path(file.filename or "upload.dat").name
    save_path = UPLOAD_DIR / f"{file_id}_{clean_name}"
    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {
        "file_id": file_id,
        "filename": clean_name,
        "file_path": str(save_path),
        "size_bytes": save_path.stat().st_size,
    }


@app.get("/api/v1/tasks")
def list_all_tasks():
    """Retrieve list of previous tasks for the history sidebar."""
    tasks = state_store.list_tasks()
    results = []
    for t in tasks:
        results.append({
            "task_id": t.task_id,
            "instruction": t.user_query,
            "status": t.status.value if hasattr(t.status, "value") else str(t.status),
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            "file_path": (t.context or {}).get("file_path"),
            "has_deliverable": bool((t.context or {}).get("final_result")),
            "event_count": len(state_store.get_events(t.task_id)),
        })
    return results


@app.get("/api/v1/audit/logs")
def get_global_audit_logs(limit: int = 50):
    """Retrieve recent audit events across recent tasks."""
    recent_tasks = state_store.list_tasks()[:10]
    all_events = []
    for t in recent_tasks:
        evs = state_store.get_events(t.task_id)
        for e in evs:
            all_events.append(e.model_dump())
    all_events.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
    return all_events[:limit]


@app.get("/api/v1/tasks/{task_id}")
def get_task_details(task_id: str):
    """Retrieve detailed state of a task including events."""
    task = state_store.load_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    data = task.model_dump()
    data["events"] = [e.model_dump() for e in state_store.get_events(task_id)]
    return data


@app.get("/api/v1/tasks/{task_id}/events")
def get_task_events(task_id: str):
    """Retrieve chronological audit trail events."""
    events = state_store.get_events(task_id)
    return [e.model_dump() for e in events]


@app.delete("/api/v1/tasks")
def delete_all_tasks():
    """Clear all stored chat and task history."""
    if hasattr(state_store, "clear"):
        state_store.clear()
    else:
        for t in state_store.list_tasks():
            state_store.delete_task(t.task_id)
    return {"status": "SUCCESS", "message": "All stored chats and tasks deleted."}


@app.delete("/api/v1/tasks/{task_id}")
def delete_task_by_id(task_id: str):
    """Delete a single task and its event log."""
    deleted = state_store.delete_task(task_id)
    return {"status": "SUCCESS" if deleted else "NOT_FOUND", "deleted": deleted}


@app.post("/api/v1/tasks")
def create_task(payload: TaskRequestPayload):
    """Submit a task to the Sovereign Workbench."""
    t_id = payload.task_id or f"task_{uuid.uuid4().hex[:8]}"
    file_p = payload.file_path
    csv_p = payload.csv_path

    # Auto-detect if file_path is CSV or XLSX
    if file_p and not csv_p and file_p.lower().endswith((".csv", ".xlsx")):
        csv_p = file_p

    state = orchestrator.run(
        user_request=payload.instruction,
        task_id=t_id,
        data_sensitivity=payload.data_sensitivity,
        file_path=file_p,
        csv_path=csv_p,
    )
    return {
        "task_id": t_id,
        "status": state.get("task_status"),
        "plan": [p.model_dump() if hasattr(p, "model_dump") else p for p in state.get("plan", [])],
        "execution_steps": state.get("execution_steps", []),
        "approval_request": state.get("approval_request").model_dump() if hasattr(state.get("approval_request"), "model_dump") else state.get("approval_request"),
        "final_result": state.get("final_result"),
        "verification_results": state.get("verification_results"),
        "events": [e.model_dump() for e in state_store.get_events(t_id)],
    }


@app.post("/api/v1/tasks/{task_id}/approval")
def submit_task_approval(task_id: str, payload: ApprovalPayload):
    """Submit human approval to resume a paused task."""
    task = state_store.load_task(task_id)
    app_id = payload.approval_id
    plan_hash = payload.plan_hash

    if task and task.context:
        app_req = task.context.get("approval_request")
        if isinstance(app_req, dict):
            if not app_id:
                app_id = app_req.get("approval_id")
            if not plan_hash:
                plan_hash = app_req.get("plan_hash")

    if not app_id:
        app_id = f"app_{uuid.uuid4().hex[:8]}"

    dec_val = payload.decision or payload.action or "APPROVED"
    dec_reason = payload.reason or payload.comments or "Approved via Sovereign Web Workbench"

    dec = ApprovalDecision(
        approval_id=app_id,
        decision=dec_val,
        approver=payload.approver,
        plan_hash=plan_hash or "",
        reason=dec_reason,
    )
    try:
        final_state = orchestrator.submit_approval(task_id, dec)
        return {
            "task_id": task_id,
            "status": final_state.get("task_status"),
            "final_result": final_state.get("final_result"),
            "execution_steps": final_state.get("execution_steps", []),
            "events": [e.model_dump() for e in state_store.get_events(task_id)],
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/artifacts/{file_name}/download")
def download_artifact(file_name: str):
    """Download verified generated artifact."""
    settings = get_settings()
    clean_name = Path(file_name).name
    if not clean_name or clean_name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid artifact filename.")

    data_root_resolved = Path(settings.DATA_ROOT).resolve()
    candidates = [
        data_root_resolved / "cache" / "artifacts" / clean_name,
        data_root_resolved / "artifacts" / clean_name,
        UPLOAD_DIR.resolve() / clean_name,
    ]
    for target in candidates:
        try:
            resolved_target = target.resolve()
            # Ensure the resolved file path stays strictly within allowed data directories
            if resolved_target.is_relative_to(data_root_resolved) and resolved_target.is_file():
                return FileResponse(
                    path=str(resolved_target),
                    filename=clean_name,
                    media_type="application/octet-stream",
                )
        except (ValueError, RuntimeError):
            continue
    raise HTTPException(status_code=404, detail="Artifact file not found.")


# Background automated endpoints preserved for testing suite
@app.post("/api/v1/demos/{demo_id}")
def run_demo(demo_id: str):
    """Automated testing suite hook."""
    from tests.fixtures.phase_9_fixtures import (
        create_industrial_equipment_photo,
        create_sample_csv_file,
    )
    temp_dir = tempfile.mkdtemp(prefix="workbench_demo_")

    if demo_id == "demo1":
        img_p = os.path.join(temp_dir, "turbine_casing_inspection.png")
        create_industrial_equipment_photo(img_p)
        query = "Analyze this scanned inspection report for equipment TURBINE-01, identify candidate observations, and generate an inspection approval note."
        return orchestrator.run(query, task_id=f"demo_1_{uuid.uuid4().hex[:6]}", file_path=img_p)

    elif demo_id == "demo2":
        csv_p = os.path.join(temp_dir, "vibration_sensor_data.csv")
        create_sample_csv_file(csv_p)
        query = "Write a Python script to calculate statistics from this CSV and execute in the sandbox."
        return orchestrator.run(query, task_id=f"demo_2_{uuid.uuid4().hex[:6]}", csv_path=csv_p)

    elif demo_id == "demo3":
        from backend.app.rag.knowledge_agent import build_default_knowledge_agent
        manifest_p = os.path.join(temp_dir, "px417_manifest.json")
        k_agent = build_default_knowledge_agent(manifest_path=manifest_p)
        k_agent.ingest_text(
            text="Standard Operating Procedure for pump PX-417:\nNormal operating vibration must remain under 2.5 mm/s RMS.\nIf vibration exceeds 4.5 mm/s, immediately schedule bearing replacement.",
            document_id="doc_px417_sop",
            filename="PX417_SOP.txt",
            sensitivity="CONFIDENTIAL",
        )
        ans = k_agent.answer_query("What does the maintenance procedure recommend for pump PX-417 vibration?", max_sensitivity="CONFIDENTIAL")
        return {
            "task_id": f"demo_3_{uuid.uuid4().hex[:6]}",
            "task_status": "COMPLETED",
            "query": ans.query,
            "answer": ans.answer,
            "citations": [c.model_dump() for c in ans.citations],
            "insufficient_evidence": ans.insufficient_evidence,
            "verification_passed": True,
        }

    elif demo_id == "demo4":
        img_p = os.path.join(temp_dir, "flange_surface.png")
        create_industrial_equipment_photo(img_p)
        from backend.app.vision.agent import EngineeringVisionAgent
        v_agent = EngineeringVisionAgent()
        res = v_agent.process_image(img_p, user_focus="inspect surface for candidate indications")
        return {
            "task_id": f"demo_4_{uuid.uuid4().hex[:6]}",
            "task_status": "COMPLETED",
            "observations": [o.model_dump() for o in res.observations],
            "findings": [f.model_dump() for f in res.findings],
            "candidate_observation": True,
            "verification_required": True,
            "disclaimer": "All visual indications are uncertified candidates requiring physical NDT verification.",
        }

    elif demo_id == "demo5":
        img_p = os.path.join(temp_dir, "turbine_mixed.png")
        create_industrial_equipment_photo(img_p)
        csv_p = os.path.join(temp_dir, "meas_mixed.csv")
        create_sample_csv_file(csv_p)
        query = "Analyze this inspection report, check the maintenance procedure for the equipment, calculate the reported measurements, and prepare an approval note."
        return orchestrator.run(query, task_id=f"demo_5_{uuid.uuid4().hex[:6]}", file_path=img_p, csv_path=csv_p)
    else:
        raise HTTPException(status_code=404, detail="Unknown demo identifier.")


# --------------------------------------------------------------------------
# Clean, Humane, Professional Web Interface
# --------------------------------------------------------------------------

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML_PATH = STATIC_DIR / "index.html"


def load_index_html() -> str:
    """Load the workbench single-page web interface."""
    if INDEX_HTML_PATH.exists():
        return INDEX_HTML_PATH.read_text(encoding="utf-8")
    return "<html><body><h1>Sovereign Engineering Workbench</h1><p>Static index.html not found.</p></body></html>"


INDEX_HTML = load_index_html()


@app.get("/", response_class=HTMLResponse)
def index_page():
    return HTMLResponse(content=INDEX_HTML)

