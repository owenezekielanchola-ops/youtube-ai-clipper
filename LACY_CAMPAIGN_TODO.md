# Lacy Campaign — Feature Specification & Implementation TODO

> **Purpose:** Source-of-truth specification and step-by-step TODO list for implementing the **"Lacy Campaign"** editing preset, automatic prompt rule injection, on-screen `"LACY"` overlay burn-in, and post-export manual upload guide without inventing unverified files or breaking existing behavior.

---

## 1. Overview & Goal

### User Workflow
1. Paste a YouTube video link (just like before).
2. Click/toggle the **"Lacy Campaign"** button **ON**.
3. Type a prompt describing which part of the video to cut and download (e.g., *"Cut the funniest 45s gaming reaction"*).
4. **Automatic Editing Rule Injection:** Any campaign rules that apply to editing are automatically added behind the scenes to both YouTube ingest and follow-up AI editor prompts, without making the user re-type them.
5. **On-Screen Preview & Burn-In:** `"LACY"` is shown in the preview player and burned into the exported `.mp4` (in addition to engaging subtitles).
6. **Manual Upload Instructions After Render:** Once the finished `.mp4` is exported and ready to download in the Export modal, the UI displays a ready-to-copy post caption (containing `"LACY"` and `"#LACY"`) alongside the full **Required Rules** and **What You MUST NOT Do (Red X's)** upload checklist.

### Design Principle
- Store campaign definitions in a single centralized registry (`frontend/src/config/campaigns.ts`) so future campaigns can reuse the same UI toggle, prompt enrichment, overlay burn-in, and upload guide components without duplicating logic.

---

## 2. Exact Lacy Campaign Rules (Source of Truth)

### A. Editing Rules (Auto-Injected When "Lacy Campaign" Is Selected)
- Make the video captions engaging subtitles of what Lacy is saying and doing.
- Include Lacy’s name (`"LACY"`) as an on-screen text overlay.
- Lacy must actually be in the edit itself—do not make Lacy a "cameo-only" intro.
- No "hate farming"—do not push fake or negative narratives just to stir up drama or hate.

### B. Manual Upload Instructions (Shown After Export Is Ready to Download)
- Include a **ready-to-copy post caption** containing `"LACY"` and `"#LACY"`.

#### Required Rules
- **Match Your Account’s Niche:** Your account’s normal audience shouldn’t clash completely with gaming/creator content (for example, posting Lacy clips on a bird-watching page risks getting rejected).
- **Mention "LACY":** Include Lacy’s name in your caption or on-screen text overlay.
- **Use the Hashtag:** Put `#LACY` in your caption.
- **Qualify for Revenue Share:** Only approved videos count, and you must hit the total view requirement to get a cut of the revenue share.
- **Read the Pool Rules:** Check the campaign's pool rules to understand how pool campaigns and payouts work.

#### What You MUST NOT Do (Red X's)
- ❌ **Don’t Make Lacy a "Cameo-Only" Intro:** If you make an edit, Lacy must actually be in the edit itself—videos that only show him in the intro will be rejected.
- ❌ **No "Hate Farming":** Don’t push fake or negative narratives just to stir up drama/hate. That can get your video rejected—or even get you banned.
- ❌ **No Fake Views or Engagement:** Don’t use bots, reposts, collab posts, or any other tricks to artificially boost views or engagement.

---

## 3. Verified Codebase Map (Exact Existing Touchpoints)

| Area | File | Verified Symbols / Locations |
| :--- | :--- | :--- |
| Frontend Types | `frontend/src/types/editor.ts` | `WorkspaceState`, `ProjectState`, `CaptionStyle` |
| Frontend Store | `frontend/src/store/useEditorStore.ts` | `EditorStoreState`, `createEmptyProject`, `ingestYoutubeUrl`, `applyAiToolCalls` |
| Top Bar & Export Modal | `frontend/src/components/TopNavbar.tsx` | Ingest form (`ingestPrompt`, lines 252–286); Export modal & `exportedFiles` list (lines 436–565) |
| AI Chat Panel | `frontend/src/components/RightAiAndStyles.tsx` | `submitAiPrompt` & AI prompt form (lines 392–434) |
| Live Video Preview | `frontend/src/components/CenterVideoPlayer.tsx` | Preview viewport, aspect-ratio badge & CSS caption overlay (lines 279–321) |
| Backend Schemas | `backend/schemas.py` | `WorkspaceState`, `ProjectState`, `IngestRequest`, `AiEditRequest` |
| Backend API | `backend/main.py` | `/api/ingest` prompt-first flow (lines 252–380) & `/api/ai-edit` |
| AI Orchestrator | `backend/services/ai_orchestrator.py` | `_classify_prompt_intent_with_ai`, `_run_gemini_viral_pipeline`, `_run_groq_fast_editor`, `process_ai_edit` |
| ASS Subtitles | `backend/services/ass_subtitles.py` | `generate_ass_subtitles` (ASS styles + `Dialogue` events) |
| FFmpeg Renderer | `backend/services/ffmpeg_render.py` | `_render_sync` (`has_captions` check at line 128, `_build_vf_filter`, individual & combined render paths) |

---

## 4. Implementation Checklist

### Phase 1 — Campaign Config, Schemas & State
- [x] Create `frontend/src/config/campaigns.ts` defining a `CampaignConfig` interface and `LACY_CAMPAIGN` entry:
  - `id`: `'lacy_campaign'`
  - `label`: `'Lacy Campaign'`
  - `overlayText`: `'LACY'` (`overlayPosition`: `'bottom_left'`)
  - `editingRules`: the 4 auto-injected editing rules verbatim
  - `buildDefaultPostCaption`: starter copyable social caption helper containing `"LACY"` and `"#LACY"`
  - `requiredRules`: the 5 Required Rules
  - `prohibitedRules`: the 3 `❌` What You MUST NOT Do rules
- [x] Update `frontend/src/types/editor.ts` and `backend/schemas.py` (with safe defaults so older saved `.json` projects remain compatible):
  - Add `activeCampaignId?: string | null` (`Optional[str] = None`) and `overlayText?: string | null` (`Optional[str] = None`) to `WorkspaceState`.
  - Add `campaign_id: Optional[str] = None` and `campaign_rules: List[str] = Field(default_factory=list)` (plus `overlay_text: Optional[str] = None` on `IngestRequest`) to `IngestRequest` and `AiEditRequest` in `backend/schemas.py`.
- [x] Update `frontend/src/store/useEditorStore.ts`:
  - Expose `setActiveCampaign(campaignId: string | null)` (sets `project.workspaceState.activeCampaignId` and syncs `project.workspaceState.overlayText` to `'LACY'` or `null`).
  - Expose `setOverlayText(text: string | null)` so `"LACY"` can be inspected or adjusted manually.

### Phase 2 — "Lacy Campaign" Toggle Button & Automatic Prompt Enrichment
- [x] Add a **"Lacy Campaign"** toggle button in `frontend/src/components/TopNavbar.tsx` next to the URL / `"Only download part..."` prompt bar, showing active visual styling and a concise hover tooltip summarizing the auto-applied campaign rules.
- [x] Show a **"Lacy Campaign"** active banner strip in `frontend/src/components/RightAiAndStyles.tsx` above the AI chat input bar when active so users know follow-up edits still enforce campaign rules.
- [x] Send `campaign_id` and `campaign_rules` from `useEditorStore.ts` (`ingestYoutubeUrl`) to `/api/ingest` and from `RightAiAndStyles.tsx` (`submitAiPrompt`) to `/api/ai-edit` (keep the user's visible chat bubble clean with a subtle campaign tag).
- [x] Update `backend/main.py` and `backend/services/ai_orchestrator.py`:
  - **Routing Pitfall Guard:** Always call `_classify_prompt_intent_with_ai` on the **raw user prompt only** (NOT the appended campaign rules block) so words like `"captions"`, `"intro"`, or `"edit"` inside the rules do not trigger wrong `timeline_edit` vs `viral_discovery` keyword routing.
  - Inject the active campaign's `campaign_rules` instruction block into both `_run_gemini_viral_pipeline` and `_run_groq_fast_editor` context/system instructions so AI enforces engaging subtitles, `"LACY"` on-screen overlay, Lacy actually featured in the edit (not cameo-only intro), and zero hate farming.
  - In `/api/ingest` (`backend/main.py`), preserve `activeCampaignId` and `overlayText` (`"LACY"` when `campaign_id == "lacy_campaign"`) on the returned `ProjectState`, and ensure captions are generated/enabled cleanly.

### Phase 3 — Live Preview & FFmpeg Burn-In for `"LACY"` On-Screen Overlay
- [x] In `frontend/src/components/CenterVideoPlayer.tsx`:
  - Render `project.workspaceState.overlayText` (e.g., `"LACY"`) inside the preview viewport at **Bottom-Left** (`bottom-3 left-3`, bold white text with dark outline) so preview matches the exported MP4.
- [x] In `backend/services/ass_subtitles.py` (`generate_ass_subtitles`):
  - When `project_state.workspaceState.overlayText` is non-empty, declare a dedicated `Style: CampaignOverlay,...` (bold, clean outline, `Alignment=1` **bottom-left**) and emit a Layer `1` `Dialogue` line spanning the full clip/timeline duration (`0:00:00.00` to `max(c.timelineStart + c.duration)`), escaped safely via `_escape_ass_text`.
- [x] In `backend/services/ffmpeg_render.py` (`_render_sync`):
  - Generate and burn the `.ass` file whenever `has_captions or bool(project_state.workspaceState.overlayText)` so `"LACY"` burns into both **Combined Timeline MP4** and **Individual Clips MP4** even when the user clears captions.

### Phase 4 — Post-Export Manual Upload Instructions & Copyable Caption
- [x] Create `frontend/src/components/CampaignUploadGuide.tsx` and render it inside `TopNavbar.tsx`'s Export Modal once `exportedFiles.length > 0` and `project.workspaceState.activeCampaignId` is active (give the Export Modal container `max-w-2xl max-h-[90vh] overflow-y-auto`).
- [x] Component contents:
  - **Ready-to-Copy Post Caption:** Editable `<textarea>` pre-populated with `"LACY"` and `"#LACY"`, plus a **Copy Caption** button with `"Copied!"` confirmation feedback.
  - **Required Rules Card:** Renders all 5 Required Rules clearly.
  - **What You MUST NOT Do (Red X's) Card:** Red-highlighted callout listing all 3 `❌` rules.
  - **Self-Check Checkboxes (UI convenience):** Lets the user tick off niche match, `"LACY"` + `#LACY` caption, Lacy in the actual edit, no hate farming, and no fake views before posting.

### Phase 5 — Verification Checklist
- [ ] **Backend compile & unit checks:**
  - `py_compile` passes on `backend/schemas.py`, `backend/main.py`, `backend/services/ai_orchestrator.py`, `backend/services/ass_subtitles.py`, and `backend/services/ffmpeg_render.py`.
  - `_classify_prompt_intent_with_ai` produces the exact same route whether campaign rules are active or not.
  - `generate_ass_subtitles` includes `Style: CampaignOverlay` and a `LACY` `Dialogue` event when `overlayText="LACY"` (both with and without caption items).
  - Older saved project JSONs without `activeCampaignId` / `overlayText` still validate cleanly under `ProjectState`.
- [ ] **Frontend build check:**
  - `npm run build` (`tsc && vite build`) in `frontend/` exits `0` with zero TypeScript errors.
- [ ] **End-to-End Manual Smoke Test:**
  - Toggle **"Lacy Campaign"** ON -> enter YouTube URL + clip prompt -> verify `"LACY"` overlay appears in preview -> Export MP4 -> confirm `"LACY"` text overlay is visible on the exported MP4 and the Export Modal displays the copyable `"LACY" / "#LACY"` caption + Required Rules + Red X's checklist.

---

## 5. Confirmed Design Decisions
1. **`"LACY"` On-Screen Overlay Position:** Confirmed **Bottom-Left** (`bottom-3 left-3` in `CenterVideoPlayer.tsx`, `Alignment=1` with bold outline in `ass_subtitles.py`) so it stays distinct from centered subtitles (`MarginV = 8%`) and avoids top badges.
2. **Starter Copyable Caption:** Pre-filled cleanly via `buildDefaultPostCaption` (`<Title> — featuring LACY\n\n#LACY #Lacy #clips`) and editable before copying.
