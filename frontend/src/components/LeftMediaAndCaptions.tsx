import React, { useMemo } from 'react';
import {
  Captions,
  Flame,
  Lock,
  Merge,
  Plus,
  Scissors,
  Sparkles,
  Trash2,
} from 'lucide-react';
import { useEditorStore } from '../store/useEditorStore';

function formatSec(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = (sec % 60).toFixed(1).padStart(4, '0');
  return `${m}:${s}`;
}

export const LeftMediaAndCaptions: React.FC = () => {
  const project = useEditorStore((s) => s.project);
  const runAutoCaptions = useEditorStore((s) => s.runAutoCaptions);
  const removeDetectedPauses = useEditorStore((s) => s.removeDetectedPauses);
  const remove_time_ranges = useEditorStore((s) => s.remove_time_ranges);
  const isolate_highlight_clips = useEditorStore((s) => s.isolate_highlight_clips);
  const add_caption = useEditorStore((s) => s.add_caption);
  const delete_caption = useEditorStore((s) => s.delete_caption);
  const combine_captions = useEditorStore((s) => s.combine_captions);
  const edit_caption = useEditorStore((s) => s.edit_caption);
  const adjust_caption_timing = useEditorStore((s) => s.adjust_caption_timing);
  const setPlayheadPosition = useEditorStore((s) => s.setPlayheadPosition);

  const topPeaks = useMemo(() => {
    const pts = [...(project.sourceVideo.heatmap || [])];
    pts.sort((a, b) => b.value - a.value);
    const chosen: typeof pts = [];
    for (const p of pts) {
      if (!chosen.some((c) => Math.abs(c.start_time - p.start_time) < 10)) {
        chosen.push(p);
      }
      if (chosen.length >= 3) break;
    }
    return chosen;
  }, [project.sourceVideo.heatmap]);

  const hasVideo = Boolean(project.sourceVideo.videoUrl);

  const handleDeleteCaption = (captionId: string) => {
    delete_caption(captionId);
  };

  const handleCombineWithNext = (idx: number) => {
    const caps = project.tracks.captions;
    if (idx < 0 || idx >= caps.length - 1) return;
    combine_captions(caps[idx].id, caps[idx + 1].id);
  };

  const toggleHighlightWord = (
    capId: string,
    text: string,
    existingHighlights: string[],
    wordToken: string,
  ) => {
    const clean = wordToken.replace(/[^\w']/g, '');
    if (!clean) return;
    const exists = existingHighlights.some(
      (h) => h.toLowerCase() === clean.toLowerCase(),
    );
    const next = exists
      ? existingHighlights.filter((h) => h.toLowerCase() !== clean.toLowerCase())
      : [...existingHighlights, clean];
    edit_caption(capId, text, next, true);
  };

  const handleAddCaptionAtPlayhead = () => {
    const firstClip = project.tracks.clips[0];
    const playhead = project.workspaceState.playheadPosition;
    // Map timeline playhead to source time
    let sourcePos = firstClip ? firstClip.sourceStart : 0;
    for (const c of project.tracks.clips) {
      if (playhead >= c.timelineStart && playhead <= c.timelineStart + c.duration) {
        sourcePos = c.sourceStart + (playhead - c.timelineStart);
        break;
      }
    }
    add_caption(sourcePos, sourcePos + 2.5, 'New custom caption');
  };

  return (
    <aside className="w-80 border-r border-zinc-800 bg-zinc-950 flex flex-col min-h-0 shrink-0">
      {/* Source Video Metadata */}
      <div className="p-3 border-b border-zinc-800 space-y-2.5">
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
            Source Media
          </span>
          {project.sourceVideo.originalDuration > 0 && (
            <span className="text-[11px] font-mono text-zinc-400">
              {formatSec(project.sourceVideo.originalDuration)}
            </span>
          )}
        </div>

        <div className="flex gap-2.5 items-center">
          {project.sourceVideo.thumbnail ? (
            <img
              src={project.sourceVideo.thumbnail}
              alt={project.sourceVideo.title}
              className="w-16 h-10 object-cover rounded border border-zinc-800 shrink-0"
            />
          ) : (
            <div className="w-16 h-10 rounded bg-zinc-900 border border-zinc-800 flex items-center justify-center text-[10px] text-zinc-500 shrink-0">
              1080p
            </div>
          )}
          <div className="min-w-0">
            <p className="text-xs font-medium text-zinc-100 line-clamp-2">
              {project.sourceVideo.title}
            </p>
          </div>
        </div>

        {/* Heatmap Peak Quick Isolation */}
        {topPeaks.length > 0 && (
          <div className="space-y-1">
            <div className="text-[10px] font-medium text-amber-400 flex items-center gap-1">
              <Flame className="w-3 h-3" />
              Most Replayed Heatmap Peaks
            </div>
            <div className="flex flex-wrap gap-1">
              {topPeaks.map((pk, idx) => {
                const start = Math.max(0, Number((pk.start_time - 6).toFixed(1)));
                const end = Math.min(
                  project.sourceVideo.originalDuration,
                  Number((pk.end_time + 14).toFixed(1)),
                );
                return (
                  <button
                    key={idx}
                    type="button"
                    onClick={() =>
                      isolate_highlight_clips([
                        {
                          source_start: start,
                          source_end: end,
                          title: `Heatmap Peak #${idx + 1}`,
                          reason: `Viewer replay intensity ${(pk.value * 100).toFixed(0)}%`,
                          virality_score: Number((7.5 + pk.value * 2.4).toFixed(1)),
                        },
                      ])
                    }
                    className="px-2 py-1 rounded bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-[10px] text-zinc-300 font-mono"
                  >
                    #{idx + 1} {formatSec(pk.start_time)} ({Math.round(pk.value * 100)}%)
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Quick Manual Actions (Shared Engine Tools) */}
        <div className="grid grid-cols-3 gap-1.5 pt-1">
          <button
            type="button"
            disabled={!hasVideo}
            onClick={() => runAutoCaptions()}
            className="px-2 py-1.5 rounded bg-zinc-900 hover:bg-zinc-800 disabled:opacity-40 border border-zinc-800 text-[11px] text-zinc-200 flex items-center justify-center gap-1"
            title="Generate auto-captions from Groq Whisper (preserves manually edited captions)"
          >
            <Captions className="w-3 h-3 text-amber-400" />
            Captions
          </button>
          <button
            type="button"
            disabled={!hasVideo}
            onClick={() => removeDetectedPauses(0.7)}
            className="px-2 py-1.5 rounded bg-zinc-900 hover:bg-zinc-800 disabled:opacity-40 border border-zinc-800 text-[11px] text-zinc-200 flex items-center justify-center gap-1"
            title="Remove pauses > 0.7s detected by Groq Whisper"
          >
            <Sparkles className="w-3 h-3 text-emerald-400" />
            No Pause
          </button>
          <button
            type="button"
            disabled={!hasVideo}
            onClick={() => {
              const firstStart = project.tracks.clips[0]?.sourceStart ?? 0;
              remove_time_ranges([{ start: firstStart, end: firstStart + 20 }]);
            }}
            className="px-2 py-1.5 rounded bg-zinc-900 hover:bg-zinc-800 disabled:opacity-40 border border-zinc-800 text-[11px] text-zinc-200 flex items-center justify-center gap-1"
            title="Cut the first 20 seconds"
          >
            <Scissors className="w-3 h-3 text-sky-400" />
            -20s Intro
          </button>
        </div>
      </div>

      {/* Manual Caption Editor Header */}
      <div className="px-3 py-2 border-b border-zinc-800 flex items-center justify-between">
        <div>
          <h3 className="text-xs font-semibold text-zinc-200">
            Manual Caption Editor
          </h3>
          <p className="text-[10px] text-zinc-500">
            Click words to highlight • Edits lock from AI overwrite
          </p>
        </div>
        <button
          type="button"
          disabled={!hasVideo}
          onClick={handleAddCaptionAtPlayhead}
          className="px-2 py-1 rounded bg-amber-400/15 hover:bg-amber-400/25 disabled:opacity-40 text-amber-300 text-[11px] font-medium flex items-center gap-1"
        >
          <Plus className="w-3 h-3" />
          Add
        </button>
      </div>

      {/* Caption List */}
      <div className="flex-1 overflow-y-auto p-2.5 space-y-2">
        {project.tracks.captions.length === 0 ? (
          <div className="text-center py-10 px-3 space-y-2">
            <Captions className="w-6 h-6 text-zinc-700 mx-auto" />
            <p className="text-xs text-zinc-500">
              No captions yet. Click <span className="text-zinc-300">Captions</span> above or ask the AI assistant to generate them.
            </p>
          </div>
        ) : (
          project.tracks.captions.map((cap, idx) => (
            <div
              key={cap.id}
              className={`rounded-lg border p-2.5 space-y-2 transition ${
                cap.isManuallyEdited
                  ? 'bg-zinc-900/90 border-amber-500/40'
                  : 'bg-zinc-900/40 border-zinc-800'
              }`}
            >
              {/* Timing + Lock Badge + Actions */}
              <div className="flex items-center justify-between gap-1">
                <div className="flex items-center gap-1">
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    value={cap.start}
                    onChange={(e) =>
                      adjust_caption_timing(
                        cap.id,
                        parseFloat(e.target.value) || 0,
                        cap.end,
                        true,
                      )
                    }
                    className="w-14 bg-zinc-950 border border-zinc-800 rounded px-1.5 py-0.5 text-[10px] font-mono text-zinc-300 outline-none focus:border-amber-400"
                  />
                  <span className="text-[10px] text-zinc-600">→</span>
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    value={cap.end}
                    onChange={(e) =>
                      adjust_caption_timing(
                        cap.id,
                        cap.start,
                        parseFloat(e.target.value) || cap.start + 0.5,
                        true,
                      )
                    }
                    className="w-14 bg-zinc-950 border border-zinc-800 rounded px-1.5 py-0.5 text-[10px] font-mono text-zinc-300 outline-none focus:border-amber-400"
                  />
                </div>

                <div className="flex items-center gap-1">
                  {cap.isManuallyEdited && (
                    <span
                      title="Manually edited — protected from AI overwrite"
                      className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 text-[9px] font-medium flex items-center gap-0.5"
                    >
                      <Lock className="w-2.5 h-2.5" />
                      Edited
                    </span>
                  )}
                  {idx < project.tracks.captions.length - 1 && (
                    <button
                      type="button"
                      onClick={() => handleCombineWithNext(idx)}
                      title="Combine with next caption"
                      className="p-1 rounded hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200"
                    >
                      <Merge className="w-3 h-3" />
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => handleDeleteCaption(cap.id)}
                    title="Delete caption"
                    className="p-1 rounded hover:bg-red-500/20 text-zinc-400 hover:text-red-400"
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                </div>
              </div>

              {/* Text Input */}
              <textarea
                rows={2}
                value={cap.text}
                onFocus={() => {
                  const clip = project.tracks.clips.find(
                    (c) => c.sourceEnd >= cap.start && c.sourceStart <= cap.end,
                  );
                  if (clip) {
                    const clampedSrc = Math.max(
                      clip.sourceStart,
                      Math.min(clip.sourceEnd, cap.start),
                    );
                    setPlayheadPosition(
                      clip.timelineStart + (clampedSrc - clip.sourceStart),
                    );
                  }
                }}
                onChange={(e) =>
                  edit_caption(cap.id, e.target.value, cap.highlightedWords, true)
                }
                className="w-full bg-zinc-950 border border-zinc-800 focus:border-amber-400/70 rounded p-1.5 text-xs text-zinc-100 resize-none outline-none"
              />

              {/* Interactive Word Highlight Toggles */}
              <div className="flex flex-wrap gap-1">
                {cap.text
                  .split(/\s+/)
                  .filter(Boolean)
                  .map((w, wi) => {
                    const clean = w.replace(/[^\w']/g, '').toLowerCase();
                    const isHl = cap.highlightedWords.some(
                      (hw) => hw.toLowerCase() === clean,
                    );
                    return (
                      <button
                        key={`${cap.id}_w_${wi}`}
                        type="button"
                        onClick={() =>
                          toggleHighlightWord(
                            cap.id,
                            cap.text,
                            cap.highlightedWords,
                            w,
                          )
                        }
                        className={`px-1.5 py-0.5 rounded text-[10px] transition ${
                          isHl
                            ? 'bg-amber-400 text-zinc-950 font-semibold'
                            : 'bg-zinc-800 text-zinc-400 hover:text-zinc-200'
                        }`}
                      >
                        {w}
                      </button>
                    );
                  })}
              </div>
            </div>
          ))
        )}
      </div>
    </aside>
  );
};
