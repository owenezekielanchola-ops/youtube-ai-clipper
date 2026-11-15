import { create } from 'zustand';
import { temporal } from 'zundo';
import type {
  AiActionLogItem,
  AiChatMessage,
  AspectRatio,
  CachedTranscript,
  CaptionItem,
  ChangeCaptionStyleArgs,
  ClipItem,
  HighlightClipInput,
  ModelStatus,
  ProjectState,
  TimeRange,
  ToolCall,
} from '../types/editor';

export const API_BASE = 'http://localhost:8000';

const makeId = (prefix: string) =>
  `${prefix}_${Math.random().toString(36).slice(2, 10)}`;

function recalculateTimeline(clips: ClipItem[]): ClipItem[] {
  let cursor = 0;
  return clips.map((clip) => {
    const start = Math.max(0, Number(clip.sourceStart.toFixed(3)));
    const end = Math.max(start + 0.1, Number(clip.sourceEnd.toFixed(3)));
    const duration = Number((end - start).toFixed(3));
    const updated: ClipItem = {
      ...clip,
      sourceStart: start,
      sourceEnd: end,
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
    },
    tracks: {
      clips: [],
      captions: [],
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
    availableGroqModels: toStringArray(data.available_groq_models),
    availableGeminiModels: toStringArray(data.available_gemini_models),
    lastRefreshedIso: typeof data.last_refreshed_iso === 'string' ? data.last_refreshed_iso : null,
    nextRefreshInSeconds:
      typeof data.next_refresh_in_seconds === 'number' ? data.next_refresh_in_seconds : 86400,
    groqConfigured: Boolean(data.groq_configured),
    geminiConfigured: Boolean(data.gemini_configured),
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

  // Playback & UI setters (excluded from undo history via zundo partialize)
  setPlayheadPosition: (pos: number) => void;
  setIsPlaying: (playing: boolean) => void;
  setSelectedClipIds: (ids: string[]) => void;
  setTimelineZoom: (zoom: number) => void;
  setProjectName: (name: string) => void;
  setStatusBanner: (msg: string | null) => void;
  setPendingYoutubeUrl: (url: string) => void;
  setIngestPrompt: (prompt: string) => void;
  loadProjectState: (project: ProjectState, transcript?: CachedTranscript | null) => void;
  setIsIngesting: (val: boolean) => void;
  setIsAiThinking: (val: boolean) => void;
  appendAiMessage: (msg: AiChatMessage) => void;

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
  add_caption: (start: number, end: number, text: string) => void;
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

      setPendingYoutubeUrl: (url) => set({ pendingYoutubeUrl: url }),
      setIngestPrompt: (prompt) => set({ ingestPrompt: prompt }),

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
          statusBanner: 'Reloading API keys & scanning available Groq/Gemini models...',
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
          set({
            modelStatus: mapped,
            isRefreshingModels: false,
            statusBanner: `AI models refreshed (${groqCount} Groq, ${geminiCount} Gemini live; primary: ${mapped.primaryToolModel ?? 'default'}).`,
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

      loadProjectState: (project, transcript = null) =>
        set({
          project,
          transcript,
          isPlaying: false,
        }),

      setIsIngesting: (val) => set({ isIngesting: val }),
      setIsAiThinking: (val) => set({ isAiThinking: val }),

      appendAiMessage: (msg) =>
        set((state) => ({ aiMessages: [...state.aiMessages, msg] })),

      // 1. isolate_highlight_clips
      isolate_highlight_clips: (clips) =>
        set((state) => {
          const maxDur = state.project.sourceVideo.originalDuration || 99999;
          const rawClips: ClipItem[] = clips
            .filter((c) => c.source_end > c.source_start + 0.2)
            .map((c, idx) => {
              const s = Math.max(0, c.source_start);
              const e = Math.min(maxDur, c.source_end);
              return {
                id: makeId('clip'),
                title: c.title || `Highlight ${idx + 1}`,
                sourceStart: s,
                sourceEnd: e,
                timelineStart: 0,
                duration: e - s,
                viralityScore: c.virality_score,
                reason: c.reason,
              };
            });

          const nextClips = recalculateTimeline(rawClips);
          return {
            project: {
              ...state.project,
              workspaceState: {
                ...state.project.workspaceState,
                playheadPosition: 0,
                selectedClipIds: nextClips.length > 0 ? [nextClips[0].id] : [],
              },
              tracks: {
                ...state.project.tracks,
                clips: nextClips,
                captions: pruneOrphanedAutoCaptions(
                  state.project.tracks.captions,
                  nextClips,
                ),
              },
            },
          };
        }),

      // 2. trim_clip
      trim_clip: (clip_id, new_source_start, new_source_end) =>
        set((state) => {
          const maxDur = state.project.sourceVideo.originalDuration || 99999;
          const updated = state.project.tracks.clips.map((clip) => {
            if (clip.id !== clip_id) return clip;
            const s = Math.max(0, Math.min(new_source_start, maxDur - 0.2));
            const e = Math.min(maxDur, Math.max(s + 0.2, new_source_end));
            return { ...clip, sourceStart: s, sourceEnd: e };
          });
          return {
            project: {
              ...state.project,
              tracks: {
                ...state.project.tracks,
                clips: recalculateTimeline(updated),
              },
            },
          };
        }),

      // 3. split_clip
      split_clip: (clip_id, split_timestamp) =>
        set((state) => {
          const nextClips: ClipItem[] = [];
          let newlyCreatedId: string | null = null;

          for (const clip of state.project.tracks.clips) {
            if (clip.id !== clip_id) {
              nextClips.push(clip);
              continue;
            }
            const clipEndTl = clip.timelineStart + clip.duration;
            // Support either timeline timestamp inside clip OR source timestamp fallback
            let offset = split_timestamp - clip.timelineStart;
            if (split_timestamp <= clip.timelineStart || split_timestamp >= clipEndTl) {
              if (split_timestamp > clip.sourceStart && split_timestamp < clip.sourceEnd) {
                offset = split_timestamp - clip.sourceStart;
              }
            }

            if (offset <= 0.15 || offset >= clip.duration - 0.15) {
              nextClips.push(clip);
              continue;
            }

            const splitSource = Number((clip.sourceStart + offset).toFixed(3));
            const rightId = makeId('clip');
            newlyCreatedId = rightId;

            nextClips.push({
              ...clip,
              sourceEnd: splitSource,
              title: `${clip.title} (A)`,
            });
            nextClips.push({
              id: rightId,
              title: `${clip.title} (B)`,
              sourceStart: splitSource,
              sourceEnd: clip.sourceEnd,
              timelineStart: 0,
              duration: clip.sourceEnd - splitSource,
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
          const idSet = new Set(clip_ids);
          const remaining = recalculateTimeline(
            state.project.tracks.clips.filter((c) => !idSet.has(c.id)),
          );
          const totalDur = remaining.reduce((acc, c) => acc + c.duration, 0);
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
              },
              tracks: {
                ...state.project.tracks,
                clips: remaining,
                captions: pruneOrphanedAutoCaptions(
                  state.project.tracks.captions,
                  remaining,
                ),
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

          // Only merge truly contiguous source clips (or directly adjacent if overlapping/close)
          if (c2.sourceStart < c1.sourceStart || c2.sourceStart - c1.sourceEnd > 0.5) {
            return {
              statusBanner:
                'Can only merge adjacent clips that are contiguous in the source video.',
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
          const clips = [...state.project.tracks.clips];
          const oldIdx = clips.findIndex((c) => c.id === clip_id);
          if (oldIdx === -1) return state;
          const clampedNewIdx = Math.max(0, Math.min(clips.length - 1, new_index));
          if (oldIdx === clampedNewIdx) return state;

          const [moved] = clips.splice(oldIdx, 1);
          clips.splice(clampedNewIdx, 0, moved);

          return {
            project: {
              ...state.project,
              tracks: {
                ...state.project.tracks,
                clips: recalculateTimeline(clips),
              },
            },
          };
        }),

      // 7. remove_time_ranges
      remove_time_ranges: (ranges) =>
        set((state) => {
          if (!ranges || ranges.length === 0) return state;
          let sortedRanges = [...ranges]
            .filter((r) => r.end > r.start)
            .sort((a, b) => a.start - b.start);

          const anyOverlap = state.project.tracks.clips.some((clip) =>
            sortedRanges.some(
              (rem) => rem.end > clip.sourceStart && rem.start < clip.sourceEnd,
            ),
          );
          if (
            !anyOverlap &&
            sortedRanges.length === 1 &&
            sortedRanges[0].start === 0 &&
            state.project.tracks.clips.length > 0
          ) {
            const firstClip = state.project.tracks.clips[0];
            if (firstClip.sourceStart > 0) {
              sortedRanges = [
                {
                  start: firstClip.sourceStart,
                  end: firstClip.sourceStart + sortedRanges[0].end,
                },
              ];
            }
          }

          const nextClips: ClipItem[] = [];
          for (const clip of state.project.tracks.clips) {
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
          return {
            project: {
              ...state.project,
              workspaceState: {
                ...state.project.workspaceState,
                playheadPosition: Math.min(
                  state.project.workspaceState.playheadPosition,
                  totalDur,
                ),
              },
              tracks: {
                ...state.project.tracks,
                clips: recalculated,
                captions: pruneOrphanedAutoCaptions(
                  state.project.tracks.captions,
                  recalculated,
                ),
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
      add_caption: (start, end, text) =>
        set((state) => {
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
            highlightedWords: [],
            words: words.map((w, idx) => ({
              word: w,
              start: Number((s + idx * perWord).toFixed(3)),
              end: Number((s + (idx + 1) * perWord).toFixed(3)),
            })),
          };
          const next = [...state.project.tracks.captions, newCap].sort(
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
          const combinedWords = [...cur.words, ...nxt.words];
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
          return {
            project: {
              ...state.project,
              captionStyle: {
                preset: args.preset ?? cur.preset,
                fontFamily: args.font_family ?? cur.fontFamily,
                fontSize: args.font_size ?? cur.fontSize,
                textColor: args.text_color ?? cur.textColor,
                highlightColor: args.highlight_color ?? cur.highlightColor,
                position: args.position ?? cur.position,
                isBold: args.is_bold ?? cur.isBold,
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

      // Shared AI Tool Call Dispatcher -> executes the exact same 13 functions
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
                const clips = (args.clips as HighlightClipInput[]) || [];
                store.isolate_highlight_clips(clips);
                summary = `Isolated ${clips.length} viral clip(s)`;
                break;
              }
              case 'trim_clip': {
                store.trim_clip(
                  String(args.clip_id),
                  Number(args.new_source_start),
                  Number(args.new_source_end),
                );
                summary = `Trimmed clip ${String(args.clip_id)}`;
                break;
              }
              case 'split_clip': {
                store.split_clip(String(args.clip_id), Number(args.split_timestamp));
                summary = `Split clip at ${Number(args.split_timestamp).toFixed(1)}s`;
                break;
              }
              case 'delete_clips': {
                const ids = (args.clip_ids as string[]) || [];
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
                store.reorder_clips(String(args.clip_id), Number(args.new_index));
                summary = `Reordered clip to #${Number(args.new_index) + 1}`;
                break;
              }
              case 'remove_time_ranges': {
                const ranges = (args.ranges as TimeRange[]) || [];
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
                store.add_caption(
                  Number(args.start),
                  Number(args.end),
                  String(args.text || ''),
                );
                summary = 'Added caption';
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
                summary = `Updated caption style${args.preset ? ` (${String(args.preset)})` : ''}`;
                break;
              }
              case 'change_video_format': {
                const ratio = (args.aspect_ratio as AspectRatio) || '16:9';
                store.change_video_format(ratio);
                summary = `Changed format to ${ratio}`;
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
        const effectivePrompt = (initialPrompt ?? get().ingestPrompt ?? '').trim();
        const isDemo = ['demo://sample', 'sample', 'demo'].includes(youtubeUrl.trim().toLowerCase());
        const useClipOnly = Boolean(effectivePrompt && !isDemo);
        set({
          isIngesting: true,
          pendingYoutubeUrl: youtubeUrl,
          statusBanner: useClipOnly
            ? `Scanning audio + transcript & downloading ONLY matching clip(s) for "${effectivePrompt}" (Low-Storage Mode)...`
            : 'Downloading video (<=1080p) & running Groq Whisper transcription...',
        });
        try {
          const resp = await fetch(`${API_BASE}/api/ingest`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              youtube_url: youtubeUrl,
              initial_prompt: useClipOnly ? effectivePrompt : undefined,
              clip_only: useClipOnly,
            }),
          });
          if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            throw new Error(err.detail || 'Ingest failed');
          }
          const data = await resp.json();
          const newMessages = [...get().aiMessages];
          let nextBanner: string | null = null;
          if (data.ai_message) {
            if (effectivePrompt) {
              newMessages.push({
                id: `u_ingest_${Date.now()}`,
                role: 'user',
                content: effectivePrompt,
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
            nextBanner = `Smart Clip Ingest complete: downloaded only ${clipDur.toFixed(1)}s (saved ~${savedPct}% storage).`;
          }
          set({
            project: data.project,
            transcript: data.transcript,
            aiMessages: newMessages,
            isPlaying: false,
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
        if (state.transcript && state.transcript.words?.length) {
          state.generate_auto_captions(startTime, endTime);
          return;
        }
        const resp = await fetch(`${API_BASE}/api/auto-captions`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            project_id: state.project.projectId,
            start_time: startTime,
            end_time: endTime,
          }),
        });
        if (resp.ok) {
          const data = await resp.json();
          state.generate_auto_captions(startTime, endTime, data.captions);
        }
      },

      removeDetectedPauses: async (minGap = 0.7) => {
        const state = get();
        if (!state.project.projectId || state.project.projectId === 'proj_empty') return;
        const resp = await fetch(`${API_BASE}/api/detect-pauses`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            project_id: state.project.projectId,
            min_gap: minGap,
          }),
        });
        if (resp.ok) {
          const data = await resp.json();
          if (Array.isArray(data.ranges) && data.ranges.length > 0) {
            state.remove_time_ranges(data.ranges);
            state.setStatusBanner(
              `Removed ${data.ranges.length} silence/pause gap(s) >= ${minGap}s.`,
            );
          } else {
            state.setStatusBanner(`No pauses >= ${minGap}s found in transcript.`);
          }
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
          a.workspaceState.targetAspectRatio === b.workspaceState.targetAspectRatio
        );
      },
      limit: 50,
    },
  ),
);
