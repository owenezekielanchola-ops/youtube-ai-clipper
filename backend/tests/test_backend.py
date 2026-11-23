"""QA suite for ClipForge AI backend — unit + API integration + middleware.
Run: PYTHONPATH=<repo>/backend pytest -v test_backend.py
Designed to EXPOSE faults, not to pass. Each test documents expected behavior.
"""
import io
import importlib
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from main import app, _sanitize_project_id
from services.youtube_ingest import normalize_clip_windows
from services import sfx_library

client = TestClient(app)


# ---------------------------------------------------------------------------
# UNIT: _sanitize_project_id
# ---------------------------------------------------------------------------
class TestSanitizeProjectId:
    def test_normal(self):
        assert _sanitize_project_id("proj_abc123") == "proj_abc123"

    def test_strips_traversal(self):
        assert "/" not in _sanitize_project_id("../../etc/passwd")
        assert ".." not in _sanitize_project_id("..%2f..")

    def test_empty_raises(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            _sanitize_project_id("")

    def test_all_illegal_raises(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            _sanitize_project_id("../../")   # becomes "" after strip -> must 400


# ---------------------------------------------------------------------------
# UNIT: normalize_clip_windows (clip-trim math — the bug-prone core)
# ---------------------------------------------------------------------------
class TestNormalizeClipWindows:
    def test_basic_passthrough(self):
        out = normalize_clip_windows([{"source_start": 10, "source_end": 30}], 120)
        assert out[0]["source_start"] == 10 and out[0]["source_end"] == 30

    def test_drops_subsecond_clip(self):
        # 0.5s clip is < 2s minimum -> should be dropped, fall back to default
        out = normalize_clip_windows([{"source_start": 5, "source_end": 5.5}], 120)
        assert all(c["source_end"] - c["source_start"] >= 2.0 for c in out)

    def test_never_exceeds_duration(self):
        out = normalize_clip_windows([{"source_start": 100, "source_end": 999}], 120)
        for c in out:
            assert c["source_end"] <= 120, f"clip end {c['source_end']} > duration 120"

    def test_negative_start_clamped(self):
        out = normalize_clip_windows([{"source_start": -50, "source_end": 20}], 120)
        assert out[0]["source_start"] >= 0

    def test_start_past_end_of_video(self):
        # start beyond video -> relocated; must stay valid (start < end <= dur)
        out = normalize_clip_windows([{"source_start": 500, "source_end": 510}], 120)
        for c in out:
            assert 0 <= c["source_start"] < c["source_end"] <= 120

    def test_empty_input_yields_default(self):
        out = normalize_clip_windows([], 120)
        assert len(out) == 1 and out[0]["title"] == "Opening Highlight"

    def test_total_cap_respected(self):
        ranges = [{"source_start": i * 60, "source_end": i * 60 + 50} for i in range(10)]
        out = normalize_clip_windows(ranges, 3600, max_clip_duration=30, max_total_duration=90)
        total = sum(c["source_end"] - c["source_start"] for c in out)
        assert total <= 90 + 0.01, f"total {total} exceeds cap 90"

    def test_per_clip_cap_respected(self):
        out = normalize_clip_windows([{"source_start": 0, "source_end": 300}], 3600,
                                     max_clip_duration=30)
        assert out[0]["source_end"] - out[0]["source_start"] <= 30 + 0.01

    def test_ordering_each_valid(self):
        ranges = [{"source_start": 50, "source_end": 70}, {"source_start": 10, "source_end": 30}]
        out = normalize_clip_windows(ranges, 120)
        for c in out:
            assert c["source_start"] < c["source_end"]

    def test_zero_duration_video(self):
        # full_duration=0 -> max(4, 0)=4 fallback; must not crash or produce negative
        out = normalize_clip_windows([{"source_start": 0, "source_end": 10}], 0)
        for c in out:
            assert c["source_start"] < c["source_end"]


# ---------------------------------------------------------------------------
# UNIT: sfx_library.save_uploaded_sfx
# ---------------------------------------------------------------------------
class TestSfxUpload:
    def _dir(self):
        d = Path(tempfile.mkdtemp())
        return d

    def test_rejects_bad_extension(self):
        with pytest.raises(ValueError):
            sfx_library.save_uploaded_sfx(self._dir(), "evil.sh", b"data")

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            sfx_library.save_uploaded_sfx(self._dir(), "a.mp3", b"")

    def test_rejects_oversize(self):
        big = b"x" * (sfx_library.MAX_SFX_BYTES + 1)
        with pytest.raises(ValueError):
            sfx_library.save_uploaded_sfx(self._dir(), "a.mp3", big)

    def test_traversal_filename_stays_inside(self):
        d = self._dir()
        asset = sfx_library.save_uploaded_sfx(d, "../../../../pwn.mp3", b"data")
        written = list(d.glob("*.mp3"))
        assert all(d.resolve() in p.resolve().parents for p in written)
        assert asset.filename == "pwn.mp3"


# ---------------------------------------------------------------------------
# INTEGRATION: API endpoints
# ---------------------------------------------------------------------------
class TestApi:
    def test_health(self):
        r = client.get("/api/health")
        assert r.status_code == 200 and r.json()["status"] == "ok"

    def test_models_status(self):
        assert client.get("/api/models/status").status_code == 200

    def test_project_save_load_roundtrip(self):
        pid = "qa_roundtrip_1"
        body = {"project_state": _minimal_project(pid)}
        assert client.post("/api/projects/save", json=body).status_code == 200
        r = client.get(f"/api/projects/{pid}")
        assert r.status_code == 200
        assert r.json()["projectId"] == pid

    def test_load_missing_project_404(self):
        assert client.get("/api/projects/does_not_exist_zzz").status_code == 404

    def test_traversal_load_blocked(self):
        for p in ["..%2f..%2f..%2fetc%2fpasswd", "....//etc/passwd"]:
            assert client.get(f"/api/projects/{p}").status_code == 404

    def test_ingest_rejects_missing_fields(self):
        assert client.post("/api/ingest", json={}).status_code == 422

    def test_ingest_rejects_file_url(self):
        r = client.post("/api/ingest", json={"youtube_url": "file:///etc/passwd",
                                             "clip_only": True, "initial_prompt": "x"})
        assert r.status_code >= 400  # must not read local file

    def test_sfx_list(self):
        r = client.get("/api/sfx")
        assert r.status_code == 200 and len(r.json()["items"]) >= 1

    def test_sfx_delete_missing_404(self):
        assert client.delete("/api/sfx/sfx_nonexistent").status_code == 404


# ---------------------------------------------------------------------------
# MIDDLEWARE: CORS
# ---------------------------------------------------------------------------
class TestCorsMiddleware:
    def test_allows_localhost_origin(self):
        r = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert r.headers.get("access-control-allow-origin") is not None

    def test_blocks_evil_origin(self):
        r = client.get("/api/health", headers={"Origin": "http://evil.example.com"})
        # evil origin must NOT be echoed back as allowed
        assert r.headers.get("access-control-allow-origin") != "http://evil.example.com"


def _minimal_project(pid):
    return {
        "projectId": pid, "projectName": "QA", "youtubeUrl": "",
        "sourceVideo": {"videoUrl": "", "title": "t", "thumbnail": "",
                        "originalDuration": 10, "heatmap": [], "audioPeaks": []},
        "workspaceState": {"playheadPosition": 0, "selectedClipIds": [],
                           "selectedTimeRange": None, "targetAspectRatio": "16:9",
                           "activeCampaignId": None, "overlayText": None, "overlayStyle": {}},
        "tracks": {"clips": [], "captions": []},
    }
