import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
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
    OverlayStyle,
    ProjectSaveRequest,
    ProjectState,
    ReferenceStyleRequest,
    ReferenceStyleResponse,
    SfxUpdateRequest,
    SourceVideo,
    SubtitleExportRequest,
    TracksState,
    WorkspaceState,
)
from services.ai_orchestrator import (
    _deterministic_viral_fallback,
    _extract_top_heatmap_peaks,
    _format_timeline_transcript_for_llm,
    _normalize_color_to_hex,
    _run_gemini_viral_pipeline,
    process_ai_edit,
)
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
from services.reference_style import clone_reference_style_for_project
from services.sfx_library import (
    delete_sfx_asset,
    list_sfx_assets,
    save_uploaded_sfx,
    update_sfx_metadata,
)
from services.transcription import (
    build_captions_from_transcript,
    detect_pause_gaps,
    load_cached_transcript,
    transcribe_audio_groq,
    transcribe_audio_slices_groq,
    transcribe_voice_blob,
)
from services.youtube_ingest import (
    IngestValidationError,
    download_youtube_clip_segments,
    extract_youtube_audio_and_meta,
    ingest_youtube_video,
    parse_explicit_clip_range,
    remap_clips_and_transcript_for_segments,
)

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
WORKSPACE_DIR = ROOT_DIR / "workspace_data"

load_dotenv(BASE_DIR / ".env")

for sub in ("downloads", "transcripts", "projects", "exports", "sfx"):
    (WORKSPACE_DIR / sub).mkdir(parents=True, exist_ok=True)

async def _periodic_model_refresh_loop() -> None:
    while True:
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(refresh_models, True)
        except Exception:
            pass


def _disable_windows_quick_edit() -> None:
    """Prevents accidental CMD clicks from entering Select mode and freezing Uvicorn."""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            new_mode = (mode.value & ~0x0040) | 0x0080  # clear ENABLE_QUICK_EDIT_MODE, keep ENABLE_EXTENDED_FLAGS
            kernel32.SetConsoleMode(handle, new_mode)
    except Exception:
        pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _disable_windows_quick_edit()
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
        campaign_overlay = (
            req.overlay_text or ("LACY" if req.campaign_id == "lacy_campaign" else "")
        ).strip() or None
        resolved_overlay_style = req.overlay_style or OverlayStyle()

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
            source_span_duration = float(meta["duration"])
            full_video_duration = float(meta.get("full_duration") or source_span_duration)
            transcribed_window_duration = float(
                meta.get("transcribed_window_duration") or source_span_duration
            )
            try:
                if meta.get("prefetched_transcript"):
                    full_transcript = meta["prefetched_transcript"]
                    full_transcript_path.parent.mkdir(parents=True, exist_ok=True)
                    full_transcript_path.write_text(
                        json.dumps(full_transcript, separators=(",", ":")),
                        encoding="utf-8",
                    )
                elif meta.get("audio_slices"):
                    full_transcript = await asyncio.to_thread(
                        transcribe_audio_slices_groq,
                        project_id=f"{project_id}_full",
                        audio_slices=meta["audio_slices"],
                        transcripts_dir=transcripts_dir,
                        duration=full_video_duration,
                    )
                else:
                    full_transcript = await asyncio.to_thread(
                        transcribe_audio_groq,
                        project_id=f"{project_id}_full",
                        audio_path=meta["audio_path"],
                        transcripts_dir=transcripts_dir,
                        duration=source_span_duration,
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
                        originalDuration=source_span_duration,
                        heatmap=[HeatmapPoint(**pt) for pt in meta["heatmap"]],
                        audioPeaks=[],
                    ),
                    workspaceState=WorkspaceState(
                        playheadPosition=0.0,
                        selectedClipIds=["clip_full"],
                        selectedTimeRange=None,
                        targetAspectRatio="16:9",
                        activeCampaignId=req.campaign_id,
                        overlayText=campaign_overlay,
                        overlayStyle=resolved_overlay_style,
                    ),
                    tracks=TracksState(
                        clips=[
                            ClipItem(
                                id="clip_full",
                                title=meta["title"][:48],
                                sourceStart=0.0,
                                sourceEnd=round(source_span_duration, 2),
                                timelineStart=0.0,
                                duration=round(source_span_duration, 2),
                            )
                        ],
                        captions=[],
                    ),
                    captionStyle=CaptionStyle(),
                )

                print(
                    f"[Ingest] Running AI clip discovery ({round(source_span_duration, 1)}s span, "
                    f"{len(full_transcript.get('segments') or [])} transcript segs)...",
                    flush=True,
                )
                ai_res = await asyncio.to_thread(
                    _run_gemini_viral_pipeline,
                    initial_prompt,
                    temp_project,
                    full_transcript,
                    req.campaign_id,
                    req.campaign_rules,
                    meta.get("chapters"),
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
                        style_obj.textColor = (
                            _normalize_color_to_hex(s_args["text_color"])
                            or s_args["text_color"].strip()
                        )
                    if isinstance(s_args.get("highlight_color"), str) and s_args["highlight_color"].strip():
                        style_obj.highlightColor = (
                            _normalize_color_to_hex(s_args["highlight_color"])
                            or s_args["highlight_color"].strip()
                        )
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
                    fb_res = _deterministic_viral_fallback(
                        initial_prompt,
                        temp_project,
                        full_transcript,
                        _extract_top_heatmap_peaks(temp_project),
                    )
                    for tc in fb_res.tool_calls:
                        if tc.name == "isolate_highlight_clips" and isinstance(
                            tc.arguments.get("clips"), list
                        ):
                            clip_ranges.extend(tc.arguments["clips"])

                allow_long_clips = bool(
                    parse_explicit_clip_range(initial_prompt, full_video_duration)
                )
                seg_data = await asyncio.to_thread(
                    download_youtube_clip_segments,
                    req.youtube_url,
                    project_id,
                    downloads_dir,
                    clip_ranges,
                    source_span_duration,
                    allow_long_clips,
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
                    source_span_duration,
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
                        activeCampaignId=req.campaign_id,
                        overlayText=campaign_overlay,
                        overlayStyle=resolved_overlay_style,
                    ),
                    tracks=TracksState(
                        clips=clips,
                        captions=captions,
                    ),
                    captionStyle=caption_style,
                )

                proj_file = WORKSPACE_DIR / "projects" / f"{project.projectId}.json"
                proj_file.write_text(project.model_dump_json(indent=2), encoding="utf-8")

                scan_note = (
                    f"sampled {round(transcribed_window_duration, 1)}s peak audio slices; "
                    if meta.get("audio_slices")
                    else ""
                )
                return IngestResponse(
                    project=project,
                    transcript=remapped_transcript,
                    ai_message=(
                        f"{ai_res.message} (Low-storage clip ingest: {scan_note}downloaded "
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
                for sl in meta.get("audio_slices") or []:
                    if isinstance(sl, dict) and sl.get("path"):
                        Path(sl["path"]).unlink(missing_ok=True)

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
        full_captions = (
            build_captions_from_transcript(transcript)
            if (req.campaign_id and transcript)
            else []
        )
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
                activeCampaignId=req.campaign_id,
                overlayText=campaign_overlay,
                overlayStyle=resolved_overlay_style,
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
                captions=full_captions,
            ),
            captionStyle=CaptionStyle(),
        )

        proj_file = WORKSPACE_DIR / "projects" / f"{project.projectId}.json"
        proj_file.write_text(project.model_dump_json(indent=2), encoding="utf-8")

        return IngestResponse(project=project, transcript=transcript)
    except HTTPException:
        raise
    except IngestValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        clean_err = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).strip()
        print(f"[Ingest] ERROR: {clean_err}", flush=True)
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
            campaign_id=req.campaign_id,
            campaign_rules=req.campaign_rules,
            sfx_dir=WORKSPACE_DIR / "sfx",
            deep_think=req.deep_think,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AI edit error: {exc}") from exc


@app.post("/api/reference-style", response_model=ReferenceStyleResponse)
async def reference_style_endpoint(req: ReferenceStyleRequest) -> ReferenceStyleResponse:
    ref_url = req.reference_url.strip()
    if not ref_url:
        raise HTTPException(status_code=400, detail="reference_url is required.")
    try:
        safe_id = _sanitize_project_id(req.project_state.projectId)
    except Exception:
        safe_id = req.project_state.projectId

    transcript = load_cached_transcript(safe_id, WORKSPACE_DIR / "transcripts")
    sfx_assets = await asyncio.to_thread(list_sfx_assets, WORKSPACE_DIR / "sfx")
    tl_text = _format_timeline_transcript_for_llm(
        transcript, req.project_state.tracks.clips, max_segments=40, max_chars=3500
    )
    try:
        profile, tool_calls, auto_captions, summary_msg, route_label = await asyncio.to_thread(
            clone_reference_style_for_project,
            ref_url,
            req.project_state,
            transcript,
            sfx_assets,
            WORKSPACE_DIR / "downloads",
            req.custom_instruction,
            tl_text,
            req.deep_think,
        )
        return ReferenceStyleResponse(
            profile=profile,
            message=summary_msg,
            tool_calls=tool_calls,
            auto_captions=auto_captions,
            route_used=route_label,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Reference style analysis failed: {exc}") from exc


@app.get("/api/sfx")
async def list_sfx_endpoint() -> Dict[str, Any]:
    assets = await asyncio.to_thread(list_sfx_assets, WORKSPACE_DIR / "sfx")
    return {"items": [a.model_dump() for a in assets]}


@app.post("/api/sfx/upload")
async def upload_sfx_endpoint(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    description: Optional[str] = Form(""),
) -> Dict[str, Any]:
    raw = await file.read()
    parsed_tags: Optional[List[str]] = None
    if tags is not None and tags.strip():
        parsed_tags = [t.strip() for t in tags.split(",") if t.strip()]
    try:
        asset = await asyncio.to_thread(
            save_uploaded_sfx,
            WORKSPACE_DIR / "sfx",
            file.filename or "sfx.mp3",
            raw,
            name=name,
            tags=parsed_tags,
            description=description or "",
        )
        return {"asset": asset.model_dump()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"SFX upload failed: {exc}") from exc


@app.patch("/api/sfx/{sfx_id}")
async def update_sfx_endpoint(sfx_id: str, req: SfxUpdateRequest) -> Dict[str, Any]:
    updated = await asyncio.to_thread(
        update_sfx_metadata,
        WORKSPACE_DIR / "sfx",
        sfx_id,
        name=req.name,
        tags=req.tags,
        description=req.description,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="SFX asset not found.")
    return {"asset": updated.model_dump()}


@app.delete("/api/sfx/{sfx_id}")
async def delete_sfx_endpoint(sfx_id: str) -> Dict[str, str]:
    deleted = await asyncio.to_thread(delete_sfx_asset, WORKSPACE_DIR / "sfx", sfx_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="SFX asset not found.")
    return {"status": "deleted", "id": sfx_id}


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
