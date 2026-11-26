# CapCut-Style Editor, Custom MP3 SFX, "Edit Like This" & AI Assistant — Feature Specification & TODO

> **Purpose:** Source-of-truth architectural specification and phased TODO checklist so every CapCut editing tool, custom MP3 sound effect workflow, YouTube reference style cloner, and AI natural-language command integrates cleanly with the existing **Shared Editing Engine** without hallucinated paths or broken exports.

---

## 1. Core Architecture & Anti-Hallucination Ground Rules

- **Single Shared Editing Engine:** Every manual UI button, canvas drawing, timeline drag, AI tool call, and `"Edit Like This"` clone mutates the same non-destructive `ProjectState` EDL via Zustand (`useEditorStore.ts`) with full `Ctrl+Z` / `Ctrl+Y` (`zundo`) Undo/Redo support.
- **Coordinate System Contract:**
  - `tracks.clips` and `tracks.captions` stay in **source seconds** (`sourceStart`, `sourceEnd`). Each `ClipItem` adds `speed` (`default 1.0`), making its timeline length `duration = (sourceEnd - sourceStart) / speed`.
  - `tracks.zooms`, `tracks.drawings`, and `tracks.sfx` use **timeline seconds** (`start`, `end`) across the master timeline.
  - `tracks.drawings` points and `tracks.zooms` focal points (`focusX`, `focusY`) use **normalized `0.0..1.0` coordinates** inside the active aspect-ratio frame (`16:9`, `9:16`, or `1:1`) so live CSS/SVG preview matches FFmpeg MP4 export at every resolution.
- **Zero-Heavy-Dependency 8GB RAM Export Strategy:**
  - **Drawings:** Rendered as native ASS vector drawing commands (`{\p1}...{\p0}` on Layer `2`) inside `backend/services/ass_subtitles.py` (no Pillow/OpenCV bloat).
  - **Zooms:** Rendered with FFmpeg dynamic `crop`/`scale` expressions driven by timestamp `t` (avoids slow `zoompan` memory overhead).
  - **Speed:** Rendered with `setpts=PTS/{speed}` + chained `atempo` filters (`0.5..2.0` per stage).
  - **Sound Effects:** Mixed into exported MP4 using `adelay` + `volume` + `amix=inputs=N:duration=first:normalize=0`.

---

## 2. Exact User Requirements (Source of Truth)

### 1. CapCut-Style Manual Editing Features
- **Zooming:** Zoom in, zoom out, and punch-in zooms on specific moments (with adjustable scale, easing, and focus position).
- **Drawing on Video:** Draw freehand lines, straight lines, arrows, circles, boxes, or highlights directly onto the video preview at specific timestamp windows.
- **Speed Controls:** Speed up or slow down the whole clip or specific sub-ranges of a clip (`0.25x` – `4.0x`).
- **Sound Effects:** Place sound effects on the timeline, drag timing, and adjust volume.

### 2. Custom MP3 Sound Effects Library
- Dedicated **SFX Library** section where the user can upload/add their own `.mp3` (and `.wav`) files (once provided), tag/describe each sound (e.g., `"vine boom"`, `"whoosh"`, `"ding"`, `"laugh"`), audition them, and add them at the playhead.
- The AI assistant and `"Edit Like This"` pipeline automatically read this custom SFX catalog so the AI picks and places the right uploaded MP3 files where they fit best.

### 3. "Edit Like This" Using a YouTube Reference Clip
- Dedicated place to paste a reference YouTube clip URL and click/prompt **"Edit Like This"**.
- The AI watches the reference clip's cuts, pacing, aspect ratio, caption style, zoom rhythm, speed changes, drawing callouts, and SFX moments (via Gemini multimodal `Part.from_uri` with `yt-dlp` fallback) and replicates that editing style onto the user's clip.

### 4. Natural-Language AI Editing Assistant
- Understands plain conversational editing prompts, resolving `"here"` / `"this part"` from `playheadPosition` / `selectedTimeRange` and `"when he says <words>"` from cached word timestamps:
  - `"Speed up this part"`
  - `"Zoom in when he says this"`
  - `"Add a sound effect here"`
  - `"Draw a line/arrow pointing here"`
- Automatically applies tool calls to the timeline for instant preview and MP4 export.

---

## 3. Verified Codebase Map (Exact Existing Touchpoints)

| Area | File | Verified Existing Symbols / Locations |
| :--- | :--- | :--- |
| Frontend Types | `frontend/src/types/editor.ts` | `ClipItem`, `TracksState`, `WorkspaceState`, `ProjectState` |
| Frontend Store | `frontend/src/store/useEditorStore.ts` | `recalculateTimeline`, `EditorStoreState`, `split_clip`, `applyAiToolCalls`, `zundo` `equality` |
| Left Panel | `frontend/src/components/LeftMediaAndCaptions.tsx` | `w-80` sidebar with Source Media + Manual Caption Editor (ideal home for **Captions \| SFX Library** tabs) |
| Live Preview | `frontend/src/components/CenterVideoPlayer.tsx` | `mapTimelineToSource`, `tick`, `previewBoxStyle` (`object-contain`), overlay & caption layers |
| Bottom Timeline | `frontend/src/components/BottomTimeline.tsx` | Top toolbar (`Split`, `Delete`, `Merge`) + Video, Audio & Heatmap, Captions tracks |
| Right Panel | `frontend/src/components/RightAiAndStyles.tsx` | AI Assistant chat (`submitAiPrompt`) + `Styles & LACY` customizer tab |
| Backend Schemas | `backend/schemas.py` | `ClipItem`, `TracksState`, `ProjectState`, `SHARED_TOOLS_DEFINITIONS`, `AiEditRequest` |
| Backend API | `backend/main.py` | `WORKSPACE_DIR` (`/media` mount), `/api/ai-edit`, `/api/export`, `/api/voice-transcribe` |
| AI Orchestrator | `backend/services/ai_orchestrator.py` | `FAST_EDIT_KEYWORDS`, `_classify_prompt_intent_with_ai`, `_run_groq_fast_editor`, `_run_gemini_viral_pipeline`, `process_ai_edit` |
| ASS Burn-In | `backend/services/ass_subtitles.py` | `_map_captions_to_clips`, `generate_ass_subtitles` |
| FFmpeg Render | `backend/services/ffmpeg_render.py` | `_build_vf_filter`, `_render_sync` (individual & combined paths) |
| Git Ignore | `.gitignore` | Excludes runtime media/files inside `workspace_data/` |

---

## 4. Phased Implementation Checklist

### Phase 1 — Shared EDL Schemas & Backward-Compatible State
- [x] Extend `backend/schemas.py` and `frontend/src/types/editor.ts` (with safe defaults so older saved `.json` projects remain 100% valid):
  - Add `speed: float = 1.0` (`number` in TS, bounded `0.25..4.0`) to `ClipItem`.
  - Add `ZoomEffectItem` (`id`, `start`, `end`, `kind`: `'zoom_in' | 'zoom_out' | 'punch_in'`, `startScale`, `endScale`, `focusX`, `focusY`, `easing`).
  - Add `DrawingPoint` (`x`, `y` normalized `0..1`) and `DrawingItem` (`id`, `start`, `end`, `tool`: `'freehand' | 'line' | 'arrow' | 'circle' | 'rect' | 'highlight'`, `points`, `color`, `strokeWidth`, `opacity`).
  - Add `SfxAsset` (`id`, `name`, `filename`, `url`, `duration`, `tags`, `description`) (TS ready; backend model in Phase 5 `sfx_library.py`) and `SfxClipItem` (`id`, `assetId`, `name`, `url`, `start`, `duration`, `volume`, `fadeIn`, `fadeOut`).
  - Add `zooms: []`, `drawings: []`, `sfx: []` to `TracksState`.
- [x] Update timeline math across `frontend/src/store/useEditorStore.ts`, `frontend/src/components/CenterVideoPlayer.tsx`, and `backend/services/ass_subtitles.py`:
  - `recalculateTimeline` computes `duration = (sourceEnd - sourceStart) / (clip.speed || 1.0)`.
  - `mapTimelineToSource` and `_map_captions_to_clips` convert timeline offsets to source time by multiplying elapsed clip time by `clip.speed`.
  - Include `tracks` (which holds clips, captions, zooms, drawings, sfx) cleanly in `zundo` `equality`.

### Phase 2 — Speed Controls (Manual UI + Live Preview + FFmpeg Export)
- [x] Store (`frontend/src/store/useEditorStore.ts`):
  - Add `set_clip_speed(clip_id, speed)` (recalculates timeline durations cleanly).
  - Add `set_range_speed(start_time, end_time, speed)` (automatically splits overlapping clip(s) at `start_time` and `end_time` and applies speed to the middle segment).
- [x] UI (`frontend/src/components/BottomTimeline.tsx`):
  - Add a Speed preset selector (`0.25x`, `0.5x`, `0.75x`, `1x`, `1.25x`, `1.5x`, `2x`, `3x`) to the timeline toolbar acting on the selected clip (or selected time range).
  - Show a clickable speed pill (`1.5x`) directly on clip cards when `speed !== 1.0`.
- [x] Live Preview (`frontend/src/components/CenterVideoPlayer.tsx`):
  - Sync `video.playbackRate = activeMapping.clip.speed || 1` and multiply timeline advancement `(srcTime - clip.sourceStart) / clipSpeed` inside `tick()`.
- [x] Export (`backend/services/ffmpeg_render.py`):
  - Apply `setpts=PTS/{speed}` to video and build chained `atempo` stages (`0.5..2.0` per stage) for audio on each clip cut when `clip.speed != 1.0`, handling video-only inputs safely.

### Phase 3 — Zoom Effects (Zoom In, Zoom Out, Punch-In Zoom)
- [x] Store (`frontend/src/store/useEditorStore.ts`):
  - Add `add_zoom_effect`, `update_zoom_effect`, `delete_zoom_effect`.
- [x] UI (`frontend/src/components/BottomTimeline.tsx` & `RightAiAndStyles.tsx`):
  - Add quick buttons in timeline/inspector: **Punch-In Zoom**, **Zoom In**, **Zoom Out** (adding a keyframed segment at playhead).
  - Add a **Zooms** track on `BottomTimeline.tsx` (clickable + draggable start/end handles).
  - Add Zoom Inspector controls in `RightAiAndStyles.tsx` (`startScale`, `endScale`, focal target `focusX`/`focusY`, click-on-preview focal picker).
- [x] Live Preview (`frontend/src/components/CenterVideoPlayer.tsx`):
  - Interpolate active `ZoomEffectItem` scale & focal point at 60fps and apply CSS `transform: scale(...)` + `transformOrigin` inside a clipping wrapper wrapping `<video>`.
- [x] Export (`backend/services/ffmpeg_render.py`):
  - Apply FFmpeg dynamic `crop`/`scale` zoom expressions (`crop=w='...':h='...':x='...':y='...',scale=...`) scoped by `between(t, start, end)` right after aspect-ratio padding and before `ass=` burn-in.

### Phase 4 — Drawing Overlays (Lines, Arrows, Circles, Boxes, Highlights)
- [x] Store (`frontend/src/store/useEditorStore.ts`):
  - Add `add_drawing`, `update_drawing`, `delete_drawing`.
- [x] Interactive Canvas & Toolbar (`frontend/src/components/CenterVideoPlayer.tsx` & `BottomTimeline.tsx`):
  - Add compact floating or top-of-player Drawing Toolbar (`Cursor`, `Freehand`, `Line`, `Arrow`, `Circle`, `Rect`, `Highlight`, color picker, stroke width, default duration).
  - Render an interactive `<svg viewBox="0 0 1000 1000" preserveAspectRatio="none">` layer above `<video>` capturing normalized pointer coordinates (`0..1`) cleanly and rendering live vector drawings visible during `[start, end]`.
  - Add a **Drawings** track on `BottomTimeline.tsx` with draggable duration edges and delete control.
- [x] Native ASS Vector Burn-In (`backend/services/ass_subtitles.py` & `ffmpeg_render.py`):
  - Add `Style: DrawingOverlay` (`Alignment=7`, `MarginL=0`, `MarginR=0`, `MarginV=0`, `Outline=0`, `Shadow=0`) and helper generating ASS vector drawing commands (`{\p1\pos(0,0)\an7\1c...}` for filled polygons/circles/rects/highlights/stroked brush paths/arrowheads on Layer `2`).
  - Update `needs_ass` in `ffmpeg_render.py` to also check `len(project_state.tracks.drawings) > 0`.

### Phase 5 — Custom MP3 Sound Effects Library & Timeline Audio Mixing
- [x] Backend Storage & API (`backend/services/sfx_library.py`, `backend/main.py`, `.gitignore`):
  - Create `workspace_data/sfx/.gitkeep`; update `.gitignore` to ignore `workspace_data/sfx/*` while keeping `workspace_data/sfx/.gitkeep`.
  - Implement `sfx_library.py`: scan `workspace_data/sfx/*.mp3|*.wav|*.m4a|*.ogg`, maintain `workspace_data/sfx/library.json` (`id`, `name`, `filename`, `duration`, `tags`, `description`), probe duration with `_probe_media_duration`.
  - Add API endpoints in `backend/main.py`: `GET /api/sfx`, `POST /api/sfx/upload` (multipart MP3/audio upload), `PATCH /api/sfx/{sfx_id}` (edit tags/description), `DELETE /api/sfx/{sfx_id}`.
- [x] Frontend SFX Section (`frontend/src/components/LeftMediaAndCaptions.tsx`, `BottomTimeline.tsx`, `CenterVideoPlayer.tsx`):
  - Convert Left Sidebar bottom section into **Captions | SFX Library** tabs.
  - Inside **SFX Library**: drag-and-drop/file picker `.mp3` uploader, search bar, editable name/tags (so AI knows when to pick each sound), click-to-preview play button, and **+ Place at Playhead** button.
  - Add **SFX** track on `BottomTimeline.tsx` showing SFX clips with draggable start position, trim duration, and volume control.
  - Add synchronized Web Audio / `HTMLAudioElement` trigger scheduler inside `CenterVideoPlayer.tsx` so SFX play accurately during preview and stop immediately on pause/scrub.
- [x] Export Mixing (`backend/services/ffmpeg_render.py`):
  - Collect `tracks.sfx` clips overlapping exported output, apply `[sN]atrim=...,asetpts=PTS-STARTPTS,volume={vol},adelay={delay_ms}|{delay_ms}[dN]`, and mix via `amix=inputs=N:duration=first:dropout_transition=0:normalize=0` (or synthesize silent base stream if source clip has no audio).

### Phase 6 — "Edit Like This" Reference YouTube Clip Style Cloner
- [x] Backend Analyzer & Style Mapper (`backend/services/reference_style.py`, `backend/main.py`):
  - Define `ReferenceStyleProfile` (`pacing`, `avg_cut_duration_sec`, `target_aspect_ratio`, `caption_preset`, `caption_position`, `zoom_frequency_per_min`, `zoom_intensity`, `speed_segments`, `sfx_density`, `sfx_cue_types`, `drawing_style`, `summary`).
  - Implement `analyze_reference_youtube_style(reference_url)` using Gemini multimodal (`types.Part.from_uri`) with `yt-dlp` metadata/caption + Groq fallback.
  - Implement `apply_reference_style_to_project(profile, project_state, transcript, sfx_library)` generating real shared `ToolCallSchema` operations (cuts/pacing, aspect ratio, caption style, zooms synced to transcript energy/beats, speed ramps, and auto-matched MP3 SFX from `workspace_data/sfx/`).
  - Add `POST /api/reference-style` endpoint in `backend/main.py`.
- [x] Frontend UI (`frontend/src/components/RightAiAndStyles.tsx`):
  - Add a collapsible **"Edit Like This (YouTube Reference URL)"** card in the AI Assistant tab (paste YouTube clip link + optional custom instruction + **Clone Style** button).
  - Show detected style summary card in AI chat and apply generated tool calls atomically via `applyAiToolCalls` (so 1-click **Undo** reverts the whole cloned edit cleanly).

### Phase 7 — Natural-Language AI Assistant Tool Expansion & Verification
- [x] Expand `SHARED_TOOLS_DEFINITIONS` (`backend/schemas.py`) and `applyAiToolCalls` (`frontend/src/store/useEditorStore.ts`) with:
  - `set_clip_speed` (supports clip ID or timeline `[start, end]` range)
  - `add_zoom_effect` / `delete_zoom_effect`
  - `add_drawing` / `delete_drawing`
  - `add_sfx_clip` / `delete_sfx_clip`
- [x] Enhance `backend/services/ai_orchestrator.py`:
  - Add CapCut keywords (`zoom`, `zoom in`, `zoom out`, `punch in`, `draw`, `arrow`, `circle`, `line`, `speed`, `slow motion`, `fast forward`, `sound effect`, `sfx`, `edit like`) to `FAST_EDIT_KEYWORDS` and handle `"edit like <youtube_url>"` prompts directly via `reference_style.py`.
  - Pass current `playheadPosition`, `selectedTimeRange`, `tracks.zooms`, `tracks.drawings`, `tracks.sfx`, and live `workspace_data/sfx/` catalog into `_build_editor_system_prompt` and Gemini prompts so prompts like:
    - *"Speed up this part"* -> reads selection or playhead window -> `set_clip_speed`
    - *"Zoom in when he says <phrase>"* -> locates exact word timestamp in transcript -> `add_zoom_effect`
    - *"Add a sound effect here"* -> picks best matching uploaded MP3 at `playheadPosition` -> `add_sfx_clip`
    - *"Draw an arrow pointing here"* -> places clean `arrow` drawing at `playheadPosition` -> `add_drawing`
    all work reliably.
- [ ] **Verification Checklist:**
  - [ ] Backend `py_compile` on all Python files (`schemas.py`, `main.py`, `ai_orchestrator.py`, `ass_subtitles.py`, `ffmpeg_render.py`, `sfx_library.py`, `reference_style.py`).
  - [ ] Python unit tests for: speed timeline duration & `atempo` chaining (`0.25x`, `4.0x`), zoom filter expressions, ASS vector drawing generation (`{\p1}...{\p0}`), SFX library metadata + FFmpeg `adelay`/`amix` graph, legacy `ProjectState` JSON load compatibility, and AI prompt routing (`"Speed up this part"`, `"Zoom in when he says X"`, `"Edit like <url>"`).
  - [x] Frontend `npm run build` (`tsc && vite build`) exits `0` with zero TypeScript errors.
  - [ ] End-to-End manual test: upload custom `.mp3` SFX -> draw arrow + punch-in zoom + 1.5x speed segment -> preview in player -> ask AI `"Zoom in when he says ..."` + test `"Edit Like This"` URL -> Export MP4 and confirm video zooms, speed, vector drawings, and mixed MP3 SFX all render cleanly.

---

## 5. Notes & Recommended Build Order
- **Custom MP3s:** Whenever you're ready to drop your MP3 files in, you'll be able to upload them directly in the new **SFX Library** UI (or copy them straight into `workspace_data/sfx/`).
- **Recommended Implementation Order:** Phase 1 (Schemas/Math) → Phase 2 (Speed) → Phase 3 (Zooms) → Phase 4 (Drawings) → Phase 5 (Custom MP3 SFX Library) → Phase 6 ("Edit Like This") → Phase 7 (AI Tool Integration & Full Verification).
