import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Merge,
  Scissors,
  Trash2,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import { useEditorStore } from '../store/useEditorStore';
import type { ClipItem } from '../types/editor';

export const BottomTimeline: React.FC = () => {
  const project = useEditorStore((s) => s.project);
  const timelineZoom = useEditorStore((s) => s.timelineZoom);
  const setTimelineZoom = useEditorStore((s) => s.setTimelineZoom);
  const setPlayheadPosition = useEditorStore((s) => s.setPlayheadPosition);
  const setSelectedClipIds = useEditorStore((s) => s.setSelectedClipIds);
  const setIsPlaying = useEditorStore((s) => s.setIsPlaying);

  const split_clip = useEditorStore((s) => s.split_clip);
  const delete_clips = useEditorStore((s) => s.delete_clips);
  const merge_clips = useEditorStore((s) => s.merge_clips);
  const reorder_clips = useEditorStore((s) => s.reorder_clips);
  const trim_clip = useEditorStore((s) => s.trim_clip);
  const adjust_caption_timing = useEditorStore((s) => s.adjust_caption_timing);

  const trackScrollRef = useRef<HTMLDivElement | null>(null);
  const [draggedClipId, setDraggedClipId] = useState<string | null>(null);
  const [trimPreview, setTrimPreview] = useState<{
    clipId: string;
    sourceStart: number;
    sourceEnd: number;
  } | null>(null);
  const [captionDragPreview, setCaptionDragPreview] = useState<{
    capId: string;
    srcStart: number;
    srcEnd: number;
  } | null>(null);

  const clips = project.tracks.clips;
  const playhead = project.workspaceState.playheadPosition;
  const selectedIds = project.workspaceState.selectedClipIds;

  const totalDuration = useMemo(
    () => Math.max(10, clips.reduce((acc, c) => acc + c.duration, 0)),
    [clips],
  );
  const timelineWidthPx = Math.max(800, Math.ceil(totalDuration * timelineZoom) + 120);

  const activeClipAtPlayhead = useMemo(() => {
    return (
      clips.find(
        (c) =>
          playhead > c.timelineStart + 0.15 &&
          playhead < c.timelineStart + c.duration - 0.15,
      ) ?? null
    );
  }, [clips, playhead]);

  const handleSplitAtPlayhead = () => {
    if (!activeClipAtPlayhead) return;
    split_clip(activeClipAtPlayhead.id, playhead);
  };

  const handleDeleteSelected = () => {
    if (selectedIds.length === 0) return;
    delete_clips(selectedIds);
  };

  const handleMergeSelectedOrAdjacent = () => {
    if (selectedIds.length >= 2) {
      merge_clips(selectedIds[0], selectedIds[1]);
      return;
    }
    if (selectedIds.length === 1) {
      const idx = clips.findIndex((c) => c.id === selectedIds[0]);
      if (idx !== -1 && idx + 1 < clips.length) {
        merge_clips(clips[idx].id, clips[idx + 1].id);
      }
    }
  };

  // Global keyboard shortcuts (Space, S, Del, Ctrl+Z, Ctrl+Y)
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const isEditingInput =
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.isContentEditable);
      if (isEditingInput) return;

      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        if (e.shiftKey) {
          useEditorStore.temporal.getState().redo();
        } else {
          useEditorStore.temporal.getState().undo();
        }
        return;
      }

      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') {
        e.preventDefault();
        useEditorStore.temporal.getState().redo();
        return;
      }

      if (e.code === 'Space') {
        e.preventDefault();
        const st = useEditorStore.getState();
        if (st.project.tracks.clips.length > 0) {
          st.setIsPlaying(!st.isPlaying);
        }
      } else if (e.key.toLowerCase() === 's' && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        handleSplitAtPlayhead();
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selectedIds.length > 0) {
          e.preventDefault();
          delete_clips(selectedIds);
        }
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [activeClipAtPlayhead, playhead, selectedIds]);

  const handleRulerMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const updateFromClientX = (clientX: number) => {
      const x = Math.max(0, clientX - rect.left);
      const sec = Math.min(totalDuration, Number((x / timelineZoom).toFixed(2)));
      setIsPlaying(false);
      setPlayheadPosition(sec);
    };
    updateFromClientX(e.clientX);

    const onMove = (ev: MouseEvent) => updateFromClientX(ev.clientX);
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  const startTrimDrag = (
    e: React.MouseEvent,
    clip: ClipItem,
    edge: 'left' | 'right',
  ) => {
    e.stopPropagation();
    const startX = e.clientX;
    const origStart = clip.sourceStart;
    const origEnd = clip.sourceEnd;
    const maxOrig = project.sourceVideo.originalDuration || origEnd + 60;

    let latestStart = origStart;
    let latestEnd = origEnd;

    const onMove = (ev: MouseEvent) => {
      const deltaSec = (ev.clientX - startX) / timelineZoom;
      if (edge === 'left') {
        latestStart = Math.max(0, Math.min(origEnd - 0.3, origStart + deltaSec));
      } else {
        latestEnd = Math.min(maxOrig, Math.max(origStart + 0.3, origEnd + deltaSec));
      }
      setTrimPreview({
        clipId: clip.id,
        sourceStart: Number(latestStart.toFixed(2)),
        sourceEnd: Number(latestEnd.toFixed(2)),
      });
    };

    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      setTrimPreview(null);
      if (
        Math.abs(latestStart - origStart) > 0.05 ||
        Math.abs(latestEnd - origEnd) > 0.05
      ) {
        trim_clip(clip.id, latestStart, latestEnd);
      }
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  const startCaptionEdgeDrag = (
    e: React.MouseEvent,
    capId: string,
    origStart: number,
    origEnd: number,
    edge: 'left' | 'right',
  ) => {
    e.stopPropagation();
    const startX = e.clientX;
    let s = origStart;
    let end = origEnd;

    const onMove = (ev: MouseEvent) => {
      const delta = (ev.clientX - startX) / timelineZoom;
      if (edge === 'left') {
        s = Math.max(0, Math.min(origEnd - 0.2, origStart + delta));
      } else {
        end = Math.max(origStart + 0.2, origEnd + delta);
      }
      setCaptionDragPreview({
        capId,
        srcStart: Number(s.toFixed(2)),
        srcEnd: Number(end.toFixed(2)),
      });
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      setCaptionDragPreview(null);
      if (Math.abs(s - origStart) > 0.04 || Math.abs(end - origEnd) > 0.04) {
        adjust_caption_timing(capId, s, end, true);
      }
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  // Ruler tick spacing
  const tickStepSec = timelineZoom >= 25 ? 2 : timelineZoom >= 12 ? 5 : 10;
  const ticks: number[] = [];
  for (let t = 0; t <= totalDuration; t += tickStepSec) {
    ticks.push(t);
  }

  // Mapped captions on timeline
  const mappedCaptions = useMemo(() => {
    const items: Array<{
      id: string;
      text: string;
      tlStart: number;
      tlEnd: number;
      srcStart: number;
      srcEnd: number;
      isManuallyEdited: boolean;
    }> = [];
    for (const clip of clips) {
      for (const cap of project.tracks.captions) {
        const preview =
          captionDragPreview && captionDragPreview.capId === cap.id
            ? captionDragPreview
            : null;
        const capStart = preview ? preview.srcStart : cap.start;
        const capEnd = preview ? preview.srcEnd : cap.end;
        const os = Math.max(clip.sourceStart, capStart);
        const oe = Math.min(clip.sourceEnd, capEnd);
        if (oe - os > 0.05) {
          items.push({
            id: cap.id,
            text: cap.text,
            tlStart: clip.timelineStart + (os - clip.sourceStart),
            tlEnd: clip.timelineStart + (oe - clip.sourceStart),
            srcStart: capStart,
            srcEnd: capEnd,
            isManuallyEdited: cap.isManuallyEdited,
          });
        }
      }
    }
    return items;
  }, [clips, project.tracks.captions, captionDragPreview]);

  const selectedIndex =
    selectedIds.length === 1
      ? clips.findIndex((c) => c.id === selectedIds[0])
      : -1;

  return (
    <section className="h-64 border-t border-zinc-800 bg-zinc-950 flex flex-col shrink-0">
      {/* Timeline Action Toolbar */}
      <div className="h-10 px-3 border-b border-zinc-800 flex items-center justify-between gap-3 shrink-0">
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            disabled={!activeClipAtPlayhead}
            onClick={handleSplitAtPlayhead}
            title="Split clip at playhead (S)"
            className="px-2.5 py-1 rounded bg-zinc-900 hover:bg-zinc-800 disabled:opacity-40 border border-zinc-800 text-xs text-zinc-200 flex items-center gap-1"
          >
            <Scissors className="w-3.5 h-3.5 text-amber-400" />
            Split (S)
          </button>

          <button
            type="button"
            disabled={selectedIds.length === 0}
            onClick={handleDeleteSelected}
            title="Delete selected clip (Del)"
            className="px-2.5 py-1 rounded bg-zinc-900 hover:bg-zinc-800 disabled:opacity-40 border border-zinc-800 text-xs text-zinc-200 flex items-center gap-1"
          >
            <Trash2 className="w-3.5 h-3.5 text-red-400" />
            Delete
          </button>

          <button
            type="button"
            disabled={
              !(
                selectedIds.length >= 2 ||
                (selectedIndex !== -1 && selectedIndex < clips.length - 1)
              )
            }
            onClick={handleMergeSelectedOrAdjacent}
            title="Merge selected or adjacent clip"
            className="px-2.5 py-1 rounded bg-zinc-900 hover:bg-zinc-800 disabled:opacity-40 border border-zinc-800 text-xs text-zinc-200 flex items-center gap-1"
          >
            <Merge className="w-3.5 h-3.5 text-emerald-400" />
            Merge
          </button>

          {selectedIndex !== -1 && clips.length > 1 && (
            <div className="flex items-center gap-0.5 ml-1">
              <button
                type="button"
                disabled={selectedIndex <= 0}
                onClick={() => reorder_clips(clips[selectedIndex].id, selectedIndex - 1)}
                title="Move clip earlier"
                className="p-1 rounded bg-zinc-900 hover:bg-zinc-800 disabled:opacity-30 border border-zinc-800 text-zinc-300"
              >
                <ArrowLeft className="w-3.5 h-3.5" />
              </button>
              <button
                type="button"
                disabled={selectedIndex >= clips.length - 1}
                onClick={() => reorder_clips(clips[selectedIndex].id, selectedIndex + 1)}
                title="Move clip later"
                className="p-1 rounded bg-zinc-900 hover:bg-zinc-800 disabled:opacity-30 border border-zinc-800 text-zinc-300"
              >
                <ArrowRight className="w-3.5 h-3.5" />
              </button>
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 text-xs text-zinc-400">
          {trimPreview && (
            <span className="font-mono text-[11px] text-amber-300 mr-2">
              Trim: {trimPreview.sourceStart.toFixed(1)}s → {trimPreview.sourceEnd.toFixed(1)}s
            </span>
          )}
          <button
            type="button"
            onClick={() => setTimelineZoom(timelineZoom - 4)}
            className="p-1 rounded hover:bg-zinc-800"
            title="Zoom out"
          >
            <ZoomOut className="w-3.5 h-3.5" />
          </button>
          <input
            type="range"
            min={4}
            max={60}
            value={timelineZoom}
            onChange={(e) => setTimelineZoom(Number(e.target.value))}
            className="w-24 accent-amber-400"
          />
          <button
            type="button"
            onClick={() => setTimelineZoom(timelineZoom + 4)}
            className="p-1 rounded hover:bg-zinc-800"
            title="Zoom in"
          >
            <ZoomIn className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Scrollable Tracks Area */}
      <div ref={trackScrollRef} className="flex-1 overflow-x-auto overflow-y-hidden relative">
        <div
          className="relative h-full flex flex-col"
          style={{ width: `${timelineWidthPx}px` }}
        >
          {/* Ruler */}
          <div
            onMouseDown={handleRulerMouseDown}
            className="h-6 border-b border-zinc-800 bg-zinc-900/70 relative cursor-ew-resize shrink-0"
          >
            {ticks.map((t) => (
              <div
                key={t}
                className="absolute top-0 bottom-0 border-l border-zinc-700/70 pl-1 text-[10px] font-mono text-zinc-500 flex items-center"
                style={{ left: `${t * timelineZoom}px` }}
              >
                {t}s
              </div>
            ))}
          </div>

          {/* Tracks Container */}
          <div className="flex-1 relative p-2 space-y-2">
            {/* Video Track */}
            <div className="h-14 relative rounded bg-zinc-900/40 border border-zinc-800/80">
              <span className="absolute left-1.5 top-1 text-[9px] uppercase tracking-wider text-zinc-600 pointer-events-none z-10">
                Video
              </span>
              {clips.map((clip, idx) => {
                const isSelected = selectedIds.includes(clip.id);
                const isTrimming = trimPreview && trimPreview.clipId === clip.id;
                const displayDuration = isTrimming
                  ? Math.max(0.1, trimPreview.sourceEnd - trimPreview.sourceStart)
                  : clip.duration;
                const leftPx = clip.timelineStart * timelineZoom;
                const widthPx = Math.max(24, displayDuration * timelineZoom);

                return (
                  <div
                    key={clip.id}
                    draggable
                    onDragStart={() => setDraggedClipId(clip.id)}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={() => {
                      if (draggedClipId && draggedClipId !== clip.id) {
                        reorder_clips(draggedClipId, idx);
                      }
                      setDraggedClipId(null);
                    }}
                    onClick={(e) => {
                      if (e.shiftKey) {
                        setSelectedClipIds(
                          isSelected
                            ? selectedIds.filter((id) => id !== clip.id)
                            : [...selectedIds, clip.id],
                        );
                      } else {
                        setSelectedClipIds([clip.id]);
                      }
                    }}
                    style={{ left: `${leftPx}px`, width: `${widthPx}px` }}
                    className={`absolute top-1 bottom-1 rounded-md border flex items-center overflow-hidden cursor-pointer group transition-colors ${
                      isSelected
                        ? 'bg-sky-500/25 border-amber-400'
                        : 'bg-sky-500/15 border-sky-500/40 hover:border-sky-400'
                    }`}
                  >
                    {/* Left Trim Handle */}
                    <div
                      onMouseDown={(e) => startTrimDrag(e, clip, 'left')}
                      title="Drag to trim clip start"
                      className="w-2 h-full bg-amber-400/30 hover:bg-amber-400 cursor-ew-resize shrink-0"
                    />
                    <div className="flex-1 px-2 min-w-0 flex items-center justify-between gap-1">
                      <span className="text-[11px] font-medium text-zinc-100 truncate">
                        {clip.title}
                      </span>
                      <span className="text-[10px] font-mono text-zinc-400 shrink-0">
                        {displayDuration.toFixed(1)}s
                      </span>
                    </div>
                    {clip.viralityScore && (
                      <span className="mr-1 px-1 py-0.5 rounded bg-amber-400/20 text-amber-300 text-[9px] font-mono shrink-0">
                        ★{clip.viralityScore}
                      </span>
                    )}
                    {/* Right Trim Handle */}
                    <div
                      onMouseDown={(e) => startTrimDrag(e, clip, 'right')}
                      title="Drag to trim clip end"
                      className="w-2 h-full bg-amber-400/30 hover:bg-amber-400 cursor-ew-resize shrink-0"
                    />
                  </div>
                );
              })}
            </div>

            {/* Lightweight Audio Peaks & Heatmap Track (Zero browser audio buffer decoding) */}
            <div className="h-9 relative rounded bg-zinc-900/40 border border-zinc-800/80 overflow-hidden flex items-center">
              <span className="absolute left-1.5 top-0.5 text-[9px] uppercase tracking-wider text-zinc-600 pointer-events-none z-10">
                Audio & Heatmap
              </span>
              {clips.map((clip) => {
                const origDur = project.sourceVideo.originalDuration || 1;
                const peaks = project.sourceVideo.audioPeaks || [];
                const leftPx = clip.timelineStart * timelineZoom;
                const widthPx = Math.max(20, clip.duration * timelineZoom);

                const startRatio = clip.sourceStart / origDur;
                const endRatio = clip.sourceEnd / origDur;
                const sliceStart = Math.floor(startRatio * peaks.length);
                const sliceEnd = Math.max(
                  sliceStart + 1,
                  Math.ceil(endRatio * peaks.length),
                );
                const clipPeaks = peaks.slice(sliceStart, sliceEnd);

                return (
                  <div
                    key={`aud_${clip.id}`}
                    style={{ left: `${leftPx}px`, width: `${widthPx}px` }}
                    className="absolute top-1 bottom-1 flex items-center gap-[1px] px-1 overflow-hidden bg-emerald-500/5 border-x border-zinc-800"
                  >
                    {clipPeaks.map((p, i) => (
                      <div
                        key={i}
                        className="flex-1 bg-emerald-400/55 rounded-full min-w-[1px]"
                        style={{ height: `${Math.max(15, Math.round(p * 100))}%` }}
                      />
                    ))}
                  </div>
                );
              })}
            </div>

            {/* Captions Track */}
            <div className="h-9 relative rounded bg-zinc-900/40 border border-zinc-800/80">
              <span className="absolute left-1.5 top-0.5 text-[9px] uppercase tracking-wider text-zinc-600 pointer-events-none z-10">
                Captions
              </span>
              {mappedCaptions.map((mc, idx) => {
                const leftPx = mc.tlStart * timelineZoom;
                const widthPx = Math.max(16, (mc.tlEnd - mc.tlStart) * timelineZoom);
                return (
                  <div
                    key={`${mc.id}_${idx}`}
                    style={{ left: `${leftPx}px`, width: `${widthPx}px` }}
                    className={`absolute top-1 bottom-1 rounded border flex items-center overflow-hidden ${
                      mc.isManuallyEdited
                        ? 'bg-amber-400/20 border-amber-400/70'
                        : 'bg-purple-500/20 border-purple-400/50'
                    }`}
                  >
                    <div
                      onMouseDown={(e) =>
                        startCaptionEdgeDrag(e, mc.id, mc.srcStart, mc.srcEnd, 'left')
                      }
                      className="w-1.5 h-full hover:bg-amber-400 cursor-ew-resize shrink-0"
                    />
                    <span className="flex-1 px-1 text-[10px] text-zinc-200 truncate">
                      {mc.text}
                    </span>
                    <div
                      onMouseDown={(e) =>
                        startCaptionEdgeDrag(e, mc.id, mc.srcStart, mc.srcEnd, 'right')
                      }
                      className="w-1.5 h-full hover:bg-amber-400 cursor-ew-resize shrink-0"
                    />
                  </div>
                );
              })}
            </div>
          </div>

          {/* Playhead Needle */}
          <div
            className="absolute top-0 bottom-0 w-0.5 bg-amber-400 pointer-events-none z-20"
            style={{ left: `${playhead * timelineZoom}px` }}
          >
            <div className="w-3 h-3 -ml-[5px] bg-amber-400 rotate-45 -mt-1 rounded-[1px]" />
          </div>
        </div>
      </div>
    </section>
  );
};
