import { create } from 'zustand';
import { temporal } from 'zundo';
import { buildCampaignAssistantNotice, getCampaignById } from '../config/campaigns';
import type {
  AiActionLogItem,
  AiChatMessage,
  AspectRatio,
  AudioEditItem,
  CachedTranscript,
  CaptionItem,
  CaptionPosition,
  CaptionPreset,
  ChangeCaptionStyleArgs,
  ClipItem,
  DrawingItem,
  DrawingPoint,
  DrawingTool,
  HighlightClipInput,
  ModelStatus,
  OverlayStyle,
  ProjectState,
  SfxAsset,
  SfxClipItem,
  TimeRange,
  ToolCall,
  VideoCropRect,
  ZoomEasing,
  ZoomEffectItem,
  ZoomKind,
} from '../types/editor';

export const DEFAULT_OVERLAY_STYLE: OverlayStyle = {
  position: 'bottom-left',
  textColor: '#FFFFFF',
  backgroundColor: '#000000',
  showBackground: true,
  fontSize: 24,
};

export const API_BASE =
  import.meta.env.VITE_API_BASE ??
  (import.meta.env.DEV ? '' : 'http://localhost:8000');

let activeIngestSeq = 0;

const makeId = (prefix: string) =>
  `${prefix}_${Math.random().toString(36).slice(2, 10)}`;

function recalculateTimeline(clips: ClipItem[]): ClipItem[] {
  let cursor = 0;
  return clips.map((clip) => {
    const start = Math.max(0, Number(clip.sourceStart.toFixed(3)));
    const end = Math.max(start + 0.1, Number(clip.sourceEnd.toFixed(3)));
    const speed = Math.max(0.25, Math.min(4.0, clip.speed ?? 1.0));
    const duration = Number(((end - start) / speed).toFixed(3));
    const updated: ClipItem = {
      ...clip,
      sourceStart: start,
      sourceEnd: end,
      speed,
      timelineStart: Number(cursor.toFixed(3)),
      duration,
    };
    cursor += duration;
    return updated;
  });
}

function buildCaptionsLocally(
  transcript: CachedTranscript | null,
  startTime?: number,
  endTime?: number,
  maxWords = 5,
): CaptionItem[] {
  if (!transcript || !transcript.words || transcript.words.length === 0) {
    return [];
  }

  const filtered = transcript.words.filter((w) => {
    if (startTime !== undefined && w.end < startTime) return false;
    if (endTime !== undefined && w.start > endTime) return false;
    return true;
  });

  if (filtered.length === 0) return [];

  const captions: CaptionItem[] = [];
  let chunk: typeof filtered = [];

  for (let i = 0; i < filtered.length; i++) {
    const w = filtered[i];
    chunk.push(w);
    const tok = w.word.trim();
    const endsSentence = tok.endsWith('.') || tok.endsWith('!') || tok.endsWith('?');
    const hasPause =
      i + 1 < filtered.length && filtered[i + 1].start - w.end > 0.45;

    if (chunk.length >= maxWords || endsSentence || hasPause) {
      captions.push({
        id: makeId('cap'),
        start: Number(chunk[0].start.toFixed(3)),
        end: Number(chunk[chunk.length - 1].end.toFixed(3)),
        text: chunk.map((item) => item.word.trim()).join(' '),
        isManuallyEdited: false,
        highlightedWords: [],
        words: chunk.map((item) => ({
          word: item.word.trim(),
          start: Number(item.start.toFixed(3)),
          end: Number(item.end.toFixed(3)),
        })),
      });
      chunk = [];
    }
  }

  if (chunk.length > 0) {
    captions.push({
      id: makeId('cap'),
      start: Number(chunk[0].start.toFixed(3)),
      end: Number(chunk[chunk.length - 1].end.toFixed(3)),
      text: chunk.map((item) => item.word.trim()).join(' '),
      isManuallyEdited: false,
      highlightedWords: [],
      words: chunk.map((item) => ({
        word: item.word.trim(),
        start: Number(item.start.toFixed(3)),
        end: Number(item.end.toFixed(3)),
      })),
    });
  }

  return captions;
}

function pruneOrphanedAutoCaptions(
  captions: CaptionItem[],
  clips: ClipItem[],
): CaptionItem[] {
  return captions.filter(
    (c) =>
      c.isManuallyEdited ||
      clips.some(
        (clip) =>
          Math.min(c.end, clip.sourceEnd) - Math.max(c.start, clip.sourceStart) >
          0.05,
      ),
  );
}

function projectTlSpanToNewClips(
  tlStart: number,
  tlEnd: number,
  oldClips: ClipItem[],
  newClips: ClipItem[],
): { start: number; end: number } | null {
  const fragments: Array<{ start: number; end: number }> = [];
  for (const oc of oldClips) {
    const ovS = Math.max(tlStart, oc.timelineStart);
    const ovE = Math.min(tlEnd, oc.timelineStart + oc.duration);
    if (ovE <= ovS + 0.02) continue;
    const oldSpd = Math.max(0.25, Math.min(4.0, oc.speed ?? 1));
    const srcS = oc.sourceStart + (ovS - oc.timelineStart) * oldSpd;
    const srcE = oc.sourceStart + (ovE - oc.timelineStart) * oldSpd;
    for (const nc of newClips) {
      const nOvS = Math.max(srcS, nc.sourceStart);
      const nOvE = Math.min(srcE, nc.sourceEnd);
      if (nOvE <= nOvS + 0.02) continue;
      const newSpd = Math.max(0.25, Math.min(4.0, nc.speed ?? 1));
      fragments.push({
        start: nc.timelineStart + (nOvS - nc.sourceStart) / newSpd,
        end: nc.timelineStart + (nOvE - nc.sourceStart) / newSpd,
      });
    }
  }
  if (fragments.length === 0) return null;
  const minS = Math.min(...fragments.map((f) => f.start));
  const maxE = Math.max(...fragments.map((f) => f.end));
  return { start: Number(minS.toFixed(3)), end: Number(maxE.toFixed(3)) };
}

function remapTimelineEffects(
  oldClips: ClipItem[],
  newClips: ClipItem[],
  tracks: Pick<ProjectState['tracks'], 'zooms' | 'drawings' | 'sfx' | 'audioEdits'>,
  crop: VideoCropRect | null | undefined,
): {
  zooms: ZoomEffectItem[];
  drawings: DrawingItem[];
  sfx: SfxClipItem[];
  audioEdits: AudioEditItem[];
  crop: VideoCropRect | null;
} {
  const totalNewDur = newClips.reduce((acc, c) => acc + c.duration, 0);

  let zoomsChanged = false;
  const nextZooms: ZoomEffectItem[] = [];
  for (const z of tracks.zooms ?? []) {
    const mapped = projectTlSpanToNewClips(z.start, z.end, oldClips, newClips);
    if (!mapped || mapped.end - mapped.start < 0.2) {
      zoomsChanged = true;
      continue;
    }
    if (mapped.start !== z.start || mapped.end !== z.end) {
      zoomsChanged = true;
      nextZooms.push({ ...z, start: mapped.start, end: mapped.end });
    } else {
      nextZooms.push(z);
    }
  }

  let drawingsChanged = false;
  const nextDrawings: DrawingItem[] = [];
  for (const d of tracks.drawings ?? []) {
    const mapped = projectTlSpanToNewClips(d.start, d.end, oldClips, newClips);
    if (!mapped || mapped.end - mapped.start < 0.2) {
      drawingsChanged = true;
      continue;
    }
    if (mapped.start !== d.start || mapped.end !== d.end) {
      drawingsChanged = true;
      nextDrawings.push({ ...d, start: mapped.start, end: mapped.end });
    } else {
      nextDrawings.push(d);
    }
  }

  let sfxChanged = false;
  const nextSfx: SfxClipItem[] = [];
  for (const sc of tracks.sfx ?? []) {
    const mapped = projectTlSpanToNewClips(
      sc.start,
      sc.start + sc.duration,
      oldClips,
      newClips,
    );
    if (!mapped || mapped.start >= totalNewDur - 0.1) {
      sfxChanged = true;
      continue;
    }
    const clampedDur = Number(
      Math.max(0.15, Math.min(sc.duration, totalNewDur - mapped.start)).toFixed(3),
    );
    if (mapped.start !== sc.start || clampedDur !== sc.duration) {
      sfxChanged = true;
      nextSfx.push({ ...sc, start: mapped.start, duration: clampedDur });
    } else {
      nextSfx.push(sc);
    }
  }

  let nextCrop: VideoCropRect | null = crop ?? null;
  if (crop && crop.start != null && crop.end != null) {
    const mappedCrop = projectTlSpanToNewClips(crop.start, crop.end, oldClips, newClips);
    if (!mappedCrop || mappedCrop.end - mappedCrop.start < 0.2) {
      nextCrop = null;
    } else if (mappedCrop.start !== crop.start || mappedCrop.end !== crop.end) {
      nextCrop = {
        ...crop,
        start: Number(mappedCrop.start.toFixed(2)),
        end: Number(mappedCrop.end.toFixed(2)),
      };
    }
  }

  let audioEditsChanged = false;
  const nextAudioEdits: AudioEditItem[] = [];
  for (const ae of tracks.audioEdits ?? []) {
    const mapped = projectTlSpanToNewClips(ae.start, ae.end, oldClips, newClips);
    if (!mapped || mapped.end - mapped.start < 0.15) {
      audioEditsChanged = true;
      continue;
    }
    if (mapped.start !== ae.start || mapped.end !== ae.end) {
      audioEditsChanged = true;
      nextAudioEdits.push({ ...ae, start: mapped.start, end: mapped.end });
    } else {
      nextAudioEdits.push(ae);
    }
  }

  return {
    zooms: zoomsChanged ? nextZooms : (tracks.zooms ?? []),
    drawings: drawingsChanged ? nextDrawings : (tracks.drawings ?? []),
    sfx: sfxChanged ? nextSfx : (tracks.sfx ?? []),
    audioEdits: audioEditsChanged ? nextAudioEdits : (tracks.audioEdits ?? []),
    crop: nextCrop,
  };
}

function overlapsProtectedCaption(
  candidate: CaptionItem,
  protectedCaptions: CaptionItem[],
): boolean {
  return protectedCaptions.some((pc) => {
    const overlap =
      Math.min(candidate.end, pc.end) - Math.max(candidate.start, pc.start);
    const dur = Math.max(0.1, candidate.end - candidate.start);
    return overlap > dur * 0.35;
  });
}

function createEmptyProject(): ProjectState {
  return {
    projectId: 'proj_empty',
    projectName: 'New ClipForge Project',
    youtubeUrl: '',
    sourceVideo: {
      videoUrl: '',
      title: 'Paste a YouTube URL above or Load Sample Video',
      thumbnail: '',
      originalDuration: 0,
      heatmap: [],
      audioPeaks: [],
    },
    workspaceState: {
      playheadPosition: 0,
      selectedClipIds: [],
      selectedTimeRange: null,
      targetAspectRatio: '16:9',
      activeCampaignId: null,
      overlayText: null,
      overlayStyle: { ...DEFAULT_OVERLAY_STYLE },
      crop: null,
    },
    tracks: {
      clips: [],
      captions: [],
      zooms: [],
      drawings: [],
      sfx: [],
      audioEdits: [],
    },
    captionStyle: {
      preset: 'karaoke',
      fontFamily: 'Inter',
      fontSize: 28,
      textColor: '#FFFFFF',
      highlightColor: '#FACC15',
      position: 'bottom',
      isBold: true,
    },
  };
}

function mapModelStatusResponse(data: Record<string, unknown>): ModelStatus {
  const toStringArray = (val: unknown): string[] =>
    Array.isArray(val) ? val.filter((item): item is string => typeof item === 'string') : [];

  const rawWinner =
    data.last_race_winner && typeof data.last_race_winner === 'object'
      ? (data.last_race_winner as Record<string, unknown>)
      : null;

  return {
    primaryToolModel: typeof data.primary_tool_model === 'string' ? data.primary_tool_model : null,
    backupToolModels: toStringArray(data.backup_tool_models),
    primaryRouterModel:
      typeof data.primary_router_model === 'string' ? data.primary_router_model : null,
    backupRouterModels: toStringArray(data.backup_router_models),
    primarySttModel: typeof data.primary_stt_model === 'string' ? data.primary_stt_model : null,
    backupSttModels: toStringArray(data.backup_stt_models),
    primaryViralModel: typeof data.primary_viral_model === 'string' ? data.primary_viral_model : null,
    backupViralModels: toStringArray(data.backup_viral_models),
    primaryNvidiaFastModel:
      typeof data.primary_nvidia_fast_model === 'string' ? data.primary_nvidia_fast_model : null,
    backupNvidiaFastModels: toStringArray(data.backup_nvidia_fast_models),
    primaryNvidiaVisionModel:
      typeof data.primary_nvidia_vision_model === 'string'
        ? data.primary_nvidia_vision_model
        : null,
    backupNvidiaVisionModels: toStringArray(data.backup_nvidia_vision_models),
    availableGroqModels: toStringArray(data.available_groq_models),
    availableGeminiModels: toStringArray(data.available_gemini_models),
    availableNvidiaModels: toStringArray(data.available_nvidia_models),
    lastRefreshedIso: typeof data.last_refreshed_iso === 'string' ? data.last_refreshed_iso : null,
    nextRefreshInSeconds:
      typeof data.next_refresh_in_seconds === 'number' ? data.next_refresh_in_seconds : 86400,
    groqConfigured: Boolean(data.groq_configured),
    geminiConfigured: Boolean(data.gemini_configured),
    nvidiaConfigured: Boolean(data.nvidia_configured),
    lastRaceWinner: rawWinner
      ? {
          provider: typeof rawWinner.provider === 'string' ? rawWinner.provider : undefined,
          model: typeof rawWinner.model === 'string' ? rawWinner.model : undefined,
          latency_ms:
            typeof rawWinner.latency_ms === 'number' ? rawWinner.latency_ms : undefined,
          task: typeof rawWinner.task === 'string' ? rawWinner.task : undefined,
        }
      : null,
  };
}

export interface EditorStoreState {
  project: ProjectState;
  transcript: CachedTranscript | null;
  isPlaying: boolean;
  isIngesting: boolean;
  isAiThinking: boolean;
  timelineZoom: number;
  aiMessages: AiChatMessage[];
  aiActionLog: AiActionLogItem[];
  statusBanner: string | null;
  modelStatus: ModelStatus | null;
  isRefreshingModels: boolean;
  pendingYoutubeUrl: string;
  ingestPrompt: string;
  activeDrawTool: DrawingTool | null;
  drawColor: string;
  drawStrokeWidth: number;
  isCropMode: boolean;
  selectedZoomId: string | null;
  selectedDrawingId: string | null;
  selectedSfxId: string | null;
  selectedAudioEditId: string | null;
  sfxLibrary: SfxAsset[];
  isLoadingSfx: boolean;

  // Playback & UI setters (excluded from undo history via zundo partialize)
  setPlayheadPosition: (pos: number) => void;
  setIsPlaying: (playing: boolean) => void;
  setSelectedClipIds: (ids: string[]) => void;
  setTimelineZoom: (zoom: number) => void;
  setProjectName: (name: string) => void;
  setStatusBanner: (msg: string | null) => void;
  setPendingYoutubeUrl: (url: string) => void;
  setIngestPrompt: (prompt: string) => void;
  setActiveDrawTool: (tool: DrawingTool | null) => void;
  setDrawColor: (color: string) => void;
  setDrawStrokeWidth: (width: number) => void;
  setIsCropMode: (val: boolean) => void;
  setSelectedZoomId: (id: string | null) => void;
  setSelectedDrawingId: (id: string | null) => void;
  setSelectedSfxId: (id: string | null) => void;
  setSelectedAudioEditId: (id: string | null) => void;
  setActiveCampaign: (campaignId: string | null) => void;
  setOverlayText: (text: string | null) => void;
  updateOverlayStyle: (patch: Partial<OverlayStyle>) => void;
  loadProjectState: (project: ProjectState, transcript?: CachedTranscript | null) => void;
  setIsIngesting: (val: boolean) => void;
  setIsAiThinking: (val: boolean) => void;
  appendAiMessage: (msg: AiChatMessage) => void;
  updateAiMessage: (id: string, patch: Partial<AiChatMessage>) => void;

  // =========================================================================
  // THE 13 SHARED TOOLS (Used identically by Manual UI & AI Function Calls)
  // =========================================================================
  isolate_highlight_clips: (clips: HighlightClipInput[]) => void;
  trim_clip: (clip_id: string, new_source_start: number, new_source_end: number) => void;
  split_clip: (clip_id: string, split_timestamp: number) => void;
  delete_clips: (clip_ids: string[]) => void;
  merge_clips: (clip_id_1: string, clip_id_2: string) => void;
  reorder_clips: (clip_id: string, new_index: number) => void;
  remove_time_ranges: (ranges: TimeRange[]) => void;
  generate_auto_captions: (
    start_time?: number,
    end_time?: number,
    providedCaptions?: CaptionItem[],
  ) => void;
  add_caption: (
    start: number,
    end: number,
    text: string,
    kind?: 'subtitle' | 'intro_hook',
  ) => void;
  delete_caption: (caption_id: string) => void;
  combine_captions: (caption_id_1: string, caption_id_2: string) => void;
  edit_caption: (
    caption_id: string,
    new_text: string,
    highlighted_words?: string[],
    markManual?: boolean,
  ) => void;
  adjust_caption_timing: (
    caption_id: string,
    new_start: number,
    new_end: number,
    markManual?: boolean,
  ) => void;
  change_caption_style: (args: ChangeCaptionStyleArgs) => void;
  change_video_format: (aspect_ratio: AspectRatio) => void;
  set_clip_speed: (clip_id: string, speed: number) => void;
  set_range_speed: (start_time: number, end_time: number, speed: number) => void;
  add_zoom_effect: (item: Omit<ZoomEffectItem, 'id'>) => string;
  update_zoom_effect: (
    id: string,
    patch: Partial<Omit<ZoomEffectItem, 'id'>>,
  ) => void;
  delete_zoom_effect: (id: string) => void;
  add_drawing: (item: Omit<DrawingItem, 'id'>) => string;
  update_drawing: (id: string, patch: Partial<Omit<DrawingItem, 'id'>>) => void;
  delete_drawing: (id: string) => void;
  add_sfx_clip: (item: Omit<SfxClipItem, 'id'>) => string;
  update_sfx_clip: (id: string, patch: Partial<Omit<SfxClipItem, 'id'>>) => void;
  delete_sfx_clip: (id: string) => void;
  add_audio_edit: (item: Omit<AudioEditItem, 'id'>) => string;
  update_audio_edit: (
    id: string,
    patch: Partial<Omit<AudioEditItem, 'id'>>,
  ) => void;
  delete_audio_edit: (id: string) => void;
  split_audio_at: (timestamp: number) => void;
  set_video_crop: (crop: VideoCropRect | null) => void;
  reset_video_crop: () => void;

  // AI Tool Dispatcher
  applyAiToolCalls: (
    toolCalls: ToolCall[],
    autoCaptions?: CaptionItem[] | null,
  ) => AiActionLogItem[];

  // Convenience async wrappers wired to backend
  ingestYoutubeUrl: (youtubeUrl: string, initialPrompt?: string) => Promise<void>;
  runAutoCaptions: (startTime?: number, endTime?: number) => Promise<void>;
  removeDetectedPauses: (minGap?: number) => Promise<void>;
  fetchModelStatus: () => Promise<void>;
  forceRefreshModels: () => Promise<void>;
  fetchSfxLibrary: () => Promise<void>;
  uploadSfxFiles: (files: FileList | File[], tags?: string[]) => Promise<SfxAsset[]>;
  updateSfxAssetMeta: (
    sfxId: string,
    patch: { name?: string; tags?: string[]; description?: string },
  ) => Promise<void>;
  deleteSfxAssetFromLibrary: (sfxId: string) => Promise<void>;
}

export const useEditorStore = create<EditorStoreState>()(
  temporal(
    (set, get) => ({
      project: createEmptyProject(),
      transcript: null,
      isPlaying: false,
      isIngesting: false,
      isAiThinking: false,
      timelineZoom: 14, // pixels per second
      aiMessages: [
        {
          id: 'welcome_msg',
          role: 'assistant',
          content:
            'Paste a YouTube link or load the sample clip, then ask me to find the funniest/viral moments, cut pauses, or style karaoke captions. Every AI edit uses the exact same timeline tools as your manual controls and supports Ctrl+Z Undo.',
        },
      ],
      aiActionLog: [],
      statusBanner: null,
      modelStatus: null,
      isRefreshingModels: false,
      pendingYoutubeUrl: '',
      ingestPrompt: '',
      activeDrawTool: null,
      drawColor: '#EF4444',
      drawStrokeWidth: 6,
      isCropMode: false,
      selectedZoomId: null,
      selectedDrawingId: null,
      selectedSfxId: null,
      selectedAudioEditId: null,
      sfxLibrary: [],
      isLoadingSfx: false,

      setPendingYoutubeUrl: (url) => set({ pendingYoutubeUrl: url }),
      setIngestPrompt: (prompt) => set({ ingestPrompt: prompt }),
      setActiveDrawTool: (tool) =>
        set((state) => ({
          activeDrawTool: tool,
          ...(tool ? { isCropMode: false, isPlaying: false } : {}),
          ...(tool ? {} : { isPlaying: state.isPlaying }),
        })),
      setDrawColor: (color) => set({ drawColor: color }),
      setDrawStrokeWidth: (width) =>
        set({ drawStrokeWidth: Math.max(2, Math.min(24, width)) }),
      setIsCropMode: (val) =>
        set((state) => ({
          isCropMode: val,
          ...(val ? { activeDrawTool: null, isPlaying: false } : { isPlaying: state.isPlaying }),
        })),
      setSelectedZoomId: (id) =>
        set((state) => ({
          selectedZoomId: id,
          ...(id
            ? {
                selectedDrawingId: null,
                selectedSfxId: null,
                selectedAudioEditId: null,
                project: {
                  ...state.project,
                  workspaceState: {
                    ...state.project.workspaceState,
                    selectedClipIds: [],
                  },
                },
              }
            : {}),
        })),
      setSelectedDrawingId: (id) =>
        set((state) => ({
          selectedDrawingId: id,
          ...(id
            ? {
                selectedZoomId: null,
                selectedSfxId: null,
                selectedAudioEditId: null,
                project: {
                  ...state.project,
                  workspaceState: {
                    ...state.project.workspaceState,
                    selectedClipIds: [],
                  },
                },
              }
            : {}),
        })),
      setSelectedSfxId: (id) =>
        set((state) => ({
          selectedSfxId: id,
          ...(id
            ? {
                selectedZoomId: null,
                selectedDrawingId: null,
                selectedAudioEditId: null,
                project: {
                  ...state.project,
                  workspaceState: {
                    ...state.project.workspaceState,
                    selectedClipIds: [],
                  },
                },
              }
            : {}),
        })),
      setSelectedAudioEditId: (id) =>
        set((state) => ({
          selectedAudioEditId: id,
          ...(id
            ? {
                selectedZoomId: null,
                selectedDrawingId: null,
                selectedSfxId: null,
                project: {
                  ...state.project,
                  workspaceState: {
                    ...state.project.workspaceState,
                    selectedClipIds: [],
                  },
                },
              }
            : {}),
        })),

      fetchModelStatus: async () => {
        try {
          const res = await fetch(`${API_BASE}/api/models/status`);
          if (!res.ok) return;
          const data = (await res.json()) as Record<string, unknown>;
          set({ modelStatus: mapModelStatusResponse(data) });
        } catch {
          // Ignore transient offline status checks during startup
        }
      },

      forceRefreshModels: async () => {
        set({
          isRefreshingModels: true,
          statusBanner: 'Reloading API keys & scanning available Groq/Gemini/NVIDIA models...',
        });
        try {
          const res = await fetch(`${API_BASE}/api/models/refresh`, {
            method: 'POST',
          });
          if (!res.ok) {
            const errText = await res.text();
            throw new Error(errText || `HTTP ${res.status}`);
          }
          const data = (await res.json()) as Record<string, unknown>;
          const mapped = mapModelStatusResponse(data);
          const groqCount = mapped.availableGroqModels.length;
          const geminiCount = mapped.availableGeminiModels.length;
          const nvidiaCount = mapped.availableNvidiaModels.length;
          set({
            modelStatus: mapped,
            isRefreshingModels: false,
            statusBanner: `AI models refreshed (${groqCount} Groq, ${geminiCount} Gemini, ${nvidiaCount} NVIDIA live; primary: ${mapped.primaryToolModel ?? mapped.primaryNvidiaFastModel ?? 'default'}).`,
          });
        } catch (err) {
          set({
            isRefreshingModels: false,
            statusBanner: `Failed to refresh AI models: ${
              err instanceof Error ? err.message : 'Network error'
            }`,
          });
        }
      },

      setPlayheadPosition: (pos) =>
        set((state) => ({
          project: {
            ...state.project,
            workspaceState: {
              ...state.project.workspaceState,
              playheadPosition: Math.max(0, pos),
            },
          },
        })),

      setIsPlaying: (playing) => set({ isPlaying: playing }),

      setSelectedClipIds: (ids) =>
        set((state) => ({
          ...(ids.length > 0
            ? {
                selectedZoomId: null,
                selectedDrawingId: null,
                selectedSfxId: null,
                selectedAudioEditId: null,
              }
            : {}),
          project: {
            ...state.project,
            workspaceState: {
              ...state.project.workspaceState,
              selectedClipIds: ids,
            },
          },
        })),

      setTimelineZoom: (zoom) =>
        set({ timelineZoom: Math.max(4, Math.min(80, zoom)) }),

      setProjectName: (name) =>
        set((state) => ({
          project: { ...state.project, projectName: name },
        })),

      setStatusBanner: (msg) => set({ statusBanner: msg }),

      setActiveCampaign: (campaignId) =>
        set((state) => {
          const campaign = getCampaignById(campaignId);
          const hasLoadedClips = state.project.tracks.clips.length > 0;
          const nextMessages = [...state.aiMessages];
          if (campaign && hasLoadedClips) {
            const notice = buildCampaignAssistantNotice(campaign, state.project.projectName);
            const alreadyPosted = nextMessages.some(
              (m) => m.role === 'assistant' && m.copyableCaption === notice.copyableCaption,
            );
            if (!alreadyPosted) {
              nextMessages.push({
                id: `a_campaign_${Date.now()}`,
                role: 'assistant',
                content: notice.content,
                campaignLabel: campaign.label,
                copyableCaption: notice.copyableCaption,
              });
            }
          }
          return {
            project: {
              ...state.project,
              workspaceState: {
                ...state.project.workspaceState,
                activeCampaignId: campaign ? campaign.id : null,
                overlayText: campaign ? campaign.overlayText : null,
              },
            },
            aiMessages: nextMessages,
            statusBanner: campaign
              ? `${campaign.label} enabled — editing rules & "${campaign.overlayText}" overlay will auto-apply.`
              : 'Campaign mode turned off.',
          };
        }),

      setOverlayText: (text) =>
        set((state) => ({
          project: {
            ...state.project,
            workspaceState: {
              ...state.project.workspaceState,
              overlayText: text && text.length > 0 ? text : null,
            },
          },
        })),

      updateOverlayStyle: (patch) =>
        set((state) => ({
          project: {
            ...state.project,
            workspaceState: {
              ...state.project.workspaceState,
              overlayStyle: {
                ...(state.project.workspaceState.overlayStyle ?? DEFAULT_OVERLAY_STYLE),
                ...patch,
              },
            },
          },
        })),

      loadProjectState: (project, transcript = null) =>
        set(() => {
          const base = createEmptyProject();
          return {
            project: {
              ...base,
              ...project,
              sourceVideo: {
                ...base.sourceVideo,
                ...(project.sourceVideo ?? {}),
                heatmap: project.sourceVideo?.heatmap ?? [],
                audioPeaks: project.sourceVideo?.audioPeaks ?? [],
              },
              workspaceState: {
                ...base.workspaceState,
                ...(project.workspaceState ?? {}),
                selectedClipIds: project.workspaceState?.selectedClipIds ?? [],
                overlayStyle: {
                  ...DEFAULT_OVERLAY_STYLE,
                  ...(project.workspaceState?.overlayStyle ?? {}),
                },
                crop: project.workspaceState?.crop ?? null,
              },
              captionStyle: {
                ...base.captionStyle,
                ...(project.captionStyle ?? {}),
              },
              tracks: {
                clips: recalculateTimeline(project.tracks?.clips ?? []),
                captions: project.tracks?.captions ?? [],
                zooms: project.tracks?.zooms ?? [],
                drawings: project.tracks?.drawings ?? [],
                sfx: project.tracks?.sfx ?? [],
                audioEdits: project.tracks?.audioEdits ?? [],
              },
            },
            transcript,
            isPlaying: false,
            isCropMode: false,
            activeDrawTool: null,
            selectedZoomId: null,
            selectedDrawingId: null,
            selectedSfxId: null,
            selectedAudioEditId: null,
          };
        }),

      setIsIngesting: (val) => set({ isIngesting: val }),
      setIsAiThinking: (val) => set({ isAiThinking: val }),

      appendAiMessage: (msg) =>
        set((state) => ({ aiMessages: [...state.aiMessages, msg] })),

      updateAiMessage: (id, patch) =>
        set((state) => ({
          aiMessages: state.aiMessages.map((m) =>
            m.id === id ? { ...m, ...patch } : m,
          ),
        })),

      // 1. isolate_highlight_clips
      isolate_highlight_clips: (clips) =>
        set((state) => {
          if (!Array.isArray(clips)) return state;
          const maxDur = state.project.sourceVideo.originalDuration || 99999;
          const rawClips: ClipItem[] = [];
          clips.forEach((c, idx) => {
            const rawS = Number(c?.source_start);
            const rawE = Number(c?.source_end);
            if (!Number.isFinite(rawS) || !Number.isFinite(rawE)) return;
            const s = Math.max(0, rawS);
            const e = Math.min(maxDur, rawE);
            if (e <= s + 0.2) return;
            rawClips.push({
              id: makeId('clip'),
              title: c.title || `Highlight ${idx + 1}`,
              sourceStart: s,
              sourceEnd: e,
              timelineStart: 0,
              duration: e - s,
              viralityScore: c.virality_score,
              reason: c.reason,
            });
          });

          if (rawClips.length === 0) {
            return {
              statusBanner: 'Skipped isolating clips: no valid highlight ranges found within video bounds.',
            };
          }

          const nextClips = recalculateTimeline(rawClips);
          const curCrop = state.project.workspaceState.crop;
          const nextCrop =
            curCrop && curCrop.start != null && curCrop.end != null ? null : (curCrop ?? null);
          return {
            selectedZoomId: null,
            selectedDrawingId: null,
            selectedSfxId: null,
            selectedAudioEditId: null,
            project: {
              ...state.project,
              workspaceState: {
                ...state.project.workspaceState,
                playheadPosition: 0,
                selectedClipIds: [nextClips[0].id],
                crop: nextCrop,
              },
              tracks: {
                ...state.project.tracks,
                clips: nextClips,
                captions: pruneOrphanedAutoCaptions(
                  state.project.tracks.captions,
                  nextClips,
                ),
                zooms: [],
                drawings: [],
                sfx: [],
                audioEdits: [],
              },
            },
          };
        }),

      // 2. trim_clip
      trim_clip: (clip_id, new_source_start, new_source_end) =>
        set((state) => {
          if (!Number.isFinite(new_source_start) || !Number.isFinite(new_source_end)) {
            return state;
          }
          const maxDur = state.project.sourceVideo.originalDuration || 99999;
          const oldClips = state.project.tracks.clips;
          const updated = oldClips.map((clip) => {
            if (clip.id !== clip_id) return clip;
            const s = Math.max(0, Math.min(new_source_start, maxDur - 0.2));
            const e = Math.min(maxDur, Math.max(s + 0.2, new_source_end));
            return { ...clip, sourceStart: s, sourceEnd: e };
          });
          const nextClips = recalculateTimeline(updated);
          const totalDur = nextClips.reduce((acc, c) => acc + c.duration, 0);
          const remapped = remapTimelineEffects(
            oldClips,
            nextClips,
            state.project.tracks,
            state.project.workspaceState.crop,
          );
          return {
            project: {
              ...state.project,
              workspaceState: {
                ...state.project.workspaceState,
                playheadPosition: Math.min(
                  state.project.workspaceState.playheadPosition,
                  totalDur,
                ),
                crop: remapped.crop,
              },
              tracks: {
                ...state.project.tracks,
                clips: nextClips,
                captions: pruneOrphanedAutoCaptions(
                  state.project.tracks.captions,
                  nextClips,
                ),
                zooms: remapped.zooms,
                drawings: remapped.drawings,
                sfx: remapped.sfx,
                audioEdits: remapped.audioEdits,
              },
            },
          };
        }),

      // 3. split_clip
      split_clip: (clip_id, split_timestamp) =>
        set((state) => {
          if (!Number.isFinite(split_timestamp)) return state;
          const nextClips: ClipItem[] = [];
          let newlyCreatedId: string | null = null;

          for (const clip of state.project.tracks.clips) {
            if (clip.id !== clip_id) {
              nextClips.push(clip);
              continue;
            }
            const clipEndTl = clip.timelineStart + clip.duration;
            const clipSpeed = Math.max(0.25, Math.min(4.0, clip.speed ?? 1));
            // Support either timeline timestamp inside clip OR source timestamp fallback
            let offset = split_timestamp - clip.timelineStart;
            if (split_timestamp <= clip.timelineStart || split_timestamp >= clipEndTl) {
              if (split_timestamp > clip.sourceStart && split_timestamp < clip.sourceEnd) {
                offset = (split_timestamp - clip.sourceStart) / clipSpeed;
              }
            }

            if (offset <= 0.15 || offset >= clip.duration - 0.15) {
              nextClips.push(clip);
              continue;
            }

            const splitSource = Number((clip.sourceStart + offset * clipSpeed).toFixed(3));
            if (splitSource <= clip.sourceStart + 0.1 || splitSource >= clip.sourceEnd - 0.1) {
              nextClips.push(clip);
              continue;
            }
            const rightId = makeId('clip');
            newlyCreatedId = rightId;

            nextClips.push({
              ...clip,
              sourceEnd: splitSource,
              speed: clipSpeed,
              title: `${clip.title} (A)`,
            });
            nextClips.push({
              id: rightId,
              title: `${clip.title} (B)`,
              sourceStart: splitSource,
              sourceEnd: clip.sourceEnd,
              timelineStart: 0,
              duration: (clip.sourceEnd - splitSource) / clipSpeed,
              speed: clipSpeed,
              viralityScore: clip.viralityScore,
              reason: clip.reason,
            });
          }

          const recalculated = recalculateTimeline(nextClips);
          return {
            project: {
              ...state.project,
              workspaceState: {
                ...state.project.workspaceState,
                selectedClipIds: newlyCreatedId
                  ? [newlyCreatedId]
                  : state.project.workspaceState.selectedClipIds,
              },
              tracks: {
                ...state.project.tracks,
                clips: recalculated,
              },
            },
          };
        }),

      // 4. delete_clips
      delete_clips: (clip_ids) =>
        set((state) => {
          if (!Array.isArray(clip_ids) || clip_ids.length === 0) return state;
          const oldClips = state.project.tracks.clips;
          const idSet = new Set(clip_ids);
          const remaining = recalculateTimeline(
            oldClips.filter((c) => !idSet.has(c.id)),
          );
          const totalDur = remaining.reduce((acc, c) => acc + c.duration, 0);
          const remapped = remapTimelineEffects(
            oldClips,
            remaining,
            state.project.tracks,
            state.project.workspaceState.crop,
          );
          return {
            project: {
              ...state.project,
              workspaceState: {
                ...state.project.workspaceState,
                playheadPosition: Math.min(
                  state.project.workspaceState.playheadPosition,
                  totalDur,
                ),
                selectedClipIds: state.project.workspaceState.selectedClipIds.filter(
                  (id) => !idSet.has(id),
                ),
                crop: remapped.crop,
              },
              tracks: {
                ...state.project.tracks,
                clips: remaining,
                captions: pruneOrphanedAutoCaptions(
                  state.project.tracks.captions,
                  remaining,
                ),
                zooms: remapped.zooms,
                drawings: remapped.drawings,
                sfx: remapped.sfx,
                audioEdits: remapped.audioEdits,
              },
            },
          };
        }),

      // 5. merge_clips
      merge_clips: (clip_id_1, clip_id_2) =>
        set((state) => {
          const clips = [...state.project.tracks.clips];
          const idx1 = clips.findIndex((c) => c.id === clip_id_1);
          const idx2 = clips.findIndex((c) => c.id === clip_id_2);
          if (idx1 === -1 || idx2 === -1 || idx1 === idx2) return state;

          const firstIdx = Math.min(idx1, idx2);
          const secondIdx = Math.max(idx1, idx2);
          if (secondIdx - firstIdx !== 1) {
            return {
              statusBanner:
                'Can only merge adjacent clips that are contiguous in the source video.',
            };
          }
          const c1 = clips[firstIdx];
          const c2 = clips[secondIdx];

          // Only merge truly contiguous source clips with matching playback speeds
          if (Math.abs(c2.sourceStart - c1.sourceEnd) > 0.5) {
            return {
              statusBanner:
                'Can only merge adjacent clips that are contiguous in the source video.',
            };
          }
          if (Math.abs((c1.speed ?? 1) - (c2.speed ?? 1)) > 0.01) {
            return {
              statusBanner: 'Can only merge clips that have the same speed.',
            };
          }

          const merged: ClipItem = {
            ...c1,
            title: c1.title.replace(/ \((A|B)\)$/, ''),
            sourceStart: Math.min(c1.sourceStart, c2.sourceStart),
            sourceEnd: Math.max(c1.sourceEnd, c2.sourceEnd),
          };

          const result = clips.filter((_, i) => i !== secondIdx);
          result[firstIdx] = merged;

          return {
            project: {
              ...state.project,
              workspaceState: {
                ...state.project.workspaceState,
                selectedClipIds: [merged.id],
              },
              tracks: {
                ...state.project.tracks,
                clips: recalculateTimeline(result),
              },
            },
          };
        }),

      // 6. reorder_clips
      reorder_clips: (clip_id, new_index) =>
        set((state) => {
          const oldClips = state.project.tracks.clips;
          const clips = [...oldClips];
          const oldIdx = clips.findIndex((c) => c.id === clip_id);
          if (oldIdx === -1) return state;
          const clampedNewIdx = Math.max(0, Math.min(clips.length - 1, new_index));
          if (oldIdx === clampedNewIdx) return state;

          const [moved] = clips.splice(oldIdx, 1);
          clips.splice(clampedNewIdx, 0, moved);
          const nextClips = recalculateTimeline(clips);
          const remapped = remapTimelineEffects(
            oldClips,
            nextClips,
            state.project.tracks,
            state.project.workspaceState.crop,
          );

          return {
            project: {
              ...state.project,
              workspaceState: {
                ...state.project.workspaceState,
                crop: remapped.crop,
              },
              tracks: {
                ...state.project.tracks,
                clips: nextClips,
                zooms: remapped.zooms,
                drawings: remapped.drawings,
                sfx: remapped.sfx,
                audioEdits: remapped.audioEdits,
              },
            },
          };
        }),

      // 7. remove_time_ranges
      remove_time_ranges: (ranges) =>
        set((state) => {
          if (!Array.isArray(ranges) || ranges.length === 0) return state;
          const oldClips = state.project.tracks.clips;
          let sortedRanges = [...ranges]
            .filter(
              (r) =>
                Number.isFinite(r?.start) &&
                Number.isFinite(r?.end) &&
                r.end > r.start,
            )
            .sort((a, b) => a.start - b.start);
          if (sortedRanges.length === 0) return state;

          const anyOverlap = oldClips.some((clip) =>
            sortedRanges.some(
              (rem) => rem.end > clip.sourceStart && rem.start < clip.sourceEnd,
            ),
          );
          if (
            !anyOverlap &&
            sortedRanges.length === 1 &&
            sortedRanges[0].start === 0 &&
            oldClips.length > 0
          ) {
            const firstClip = oldClips[0];
            if (firstClip.sourceStart > 0) {
              const spd0 = Math.max(0.25, Math.min(4.0, firstClip.speed ?? 1));
              sortedRanges = [
                {
                  start: firstClip.sourceStart,
                  end: firstClip.sourceStart + sortedRanges[0].end * spd0,
                },
              ];
            }
          }

          const nextClips: ClipItem[] = [];
          for (const clip of oldClips) {
            let pieces: Array<{ start: number; end: number }> = [
              { start: clip.sourceStart, end: clip.sourceEnd },
            ];

            for (const rem of sortedRanges) {
              const updatedPieces: Array<{ start: number; end: number }> = [];
              for (const p of pieces) {
                if (rem.end <= p.start || rem.start >= p.end) {
                  updatedPieces.push(p);
                } else {
                  if (rem.start > p.start + 0.15) {
                    updatedPieces.push({ start: p.start, end: rem.start });
                  }
                  if (rem.end < p.end - 0.15) {
                    updatedPieces.push({ start: rem.end, end: p.end });
                  }
                }
              }
              pieces = updatedPieces;
            }

            pieces.forEach((p, idx) => {
              if (p.end - p.start >= 0.15) {
                nextClips.push({
                  ...clip,
                  id: idx === 0 ? clip.id : makeId('clip'),
                  sourceStart: p.start,
                  sourceEnd: p.end,
                });
              }
            });
          }

          const recalculated = recalculateTimeline(nextClips);
          const totalDur = recalculated.reduce((sum, c) => sum + c.duration, 0);
          const survivingClipIds = new Set(recalculated.map((c) => c.id));
          const remapped = remapTimelineEffects(
            oldClips,
            recalculated,
            state.project.tracks,
            state.project.workspaceState.crop,
          );
          return {
            project: {
              ...state.project,
              workspaceState: {
                ...state.project.workspaceState,
                playheadPosition: Math.min(
                  state.project.workspaceState.playheadPosition,
                  totalDur,
                ),
                selectedClipIds:
                  state.project.workspaceState.selectedClipIds.filter((id) =>
                    survivingClipIds.has(id),
                  ),
                crop: remapped.crop,
              },
              tracks: {
                ...state.project.tracks,
                clips: recalculated,
                captions: pruneOrphanedAutoCaptions(
                  state.project.tracks.captions,
                  recalculated,
                ),
                zooms: remapped.zooms,
                drawings: remapped.drawings,
                sfx: remapped.sfx,
                audioEdits: remapped.audioEdits,
              },
            },
          };
        }),

      // 8. generate_auto_captions (Preserves isManuallyEdited === true!)
      generate_auto_captions: (start_time, end_time, providedCaptions) =>
        set((state) => {
          const normStart =
            typeof start_time === 'number' && !Number.isNaN(start_time)
              ? start_time
              : undefined;
          const normEnd =
            typeof end_time === 'number' && !Number.isNaN(end_time)
              ? end_time
              : undefined;
          const existing = state.project.tracks.captions;
          const protectedCaptions = existing.filter((c) => c.isManuallyEdited);
          const clips = state.project.tracks.clips;

          let rawFreshAuto: CaptionItem[];
          if (providedCaptions && providedCaptions.length > 0) {
            rawFreshAuto = providedCaptions;
          } else if (
            normStart === undefined &&
            normEnd === undefined &&
            clips.length > 0
          ) {
            const combined: CaptionItem[] = [];
            for (const clip of clips) {
              const slice = buildCaptionsLocally(
                state.transcript,
                clip.sourceStart,
                clip.sourceEnd,
              );
              for (const cand of slice) {
                const alreadyAdded = combined.some(
                  (c) =>
                    Math.min(cand.end, c.end) - Math.max(cand.start, c.start) >
                    0.05,
                );
                if (!alreadyAdded) {
                  combined.push(cand);
                }
              }
            }
            rawFreshAuto = combined;
          } else {
            rawFreshAuto = buildCaptionsLocally(
              state.transcript,
              normStart,
              normEnd,
            );
          }

          const freshAuto =
            clips.length > 0
              ? pruneOrphanedAutoCaptions(rawFreshAuto, clips)
              : rawFreshAuto;

          // Keep auto-captions outside the requested partial range if a sub-range was specified
          const keptUnedited =
            normStart !== undefined || normEnd !== undefined
              ? existing.filter(
                  (c) =>
                    !c.isManuallyEdited &&
                    ((normStart !== undefined && c.end < normStart) ||
                      (normEnd !== undefined && c.start > normEnd)),
                )
              : [];

          // Never overwrite or duplicate over any manually edited or kept auto caption
          const filteredFresh = freshAuto.filter(
            (cand) =>
              !overlapsProtectedCaption(cand, protectedCaptions) &&
              !keptUnedited.some(
                (ku) =>
                  Math.min(cand.end, ku.end) - Math.max(cand.start, ku.start) >
                  0.05,
              ),
          );

          const merged = [...protectedCaptions, ...keptUnedited, ...filteredFresh].sort(
            (a, b) => a.start - b.start,
          );

          return {
            project: {
              ...state.project,
              tracks: {
                ...state.project.tracks,
                captions: merged,
              },
            },
          };
        }),

      // 9. add_caption
      add_caption: (start, end, text, kind = 'subtitle') =>
        set((state) => {
          if (!Number.isFinite(start) || !Number.isFinite(end)) return state;
          const s = Math.max(0, Number(start.toFixed(3)));
          const e = Math.max(s + 0.3, Number(end.toFixed(3)));
          const words = text
            .trim()
            .split(/\s+/)
            .filter(Boolean);
          const perWord = (e - s) / Math.max(1, words.length);
          const newCap: CaptionItem = {
            id: makeId('cap'),
            start: s,
            end: e,
            text: text.trim(),
            isManuallyEdited: true,
            captionKind: kind,
            highlightedWords: [],
            words: words.map((w, idx) => ({
              word: w,
              start: Number((s + idx * perWord).toFixed(3)),
              end: Number((s + (idx + 1) * perWord).toFixed(3)),
            })),
          };
          const baseCaptions =
            kind === 'intro_hook'
              ? state.project.tracks.captions.flatMap((c) => {
                  if (c.isManuallyEdited) return [c];
                  if (c.end <= s || c.start >= e) return [c];
                  if (c.end > e + 0.25) {
                    const remWords = c.words.filter((w) => w.end > e + 0.03);
                    const remText = remWords.length
                      ? remWords.map((w) => w.word).join(' ')
                      : c.text;
                    return [
                      {
                        ...c,
                        start: Number(e.toFixed(3)),
                        text: remText,
                        words: remWords,
                      },
                    ];
                  }
                  if (c.start < s - 0.25) {
                    const remWords = c.words.filter((w) => w.start < s - 0.03);
                    const remText = remWords.length
                      ? remWords.map((w) => w.word).join(' ')
                      : c.text;
                    return [
                      {
                        ...c,
                        end: Number(s.toFixed(3)),
                        text: remText,
                        words: remWords,
                      },
                    ];
                  }
                  return [];
                })
              : state.project.tracks.captions;
          const next = [...baseCaptions, newCap].sort(
            (a, b) => a.start - b.start,
          );
          return {
            project: {
              ...state.project,
              tracks: { ...state.project.tracks, captions: next },
            },
          };
        }),

      delete_caption: (caption_id) =>
        set((state) => ({
          project: {
            ...state.project,
            tracks: {
              ...state.project.tracks,
              captions: state.project.tracks.captions.filter(
                (c) => c.id !== caption_id,
              ),
            },
          },
        })),

      combine_captions: (caption_id_1, caption_id_2) =>
        set((state) => {
          const caps = state.project.tracks.captions;
          const idx1 = caps.findIndex((c) => c.id === caption_id_1);
          const idx2 = caps.findIndex((c) => c.id === caption_id_2);
          if (idx1 === -1 || idx2 === -1 || idx1 === idx2) return state;

          const firstIdx = Math.min(idx1, idx2);
          const secondIdx = Math.max(idx1, idx2);
          const cur = caps[firstIdx];
          const nxt = caps[secondIdx];
          const combinedText = `${cur.text.trim()} ${nxt.text.trim()}`.trim();
          const synthWords = (c: CaptionItem) => {
            if (c.words.length > 0) return c.words;
            const toks = c.text.trim().split(/\s+/).filter(Boolean);
            const span = Math.max(0.2, c.end - c.start);
            const per = span / Math.max(1, toks.length);
            return toks.map((w, wi) => ({
              word: w,
              start: Number((c.start + wi * per).toFixed(3)),
              end: Number((c.start + (wi + 1) * per).toFixed(3)),
            }));
          };
          const combinedWords = [...synthWords(cur), ...synthWords(nxt)];
          const combinedHighlights = Array.from(
            new Set([...cur.highlightedWords, ...nxt.highlightedWords]),
          );
          const updated = caps
            .filter((_, i) => i !== secondIdx)
            .map((c, i) =>
              i === firstIdx
                ? {
                    ...c,
                    start: Math.min(cur.start, nxt.start),
                    end: Math.max(cur.end, nxt.end),
                    text: combinedText,
                    words: combinedWords,
                    highlightedWords: combinedHighlights,
                    isManuallyEdited: true,
                  }
                : c,
            );
          return {
            project: {
              ...state.project,
              tracks: { ...state.project.tracks, captions: updated },
            },
          };
        }),

      // 10. edit_caption
      edit_caption: (caption_id, new_text, highlighted_words, markManual = true) =>
        set((state) => {
          const nextCaptions = state.project.tracks.captions.map((cap) => {
            if (cap.id !== caption_id) return cap;
            const tokens = new_text.trim().split(/\s+/).filter(Boolean);
            const span = Math.max(0.2, cap.end - cap.start);
            const perWord = span / Math.max(1, tokens.length);
            return {
              ...cap,
              text: new_text,
              isManuallyEdited: markManual ? true : cap.isManuallyEdited,
              highlightedWords:
                highlighted_words !== undefined
                  ? highlighted_words
                  : cap.highlightedWords,
              words:
                tokens.length === cap.words.length
                  ? cap.words.map((w, idx) => ({ ...w, word: tokens[idx] }))
                  : tokens.map((w, idx) => ({
                      word: w,
                      start: Number((cap.start + idx * perWord).toFixed(3)),
                      end: Number((cap.start + (idx + 1) * perWord).toFixed(3)),
                    })),
            };
          });
          return {
            project: {
              ...state.project,
              tracks: { ...state.project.tracks, captions: nextCaptions },
            },
          };
        }),

      // 11. adjust_caption_timing
      adjust_caption_timing: (caption_id, new_start, new_end, markManual = true) =>
        set((state) => {
          if (!Number.isFinite(new_start) || !Number.isFinite(new_end)) return state;
          const s = Math.max(0, Number(new_start.toFixed(3)));
          const e = Math.max(s + 0.2, Number(new_end.toFixed(3)));
          const nextCaptions = state.project.tracks.captions
            .map((cap) => {
              if (cap.id !== caption_id) return cap;
              const tokens = cap.words.length
                ? cap.words.map((w) => w.word)
                : cap.text.split(/\s+/).filter(Boolean);
              const perWord = (e - s) / Math.max(1, tokens.length);
              return {
                ...cap,
                start: s,
                end: e,
                isManuallyEdited: markManual ? true : cap.isManuallyEdited,
                words: tokens.map((w, idx) => ({
                  word: w,
                  start: Number((s + idx * perWord).toFixed(3)),
                  end: Number((s + (idx + 1) * perWord).toFixed(3)),
                })),
              };
            })
            .sort((a, b) => a.start - b.start);

          return {
            project: {
              ...state.project,
              tracks: { ...state.project.tracks, captions: nextCaptions },
            },
          };
        }),

      // 12. change_caption_style
      change_caption_style: (args) =>
        set((state) => {
          const cur = state.project.captionStyle;
          const rawArgs = args as ChangeCaptionStyleArgs & Record<string, unknown>;
          const colorMap: Record<string, string> = {
            black: '#000000',
            white: '#FFFFFF',
            yellow: '#FACC15',
            gold: '#FACC15',
            red: '#EF4444',
            crimson: '#DC2626',
            blue: '#38BDF8',
            sky: '#38BDF8',
            navy: '#1E3A8A',
            green: '#22C55E',
            lime: '#84CC16',
            emerald: '#10B981',
            orange: '#F97316',
            amber: '#F59E0B',
            pink: '#EC4899',
            rose: '#F43F5E',
            magenta: '#D946EF',
            purple: '#A855F7',
            violet: '#8B5CF6',
            cyan: '#06B6D4',
            teal: '#14B8A6',
            aqua: '#06B6D4',
            gray: '#94A3B8',
            grey: '#94A3B8',
            silver: '#CBD5E1',
            brown: '#92400E',
          };
          const normColor = (val: unknown, fallback: string): string => {
            if (typeof val !== 'string' || !val.trim()) return fallback;
            const t = val.trim();
            const mapped = colorMap[t.toLowerCase()];
            if (mapped) return mapped;
            const hex = t.replace('#', '');
            if (/^[0-9a-fA-F]{3}$/.test(hex)) {
              return `#${hex[0]}${hex[0]}${hex[1]}${hex[1]}${hex[2]}${hex[2]}`.toUpperCase();
            }
            if (/^[0-9a-fA-F]{6}$/.test(hex)) {
              return `#${hex.toUpperCase()}`;
            }
            return fallback;
          };
          const nextTextCol = normColor(
            rawArgs.text_color ?? rawArgs.textColor ?? rawArgs.color,
            cur.textColor,
          );
          const nextHlCol = normColor(
            rawArgs.highlight_color ?? rawArgs.highlightColor,
            cur.highlightColor,
          );
          const rawFontSize = Number(rawArgs.font_size ?? rawArgs.fontSize ?? cur.fontSize);
          const rawPreset = String(rawArgs.preset ?? cur.preset).trim().toLowerCase();
          const validPresets: CaptionPreset[] = [
            'karaoke',
            'bold_social',
            'yellow_highlight',
            'minimal',
          ];
          const nextPreset: CaptionPreset = validPresets.includes(rawPreset as CaptionPreset)
            ? (rawPreset as CaptionPreset)
            : cur.preset;
          const rawPosStr = String(rawArgs.position ?? cur.position).trim().toLowerCase();
          const mappedPos = rawPosStr === 'center' ? 'middle' : rawPosStr;
          const validPositions: CaptionPosition[] = ['top', 'middle', 'bottom'];
          const nextPosition: CaptionPosition = validPositions.includes(
            mappedPos as CaptionPosition,
          )
            ? (mappedPos as CaptionPosition)
            : cur.position;
          return {
            project: {
              ...state.project,
              captionStyle: {
                preset: nextPreset,
                fontFamily: String(
                  rawArgs.font_family ?? rawArgs.fontFamily ?? cur.fontFamily,
                ),
                fontSize: Number.isFinite(rawFontSize)
                  ? Math.max(14, Math.min(64, Math.round(rawFontSize)))
                  : cur.fontSize,
                textColor: nextTextCol,
                highlightColor: nextHlCol,
                position: nextPosition,
                isBold:
                  typeof (rawArgs.is_bold ?? rawArgs.isBold) === 'boolean'
                    ? Boolean(rawArgs.is_bold ?? rawArgs.isBold)
                    : cur.isBold,
              },
            },
          };
        }),

      // 13. change_video_format
      change_video_format: (aspect_ratio) =>
        set((state) => ({
          project: {
            ...state.project,
            workspaceState: {
              ...state.project.workspaceState,
              targetAspectRatio: aspect_ratio,
            },
          },
        })),

      // 14. Speed controls
      set_clip_speed: (clip_id, speed) =>
        set((state) => {
          if (!Number.isFinite(speed) || speed <= 0) return state;
          const oldClips = state.project.tracks.clips;
          const clamped = Math.max(0.25, Math.min(4.0, speed));
          const updated = oldClips.map((c) =>
            c.id === clip_id ? { ...c, speed: clamped } : c,
          );
          const nextClips = recalculateTimeline(updated);
          const totalDur = nextClips.reduce((acc, c) => acc + c.duration, 0);
          const remapped = remapTimelineEffects(
            oldClips,
            nextClips,
            state.project.tracks,
            state.project.workspaceState.crop,
          );
          return {
            project: {
              ...state.project,
              workspaceState: {
                ...state.project.workspaceState,
                playheadPosition: Math.min(
                  state.project.workspaceState.playheadPosition,
                  totalDur,
                ),
                crop: remapped.crop,
              },
              tracks: {
                ...state.project.tracks,
                clips: nextClips,
                zooms: remapped.zooms,
                drawings: remapped.drawings,
                sfx: remapped.sfx,
                audioEdits: remapped.audioEdits,
              },
            },
          };
        }),

      set_range_speed: (start_time, end_time, speed) =>
        set((state) => {
          if (
            !Number.isFinite(start_time) ||
            !Number.isFinite(end_time) ||
            !Number.isFinite(speed) ||
            speed <= 0
          ) {
            return state;
          }
          const oldClips = state.project.tracks.clips;
          const sTl = Math.max(0, start_time);
          const eTl = Math.max(sTl + 0.1, end_time);
          const clamped = Math.max(0.25, Math.min(4.0, speed));
          const nextClips: ClipItem[] = [];
          const selectedNewIds: string[] = [];

          for (const clip of oldClips) {
            const cStart = clip.timelineStart;
            const cEnd = clip.timelineStart + clip.duration;
            const spd = Math.max(0.25, Math.min(4.0, clip.speed ?? 1));
            if (eTl <= cStart + 0.05 || sTl >= cEnd - 0.05) {
              nextClips.push(clip);
              continue;
            }
            const cutStartSrc =
              sTl > cStart + 0.15
                ? Number((clip.sourceStart + (sTl - cStart) * spd).toFixed(3))
                : clip.sourceStart;
            const cutEndSrc =
              eTl < cEnd - 0.15
                ? Number((clip.sourceStart + (eTl - cStart) * spd).toFixed(3))
                : clip.sourceEnd;

            if (cutStartSrc > clip.sourceStart + 0.05) {
              nextClips.push({
                ...clip,
                id: makeId('clip'),
                sourceEnd: cutStartSrc,
                speed: spd,
              });
            }
            const midId = makeId('clip');
            selectedNewIds.push(midId);
            nextClips.push({
              ...clip,
              id: midId,
              sourceStart: cutStartSrc,
              sourceEnd: cutEndSrc,
              speed: clamped,
            });
            if (cutEndSrc < clip.sourceEnd - 0.05) {
              nextClips.push({
                ...clip,
                id: makeId('clip'),
                sourceStart: cutEndSrc,
                speed: spd,
              });
            }
          }

          const recalculated = recalculateTimeline(nextClips);
          const totalDur = recalculated.reduce((acc, c) => acc + c.duration, 0);
          const remapped = remapTimelineEffects(
            oldClips,
            recalculated,
            state.project.tracks,
            state.project.workspaceState.crop,
          );

          return {
            project: {
              ...state.project,
              workspaceState: {
                ...state.project.workspaceState,
                playheadPosition: Math.min(
                  state.project.workspaceState.playheadPosition,
                  totalDur,
                ),
                selectedClipIds:
                  selectedNewIds.length > 0
                    ? selectedNewIds
                    : state.project.workspaceState.selectedClipIds,
                crop: remapped.crop,
              },
              tracks: {
                ...state.project.tracks,
                clips: recalculated,
                zooms: remapped.zooms,
                drawings: remapped.drawings,
                sfx: remapped.sfx,
                audioEdits: remapped.audioEdits,
              },
            },
          };
        }),

      // 15. Zoom effects
      add_zoom_effect: (item) => {
        const id = makeId('zoom');
        const safeS = Number.isFinite(item.start) ? item.start : 0;
        const safeE = Number.isFinite(item.end) ? item.end : safeS + 2.0;
        const start = Math.max(0, Number(safeS.toFixed(3)));
        const end = Math.max(start + 0.25, Number(safeE.toFixed(3)));
        const stSc = Number.isFinite(item.startScale) ? item.startScale : 1.0;
        const enSc = Number.isFinite(item.endScale) ? item.endScale : 1.35;
        const fx = Number.isFinite(item.focusX) ? item.focusX : 0.5;
        const fy = Number.isFinite(item.focusY) ? item.focusY : 0.5;
        const newZoom: ZoomEffectItem = {
          ...item,
          id,
          start,
          end,
          startScale: Math.max(1.0, Math.min(3.0, stSc)),
          endScale: Math.max(1.0, Math.min(3.0, enSc)),
          focusX: Math.max(0.0, Math.min(1.0, fx)),
          focusY: Math.max(0.0, Math.min(1.0, fy)),
        };
        set((state) => ({
          selectedZoomId: id,
          selectedDrawingId: null,
          selectedSfxId: null,
          project: {
            ...state.project,
            workspaceState: {
              ...state.project.workspaceState,
              selectedClipIds: [],
            },
            tracks: {
              ...state.project.tracks,
              zooms: [...(state.project.tracks.zooms ?? []), newZoom].sort(
                (a, b) => a.start - b.start,
              ),
            },
          },
        }));
        return id;
      },

      update_zoom_effect: (id, patch) =>
        set((state) => ({
          project: {
            ...state.project,
            tracks: {
              ...state.project.tracks,
              zooms: (state.project.tracks.zooms ?? []).map((z) => {
                if (z.id !== id) return z;
                const nextStart =
                  patch.start !== undefined ? Math.max(0, Number(patch.start.toFixed(3))) : z.start;
                const nextEnd =
                  patch.end !== undefined
                    ? Math.max(nextStart + 0.2, Number(patch.end.toFixed(3)))
                    : Math.max(nextStart + 0.2, z.end);
                return {
                  ...z,
                  ...patch,
                  start: nextStart,
                  end: nextEnd,
                };
              }),
            },
          },
        })),

      delete_zoom_effect: (id) =>
        set((state) => {
          const curZooms = state.project.tracks.zooms ?? [];
          if (!curZooms.some((z) => z.id === id)) {
            return state.selectedZoomId === id ? { selectedZoomId: null } : state;
          }
          return {
            selectedZoomId: state.selectedZoomId === id ? null : state.selectedZoomId,
            project: {
              ...state.project,
              tracks: {
                ...state.project.tracks,
                zooms: curZooms.filter((z) => z.id !== id),
              },
            },
          };
        }),

      // 16. Drawing overlays
      add_drawing: (item) => {
        const id = makeId('draw');
        const safeS = Number.isFinite(item.start) ? item.start : 0;
        const safeE = Number.isFinite(item.end) ? item.end : safeS + 2.5;
        const start = Math.max(0, Number(safeS.toFixed(3)));
        const end = Math.max(start + 0.25, Number(safeE.toFixed(3)));
        const sw = Number.isFinite(item.strokeWidth) ? item.strokeWidth : 6;
        const op = Number.isFinite(item.opacity) ? item.opacity : 1.0;
        const newDrawing: DrawingItem = {
          ...item,
          id,
          start,
          end,
          strokeWidth: Math.max(2, Math.min(24, sw)),
          opacity: Math.max(0.1, Math.min(1.0, op)),
        };
        set((state) => ({
          selectedDrawingId: id,
          selectedZoomId: null,
          selectedSfxId: null,
          project: {
            ...state.project,
            workspaceState: {
              ...state.project.workspaceState,
              selectedClipIds: [],
            },
            tracks: {
              ...state.project.tracks,
              drawings: [...(state.project.tracks.drawings ?? []), newDrawing].sort(
                (a, b) => a.start - b.start,
              ),
            },
          },
        }));
        return id;
      },

      update_drawing: (id, patch) =>
        set((state) => ({
          project: {
            ...state.project,
            tracks: {
              ...state.project.tracks,
              drawings: (state.project.tracks.drawings ?? []).map((d) => {
                if (d.id !== id) return d;
                const nextStart =
                  patch.start !== undefined ? Math.max(0, Number(patch.start.toFixed(3))) : d.start;
                const nextEnd =
                  patch.end !== undefined
                    ? Math.max(nextStart + 0.2, Number(patch.end.toFixed(3)))
                    : Math.max(nextStart + 0.2, d.end);
                return {
                  ...d,
                  ...patch,
                  start: nextStart,
                  end: nextEnd,
                };
              }),
            },
          },
        })),

      delete_drawing: (id) =>
        set((state) => {
          const curDrawings = state.project.tracks.drawings ?? [];
          if (!curDrawings.some((d) => d.id === id)) {
            return state.selectedDrawingId === id ? { selectedDrawingId: null } : state;
          }
          return {
            selectedDrawingId: state.selectedDrawingId === id ? null : state.selectedDrawingId,
            project: {
              ...state.project,
              tracks: {
                ...state.project.tracks,
                drawings: curDrawings.filter((d) => d.id !== id),
              },
            },
          };
        }),

      // 17. SFX timeline clips
      add_sfx_clip: (item) => {
        const id = makeId('sfxclip');
        const matchedAsset = get().sfxLibrary.find((a) => a.id === item.assetId);
        const maxSourceDur =
          matchedAsset && matchedAsset.duration > 0
            ? matchedAsset.duration
            : item.sourceDuration && item.sourceDuration > 0
              ? item.sourceDuration
              : null;
        const safeS = Number.isFinite(item.start) ? item.start : 0;
        const start = Math.max(0, Number(safeS.toFixed(3)));
        const rawTrim = Number.isFinite(item.trimStart) ? (item.trimStart as number) : 0;
        let trimStart = Math.max(0, Number(rawTrim.toFixed(3)));
        if (maxSourceDur !== null) {
          trimStart = Math.min(Math.max(0, maxSourceDur - 0.15), trimStart);
        }
        const rawDur = Number.isFinite(item.duration) && item.duration > 0 ? item.duration : 1.5;
        let duration = Math.max(0.15, Number(rawDur.toFixed(3)));
        if (maxSourceDur !== null) {
          duration = Math.min(Math.max(0.15, maxSourceDur - trimStart), duration);
        }
        const safeVol = Number.isFinite(item.volume) ? item.volume : 0.85;
        const volume = Math.max(0, Math.min(2.0, Number(safeVol.toFixed(2))));
        const newSfx: SfxClipItem = {
          ...item,
          id,
          start,
          duration: Number(duration.toFixed(3)),
          trimStart: Number(trimStart.toFixed(3)),
          volume,
          ...(maxSourceDur !== null ? { sourceDuration: maxSourceDur } : {}),
        };
        set((state) => ({
          selectedSfxId: id,
          selectedZoomId: null,
          selectedDrawingId: null,
          project: {
            ...state.project,
            workspaceState: {
              ...state.project.workspaceState,
              selectedClipIds: [],
            },
            tracks: {
              ...state.project.tracks,
              sfx: [...(state.project.tracks.sfx ?? []), newSfx].sort(
                (a, b) => a.start - b.start,
              ),
            },
          },
        }));
        return id;
      },

      update_sfx_clip: (id, patch) =>
        set((state) => ({
          project: {
            ...state.project,
            tracks: {
              ...state.project.tracks,
              sfx: (state.project.tracks.sfx ?? []).map((sc) => {
                if (sc.id !== id) return sc;
                const matchedAsset = state.sfxLibrary.find((a) => a.id === sc.assetId);
                const maxSourceDur =
                  matchedAsset && matchedAsset.duration > 0 ? matchedAsset.duration : null;
                const nextStart =
                  patch.start !== undefined
                    ? Math.max(0, Number(patch.start.toFixed(3)))
                    : sc.start;
                let nextTrimStart =
                  patch.trimStart !== undefined
                    ? Math.max(0, Number(patch.trimStart.toFixed(3)))
                    : Math.max(0, sc.trimStart ?? 0);
                if (maxSourceDur !== null) {
                  nextTrimStart = Math.min(Math.max(0, maxSourceDur - 0.15), nextTrimStart);
                }
                let nextDur =
                  patch.duration !== undefined
                    ? Math.max(0.15, Number(patch.duration.toFixed(3)))
                    : Math.max(0.15, sc.duration);
                if (maxSourceDur !== null) {
                  nextDur = Math.min(Math.max(0.15, maxSourceDur - nextTrimStart), nextDur);
                }
                const nextVol =
                  patch.volume !== undefined
                    ? Math.max(0, Math.min(2.0, Number(patch.volume.toFixed(2))))
                    : sc.volume;
                return {
                  ...sc,
                  ...patch,
                  start: nextStart,
                  trimStart: Number(nextTrimStart.toFixed(3)),
                  duration: Number(nextDur.toFixed(3)),
                  volume: nextVol,
                };
              }),
            },
          },
        })),

      delete_sfx_clip: (id) =>
        set((state) => {
          const curSfx = state.project.tracks.sfx ?? [];
          if (!curSfx.some((sc) => sc.id === id)) {
            return state.selectedSfxId === id ? { selectedSfxId: null } : state;
          }
          return {
            selectedSfxId: state.selectedSfxId === id ? null : state.selectedSfxId,
            project: {
              ...state.project,
              tracks: {
                ...state.project.tracks,
                sfx: curSfx.filter((sc) => sc.id !== id),
              },
            },
          };
        }),

      // 18. Video Audio Mute / Cut / Volume Segments
      add_audio_edit: (item) => {
        const id = makeId('aud');
        const safeS = Number.isFinite(item.start) ? item.start : 0;
        const safeE = Number.isFinite(item.end) ? item.end : safeS + 2.0;
        const rawVol = Number.isFinite(item.volume) ? item.volume : item.muted ? 0 : 1;
        const muted = Boolean(item.muted) || rawVol <= 0.001;
        const vol = muted ? 0 : Math.max(0, Math.min(2, Number(rawVol.toFixed(2))));
        set((state) => ({
          selectedAudioEditId: id,
          selectedZoomId: null,
          selectedDrawingId: null,
          selectedSfxId: null,
          project: {
            ...state.project,
            workspaceState: {
              ...state.project.workspaceState,
              selectedClipIds: [],
            },
            tracks: {
              ...state.project.tracks,
              audioEdits: [
                ...(state.project.tracks.audioEdits ?? []),
                {
                  id,
                  start: Math.max(0, Number(safeS.toFixed(3))),
                  end: Math.max(safeS + 0.15, Number(safeE.toFixed(3))),
                  muted,
                  volume: vol,
                  ...(item.label ? { label: String(item.label).slice(0, 60) } : {}),
                },
              ],
            },
          },
        }));
        return id;
      },

      update_audio_edit: (id, patch) =>
        set((state) => ({
          project: {
            ...state.project,
            tracks: {
              ...state.project.tracks,
              audioEdits: (state.project.tracks.audioEdits ?? []).map((ae) => {
                if (ae.id !== id) return ae;
                const nextS =
                  patch.start !== undefined && Number.isFinite(patch.start)
                    ? Math.max(0, Number(patch.start.toFixed(3)))
                    : ae.start;
                const nextE =
                  patch.end !== undefined && Number.isFinite(patch.end)
                    ? Math.max(nextS + 0.15, Number(patch.end.toFixed(3)))
                    : Math.max(nextS + 0.15, ae.end);
                const nextMuted =
                  patch.muted !== undefined ? Boolean(patch.muted) : ae.muted;
                const nextVol =
                  patch.volume !== undefined && Number.isFinite(patch.volume)
                    ? Math.max(0, Math.min(2, Number(patch.volume.toFixed(2))))
                    : nextMuted
                      ? 0
                      : ae.volume > 0.001
                        ? ae.volume
                        : 1;
                const finalMuted =
                  patch.muted !== undefined
                    ? nextMuted
                    : patch.volume !== undefined
                      ? nextVol <= 0.001
                      : nextMuted;
                return {
                  ...ae,
                  ...patch,
                  start: nextS,
                  end: nextE,
                  muted: finalMuted,
                  volume: finalMuted ? 0 : nextVol,
                };
              }),
            },
          },
        })),

      delete_audio_edit: (id) =>
        set((state) => {
          const cur = state.project.tracks.audioEdits ?? [];
          if (id === 'all') {
            if (cur.length === 0 && !state.selectedAudioEditId) return state;
            return {
              selectedAudioEditId: null,
              project: {
                ...state.project,
                tracks: {
                  ...state.project.tracks,
                  audioEdits: [],
                },
              },
            };
          }
          if (!cur.some((ae) => ae.id === id)) {
            return state.selectedAudioEditId === id ? { selectedAudioEditId: null } : state;
          }
          return {
            selectedAudioEditId:
              state.selectedAudioEditId === id ? null : state.selectedAudioEditId,
            project: {
              ...state.project,
              tracks: {
                ...state.project.tracks,
                audioEdits: cur.filter((ae) => ae.id !== id),
              },
            },
          };
        }),

      split_audio_at: (timestamp) =>
        set((state) => {
          if (!Number.isFinite(timestamp)) return state;
          const ts = Number(Math.max(0, timestamp).toFixed(3));
          const curEdits = state.project.tracks.audioEdits ?? [];
          const existingIdx = curEdits.findIndex(
            (ae) => ts > ae.start + 0.15 && ts < ae.end - 0.15,
          );
          if (existingIdx !== -1) {
            const target = curEdits[existingIdx];
            const rightId = makeId('aud');
            const nextEdits = [
              ...curEdits.slice(0, existingIdx),
              { ...target, end: ts },
              {
                id: rightId,
                start: ts,
                end: target.end,
                muted: target.muted,
                volume: target.volume,
                label: target.label,
              },
              ...curEdits.slice(existingIdx + 1),
            ];
            return {
              selectedAudioEditId: rightId,
              selectedZoomId: null,
              selectedDrawingId: null,
              selectedSfxId: null,
              project: {
                ...state.project,
                workspaceState: {
                  ...state.project.workspaceState,
                  selectedClipIds: [],
                },
                tracks: {
                  ...state.project.tracks,
                  audioEdits: nextEdits,
                },
              },
            };
          }

          const targetClip = state.project.tracks.clips.find(
            (c) => ts > c.timelineStart + 0.15 && ts < c.timelineStart + c.duration - 0.15,
          );
          if (!targetClip) return state;

          const cStart = Number(targetClip.timelineStart.toFixed(3));
          const cEnd = Number((targetClip.timelineStart + targetClip.duration).toFixed(3));
          const leftId = makeId('aud');
          const rightId = makeId('aud');
          const overlapsRange = (rs: number, re: number) =>
            curEdits.some((ae) => Math.min(ae.end, re) - Math.max(ae.start, rs) > 0.1);
          const toAdd: AudioEditItem[] = [];
          if (!overlapsRange(cStart, ts)) {
            toAdd.push({
              id: leftId,
              start: cStart,
              end: ts,
              muted: false,
              volume: 1.0,
              label: 'Audio Part 1',
            });
          }
          if (!overlapsRange(ts, cEnd)) {
            toAdd.push({
              id: rightId,
              start: ts,
              end: cEnd,
              muted: false,
              volume: 1.0,
              label: 'Audio Part 2',
            });
          }
          if (toAdd.length === 0) return state;
          const selectId = toAdd[toAdd.length - 1].id;
          return {
            selectedAudioEditId: selectId,
            selectedZoomId: null,
            selectedDrawingId: null,
            selectedSfxId: null,
            project: {
              ...state.project,
              workspaceState: {
                ...state.project.workspaceState,
                selectedClipIds: [],
              },
              tracks: {
                ...state.project.tracks,
                audioEdits: [...curEdits, ...toAdd],
              },
            },
          };
        }),

      // 19. Source video crop rectangle
      set_video_crop: (crop) =>
        set((state) => {
          if (!crop) {
            return {
              project: {
                ...state.project,
                workspaceState: {
                  ...state.project.workspaceState,
                  crop: null,
                },
              },
            };
          }
          const rawX = Number(crop.x ?? 0);
          const rawY = Number(crop.y ?? 0);
          const rawW = Number(crop.w ?? 1);
          const rawH = Number(crop.h ?? 1);
          if (
            !Number.isFinite(rawX) ||
            !Number.isFinite(rawY) ||
            !Number.isFinite(rawW) ||
            !Number.isFinite(rawH)
          ) {
            return state;
          }
          const x = Math.max(0, Math.min(0.9, Number(rawX.toFixed(4))));
          const y = Math.max(0, Math.min(0.9, Number(rawY.toFixed(4))));
          const w = Math.max(0.1, Math.min(1 - x, Number(rawW.toFixed(4))));
          const h = Math.max(0.1, Math.min(1 - y, Number(rawH.toFixed(4))));
          const isFullFrame = x <= 0.005 && y <= 0.005 && w >= 0.99 && h >= 0.99;
          const hasTimeWindow =
            crop.start != null &&
            crop.end != null &&
            Number.isFinite(crop.start) &&
            Number.isFinite(crop.end) &&
            Number(crop.end) > Number(crop.start) + 0.1;
          const nextCrop: VideoCropRect | null = isFullFrame
            ? null
            : hasTimeWindow
              ? {
                  x,
                  y,
                  w,
                  h,
                  start: Math.max(0, Number(Number(crop.start).toFixed(2))),
                  end: Math.max(0.2, Number(Number(crop.end).toFixed(2))),
                }
              : { x, y, w, h };
          return {
            project: {
              ...state.project,
              workspaceState: {
                ...state.project.workspaceState,
                crop: nextCrop,
              },
            },
          };
        }),

      reset_video_crop: () =>
        set((state) => ({
          project: {
            ...state.project,
            workspaceState: {
              ...state.project.workspaceState,
              crop: null,
            },
          },
        })),

      // Shared AI Tool Call Dispatcher -> executes the exact same 21 functions
      applyAiToolCalls: (toolCalls, autoCaptions) => {
        const actions: AiActionLogItem[] = [];
        const store = get();
        const temporalApi = useEditorStore.temporal.getState();
        const prevProject = store.project;
        const wasTracking = temporalApi.isTracking;
        let pausedForBatch = false;

        try {
          for (const tc of toolCalls) {
            const args = tc.arguments || {};
            let summary = tc.name;

            switch (tc.name) {
              case 'isolate_highlight_clips': {
                const clips = Array.isArray(args.clips)
                  ? (args.clips as HighlightClipInput[])
                  : [];
                store.isolate_highlight_clips(clips);
                summary = `Isolated ${clips.length} viral clip(s)`;
                break;
              }
              case 'trim_clip': {
                const s = Number(args.new_source_start);
                const e = Number(args.new_source_end);
                if (Number.isFinite(s) && Number.isFinite(e)) {
                  store.trim_clip(String(args.clip_id), s, e);
                  summary = `Trimmed clip ${String(args.clip_id)}`;
                }
                break;
              }
              case 'split_clip': {
                const ts = Number(args.split_timestamp);
                if (Number.isFinite(ts)) {
                  store.split_clip(String(args.clip_id), ts);
                  summary = `Split clip at ${ts.toFixed(1)}s`;
                }
                break;
              }
              case 'delete_clips': {
                const ids = Array.isArray(args.clip_ids)
                  ? (args.clip_ids as string[])
                  : [];
                store.delete_clips(ids);
                summary = `Deleted ${ids.length} clip(s)`;
                break;
              }
              case 'merge_clips': {
                store.merge_clips(String(args.clip_id_1), String(args.clip_id_2));
                summary = 'Merged adjacent clips';
                break;
              }
              case 'reorder_clips': {
                const ni = Number(args.new_index);
                if (Number.isFinite(ni)) {
                  store.reorder_clips(String(args.clip_id), ni);
                  summary = `Reordered clip to #${ni + 1}`;
                }
                break;
              }
              case 'remove_time_ranges': {
                const ranges = Array.isArray(args.ranges)
                  ? (args.ranges as TimeRange[])
                  : [];
                store.remove_time_ranges(ranges);
                summary = `Removed ${ranges.length} time range(s)`;
                break;
              }
              case 'generate_auto_captions': {
                store.generate_auto_captions(
                  args.start_time != null ? Number(args.start_time) : undefined,
                  args.end_time != null ? Number(args.end_time) : undefined,
                  autoCaptions ?? undefined,
                );
                summary = 'Generated auto captions';
                break;
              }
              case 'add_caption': {
                const capKind =
                  args.kind === 'intro_hook' || args.caption_kind === 'intro_hook'
                    ? 'intro_hook'
                    : 'subtitle';
                store.add_caption(
                  Number(args.start),
                  Number(args.end),
                  String(args.text || ''),
                  capKind,
                );
                summary =
                  capKind === 'intro_hook' ? 'Added intro hook caption' : 'Added caption';
                break;
              }
              case 'delete_caption': {
                store.delete_caption(String(args.caption_id));
                summary = `Deleted caption ${String(args.caption_id)}`;
                break;
              }
              case 'combine_captions': {
                store.combine_captions(
                  String(args.caption_id_1),
                  String(args.caption_id_2),
                );
                summary = 'Combined captions';
                break;
              }
              case 'edit_caption': {
                store.edit_caption(
                  String(args.caption_id),
                  String(args.new_text || ''),
                  args.highlighted_words as string[] | undefined,
                  false,
                );
                summary = `Edited caption ${String(args.caption_id)}`;
                break;
              }
              case 'adjust_caption_timing': {
                store.adjust_caption_timing(
                  String(args.caption_id),
                  Number(args.new_start),
                  Number(args.new_end),
                  false,
                );
                summary = `Adjusted timing for ${String(args.caption_id)}`;
                break;
              }
              case 'change_caption_style': {
                store.change_caption_style(args as ChangeCaptionStyleArgs);
                const details: string[] = [];
                if (args.preset) details.push(String(args.preset));
                if (args.text_color || args.color)
                  details.push(`text ${String(args.text_color || args.color)}`);
                if (args.highlight_color)
                  details.push(`highlight ${String(args.highlight_color)}`);
                if (args.position) details.push(String(args.position));
                summary = `Updated caption style${details.length > 0 ? ` (${details.join(', ')})` : ''}`;
                break;
              }
              case 'change_video_format': {
                const ratio = (args.aspect_ratio as AspectRatio) || '16:9';
                store.change_video_format(ratio);
                summary = `Changed format to ${ratio}`;
                break;
              }
              case 'set_clip_speed': {
                const spd = Math.max(0.25, Math.min(4.0, Number(args.speed ?? 1)));
                if (args.start_time != null && args.end_time != null) {
                  const st = Number(args.start_time);
                  const en = Number(args.end_time);
                  store.set_range_speed(st, en, spd);
                  summary = `Set ${st.toFixed(1)}s–${en.toFixed(1)}s speed to ${spd}x`;
                } else {
                  const liveProj = get().project;
                  const targetClipId =
                    (args.clip_id as string | undefined) ||
                    liveProj.workspaceState.selectedClipIds[0] ||
                    liveProj.tracks.clips[0]?.id;
                  if (targetClipId) {
                    store.set_clip_speed(String(targetClipId), spd);
                    summary = `Set clip speed to ${spd}x`;
                  } else {
                    summary = 'No active clip to change speed';
                  }
                }
                break;
              }
              case 'add_zoom_effect': {
                const kind = (args.kind as ZoomKind) || 'punch_in';
                const playheadNow = get().project.workspaceState.playheadPosition;
                const start = Number(args.start ?? playheadNow ?? 0);
                const end = Number(args.end ?? start + 2.0);
                const startScale = Number(
                  args.start_scale ?? args.startScale ?? (kind === 'zoom_out' ? 1.4 : 1.0),
                );
                const endScale = Number(
                  args.end_scale ?? args.endScale ?? (kind === 'zoom_out' ? 1.0 : 1.4),
                );
                const focusX = Number(args.focus_x ?? args.focusX ?? 0.5);
                const focusY = Number(args.focus_y ?? args.focusY ?? 0.5);
                const easing =
                  (args.easing as ZoomEasing) ||
                  (kind === 'punch_in' ? 'instant' : 'ease_in_out');
                store.add_zoom_effect({
                  start,
                  end,
                  kind,
                  startScale,
                  endScale,
                  focusX,
                  focusY,
                  easing,
                });
                summary = `Added ${kind.replace('_', ' ')} (${start.toFixed(1)}s–${end.toFixed(1)}s)`;
                break;
              }
              case 'delete_zoom_effect': {
                const reqZoomId = String(args.zoom_id || '').trim();
                const currentZooms = get().project.tracks.zooms ?? [];
                const exactZoom = currentZooms.find((z) => z.id === reqZoomId);
                if (exactZoom) {
                  store.delete_zoom_effect(exactZoom.id);
                  summary = `Removed zoom (${exactZoom.start.toFixed(1)}s–${exactZoom.end.toFixed(1)}s)`;
                } else if (currentZooms.length > 0) {
                  const fallbackZoom = currentZooms[currentZooms.length - 1];
                  store.delete_zoom_effect(fallbackZoom.id);
                  summary = `Removed zoom (${fallbackZoom.start.toFixed(1)}s–${fallbackZoom.end.toFixed(1)}s)`;
                } else {
                  summary = 'No active zoom to remove';
                }
                break;
              }
              case 'add_drawing': {
                const tool = (args.tool as DrawingTool) || 'arrow';
                const playheadNow = get().project.workspaceState.playheadPosition;
                const start = Number(args.start ?? playheadNow ?? 0);
                const end = Number(args.end ?? start + 2.5);
                const rawPts = Array.isArray(args.points) ? args.points : [];
                const parsedPts: DrawingPoint[] = rawPts
                  .map((pt) => {
                    const obj = pt as { x?: unknown; y?: unknown };
                    return {
                      x: Math.max(0, Math.min(1, Number(obj?.x ?? 0.5))),
                      y: Math.max(0, Math.min(1, Number(obj?.y ?? 0.5))),
                    };
                  })
                  .filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
                const points =
                  parsedPts.length >= 2
                    ? parsedPts
                    : [
                        { x: 0.25, y: 0.28 },
                        { x: 0.5, y: 0.48 },
                      ];
                const color = String(args.color || '#EF4444');
                const strokeWidth = Number(args.stroke_width ?? args.strokeWidth ?? 6);
                const opacity = Number(args.opacity ?? 1.0);
                store.add_drawing({
                  start,
                  end,
                  tool,
                  points,
                  color,
                  strokeWidth,
                  opacity,
                });
                summary = `Drew ${tool} (${start.toFixed(1)}s–${end.toFixed(1)}s)`;
                break;
              }
              case 'delete_drawing': {
                const reqDrawId = String(args.drawing_id || '').trim();
                const curDrawings = get().project.tracks.drawings ?? [];
                const exactDraw = curDrawings.find((d) => d.id === reqDrawId);
                if (exactDraw) {
                  store.delete_drawing(exactDraw.id);
                  summary = `Deleted ${exactDraw.tool} drawing`;
                } else if (curDrawings.length > 0) {
                  const fallbackDraw = curDrawings[curDrawings.length - 1];
                  store.delete_drawing(fallbackDraw.id);
                  summary = `Deleted ${fallbackDraw.tool} drawing`;
                } else {
                  summary = 'No active drawing to delete';
                }
                break;
              }
              case 'add_sfx_clip': {
                const lib = get().sfxLibrary;
                const reqAssetId = String(args.asset_id || '').trim();
                const reqQuery = String(args.sfx_name || args.name || '').trim().toLowerCase();
                let asset = lib.find((a) => a.id === reqAssetId);
                if (!asset && reqQuery) {
                  asset =
                    lib.find(
                      (a) =>
                        a.name.toLowerCase().includes(reqQuery) ||
                        a.tags.some((t) => reqQuery.includes(t.toLowerCase())),
                    ) ?? undefined;
                }
                if (!asset && lib.length > 0 && !args.url) {
                  asset = lib[0];
                }
                const resolvedUrl = String(args.url || asset?.url || '').trim();
                const resolvedAssetId = asset?.id || reqAssetId || makeId('asset');
                const resolvedName = String(
                  args.name || args.sfx_name || asset?.name || 'Custom SFX',
                );
                const resolvedDur = Number(args.duration ?? asset?.duration ?? 1.5);
                const trimStart = Number(args.trim_start ?? args.trimStart ?? 0);
                const playheadNow = get().project.workspaceState.playheadPosition;
                const start = Number(args.start ?? playheadNow ?? 0);
                const volume = Number(args.volume ?? 0.85);
                if (resolvedUrl) {
                  store.add_sfx_clip({
                    assetId: resolvedAssetId,
                    name: resolvedName,
                    url: resolvedUrl,
                    start,
                    duration: resolvedDur,
                    trimStart,
                    volume,
                  });
                  summary = `Added SFX '${resolvedName}' at ${start.toFixed(1)}s`;
                } else {
                  summary = 'No custom MP3 SFX uploaded yet';
                }
                break;
              }
              case 'trim_sfx_clip': {
                const reqSfxId = String(args.sfx_id || '').trim();
                const reqSfxName = String(args.sfx_name || args.name || '')
                  .trim()
                  .toLowerCase();
                const curSfx = get().project.tracks.sfx ?? [];
                const selectedId = get().selectedSfxId;
                const targetSfx =
                  curSfx.find((s) => s.id === reqSfxId) ||
                  (reqSfxName
                    ? curSfx.find((s) => s.name.toLowerCase().includes(reqSfxName))
                    : undefined) ||
                  curSfx.find((s) => s.id === selectedId) ||
                  curSfx[curSfx.length - 1];
                if (targetSfx) {
                  const patch: Partial<SfxClipItem> = {};
                  if (args.trim_start != null || args.trimStart != null) {
                    patch.trimStart = Math.max(
                      0,
                      Number(args.trim_start ?? args.trimStart ?? 0),
                    );
                  }
                  if (args.duration != null) {
                    patch.duration = Math.max(0.15, Number(args.duration));
                  }
                  if (args.start != null) {
                    patch.start = Math.max(0, Number(args.start));
                  }
                  store.update_sfx_clip(targetSfx.id, patch);
                  summary = `Trimmed SFX '${targetSfx.name}'`;
                } else {
                  summary = 'No active SFX clip to trim';
                }
                break;
              }
              case 'delete_sfx_clip': {
                const reqSfxId = String(args.sfx_id || '').trim();
                const curSfx = get().project.tracks.sfx ?? [];
                const exactSfx = curSfx.find((s) => s.id === reqSfxId);
                if (exactSfx) {
                  store.delete_sfx_clip(exactSfx.id);
                  summary = `Deleted SFX '${exactSfx.name}'`;
                } else if (curSfx.length > 0) {
                  const fallbackSfx = curSfx[curSfx.length - 1];
                  store.delete_sfx_clip(fallbackSfx.id);
                  summary = `Deleted SFX '${fallbackSfx.name}'`;
                } else {
                  summary = 'No active SFX clip to delete';
                }
                break;
              }
              case 'set_video_crop': {
                const x = Number(args.x ?? 0);
                const y = Number(args.y ?? 0);
                const w = Number(args.w ?? 1);
                const h = Number(args.h ?? 1);
                const start =
                  args.start != null && Number.isFinite(Number(args.start))
                    ? Number(args.start)
                    : undefined;
                const end =
                  args.end != null && Number.isFinite(Number(args.end))
                    ? Number(args.end)
                    : undefined;
                store.set_video_crop({ x, y, w, h, start, end });
                const isReset = x <= 0.005 && y <= 0.005 && w >= 0.99 && h >= 0.99;
                if (!isReset && start !== undefined && end !== undefined && end > start) {
                  store.setPlayheadPosition(start);
                }
                summary = isReset
                  ? 'Reset video crop to full frame'
                  : start !== undefined && end !== undefined && end > start
                    ? `Cropped frame (${Math.round(w * 100)}% x ${Math.round(h * 100)}% from ${start.toFixed(1)}s to ${end.toFixed(1)}s)`
                    : `Cropped frame (${Math.round(w * 100)}% x ${Math.round(h * 100)}%)`;
                break;
              }
              case 'edit_video_audio': {
                const clipsNow = get().project.tracks.clips;
                const totalDur = Math.max(
                  0.5,
                  clipsNow.reduce((acc, c) => acc + c.duration, 0),
                );
                const ph = get().project.workspaceState.playheadPosition;
                const rawS = Number(args.start ?? ph ?? 0);
                const start = Math.max(0, Math.min(totalDur - 0.15, Number.isFinite(rawS) ? rawS : 0));
                const rawE = Number(args.end ?? Math.min(totalDur, start + 3.0));
                const end = Math.min(
                  totalDur,
                  Math.max(start + 0.15, Number.isFinite(rawE) ? rawE : start + 3.0),
                );
                const muted = Boolean(args.muted ?? true);
                const rawVol = Number(args.volume ?? (muted ? 0 : 0.25));
                const volume = muted ? 0 : Math.max(0, Math.min(2, Number.isFinite(rawVol) ? rawVol : 0.25));
                const label = typeof args.label === 'string' ? args.label : undefined;
                store.add_audio_edit({ start, end, muted, volume, label });
                summary = muted
                  ? `Muted video audio (${start.toFixed(1)}s–${end.toFixed(1)}s)`
                  : `Set video audio ${Math.round(volume * 100)}% (${start.toFixed(1)}s–${end.toFixed(1)}s)`;
                break;
              }
              case 'delete_audio_edit': {
                const reqAudId = String(args.audio_edit_id || '').trim();
                const curEdits = get().project.tracks.audioEdits ?? [];
                if (reqAudId.toLowerCase() === 'all') {
                  store.delete_audio_edit('all');
                  summary = 'Restored all video audio';
                } else {
                  const exact = curEdits.find((ae) => ae.id === reqAudId);
                  if (exact) {
                    store.delete_audio_edit(exact.id);
                    summary = `Restored video audio (${exact.start.toFixed(1)}s–${exact.end.toFixed(1)}s)`;
                  } else if (curEdits.length > 0) {
                    const lastAe = curEdits[curEdits.length - 1];
                    store.delete_audio_edit(lastAe.id);
                    summary = `Restored video audio (${lastAe.start.toFixed(1)}s–${lastAe.end.toFixed(1)}s)`;
                  } else {
                    summary = 'No active video audio edit to remove';
                  }
                }
                break;
              }
              default:
                summary = `Unknown tool: ${tc.name}`;
            }

            if (wasTracking && !pausedForBatch && get().project !== prevProject) {
              useEditorStore.temporal.getState().pause();
              pausedForBatch = true;
            }

            actions.push({
              id: tc.id || makeId('act'),
              toolName: tc.name,
              summary,
              timestamp: new Date().toLocaleTimeString(),
            });
          }
        } finally {
          if (pausedForBatch) {
            useEditorStore.temporal.getState().resume();
          }
        }

        set((state) => ({
          aiActionLog: [...actions, ...state.aiActionLog].slice(0, 20),
        }));
        return actions;
      },

      ingestYoutubeUrl: async (youtubeUrl, initialPrompt) => {
        if (get().isIngesting) return;
        const seq = ++activeIngestSeq;
        const effectivePrompt = (initialPrompt ?? get().ingestPrompt ?? '').trim();
        const isDemo = ['demo://sample', 'sample', 'demo'].includes(youtubeUrl.trim().toLowerCase());
        const useClipOnly = Boolean(effectivePrompt && !isDemo);
        const prevOverlayStyle =
          get().project.workspaceState.overlayStyle ?? DEFAULT_OVERLAY_STYLE;
        const activeCampaign = getCampaignById(get().project.workspaceState.activeCampaignId);
        const campaignPrefix = activeCampaign ? `[${activeCampaign.label}] ` : '';
        set({
          isIngesting: true,
          pendingYoutubeUrl: youtubeUrl,
          statusBanner: useClipOnly
            ? `${campaignPrefix}Scanning captions/heatmap & downloading ONLY matching clip(s) for "${effectivePrompt}" (Low-Storage Mode)...`
            : `${campaignPrefix}Downloading video (<=1080p) & running Groq Whisper transcription...`,
        });
        try {
          const resp = await fetch(`${API_BASE}/api/ingest`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              youtube_url: youtubeUrl,
              initial_prompt: useClipOnly ? effectivePrompt : undefined,
              clip_only: useClipOnly,
              campaign_id: activeCampaign?.id,
              campaign_rules: activeCampaign?.editingRules ?? [],
              overlay_text: activeCampaign?.overlayText,
              overlay_style: prevOverlayStyle,
            }),
          });
          if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            throw new Error(err.detail || 'Ingest failed');
          }
          const data = await resp.json();
          if (seq !== activeIngestSeq) return;
          if (!data?.project?.sourceVideo || !data?.project?.tracks) {
            throw new Error('Malformed project response from ingest endpoint.');
          }
          const newMessages = [...get().aiMessages];
          let nextBanner: string | null = null;
          if (data.ai_message) {
            if (effectivePrompt) {
              newMessages.push({
                id: `u_ingest_${Date.now()}`,
                role: 'user',
                content: effectivePrompt,
                campaignLabel: activeCampaign?.label,
              });
            }
            newMessages.push({
              id: `a_ingest_${Date.now() + 1}`,
              role: 'assistant',
              content: data.ai_message,
              routeUsed: data.route_used || undefined,
            });
            const clipDur = Number(data.project?.sourceVideo?.originalDuration || 0);
            const savedPct =
              typeof data.storage_saved_pct === 'number' ? data.storage_saved_pct : 0;
            nextBanner = `${campaignPrefix}Smart Clip Ingest complete: downloaded only ${clipDur.toFixed(1)}s (saved ~${savedPct}% storage).`;
          }
          const loadedProject: ProjectState = {
            ...data.project,
            workspaceState: {
              ...data.project.workspaceState,
              activeCampaignId:
                data.project?.workspaceState?.activeCampaignId ?? activeCampaign?.id ?? null,
              overlayText:
                data.project?.workspaceState?.overlayText ?? activeCampaign?.overlayText ?? null,
              overlayStyle:
                data.project?.workspaceState?.overlayStyle ?? prevOverlayStyle,
            },
            tracks: {
              clips: recalculateTimeline(data.project?.tracks?.clips ?? []),
              captions: data.project?.tracks?.captions ?? [],
              zooms: data.project?.tracks?.zooms ?? [],
              drawings: data.project?.tracks?.drawings ?? [],
              sfx: data.project?.tracks?.sfx ?? [],
              audioEdits: data.project?.tracks?.audioEdits ?? [],
            },
          };
          if (activeCampaign) {
            const notice = buildCampaignAssistantNotice(
              activeCampaign,
              loadedProject.projectName,
            );
            newMessages.push({
              id: `a_campaign_upload_${Date.now() + 2}`,
              role: 'assistant',
              content: notice.content,
              campaignLabel: activeCampaign.label,
              copyableCaption: notice.copyableCaption,
            });
          }
          set({
            project: loadedProject,
            transcript: data.transcript,
            aiMessages: newMessages,
            isPlaying: false,
            isCropMode: false,
            activeDrawTool: null,
            selectedZoomId: null,
            selectedDrawingId: null,
            selectedSfxId: null,
            selectedAudioEditId: null,
            statusBanner: nextBanner,
          });
          // Clear undo history on fresh ingest so undo only reverts edits on this project
          useEditorStore.temporal.getState().clear();
        } catch (err) {
          set({
            statusBanner: `Ingest error: ${err instanceof Error ? err.message : String(err)}`,
          });
        } finally {
          set({ isIngesting: false });
        }
      },

      runAutoCaptions: async (startTime, endTime) => {
        const state = get();
        const reqProjectId = state.project.projectId;
        if (state.transcript && state.transcript.words?.length) {
          state.generate_auto_captions(startTime, endTime);
          return;
        }
        try {
          const resp = await fetch(`${API_BASE}/api/auto-captions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              project_id: reqProjectId,
              start_time: startTime,
              end_time: endTime,
            }),
          });
          if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            get().setStatusBanner(err.detail || 'Auto captions failed.');
            return;
          }
          const data = await resp.json();
          if (get().project.projectId !== reqProjectId) return;
          get().generate_auto_captions(startTime, endTime, data.captions);
        } catch (err) {
          get().setStatusBanner(
            `Auto captions error: ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      },

      removeDetectedPauses: async (minGap = 0.7) => {
        const state = get();
        const reqProjectId = state.project.projectId;
        if (!reqProjectId || reqProjectId === 'proj_empty') return;
        try {
          const resp = await fetch(`${API_BASE}/api/detect-pauses`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              project_id: reqProjectId,
              min_gap: minGap,
            }),
          });
          if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            get().setStatusBanner(err.detail || 'Pause detection failed.');
            return;
          }
          const data = await resp.json();
          if (get().project.projectId !== reqProjectId) return;
          if (Array.isArray(data.ranges) && data.ranges.length > 0) {
            get().remove_time_ranges(data.ranges);
            get().setStatusBanner(
              `Removed ${data.ranges.length} silence/pause gap(s) >= ${minGap}s.`,
            );
          } else {
            get().setStatusBanner(`No pauses >= ${minGap}s found in transcript.`);
          }
        } catch (err) {
          get().setStatusBanner(
            `Pause detection error: ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      },

      fetchSfxLibrary: async () => {
        set({ isLoadingSfx: true });
        try {
          const resp = await fetch(`${API_BASE}/api/sfx`);
          if (!resp.ok) return;
          const data = await resp.json();
          const items = Array.isArray(data.items) ? (data.items as SfxAsset[]) : [];
          set({ sfxLibrary: items });
        } catch {
          // Ignore network glitches during background library poll
        } finally {
          set({ isLoadingSfx: false });
        }
      },

      uploadSfxFiles: async (files, tags) => {
        const list = Array.from(files);
        if (list.length === 0) return [];
        set({ isLoadingSfx: true });
        const uploaded: SfxAsset[] = [];
        try {
          for (const file of list) {
            const fd = new FormData();
            fd.append('file', file);
            if (tags && tags.length > 0) {
              fd.append('tags', tags.join(','));
            }
            const resp = await fetch(`${API_BASE}/api/sfx/upload`, {
              method: 'POST',
              body: fd,
            });
            if (!resp.ok) {
              const err = await resp.json().catch(() => ({ detail: resp.statusText }));
              throw new Error(err.detail || `Failed uploading ${file.name}`);
            }
            const data = await resp.json();
            if (data.asset) {
              uploaded.push(data.asset as SfxAsset);
            }
          }
          await get().fetchSfxLibrary();
          if (uploaded.length > 0) {
            set({
              statusBanner: `Uploaded ${uploaded.length} custom SFX file(s) to your library.`,
            });
          }
          return uploaded;
        } catch (err) {
          set({
            statusBanner: `SFX upload error: ${err instanceof Error ? err.message : String(err)}`,
          });
          return uploaded;
        } finally {
          set({ isLoadingSfx: false });
        }
      },

      updateSfxAssetMeta: async (sfxId, patch) => {
        try {
          const resp = await fetch(`${API_BASE}/api/sfx/${encodeURIComponent(sfxId)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(patch),
          });
          if (!resp.ok) return;
          const data = await resp.json();
          if (data.asset) {
            const updated = data.asset as SfxAsset;
            set((state) => ({
              sfxLibrary: state.sfxLibrary.map((a) => (a.id === sfxId ? updated : a)),
            }));
          }
        } catch {
          // Non-fatal
        }
      },

      deleteSfxAssetFromLibrary: async (sfxId) => {
        try {
          const resp = await fetch(`${API_BASE}/api/sfx/${encodeURIComponent(sfxId)}`, {
            method: 'DELETE',
          });
          if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            set({ statusBanner: err.detail || 'Failed to delete SFX asset.' });
            return;
          }
          set((state) => {
            const curSfx = state.project.tracks.sfx ?? [];
            const nextSfx = curSfx.filter((sc) => sc.assetId !== sfxId);
            const selectedWasRemoved =
              state.selectedSfxId !== null &&
              !nextSfx.some((sc) => sc.id === state.selectedSfxId);
            return {
              sfxLibrary: state.sfxLibrary.filter((a) => a.id !== sfxId),
              selectedSfxId: selectedWasRemoved ? null : state.selectedSfxId,
              project:
                nextSfx.length === curSfx.length
                  ? state.project
                  : {
                      ...state.project,
                      tracks: {
                        ...state.project.tracks,
                        sfx: nextSfx,
                      },
                    },
            };
          });
        } catch (err) {
          set({
            statusBanner: `Delete SFX error: ${err instanceof Error ? err.message : String(err)}`,
          });
        }
      },
    }),
    {
      partialize: (state) => ({ project: state.project }),
      equality: (pastState, currentState) => {
        const a = pastState.project;
        const b = currentState.project;
        return (
          a.projectId === b.projectId &&
          a.projectName === b.projectName &&
          a.tracks === b.tracks &&
          a.captionStyle === b.captionStyle &&
          a.workspaceState.targetAspectRatio === b.workspaceState.targetAspectRatio &&
          a.workspaceState.activeCampaignId === b.workspaceState.activeCampaignId &&
          a.workspaceState.overlayText === b.workspaceState.overlayText &&
          a.workspaceState.overlayStyle === b.workspaceState.overlayStyle &&
          a.workspaceState.crop === b.workspaceState.crop
        );
      },
      limit: 50,
    },
  ),
);
