import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Captions,
  ChevronDown,
  ChevronUp,
  Flame,
  Lock,
  Merge,
  Music,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Scissors,
  Search,
  Sparkles,
  Trash2,
  Upload,
  Volume2,
} from 'lucide-react';
import { useEditorStore } from '../store/useEditorStore';
import type { SfxAsset } from '../types/editor';

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
  const sfxLibrary = useEditorStore((s) => s.sfxLibrary);
  const isLoadingSfx = useEditorStore((s) => s.isLoadingSfx);
  const fetchSfxLibrary = useEditorStore((s) => s.fetchSfxLibrary);
  const uploadSfxFiles = useEditorStore((s) => s.uploadSfxFiles);
  const updateSfxAssetMeta = useEditorStore((s) => s.updateSfxAssetMeta);
  const deleteSfxAssetFromLibrary = useEditorStore((s) => s.deleteSfxAssetFromLibrary);
  const add_sfx_clip = useEditorStore((s) => s.add_sfx_clip);

  const [leftTab, setLeftTab] = useState<'captions' | 'sfx'>('captions');
  const [showSourceDetails, setShowSourceDetails] = useState(true);
  const [sfxSearch, setSfxSearch] = useState('');
  const [playingAssetId, setPlayingAssetId] = useState<string | null>(null);
  const [tagDrafts, setTagDrafts] = useState<Record<string, string>>({});
  const [isDragOver, setIsDragOver] = useState(false);

  const auditionAudioRef = useRef<HTMLAudioElement | null>(null);
  const sfxInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    void fetchSfxLibrary();
    return () => {
      if (auditionAudioRef.current) {
        auditionAudioRef.current.pause();
        auditionAudioRef.current = null;
      }
    };
  }, [fetchSfxLibrary]);

  const stopAudition = () => {
    if (auditionAudioRef.current) {
      auditionAudioRef.current.pause();
      auditionAudioRef.current = null;
    }
    setPlayingAssetId(null);
  };

  const toggleAudition = (asset: SfxAsset) => {
    if (playingAssetId === asset.id) {
      stopAudition();
      return;
    }
    stopAudition();
    const audio = new Audio(asset.url);
    audio.volume = 0.85;
    audio.onended = () => setPlayingAssetId(null);
    auditionAudioRef.current = audio;
    setPlayingAssetId(asset.id);
    audio.play().catch(() => setPlayingAssetId(null));
  };

  const filteredSfx = useMemo(() => {
    const q = sfxSearch.trim().toLowerCase();
    if (!q) return sfxLibrary;
    return sfxLibrary.filter(
      (a) =>
        a.name.toLowerCase().includes(q) ||
        a.tags.some((t) => t.toLowerCase().includes(q)) ||
        (a.description || '').toLowerCase().includes(q),
    );
  }, [sfxLibrary, sfxSearch]);

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
    // Map timeline playhead to source time (accounting for clip speed)
    let sourcePos = firstClip ? firstClip.sourceStart : 0;
    for (const c of project.tracks.clips) {
      if (playhead >= c.timelineStart && playhead <= c.timelineStart + c.duration) {
        const spd = Math.max(0.25, Math.min(4.0, c.speed ?? 1));
        sourcePos = c.sourceStart + (playhead - c.timelineStart) * spd;
        break;
      }
    }
    add_caption(sourcePos, sourcePos + 2.5, 'New custom caption');
  };

  return (
    <aside className="w-72 xl:w-80 border-r border-[#272f40] bg-[#151922] flex flex-col min-h-0 shrink-0">
      {/* Compact Collapsible Source Media Card */}
      <div className="p-2.5 border-b border-[#272f40] space-y-2 shrink-0">
        <div className="flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={() => setShowSourceDetails((prev) => !prev)}
            className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400 hover:text-slate-200 transition cursor-pointer"
            title={showSourceDetails ? 'Collapse Source Media card' : 'Expand Source Media card'}
          >
            {showSourceDetails ? (
              <ChevronUp className="w-3.5 h-3.5 text-slate-400" />
            ) : (
              <ChevronDown className="w-3.5 h-3.5 text-amber-300" />
            )}
            <span>Source Media</span>
          </button>
          <div className="flex items-center gap-1.5 min-w-0">
            {!showSourceDetails && project.sourceVideo.title && (
              <span className="text-[11px] text-slate-400 truncate max-w-[110px]">
                {project.sourceVideo.title}
              </span>
            )}
            {project.sourceVideo.originalDuration > 0 && (
              <span className="text-[11px] font-mono tabular-nums px-1.5 py-0.5 rounded bg-[#1c212d] border border-[#272f40] text-slate-300 shrink-0">
                {formatSec(project.sourceVideo.originalDuration)}
              </span>
            )}
          </div>
        </div>

        {showSourceDetails && (
          <>
            <div className="flex gap-2 items-center bg-[#1c212d] border border-[#272f40] rounded-xl p-1.5">
              {project.sourceVideo.thumbnail ? (
                <img
                  src={project.sourceVideo.thumbnail}
                  alt={project.sourceVideo.title}
                  className="w-12 h-8 object-cover rounded-lg border border-[#272f40] shrink-0"
                />
              ) : (
                <div className="w-12 h-8 rounded-lg bg-[#12161f] border border-[#272f40] flex items-center justify-center text-[10px] font-medium text-slate-500 shrink-0">
                  Video
                </div>
              )}
              <div className="min-w-0">
                <p
                  className="text-xs font-medium text-slate-100 leading-snug line-clamp-1"
                  title={project.sourceVideo.title}
                >
                  {project.sourceVideo.title}
                </p>
              </div>
            </div>

            {/* Heatmap Peak Quick Isolation */}
            {topPeaks.length > 0 && (
              <div className="space-y-1">
                <div className="text-[10px] font-medium text-amber-300 flex items-center gap-1">
                  <Flame className="w-3 h-3" />
                  Most Replayed Peaks
                </div>
                <div className="flex flex-wrap gap-1 max-h-14 overflow-y-auto">
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
                        className="px-1.5 py-0.5 rounded-md bg-[#1c212d] hover:bg-[#232a39] border border-[#272f40] text-[10px] text-slate-200 font-mono tabular-nums transition"
                      >
                        #{idx + 1} {formatSec(pk.start_time)} ({Math.round(pk.value * 100)}%)
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Quick Clip Actions */}
            <div className="grid grid-cols-3 gap-1">
              <button
                type="button"
                disabled={!hasVideo}
                onClick={() => runAutoCaptions()}
                className="px-1.5 py-1.5 rounded-lg bg-[#1c212d] hover:bg-[#232a39] disabled:opacity-40 border border-[#272f40] text-[11px] font-medium text-slate-200 flex items-center justify-center gap-1 transition"
                title="Generate auto-captions from speech (preserves locked manual edits)"
              >
                <Captions className="w-3 h-3 text-amber-300 shrink-0" />
                <span>Captions</span>
              </button>
              <button
                type="button"
                disabled={!hasVideo}
                onClick={() => removeDetectedPauses(0.7)}
                className="px-1.5 py-1.5 rounded-lg bg-[#1c212d] hover:bg-[#232a39] disabled:opacity-40 border border-[#272f40] text-[11px] font-medium text-slate-200 flex items-center justify-center gap-1 transition"
                title="Automatically cut silent pauses longer than 0.7 seconds"
              >
                <Sparkles className="w-3 h-3 text-emerald-300 shrink-0" />
                <span>Pauses</span>
              </button>
              <button
                type="button"
                disabled={!hasVideo}
                onClick={() => {
                  const firstStart = project.tracks.clips[0]?.sourceStart ?? 0;
                  remove_time_ranges([{ start: firstStart, end: firstStart + 20 }]);
                }}
                className="px-1.5 py-1.5 rounded-lg bg-[#1c212d] hover:bg-[#232a39] disabled:opacity-40 border border-[#272f40] text-[11px] font-medium text-slate-200 flex items-center justify-center gap-1 transition"
                title="Remove the first 20 seconds from the timeline"
              >
                <Scissors className="w-3 h-3 text-sky-300 shrink-0" />
                <span>Trim 20s</span>
              </button>
            </div>
          </>
        )}
      </div>

      {/* Segmented Tabs: Captions | My SFX */}
      <div className="p-2 border-b border-[#272f40] shrink-0">
        <div className="grid grid-cols-2 gap-1 bg-[#0f1218] p-1 rounded-xl border border-[#272f40]">
          <button
            type="button"
            onClick={() => {
              stopAudition();
              setLeftTab('captions');
            }}
            className={`py-1.5 rounded-lg text-xs font-semibold flex items-center justify-center gap-1.5 transition ${
              leftTab === 'captions'
                ? 'bg-[#1c212d] text-amber-300 border border-[#2f394e] shadow-sm'
                : 'text-slate-400 hover:text-slate-200 border border-transparent'
            }`}
          >
            <Captions className="w-3.5 h-3.5" />
            Captions ({project.tracks.captions.length})
          </button>
          <button
            type="button"
            onClick={() => setLeftTab('sfx')}
            className={`py-1.5 rounded-lg text-xs font-semibold flex items-center justify-center gap-1.5 transition ${
              leftTab === 'sfx'
                ? 'bg-[#1c212d] text-emerald-300 border border-[#2f394e] shadow-sm'
                : 'text-slate-400 hover:text-slate-200 border border-transparent'
            }`}
          >
            <Music className="w-3.5 h-3.5" />
            My SFX ({sfxLibrary.length})
          </button>
        </div>
      </div>

      {leftTab === 'captions' ? (
        <div className="flex-1 min-h-0 overflow-y-auto">
          {/* Sticky Manual Caption Editor Header */}
          <div className="sticky top-0 z-10 bg-[#151922]/95 backdrop-blur-sm px-3 py-2 border-b border-[#272f40] flex items-center justify-between">
            <div>
              <h3 className="text-xs font-semibold text-slate-100">
                Subtitles &amp; Highlights
              </h3>
              <p className="text-[11px] text-slate-400">
                Tap a word below to highlight it
              </p>
            </div>
            <button
              type="button"
              disabled={!hasVideo}
              onClick={handleAddCaptionAtPlayhead}
              className="px-2.5 py-1 rounded-lg bg-amber-400/15 hover:bg-amber-400/25 disabled:opacity-40 border border-amber-400/30 text-amber-200 text-xs font-medium flex items-center gap-1 transition"
            >
              <Plus className="w-3.5 h-3.5" />
              Add
            </button>
          </div>

          {/* Caption List */}
          <div className="p-2.5 space-y-2.5">
            {project.tracks.captions.length === 0 ? (
              <div className="text-center py-8 px-4 space-y-2">
                <Captions className="w-7 h-7 text-slate-600 mx-auto" />
                <p className="text-xs text-slate-400 leading-relaxed">
                  No subtitles yet. Click <strong className="text-slate-200">Captions</strong> above or ask the AI Editor to generate them.
                </p>
              </div>
            ) : (
              project.tracks.captions.map((cap, idx) => (
                <div
                  key={cap.id}
                  className={`rounded-xl border p-3 space-y-2.5 transition ${
                    cap.isManuallyEdited
                      ? 'bg-[#1c212d] border-amber-400/40'
                      : 'bg-[#1c212d]/75 border-[#272f40]'
                  }`}
                >
                  {/* Timing + Lock Badge + Actions */}
                  <div className="flex items-center justify-between gap-1.5">
                    <div className="flex items-center gap-1">
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        value={cap.start}
                        aria-label="Caption start time"
                        onChange={(e) =>
                          adjust_caption_timing(
                            cap.id,
                            parseFloat(e.target.value) || 0,
                            cap.end,
                            true,
                          )
                        }
                        className="w-14 bg-[#12161f] border border-[#272f40] focus:border-amber-400/70 rounded-md px-1.5 py-1 text-[11px] font-mono tabular-nums text-slate-200 outline-none"
                      />
                      <span className="text-xs text-slate-500">→</span>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        value={cap.end}
                        aria-label="Caption end time"
                        onChange={(e) =>
                          adjust_caption_timing(
                            cap.id,
                            cap.start,
                            parseFloat(e.target.value) || cap.start + 0.5,
                            true,
                          )
                        }
                        className="w-14 bg-[#12161f] border border-[#272f40] focus:border-amber-400/70 rounded-md px-1.5 py-1 text-[11px] font-mono tabular-nums text-slate-200 outline-none"
                      />
                    </div>

                    <div className="flex items-center gap-1">
                      {cap.isManuallyEdited && (
                        <span
                          title="Manually edited — locked so AI won't overwrite your changes"
                          className="px-2 py-0.5 rounded-md bg-amber-400/15 text-amber-200 text-[10px] font-medium flex items-center gap-1"
                        >
                          <Lock className="w-2.5 h-2.5" />
                          Locked
                        </span>
                      )}
                      {idx < project.tracks.captions.length - 1 && (
                        <button
                          type="button"
                          onClick={() => handleCombineWithNext(idx)}
                          title="Combine with next caption"
                          className="p-1.5 rounded-md hover:bg-white/5 text-slate-400 hover:text-slate-100 transition"
                        >
                          <Merge className="w-3.5 h-3.5" />
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => handleDeleteCaption(cap.id)}
                        title="Delete caption"
                        className="p-1.5 rounded-md hover:bg-red-500/20 text-slate-400 hover:text-red-300 transition"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
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
                        const spd = Math.max(0.25, Math.min(4.0, clip.speed ?? 1));
                        const clampedSrc = Math.max(
                          clip.sourceStart,
                          Math.min(clip.sourceEnd, cap.start),
                        );
                        setPlayheadPosition(
                          clip.timelineStart + (clampedSrc - clip.sourceStart) / spd,
                        );
                      }
                    }}
                    onChange={(e) =>
                      edit_caption(cap.id, e.target.value, cap.highlightedWords, true)
                    }
                    className="w-full bg-[#12161f] border border-[#272f40] focus:border-amber-400/70 rounded-lg p-2 text-[13px] leading-snug text-slate-100 resize-none outline-none"
                  />

                  {/* Interactive Word Highlight Toggles */}
                  <div className="flex flex-wrap gap-1.5">
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
                            className={`px-2 py-0.5 rounded-md text-[11px] transition ${
                              isHl
                                ? 'bg-amber-400 text-slate-950 font-semibold'
                                : 'bg-[#12161f] border border-[#272f40] text-slate-300 hover:text-slate-100 hover:border-slate-600'
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
        </div>
      ) : (
        /* Custom MP3 Sound Effects Library */
        <div className="flex-1 min-h-0 overflow-y-auto">
          <div className="sticky top-0 z-10 bg-[#151922]/95 backdrop-blur-sm px-2.5 py-2 border-b border-[#272f40]">
            <input
              ref={sfxInputRef}
              type="file"
              multiple
              accept=".mp3,.wav,.m4a,.ogg,audio/*"
              className="hidden"
              onChange={(e) => {
                if (e.target.files && e.target.files.length > 0) {
                  void uploadSfxFiles(e.target.files);
                  e.target.value = '';
                }
              }}
            />
            <div className="flex items-center gap-1.5">
              <div className="relative flex-1">
                <Search className="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  value={sfxSearch}
                  onChange={(e) => setSfxSearch(e.target.value)}
                  placeholder="Search SFX name or tag..."
                  className="w-full bg-[#1c212d] border border-[#272f40] focus:border-emerald-400/70 rounded-lg pl-8 pr-2.5 py-1.5 text-xs text-slate-100 placeholder:text-slate-400 outline-none"
                />
              </div>
              <button
                type="button"
                onClick={() => void fetchSfxLibrary()}
                disabled={isLoadingSfx}
                title="Refresh SFX folder"
                aria-label="Refresh SFX folder"
                className="p-2 rounded-lg bg-[#1c212d] hover:bg-[#232a39] border border-[#272f40] text-slate-300 transition"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${isLoadingSfx ? 'animate-spin' : ''}`} />
              </button>
            </div>
          </div>

          <div className="p-2.5 space-y-2.5">
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setIsDragOver(true);
              }}
              onDragLeave={() => setIsDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setIsDragOver(false);
                if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                  void uploadSfxFiles(e.dataTransfer.files);
                }
              }}
              onClick={() => sfxInputRef.current?.click()}
              className={`border border-dashed rounded-xl py-2 px-3 flex items-center gap-2.5 cursor-pointer transition ${
                isDragOver
                  ? 'border-emerald-400 bg-emerald-500/15'
                  : 'border-[#333c4e] hover:border-emerald-400/70 bg-[#1c212d]/60'
              }`}
            >
              <Upload className="w-4 h-4 text-emerald-300 shrink-0" />
              <div className="min-w-0 text-left">
                <p className="text-xs font-semibold text-slate-100">
                  Upload MP3 Sound Effects
                </p>
                <p className="text-[10px] text-slate-400 leading-snug">
                  Drop .mp3 files — you &amp; AI can place them
                </p>
              </div>
            </div>

            {filteredSfx.length === 0 ? (
              <div className="text-center py-8 px-4 space-y-2">
                <Volume2 className="w-7 h-7 text-slate-600 mx-auto" />
                <p className="text-xs font-medium text-slate-300">
                  {sfxLibrary.length === 0
                    ? 'No custom sound effects uploaded yet'
                    : 'No sound effects match your search'}
                </p>
                <p className="text-[11px] text-slate-400 leading-relaxed">
                  Add MP3s above to preview them and let the AI automatically match sounds to reactions &amp; zooms.
                </p>
              </div>
            ) : (
              filteredSfx.map((asset) => {
                const isAuditioning = playingAssetId === asset.id;
                const tagsStr = tagDrafts[asset.id] ?? asset.tags.join(', ');
                const commitTags = () => {
                  const parsed = tagsStr
                    .split(',')
                    .map((t) => t.trim())
                    .filter(Boolean);
                  void updateSfxAssetMeta(asset.id, { tags: parsed });
                };
                return (
                  <div
                    key={asset.id}
                    className="rounded-xl border border-[#272f40] bg-[#1c212d] p-2.5 space-y-2"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <button
                          type="button"
                          onClick={() => toggleAudition(asset)}
                          title={isAuditioning ? 'Stop preview' : 'Listen to SFX'}
                          className={`w-7 h-7 rounded-full flex items-center justify-center shrink-0 transition ${
                            isAuditioning
                              ? 'bg-emerald-400 text-slate-950'
                              : 'bg-[#12161f] hover:bg-white/10 text-emerald-300 border border-[#272f40]'
                          }`}
                        >
                          {isAuditioning ? (
                            <Pause className="w-3.5 h-3.5" />
                          ) : (
                            <Play className="w-3.5 h-3.5 ml-0.5" />
                          )}
                        </button>
                        <span
                          className="text-xs font-medium text-slate-100 truncate"
                          title={asset.filename}
                        >
                          {asset.name}
                        </span>
                      </div>
                      <span className="text-[11px] font-mono tabular-nums text-slate-400 shrink-0">
                        {asset.duration.toFixed(1)}s
                      </span>
                    </div>

                    <div className="space-y-1">
                      <label className="text-[10px] font-medium text-slate-400">
                        AI Tags (when should AI use this sound?)
                      </label>
                      <input
                        type="text"
                        value={tagsStr}
                        onChange={(e) =>
                          setTagDrafts((prev) => ({
                            ...prev,
                            [asset.id]: e.target.value,
                          }))
                        }
                        onBlur={commitTags}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.currentTarget.blur();
                          }
                        }}
                        placeholder="e.g. boom, whoosh, laugh, pop"
                        className="w-full bg-[#12161f] border border-[#272f40] focus:border-emerald-400/70 rounded-lg px-2 py-1 text-[11px] text-slate-200 placeholder:text-slate-500 outline-none"
                      />
                    </div>

                    <div className="flex items-center justify-between gap-2">
                      <button
                        type="button"
                        disabled={!hasVideo}
                        onClick={() =>
                          add_sfx_clip({
                            assetId: asset.id,
                            name: asset.name,
                            url: asset.url,
                            start: project.workspaceState.playheadPosition,
                            duration: asset.duration,
                            volume: 0.85,
                          })
                        }
                        className="flex-1 py-1.5 rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 disabled:opacity-40 border border-emerald-500/35 text-[11px] font-semibold text-emerald-200 flex items-center justify-center gap-1 transition"
                      >
                        <Plus className="w-3.5 h-3.5" />
                        Place at Playhead
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          if (isAuditioning) stopAudition();
                          void deleteSfxAssetFromLibrary(asset.id);
                        }}
                        title="Delete SFX from library"
                        className="p-1.5 rounded-lg hover:bg-red-500/20 text-slate-400 hover:text-red-300 transition"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}
    </aside>
  );
};
