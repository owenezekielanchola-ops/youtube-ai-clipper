import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Set
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from schemas import (
    AiEditRequest,
    AiEditResponse,
    AutoCaptionsRequest,
    CaptionStyle,
    ClipItem,
    DetectPausesRequest,
    ExportRequest,
    ExportResponse,
    HeatmapPoint,
    IngestRequest,
    IngestResponse,
    ProjectSaveRequest,
    ProjectState,
    SourceVideo,
    SubtitleExportRequest,
    TracksState,
    WorkspaceState,
)
from services.ai_orchestrator import _run_gemini_viral_pipeline, process_ai_edit
from services.ass_subtitles import (
    generate_ass_subtitles,
    generate_srt_content,
    generate_vtt_content,
)
from services.ffmpeg_render import render_project_video
from services.model_registry import (
    REFRESH_INTERVAL_SECONDS,
    get_registry_state,
    refresh_models,
)
from services.transcription import (
    build_captions_from_transcript,
    detect_pause_gaps,
    load_cached_transcript,
    transcribe_audio_groq,
    transcribe_voice_blob,
)
from services.youtube_ingest import (
    download_youtube_clip_segments,
    extract_youtube_audio_and_meta,
    ingest_youtube_video,
    remap_clips_and_transcript_for_segments,
)

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
WORKSPACE_DIR = ROOT_DIR / "workspace_data"

load_dotenv(BASE_DIR / ".env")

for sub in ("downloads", "transcripts", "projects", "exports"):
    (WORKSPACE_DIR / sub).mkdir(parents=True, exist_ok=True)

async def _periodic_model_refresh_loop() -> None:
    while True:
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(refresh_models, True)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_task = asyncio.create_task(asyncio.to_thread(get_registry_state, False))
    refresh_task = asyncio.create_task(_periodic_model_refresh_loop())
    try:
        yield
    finally:
        refresh_task.cancel()
        init_task.cancel()
        for task in (refresh_task, init_task):
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(
    title="YouTube AI Clipper & Editor API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/media", StaticFiles(directory=str(WORKSPACE_DIR)), name="media")


class ProgressConnectionManager:
    def __init__(self) -> None:
        self.active: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        dead: List[WebSocket] = []
        for ws in list(self.active):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.discard(ws)


progress_manager = ProgressConnectionManager()


def _sanitize_project_id(raw_id: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]", "", (raw_id or "").strip())
    if not clean:
        raise HTTPException(status_code=400, detail="Invalid project_id")
    return clean


@app.get("/api/health")
@app.get("/health")
async def health_check() -> Dict[str, Any]:
    state = await asyncio.to_thread(get_registry_state, False)
    return {
        "status": "ok",
        "workspace": str(WORKSPACE_DIR),
        "groq_configured": state["groq_configured"],
        "gemini_configured": state["gemini_configured"],
        "primary_tool_model": state["primary_tool_model"],
        "backup_tool_models": state["backup_tool_models"],
        "primary_stt_model": state["primary_stt_model"],
        "backup_stt_models": state["backup_stt_models"],
        "primary_viral_model": state["primary_viral_model"],
        "backup_viral_models": state["backup_viral_models"],
        "last_refreshed_iso": state["last_refreshed_iso"],
    }


@app.get("/api/models/status")
async def get_models_status() -> Dict[str, Any]:
    return await asyncio.to_thread(get_registry_state, False)


@app.post("/api/models/refresh")
async def force_refresh_models() -> Dict[str, Any]:
    state = await asyncio.to_thread(refresh_models, True)
    return {"status": "refreshed", **state}


@app.post("/ingest", response_model=IngestResponse)
@app.post("/api/ingest", response_model=IngestResponse)
async def ingest_video_endpoint(req: IngestRequest) -> IngestResponse:
    try:
        initial_prompt = (req.initial_prompt or "").strip()
        is_demo_url = req.youtube_url.strip().lower() in {"demo://sample", "sample", "demo"}

        if initial_prompt and req.clip_only and not is_demo_url:
            project_id = f"proj_{uuid.uuid4().hex[:8]}"
            downloads_dir = WORKSPACE_DIR / "downloads"
            transcripts_dir = WORKSPACE_DIR / "transcripts"
            full_transcript_path = transcripts_dir / f"{project_id}_full.json"

            meta = await asyncio.to_thread(
                extract_youtube_audio_and_meta,
                req.youtube_url,
                project_id,
                downloads_dir,
                initial_prompt,
            )
            full_duration = float(meta["duration"])
            full_video_duration = float(meta.get("full_duration") or full_duration)
            try:
                if meta.get("prefetched_transcript"):
                    full_transcript = meta["prefetched_transcript"]
                    full_transcript_path.parent.mkdir(parents=True, exist_ok=True)
                    full_transcript_path.write_text(
                        json.dumps(full_transcript, indent=2), encoding="utf-8"
                    )
                else:
                    full_transcript = await asyncio.to_thread(
                        transcribe_audio_groq,
                        project_id=f"{project_id}_full",
                        audio_path=meta["audio_path"],
                        transcripts_dir=transcripts_dir,
                        duration=full_duration,
                        is_demo=False,
                    )

                temp_project = ProjectState(
                    projectId=f"{project_id}_full",
                    projectName=req.project_name or meta["title"],
                    youtubeUrl=req.youtube_url,
                    sourceVideo=SourceVideo(
                        videoUrl="",
                        title=meta["title"],
                        thumbnail=meta["thumbnail"],
                        originalDuration=full_duration,
                        heatmap=[HeatmapPoint(**pt) for pt in meta["heatmap"]],
                        audioPeaks=[],
                    ),
                    workspaceState=WorkspaceState(
                        playheadPosition=0.0,
                        selectedClipIds=["clip_full"],
                        selectedTimeRange=None,
                        targetAspectRatio="16:9",
                    ),
                    tracks=TracksState(
                        clips=[
                            ClipItem(
                                id="clip_full",
                                title=meta["title"][:48],
                                sourceStart=0.0,
                                sourceEnd=round(full_duration, 2),
                                timelineStart=0.0,
                                duration=round(full_duration, 2),
                            )
                        ],
                        captions=[],
                    ),
                    captionStyle=CaptionStyle(),
                )

                ai_res = await asyncio.to_thread(
                    process_ai_edit,
                    prompt=initial_prompt,
                    project_state=temp_project,
                    transcripts_dir=transcripts_dir,
                    history=[],
                )
                clip_ranges: List[Dict[str, Any]] = []
                target_ratio = "16:9"
                caption_style = CaptionStyle()

                def _apply_style_args(style_obj: CaptionStyle, s_args: Dict[str, Any]) -> None:
                    if s_args.get("preset") in {"karaoke", "bold_social", "yellow_highlight", "minimal"}:
                        style_obj.preset = s_args["preset"]
                    if isinstance(s_args.get("font_family"), str) and s_args["font_family"].strip():
                        style_obj.fontFamily = s_args["font_family"].strip()
                    if s_args.get("font_size") is not None:
                        try:
                            style_obj.fontSize = max(12, min(72, int(s_args["font_size"])))
                        except (TypeError, ValueError):
                            pass
                    if isinstance(s_args.get("text_color"), str) and s_args["text_color"].strip():
                        style_obj.textColor = s_args["text_color"].strip()
                    if isinstance(s_args.get("highlight_color"), str) and s_args["highlight_color"].strip():
                        style_obj.highlightColor = s_args["highlight_color"].strip()
                    if s_args.get("position") in {"top", "middle", "bottom"}:
                        style_obj.position = s_args["position"]
                    if isinstance(s_args.get("is_bold"), bool):
                        style_obj.isBold = s_args["is_bold"]

                for tc in ai_res.tool_calls:
                    if tc.name == "isolate_highlight_clips" and isinstance(
                        tc.arguments.get("clips"), list
                    ):
                        clip_ranges.extend(tc.arguments["clips"])
                    elif tc.name == "change_video_format" and tc.arguments.get("aspect_ratio") in {
                        "16:9",
                        "9:16",
                        "1:1",
                    }:
                        target_ratio = str(tc.arguments["aspect_ratio"])
                    elif tc.name == "change_caption_style" and isinstance(tc.arguments, dict):
                        _apply_style_args(caption_style, tc.arguments)

                if not clip_ranges:
                    ai_res = await asyncio.to_thread(
                        _run_gemini_viral_pipeline,
                        initial_prompt,
                        temp_project,
                        full_transcript,
                    )
                    for tc in ai_res.tool_calls:
                        if tc.name == "isolate_highlight_clips" and isinstance(
                            tc.arguments.get("clips"), list
                        ):
                            clip_ranges.extend(tc.arguments["clips"])
                        elif tc.name == "change_video_format" and tc.arguments.get(
                            "aspect_ratio"
                        ) in {"16:9", "9:16", "1:1"}:
                            target_ratio = str(tc.arguments["aspect_ratio"])
                        elif tc.name == "change_caption_style" and isinstance(tc.arguments, dict):
                            _apply_style_args(caption_style, tc.arguments)

                seg_data = await asyncio.to_thread(
                    download_youtube_clip_segments,
                    req.youtube_url,
                    project_id,
                    downloads_dir,
                    clip_ranges,
                    full_duration,
                )

                (
                    clips,
                    remapped_transcript,
                    remapped_heatmap,
                    total_clip_duration,
                    _window_saved_pct,
                ) = remap_clips_and_transcript_for_segments(
                    seg_data["normalized_ranges"],
                    full_transcript,
                    meta["heatmap"],
                    full_duration,
                )

                final_duration = float(seg_data["duration"] or total_clip_duration)
                storage_saved_pct = max(
                    0,
                    min(
                        99,
                        round((1.0 - final_duration / max(1.0, full_video_duration)) * 100),
                    ),
                )
                final_transcript_path = transcripts_dir / f"{project_id}.json"
                final_transcript_path.write_text(
                    json.dumps(remapped_transcript, indent=2), encoding="utf-8"
                )
                captions = build_captions_from_transcript(remapped_transcript)

                project = ProjectState(
                    projectId=project_id,
                    projectName=req.project_name or meta["title"],
                    youtubeUrl=req.youtube_url,
                    sourceVideo=SourceVideo(
                        videoUrl=f"http://localhost:8000/media/downloads/{project_id}.mp4",
                        title=meta["title"],
                        thumbnail=meta["thumbnail"],
                        originalDuration=round(final_duration, 2),
                        heatmap=[HeatmapPoint(**pt) for pt in remapped_heatmap],
                        audioPeaks=seg_data["audio_peaks"],
                    ),
                    workspaceState=WorkspaceState(
                        playheadPosition=0.0,
                        selectedClipIds=[clips[0].id] if clips else [],
                        selectedTimeRange=None,
                        targetAspectRatio=target_ratio,  # type: ignore[arg-type]
                    ),
                    tracks=TracksState(
                        clips=clips,
                        captions=captions,
                    ),
                    captionStyle=caption_style,
                )

                proj_file = WORKSPACE_DIR / "projects" / f"{project.projectId}.json"
                proj_file.write_text(project.model_dump_json(indent=2), encoding="utf-8")

                return IngestResponse(
                    project=project,
                    transcript=remapped_transcript,
                    ai_message=(
                        f"{ai_res.message} (Low-storage clip ingest: downloaded "
                        f"{round(final_duration, 1)}s of {round(full_video_duration, 1)}s total, "
                        f"saved ~{storage_saved_pct}% storage.)"
                    ),
                    route_used=f"{ai_res.route_used} [prompt-first-ingest]",
                    storage_saved_pct=storage_saved_pct,
                )
            finally:
                full_transcript_path.unlink(missing_ok=True)
                if meta.get("audio_path"):
                    Path(meta["audio_path"]).unlink(missing_ok=True)

        ingest_data = await ingest_youtube_video(
            req.youtube_url, WORKSPACE_DIR, base_url="http://localhost:8000"
        )
        transcript = await asyncio.to_thread(
            transcribe_audio_groq,
            project_id=ingest_data["project_id"],
            audio_path=ingest_data["audio_path"],
            transcripts_dir=WORKSPACE_DIR / "transcripts",
            duration=ingest_data["duration"],
            is_demo=ingest_data.get("is_demo", False),
        )

        duration = float(ingest_data["duration"])
        project = ProjectState(
            projectId=ingest_data["project_id"],
            projectName=req.project_name or ingest_data["title"],
            youtubeUrl=req.youtube_url,
            sourceVideo=SourceVideo(
                videoUrl=ingest_data["video_url"],
                title=ingest_data["title"],
                thumbnail=ingest_data["thumbnail"],
                originalDuration=duration,
                heatmap=[HeatmapPoint(**pt) for pt in ingest_data["heatmap"]],
                audioPeaks=ingest_data["audio_peaks"],
            ),
            workspaceState=WorkspaceState(
                playheadPosition=0.0,
                selectedClipIds=["clip_1"],
                selectedTimeRange=None,
                targetAspectRatio="16:9",
            ),
            tracks=TracksState(
                clips=[
                    ClipItem(
                        id="clip_1",
                        title=ingest_data["title"][:48],
                        sourceStart=0.0,
                        sourceEnd=round(duration, 2),
                        timelineStart=0.0,
                        duration=round(duration, 2),
                    )
                ],
                captions=[],
            ),
            captionStyle=CaptionStyle(),
        )

        proj_file = WORKSPACE_DIR / "projects" / f"{project.projectId}.json"
        proj_file.write_text(project.model_dump_json(indent=2), encoding="utf-8")

        return IngestResponse(project=project, transcript=transcript)
    except Exception as exc:
        clean_err = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).strip()
        raise HTTPException(status_code=500, detail=f"Ingest failed: {clean_err}") from exc


@app.post("/ai-edit", response_model=AiEditResponse)
@app.post("/api/ai-edit", response_model=AiEditResponse)
async def ai_edit_endpoint(req: AiEditRequest) -> AiEditResponse:
    try:
        return await asyncio.to_thread(
            process_ai_edit,
            prompt=req.prompt,
            project_state=req.project_state,
            transcripts_dir=WORKSPACE_DIR / "transcripts",
            history=req.history,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AI edit error: {exc}") from exc


@app.post("/api/auto-captions")
async def auto_captions_endpoint(req: AutoCaptionsRequest) -> Dict[str, Any]:
    safe_id = _sanitize_project_id(req.project_id)
    transcript = load_cached_transcript(safe_id, WORKSPACE_DIR / "transcripts")
    if not transcript:
        raise HTTPException(status_code=404, detail="Cached transcript not found for project.")
    captions = build_captions_from_transcript(transcript, req.start_time, req.end_time)
    return {"captions": [c.model_dump() for c in captions]}


@app.post("/api/detect-pauses")
async def detect_pauses_endpoint(req: DetectPausesRequest) -> Dict[str, Any]:
    safe_id = _sanitize_project_id(req.project_id)
    transcript = load_cached_transcript(safe_id, WORKSPACE_DIR / "transcripts")
    if not transcript:
        raise HTTPException(status_code=404, detail="Cached transcript not found for project.")
    ranges = detect_pause_gaps(transcript, min_gap=req.min_gap)
    return {"ranges": ranges}


@app.post("/api/voice-transcribe")
async def voice_transcribe_endpoint(file: UploadFile = File(...)) -> Dict[str, str]:
    try:
        raw = await file.read()
        text = await asyncio.to_thread(
            transcribe_voice_blob, raw, file.filename or "command.webm"
        )
        return {"text": text}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Voice transcription failed: {exc}") from exc


@app.post("/export", response_model=ExportResponse)
@app.post("/api/export", response_model=ExportResponse)
async def export_video_endpoint(req: ExportRequest) -> ExportResponse:
    async def on_progress(pct: int, status_msg: str) -> None:
        await progress_manager.broadcast(
            {"progress": pct, "status": status_msg, "projectId": req.project_state.projectId}
        )

    try:
        files = await render_project_video(
            project_state=req.project_state,
            export_mode=req.export_mode,
            workspace_dir=WORKSPACE_DIR,
            progress_cb=on_progress,
        )
        full_files = [
            {
                "filename": f["filename"],
                "title": f["title"],
                "url": f"http://localhost:8000{f['url']}",
            }
            for f in files
        ]
        await progress_manager.broadcast(
            {
                "progress": 100,
                "status": "Export complete!",
                "projectId": req.project_state.projectId,
                "files": full_files,
            }
        )
        return ExportResponse(status="completed", files=full_files)
    except Exception as exc:
        await progress_manager.broadcast(
            {
                "progress": 0,
                "status": f"Export error: {exc}",
                "projectId": req.project_state.projectId,
                "error": True,
            }
        )
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc


@app.post("/api/subtitles")
async def export_subtitles_endpoint(req: SubtitleExportRequest) -> PlainTextResponse:
    safe_id = _sanitize_project_id(req.project_state.projectId)
    if req.format == "srt":
        content = generate_srt_content(req.project_state)
        media_type = "application/x-subrip"
    elif req.format == "vtt":
        content = generate_vtt_content(req.project_state)
        media_type = "text/vtt"
    else:
        ass_tmp = WORKSPACE_DIR / "exports" / f"{safe_id}_{uuid.uuid4().hex[:8]}.ass"
        try:
            generate_ass_subtitles(req.project_state, ass_tmp)
            content = ass_tmp.read_text(encoding="utf-8")
        finally:
            ass_tmp.unlink(missing_ok=True)
        media_type = "text/plain"
    return PlainTextResponse(content=content, media_type=media_type)


@app.get("/api/projects")
async def list_projects() -> Dict[str, List[Dict[str, Any]]]:
    projects_dir = WORKSPACE_DIR / "projects"
    items: List[Dict[str, Any]] = []
    for p in sorted(projects_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            items.append(
                {
                    "projectId": data.get("projectId"),
                    "projectName": data.get("projectName") or data.get("sourceVideo", {}).get("title"),
                    "youtubeUrl": data.get("youtubeUrl"),
                    "duration": data.get("sourceVideo", {}).get("originalDuration", 0),
                }
            )
        except Exception:
            continue
    return {"projects": items}


@app.post("/api/projects/save")
async def save_project_endpoint(req: ProjectSaveRequest) -> Dict[str, str]:
    safe_id = _sanitize_project_id(req.project_state.projectId)
    proj_file = WORKSPACE_DIR / "projects" / f"{safe_id}.json"
    proj_file.write_text(req.project_state.model_dump_json(indent=2), encoding="utf-8")
    return {"status": "saved", "path": str(proj_file)}


@app.get("/api/projects/{project_id}", response_model=ProjectState)
async def load_project_endpoint(project_id: str) -> ProjectState:
    safe_id = _sanitize_project_id(project_id)
    proj_file = WORKSPACE_DIR / "projects" / f"{safe_id}.json"
    if not proj_file.exists():
        raise HTTPException(status_code=404, detail="Project JSON not found.")
    return ProjectState.model_validate_json(proj_file.read_text(encoding="utf-8"))


@app.get("/api/projects/{project_id}/transcript")
async def get_project_transcript_endpoint(project_id: str) -> Dict[str, Any]:
    safe_id = _sanitize_project_id(project_id)
    transcript = load_cached_transcript(safe_id, WORKSPACE_DIR / "transcripts")
    return {"transcript": transcript}


@app.websocket("/ws/progress")
async def websocket_progress(websocket: WebSocket) -> None:
    await progress_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        progress_manager.disconnect(websocket)
