import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  AudioLines,
  ChevronDown,
  ChevronUp,
  Crop,
  Gauge,
  Merge,
  Scissors,
  Trash2,
  Volume2,
  VolumeX,
  X,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import { useEditorStore } from '../store/useEditorStore';
import type { ClipItem, SfxClipItem } from '../types/editor';

function formatRulerTick(sec: number): string {
  const total = Math.max(0, Math.round(sec));
  if (total < 60) return `${total}s`;
  const hrs = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hrs > 0) {
    return `${hrs}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }
  return `${mins}:${String(secs).padStart(2, '0')}`;
}

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
  const set_clip_speed = useEditorStore((s) => s.set_clip_speed);
  const selectedZoomId = useEditorStore((s) => s.selectedZoomId);
  const setSelectedZoomId = useEditorStore((s) => s.setSelectedZoomId);
  const update_zoom_effect = useEditorStore((s) => s.update_zoom_effect);
  const delete_zoom_effect = useEditorStore((s) => s.delete_zoom_effect);
  const selectedDrawingId = useEditorStore((s) => s.selectedDrawingId);
  const setSelectedDrawingId = useEditorStore((s) => s.setSelectedDrawingId);
  const update_drawing = useEditorStore((s) => s.update_drawing);
  const delete_drawing = useEditorStore((s) => s.delete_drawing);
  const selectedSfxId = useEditorStore((s) => s.selectedSfxId);
  const setSelectedSfxId = useEditorStore((s) => s.setSelectedSfxId);
  const sfxLibrary = useEditorStore((s) => s.sfxLibrary);
  const update_sfx_clip = useEditorStore((s) => s.update_sfx_clip);
  const delete_sfx_clip = useEditorStore((s) => s.delete_sfx_clip);
  const selectedAudioEditId = useEditorStore((s) => s.selectedAudioEditId);
  const setSelectedAudioEditId = useEditorStore((s) => s.setSelectedAudioEditId);
  const add_audio_edit = useEditorStore((s) => s.add_audio_edit);
  const update_audio_edit = useEditorStore((s) => s.update_audio_edit);
  const delete_audio_edit = useEditorStore((s) => s.delete_audio_edit);
  const split_audio_at = useEditorStore((s) => s.split_audio_at);

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
  const [rangeDragPreview, setRangeDragPreview] = useState<{
    id: string;
    start: number;
    end: number;
  } | null>(null);
  const [sfxTrimPreview, setSfxTrimPreview] = useState<{
    id: string;
    trimStart: number;
    duration: number;
  } | null>(null);

  const clips = project.tracks.clips;
  const playhead = project.workspaceState.playheadPosition;
  const selectedIds = project.workspaceState.selectedClipIds;

  const totalDuration = useMemo(
    () => clips.reduce((acc, c) => acc + c.duration, 0),
    [clips],
  );
  const visualDuration = Math.max(10, totalDuration);
  const timelineWidthPx = Math.max(800, Math.ceil(visualDuration * timelineZoom) + 120);
  const activeDragCleanupRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    return () => {
      activeDragCleanupRef.current?.();
      activeDragCleanupRef.current = null;
    };
  }, []);

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
          target.tagName === 'SELECT' ||
          target.isContentEditable);
      if (isEditingInput) return;
      if (document.querySelector('[aria-modal="true"]')) return;

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

      const st = useEditorStore.getState();
      if (e.code === 'Space') {
        e.preventDefault();
        if (st.isCropMode) st.setIsCropMode(false);
        if (st.activeDrawTool) st.setActiveDrawTool(null);
        if (st.project.tracks.clips.length > 0) {
          st.setIsPlaying(!st.isPlaying);
        }
      } else if (e.key.toLowerCase() === 's' && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        const ph = st.project.workspaceState.playheadPosition;
        const active = st.project.tracks.clips.find(
          (c) =>
            ph > c.timelineStart + 0.15 &&
            ph < c.timelineStart + c.duration - 0.15,
        );
        if (active) {
          st.split_clip(active.id, ph);
        }
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        const tracks = st.project.tracks;
        const zId = st.selectedZoomId;
        const dId = st.selectedDrawingId;
        const sfxId = st.selectedSfxId;
        const audId = st.selectedAudioEditId;
        const selClipIds = st.project.workspaceState.selectedClipIds;
        if (zId && (tracks.zooms ?? []).some((z) => z.id === zId)) {
          e.preventDefault();
          st.delete_zoom_effect(zId);
        } else if (dId && (tracks.drawings ?? []).some((d) => d.id === dId)) {
          e.preventDefault();
          st.delete_drawing(dId);
        } else if (sfxId && (tracks.sfx ?? []).some((s) => s.id === sfxId)) {
          e.preventDefault();
          st.delete_sfx_clip(sfxId);
        } else if (audId && (tracks.audioEdits ?? []).some((a) => a.id === audId)) {
          e.preventDefault();
          st.delete_audio_edit(audId);
        } else if (selClipIds.length > 0) {
          e.preventDefault();
          st.delete_clips(selClipIds);
        }
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const handleRulerMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const updateFromClientX = (clientX: number) => {
      const x = Math.max(0, clientX - rect.left);
      const sec = Math.min(totalDuration, Number((x / timelineZoom).toFixed(2)));
      setIsPlaying(false);
      setPlayheadPosition(sec);
    };
    updateFromClientX(e.clientX);

    activeDragCleanupRef.current?.();
    const onMove = (ev: MouseEvent) => updateFromClientX(ev.clientX);
    const cleanup = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      if (activeDragCleanupRef.current === cleanup) {
        activeDragCleanupRef.current = null;
      }
    };
    const onUp = () => cleanup();
    activeDragCleanupRef.current = cleanup;
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  // Drag clip left/right trim edge
  const startTrimDrag = (
    e: React.MouseEvent,
    clip: ClipItem,
    edge: 'left' | 'right',
  ) => {
    e.stopPropagation();
    e.preventDefault();
    const startX = e.clientX;
    const origStart = clip.sourceStart;
    const origEnd = clip.sourceEnd;
    const clipSpeed = Math.max(0.25, Math.min(4.0, clip.speed ?? 1));
    const maxSource = project.sourceVideo.originalDuration || origEnd + 60;
    let latestStart = origStart;
    let latestEnd = origEnd;

    activeDragCleanupRef.current?.();
    const onMove = (ev: MouseEvent) => {
      const deltaPx = ev.clientX - startX;
      const deltaSec = (deltaPx / timelineZoom) * clipSpeed;
      if (edge === 'left') {
        latestStart = Math.max(0, Math.min(origEnd - 0.4, origStart + deltaSec));
      } else {
        latestEnd = Math.min(maxSource, Math.max(origStart + 0.4, origEnd + deltaSec));
      }
      setTrimPreview({
        clipId: clip.id,
        sourceStart: Number(latestStart.toFixed(2)),
        sourceEnd: Number(latestEnd.toFixed(2)),
      });
    };

    const cleanup = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      if (activeDragCleanupRef.current === cleanup) {
        activeDragCleanupRef.current = null;
      }
    };
    const onUp = () => {
      cleanup();
      setTrimPreview(null);
      if (
        Math.abs(latestStart - origStart) > 0.05 ||
        Math.abs(latestEnd - origEnd) > 0.05
      ) {
        trim_clip(clip.id, latestStart, latestEnd);
      }
    };

    activeDragCleanupRef.current = cleanup;
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  // Drag caption edge handle on timeline
  const startCaptionEdgeDrag = (
    e: React.MouseEvent,
    capId: string,
    origStart: number,
    origEnd: number,
    edge: 'left' | 'right',
    clipSpeed = 1,
  ) => {
    e.stopPropagation();
    e.preventDefault();
    const startX = e.clientX;
    const speedFactor = Math.max(0.25, Math.min(4.0, clipSpeed));
    const maxSource = project.sourceVideo.originalDuration > 0
      ? project.sourceVideo.originalDuration
      : origEnd + 60;
    let s = origStart;
    let end = origEnd;

    activeDragCleanupRef.current?.();
    const onMove = (ev: MouseEvent) => {
      const delta = ((ev.clientX - startX) / timelineZoom) * speedFactor;
      if (edge === 'left') {
        s = Math.max(0, Math.min(origEnd - 0.2, origStart + delta));
      } else {
        end = Math.min(maxSource, Math.max(origStart + 0.2, origEnd + delta));
      }
      setCaptionDragPreview({
        capId,
        srcStart: Number(s.toFixed(2)),
        srcEnd: Number(end.toFixed(2)),
      });
    };
    const cleanup = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      if (activeDragCleanupRef.current === cleanup) {
        activeDragCleanupRef.current = null;
      }
    };
    const onUp = () => {
      cleanup();
      setCaptionDragPreview(null);
      if (Math.abs(s - origStart) > 0.04 || Math.abs(end - origEnd) > 0.04) {
        adjust_caption_timing(capId, s, end, true);
      }
    };
    activeDragCleanupRef.current = cleanup;
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  const startRangeDrag = (
    e: React.MouseEvent,
    id: string,
    origStart: number,
    origEnd: number,
    mode: 'left' | 'right' | 'move',
    onCommit: (nextStart: number, nextEnd: number) => void,
  ) => {
    e.stopPropagation();
    e.preventDefault();
    const startX = e.clientX;
    let s = origStart;
    let end = origEnd;
    const span = Math.max(0.25, origEnd - origStart);
    const maxBound = Math.max(totalDuration, origEnd);

    activeDragCleanupRef.current?.();
    const onMove = (ev: MouseEvent) => {
      const delta = (ev.clientX - startX) / timelineZoom;
      if (mode === 'left') {
        s = Math.max(0, Math.min(origEnd - 0.25, origStart + delta));
      } else if (mode === 'right') {
        end = Math.min(maxBound, Math.max(origStart + 0.25, origEnd + delta));
      } else {
        s = Math.max(0, Math.min(Math.max(0, maxBound - span), origStart + delta));
        end = s + span;
      }
      setRangeDragPreview({
        id,
        start: Number(s.toFixed(2)),
        end: Number(end.toFixed(2)),
      });
    };

    const cleanup = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      if (activeDragCleanupRef.current === cleanup) {
        activeDragCleanupRef.current = null;
      }
    };
    const onUp = () => {
      cleanup();
      setRangeDragPreview(null);
      if (Math.abs(s - origStart) > 0.04 || Math.abs(end - origEnd) > 0.04) {
        onCommit(Number(s.toFixed(3)), Number(end.toFixed(3)));
      }
    };

    activeDragCleanupRef.current = cleanup;
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  const startSfxDrag = (
    e: React.MouseEvent,
    sc: SfxClipItem,
    mode: 'left' | 'right' | 'move',
    sourceDur: number,
  ) => {
    e.stopPropagation();
    e.preventDefault();
    const startX = e.clientX;
    const origStart = sc.start;
    const origDur = sc.duration;
    const origEnd = origStart + origDur;
    const origTrimStart = Math.max(0, sc.trimStart ?? 0);
    const maxBound = Math.max(totalDuration, origEnd);

    let nextStart = origStart;
    let nextEnd = origEnd;
    let nextTrimStart = origTrimStart;

    activeDragCleanupRef.current?.();
    const onMove = (ev: MouseEvent) => {
      const delta = (ev.clientX - startX) / timelineZoom;
      if (mode === 'left') {
        const minStart = Math.max(0, origStart - origTrimStart);
        const maxStart = Math.max(minStart, origEnd - 0.15);
        nextStart = Math.max(minStart, Math.min(maxStart, origStart + delta));
        nextTrimStart = Math.max(0, origTrimStart + (nextStart - origStart));
        nextEnd = origEnd;
      } else if (mode === 'right') {
        const maxRemaining = Math.max(0.15, sourceDur - origTrimStart);
        const maxEnd = Math.min(maxBound, origStart + maxRemaining);
        const minEnd = Math.min(maxEnd, origStart + 0.15);
        nextStart = origStart;
        nextEnd = Math.max(minEnd, Math.min(maxEnd, origEnd + delta));
      } else {
        const maxMoveStart = Math.max(
          0,
          totalDuration - Math.min(origDur, Math.max(0.5, totalDuration - 0.5)),
        );
        nextStart = Math.max(0, Math.min(maxMoveStart, origStart + delta));
        nextEnd = nextStart + origDur;
      }
      setRangeDragPreview({
        id: sc.id,
        start: Number(nextStart.toFixed(2)),
        end: Number(nextEnd.toFixed(2)),
      });
      if (mode !== 'move') {
        setSfxTrimPreview({
          id: sc.id,
          trimStart: Number(nextTrimStart.toFixed(2)),
          duration: Number((nextEnd - nextStart).toFixed(2)),
        });
      }
    };

    const cleanup = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      if (activeDragCleanupRef.current === cleanup) {
        activeDragCleanupRef.current = null;
      }
    };
    const onUp = () => {
      cleanup();
      setRangeDragPreview(null);
      setSfxTrimPreview(null);
      const nextDur = Math.max(0.15, Number((nextEnd - nextStart).toFixed(3)));
      if (
        Math.abs(nextStart - origStart) > 0.04 ||
        Math.abs(nextEnd - origEnd) > 0.04 ||
        Math.abs(nextTrimStart - origTrimStart) > 0.04
      ) {
        update_sfx_clip(sc.id, {
          start: Number(nextStart.toFixed(3)),
          trimStart: Number(nextTrimStart.toFixed(3)),
          duration: nextDur,
        });
      }
    };

    activeDragCleanupRef.current = cleanup;
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  // Adaptive ruler tick spacing (caps DOM tick nodes cleanly even on multi-hour timelines)
  const baseTickStepSec = timelineZoom >= 25 ? 2 : timelineZoom >= 12 ? 5 : 10;
  const minStepForDom = visualDuration > 300 ? Math.ceil(visualDuration / 150) : baseTickStepSec;
  const cleanSteps = [2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
  const tickStepSec =
    cleanSteps.find((step) => step >= Math.max(baseTickStepSec, minStepForDom)) ??
    Math.ceil(minStepForDom / 600) * 600;
  const ticks: number[] = [];
  for (let t = 0; t <= visualDuration; t += tickStepSec) {
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
      clipSpeed: number;
      isManuallyEdited: boolean;
    }> = [];
    for (const clip of clips) {
      const clipSpeed = Math.max(0.25, Math.min(4.0, clip.speed ?? 1));
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
            tlStart: clip.timelineStart + (os - clip.sourceStart) / clipSpeed,
            tlEnd: clip.timelineStart + (oe - clip.sourceStart) / clipSpeed,
            srcStart: capStart,
            srcEnd: capEnd,
            clipSpeed,
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
  const selectedClip = selectedIndex !== -1 ? clips[selectedIndex] : null;
  const zooms = project.tracks.zooms ?? [];
  const drawings = project.tracks.drawings ?? [];
  const sfxClips = project.tracks.sfx ?? [];
  const audioEdits = project.tracks.audioEdits ?? [];
  const selectedAudioEdit =
    audioEdits.find((ae) => ae.id === selectedAudioEditId) ?? null;
  const canSplitAudioAtPlayhead = useMemo(
    () =>
      audioEdits.some(
        (ae) => playhead > ae.start + 0.15 && playhead < ae.end - 0.15,
      ) || Boolean(activeClipAtPlayhead),
    [audioEdits, playhead, activeClipAtPlayhead],
  );
  const selectedClipAudioMute = useMemo(() => {
    if (!selectedClip) return null;
    const cEnd = selectedClip.timelineStart + selectedClip.duration;
    return (
      audioEdits.find(
        (ae) =>
          ae.muted &&
          Math.abs(ae.start - selectedClip.timelineStart) < 0.15 &&
          Math.abs(ae.end - cEnd) < 0.15,
      ) ?? null
    );
  }, [audioEdits, selectedClip]);
  const rawCrop = project.workspaceState.crop;
  const timedCrop =
    rawCrop &&
    (rawCrop.x > 0.005 || rawCrop.y > 0.005 || rawCrop.w < 0.99 || rawCrop.h < 0.99) &&
    rawCrop.start != null &&
    rawCrop.end != null &&
    rawCrop.end > rawCrop.start
      ? { start: rawCrop.start, end: rawCrop.end }
      : null;

  const trackLabelRows = [
    { label: 'Video', heightClass: 'h-14', dotClass: 'bg-sky-400' },
    { label: 'Audio', heightClass: 'h-9', dotClass: 'bg-emerald-400' },
    { label: 'Captions', heightClass: 'h-9', dotClass: 'bg-purple-400' },
    { label: 'Zooms', heightClass: 'h-7', dotClass: 'bg-sky-300' },
    { label: 'Drawings', heightClass: 'h-7', dotClass: 'bg-rose-400' },
    { label: 'SFX', heightClass: 'h-7', dotClass: 'bg-teal-400' },
  ];

  const [timelineHeight, setTimelineHeight] = useState(215);
  const [isTimelineCollapsed, setIsTimelineCollapsed] = useState(false);

  const startTimelineResize = (e: React.MouseEvent) => {
    e.preventDefault();
    const startY = e.clientY;
    const startH = isTimelineCollapsed ? 215 : timelineHeight;
    if (isTimelineCollapsed) {
      setIsTimelineCollapsed(false);
    }

    const onMove = (ev: MouseEvent) => {
      const nextH = Math.max(140, Math.min(440, startH + (startY - ev.clientY)));
      setTimelineHeight(nextH);
    };

    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  return (
    <section
      style={{ height: isTimelineCollapsed ? '44px' : `${timelineHeight}px` }}
      className="relative border-t border-[#272f40] bg-[#151922] flex flex-col shrink-0"
    >
      {/* Top Drag-Resize Handle */}
      <div
        onMouseDown={startTimelineResize}
        title="Drag up/down to resize timeline height"
        className="absolute -top-1 left-0 right-0 h-2 cursor-row-resize z-40 group flex items-center justify-center"
      >
        <div className="w-12 h-1 rounded-full bg-slate-600/40 group-hover:bg-amber-400/80 transition" />
      </div>

      {/* Timeline Action Toolbar */}
      <div className="h-11 px-3.5 border-b border-[#272f40] flex items-center justify-between gap-3 shrink-0">
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            disabled={!activeClipAtPlayhead}
            onClick={handleSplitAtPlayhead}
            title="Split clip at playhead (S)"
            className="px-2.5 py-1.5 rounded-lg bg-[#1c212d] hover:bg-[#232a39] disabled:opacity-40 border border-[#272f40] text-xs font-medium text-slate-200 flex items-center gap-1.5 transition"
          >
            <Scissors className="w-3.5 h-3.5 text-amber-300" />
            Split (S)
          </button>

          <button
            type="button"
            disabled={selectedIds.length === 0}
            onClick={handleDeleteSelected}
            title="Delete selected clip (Del)"
            className="px-2.5 py-1.5 rounded-lg bg-[#1c212d] hover:bg-[#232a39] disabled:opacity-40 border border-[#272f40] text-xs font-medium text-slate-200 flex items-center gap-1.5 transition"
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
            className="px-2.5 py-1.5 rounded-lg bg-[#1c212d] hover:bg-[#232a39] disabled:opacity-40 border border-[#272f40] text-xs font-medium text-slate-200 flex items-center gap-1.5 transition"
          >
            <Merge className="w-3.5 h-3.5 text-emerald-300" />
            Merge
          </button>

          {selectedIndex !== -1 && clips.length > 1 && (
            <div className="flex items-center gap-1 ml-1">
              <button
                type="button"
                disabled={selectedIndex <= 0}
                onClick={() => reorder_clips(clips[selectedIndex].id, selectedIndex - 1)}
                title="Move clip earlier"
                aria-label="Move clip earlier"
                className="p-1.5 rounded-lg bg-[#1c212d] hover:bg-[#232a39] disabled:opacity-30 border border-[#272f40] text-slate-300"
              >
                <ArrowLeft className="w-3.5 h-3.5" />
              </button>
              <button
                type="button"
                disabled={selectedIndex >= clips.length - 1}
                onClick={() => reorder_clips(clips[selectedIndex].id, selectedIndex + 1)}
                title="Move clip later"
                aria-label="Move clip later"
                className="p-1.5 rounded-lg bg-[#1c212d] hover:bg-[#232a39] disabled:opacity-30 border border-[#272f40] text-slate-300"
              >
                <ArrowRight className="w-3.5 h-3.5" />
              </button>
            </div>
          )}

          {/* Speed Control for Selected Clip */}
          {selectedClip && (
            <div className="flex items-center gap-1.5 ml-1 bg-[#1c212d] border border-[#272f40] rounded-lg px-2.5 py-1">
              <Gauge className="w-3.5 h-3.5 text-cyan-300" />
              <span className="text-[11px] text-slate-400">Speed:</span>
              <select
                value={String(selectedClip.speed ?? 1)}
                onChange={(e) => set_clip_speed(selectedClip.id, Number(e.target.value))}
                title="Clip Playback Speed"
                aria-label="Clip playback speed"
                className="bg-transparent text-xs font-mono tabular-nums text-cyan-200 outline-none cursor-pointer"
              >
                {Array.from(
                  new Set([0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4, selectedClip.speed ?? 1]),
                )
                  .sort((a, b) => a - b)
                  .map((spd) => (
                    <option key={spd} value={spd} className="bg-[#161b26] text-slate-100">
                      {spd}x
                    </option>
                  ))}
              </select>
            </div>
          )}

          {/* Video Audio Cut & Mute Controls (affect audio only, not video frames) */}
          <div className="flex items-center gap-1 ml-1 pl-1.5 border-l border-[#272f40]">
            <button
              type="button"
              disabled={!canSplitAudioAtPlayhead}
              onClick={() => split_audio_at(playhead)}
              title="Cut / split video audio at playhead so you can mute or adjust one part"
              className="px-2 py-1.5 rounded-lg bg-[#1c212d] hover:bg-[#232a39] disabled:opacity-40 border border-[#272f40] text-xs font-medium text-emerald-200 flex items-center gap-1 transition cursor-pointer"
            >
              <AudioLines className="w-3.5 h-3.5 text-emerald-300" />
              <span className="hidden sm:inline">Cut Audio</span>
            </button>
            <button
              type="button"
              disabled={clips.length === 0 || totalDuration <= 0.15}
              onClick={() => {
                if (selectedAudioEdit) {
                  update_audio_edit(selectedAudioEdit.id, {
                    muted: !selectedAudioEdit.muted,
                    volume: !selectedAudioEdit.muted ? 0 : 1,
                    label: !selectedAudioEdit.muted ? 'Muted audio' : 'Audio 100%',
                  });
                } else {
                  const s = Math.max(0, Math.min(totalDuration - 0.2, playhead));
                  const e = Math.min(totalDuration, Math.max(s + 0.25, s + 2.5));
                  add_audio_edit({
                    start: Number(s.toFixed(2)),
                    end: Number(e.toFixed(2)),
                    muted: true,
                    volume: 0,
                    label: 'Muted audio',
                  });
                }
              }}
              title={
                selectedAudioEdit
                  ? selectedAudioEdit.muted
                    ? 'Unmute selected audio part'
                    : 'Mute / delete selected audio part'
                  : 'Mute a 2.5s video audio segment starting at playhead (keeps video)'
              }
              className={`px-2 py-1.5 rounded-lg border text-xs font-medium flex items-center gap-1 transition cursor-pointer disabled:opacity-40 ${
                selectedAudioEdit?.muted
                  ? 'bg-rose-500/20 border-rose-400/60 text-rose-200'
                  : 'bg-[#1c212d] hover:bg-[#232a39] border-[#272f40] text-rose-200'
              }`}
            >
              <VolumeX className="w-3.5 h-3.5 text-rose-400" />
              <span className="hidden md:inline">
                {selectedAudioEdit?.muted ? 'Unmute Part' : 'Mute Audio'}
              </span>
            </button>
            {selectedClip && (
              <button
                type="button"
                onClick={() => {
                  if (selectedClipAudioMute) {
                    delete_audio_edit(selectedClipAudioMute.id);
                  } else {
                    add_audio_edit({
                      start: Number(selectedClip.timelineStart.toFixed(3)),
                      end: Number(
                        (selectedClip.timelineStart + selectedClip.duration).toFixed(3),
                      ),
                      muted: true,
                      volume: 0,
                      label: `Muted: ${selectedClip.title}`,
                    });
                  }
                }}
                title={
                  selectedClipAudioMute
                    ? 'Restore selected clip video audio'
                    : 'Mute video audio for the entire selected clip'
                }
                className={`px-2 py-1.5 rounded-lg border text-xs font-medium transition cursor-pointer ${
                  selectedClipAudioMute
                    ? 'bg-rose-500/25 border-rose-400 text-rose-100'
                    : 'bg-[#1c212d] hover:bg-[#232a39] border-[#272f40] text-slate-200'
                }`}
              >
                {selectedClipAudioMute ? 'Unmute Clip' : 'Mute Clip'}
              </button>
            )}
          </div>
        </div>

        {/* Subtle Keyboard Cheat-Sheet */}
        <div className="hidden xl:flex items-center gap-3 text-[11px] text-slate-400">
          <span>
            <strong className="text-slate-200 font-medium">Space</strong> Play
          </span>
          <span className="text-slate-600">·</span>
          <span>
            <strong className="text-slate-200 font-medium">S</strong> Split
          </span>
          <span className="text-slate-600">·</span>
          <span>
            <strong className="text-slate-200 font-medium">Del</strong> Remove
          </span>
          <span className="text-slate-600">·</span>
          <span>
            <strong className="text-slate-200 font-medium">Ctrl+Z</strong> Undo
          </span>
        </div>

        <div className="flex items-center gap-2 text-xs text-slate-400">
          {trimPreview && (
            <span className="font-mono tabular-nums text-xs text-amber-300 mr-2">
              Trim: {trimPreview.sourceStart.toFixed(1)}s → {trimPreview.sourceEnd.toFixed(1)}s
            </span>
          )}
          {sfxTrimPreview && (
            <span className="font-mono tabular-nums text-xs text-emerald-300 mr-2">
              SFX Trim: {sfxTrimPreview.trimStart.toFixed(1)}s →{' '}
              {(sfxTrimPreview.trimStart + sfxTrimPreview.duration).toFixed(1)}s (
              {sfxTrimPreview.duration.toFixed(1)}s)
            </span>
          )}
          <button
            type="button"
            onClick={() => setTimelineZoom(timelineZoom - 4)}
            className="p-1.5 rounded-lg hover:bg-white/5 text-slate-300"
            title="Zoom out timeline"
            aria-label="Zoom out timeline"
          >
            <ZoomOut className="w-3.5 h-3.5" />
          </button>
          <input
            type="range"
            min={4}
            max={60}
            value={timelineZoom}
            aria-label="Timeline horizontal zoom"
            onChange={(e) => setTimelineZoom(Number(e.target.value))}
            className="w-24 accent-amber-400"
          />
          <button
            type="button"
            onClick={() => setTimelineZoom(timelineZoom + 4)}
            className="p-1.5 rounded-lg hover:bg-white/5 text-slate-300"
            title="Zoom in timeline"
            aria-label="Zoom in timeline"
          >
            <ZoomIn className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={() => setIsTimelineCollapsed((prev) => !prev)}
            className="ml-1 p-1.5 rounded-lg bg-[#1c212d] hover:bg-[#232a39] border border-[#272f40] text-slate-300 transition"
            title={isTimelineCollapsed ? 'Expand timeline' : 'Minimize timeline for larger workspace'}
            aria-label={isTimelineCollapsed ? 'Expand timeline' : 'Minimize timeline'}
          >
            {isTimelineCollapsed ? (
              <ChevronUp className="w-3.5 h-3.5 text-amber-300" />
            ) : (
              <ChevronDown className="w-3.5 h-3.5" />
            )}
          </button>
        </div>
      </div>

      {/* Scrollable Tracks Area with Sticky Track-Names Column */}
      {!isTimelineCollapsed && (
      <div ref={trackScrollRef} className="flex-1 overflow-auto relative">
        <div className="flex min-h-full min-w-max">
          {/* Left Sticky Track Header Column */}
          <div className="sticky left-0 z-30 w-24 shrink-0 bg-[#151922] border-r border-[#272f40] flex flex-col">
            <div className="h-7 border-b border-[#272f40] px-2.5 flex items-center text-[10px] font-semibold uppercase tracking-wider text-slate-400 shrink-0">
              Tracks
            </div>
            <div className="py-2 px-1.5 space-y-2">
              {trackLabelRows.map((row) => (
                <div
                  key={row.label}
                  className={`${row.heightClass} flex items-center gap-1.5 px-2 rounded-lg bg-[#1c212d]/45 border border-[#272f40]/70 text-[11px] font-medium text-slate-200`}
                >
                  <span className={`w-1.5 h-1.5 rounded-full ${row.dotClass} shrink-0`} />
                  <span className="truncate">{row.label}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Right Time Ruler & Timeline Lanes */}
          <div
            className="relative flex flex-col shrink-0"
            style={{ width: `${timelineWidthPx}px` }}
          >
            {/* Ruler */}
            <div
              onMouseDown={handleRulerMouseDown}
              className="h-7 border-b border-[#272f40] bg-[#12161f] relative cursor-ew-resize shrink-0"
            >
              {ticks.map((t) => (
                <div
                  key={t}
                  className="absolute top-0 bottom-0 border-l border-[#272f40] pl-1.5 text-[11px] font-mono tabular-nums text-slate-400 flex items-center"
                  style={{ left: `${t * timelineZoom}px` }}
                >
                  {formatRulerTick(t)}
                </div>
              ))}
            </div>

            {/* Tracks Container */}
            <div className="flex-1 relative py-2 space-y-2">
              {/* Video Track */}
              <div className="h-14 relative rounded-lg bg-[#0f1218]/75 border border-[#272f40]/80">
                {clips.map((clip, idx) => {
                  const clipSpeed = Math.max(0.25, Math.min(4.0, clip.speed ?? 1));
                  const isSelected = selectedIds.includes(clip.id);
                  const isTrimming = trimPreview && trimPreview.clipId === clip.id;
                  const displayDuration = isTrimming
                    ? Math.max(0.1, (trimPreview.sourceEnd - trimPreview.sourceStart) / clipSpeed)
                    : clip.duration;
                  const leftPx = clip.timelineStart * timelineZoom;
                  const nextClipStart =
                    idx + 1 < clips.length ? clips[idx + 1].timelineStart : null;
                  const maxWidthPx =
                    nextClipStart != null
                      ? Math.max(4, (nextClipStart - clip.timelineStart) * timelineZoom)
                      : Infinity;
                  const widthPx = Math.min(
                    maxWidthPx,
                    Math.max(24, displayDuration * timelineZoom),
                  );

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
                      className={`absolute top-1 bottom-1 rounded-lg border flex items-center overflow-hidden cursor-pointer group transition-colors ${
                        isSelected
                          ? 'bg-sky-500/25 border-amber-400'
                          : 'bg-sky-500/15 border-sky-500/40 hover:border-sky-400'
                      }`}
                    >
                      {/* Left Trim Handle */}
                      <div
                        draggable={false}
                        onDragStart={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                        }}
                        onMouseDown={(e) => startTrimDrag(e, clip, 'left')}
                        title="Drag to trim clip start"
                        className="w-2 h-full bg-amber-400/35 hover:bg-amber-400 cursor-ew-resize shrink-0"
                      />
                      <div className="flex-1 px-2 min-w-0 flex items-center justify-between gap-1.5">
                        <span className="text-xs font-medium text-slate-100 truncate">
                          {clip.title}
                        </span>
                        <span className="text-[11px] font-mono tabular-nums text-slate-300 shrink-0">
                          {displayDuration.toFixed(1)}s
                        </span>
                      </div>
                      {Math.abs((clip.speed ?? 1) - 1) > 0.01 && (
                        <span className="mr-1 px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-200 text-[10px] font-mono tabular-nums shrink-0">
                          {clip.speed}x
                        </span>
                      )}
                      {clip.viralityScore && (
                        <span className="mr-1 px-1.5 py-0.5 rounded bg-amber-400/20 text-amber-200 text-[10px] font-mono tabular-nums shrink-0">
                          ★{clip.viralityScore}
                        </span>
                      )}
                      {/* Right Trim Handle */}
                      <div
                        draggable={false}
                        onDragStart={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                        }}
                        onMouseDown={(e) => startTrimDrag(e, clip, 'right')}
                        title="Drag to trim clip end"
                        className="w-2 h-full bg-amber-400/35 hover:bg-amber-400 cursor-ew-resize shrink-0"
                      />
                    </div>
                  );
                })}
                {timedCrop && (
                  <div
                    style={{
                      left: `${timedCrop.start * timelineZoom}px`,
                      width: `${Math.max(24, (timedCrop.end - timedCrop.start) * timelineZoom)}px`,
                    }}
                    title={`Timed Crop: ${timedCrop.start.toFixed(1)}s – ${timedCrop.end.toFixed(1)}s`}
                    className="absolute top-0 h-4 bg-amber-400/90 text-slate-950 rounded-b-md px-1.5 text-[9px] font-semibold flex items-center gap-1 pointer-events-none z-10 shadow-sm truncate"
                  >
                    <Crop className="w-2.5 h-2.5 shrink-0" />
                    <span className="truncate">
                      Crop {timedCrop.start.toFixed(1)}s–{timedCrop.end.toFixed(1)}s
                    </span>
                  </div>
                )}
              </div>

              {/* Lightweight Audio Peaks Track */}
              <div className="h-9 relative rounded-lg bg-[#0f1218]/75 border border-[#272f40]/80 overflow-hidden flex items-center">
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
                      className="absolute top-1 bottom-1 flex items-center gap-[1px] px-1 overflow-hidden bg-emerald-500/10 border-x border-[#272f40]"
                    >
                      {clipPeaks.map((p, i) => (
                        <div
                          key={i}
                          className="flex-1 bg-emerald-400/60 rounded-full min-w-[1px]"
                          style={{ height: `${Math.max(15, Math.round(p * 100))}%` }}
                        />
                      ))}
                    </div>
                  );
                })}

                {/* Interactive Video Audio Mute / Cut / Volume Segments */}
                {audioEdits.map((ae) => {
                  const drag =
                    rangeDragPreview && rangeDragPreview.id === ae.id ? rangeDragPreview : null;
                  const s = drag ? drag.start : ae.start;
                  const eTime = drag ? drag.end : ae.end;
                  const leftPx = s * timelineZoom;
                  const widthPx = Math.max(26, (eTime - s) * timelineZoom);
                  const active = selectedAudioEditId === ae.id;
                  const effGain = ae.muted ? 0 : ae.volume;
                  const isMutedSeg = ae.muted || effGain <= 0.001;
                  const isBoostSeg = !isMutedSeg && effGain > 1.01;
                  const isDuckSeg = !isMutedSeg && effGain < 0.99;

                  return (
                    <div
                      key={ae.id}
                      onClick={(ev) => {
                        ev.stopPropagation();
                        setSelectedAudioEditId(ae.id);
                      }}
                      onMouseDown={(ev) =>
                        startRangeDrag(ev, ae.id, ae.start, ae.end, 'move', (ns, ne) =>
                          update_audio_edit(ae.id, { start: ns, end: ne }),
                        )
                      }
                      title={`${ae.label || (isMutedSeg ? 'Muted audio' : `Audio ${Math.round(effGain * 100)}%`)} (${s.toFixed(1)}s–${eTime.toFixed(1)}s) · Drag edges to resize`}
                      style={{ left: `${leftPx}px`, width: `${widthPx}px` }}
                      className={`absolute top-0.5 bottom-0.5 rounded-md border flex items-center overflow-hidden cursor-grab z-10 ${
                        isMutedSeg
                          ? active
                            ? 'bg-rose-500/40 border-rose-300'
                            : 'bg-rose-500/25 border-rose-400/70 hover:border-rose-300'
                          : isDuckSeg
                            ? active
                              ? 'bg-amber-500/35 border-amber-300'
                              : 'bg-amber-500/20 border-amber-400/60 hover:border-amber-300'
                            : isBoostSeg
                              ? active
                                ? 'bg-cyan-500/35 border-cyan-300'
                                : 'bg-cyan-500/20 border-cyan-400/60 hover:border-cyan-300'
                              : active
                                ? 'bg-emerald-500/35 border-emerald-200'
                                : 'bg-emerald-500/20 border-emerald-300/70 hover:border-emerald-200'
                      }`}
                    >
                      <div
                        onMouseDown={(ev) =>
                          startRangeDrag(ev, ae.id, ae.start, ae.end, 'left', (ns, ne) =>
                            update_audio_edit(ae.id, { start: ns, end: ne }),
                          )
                        }
                        className="w-1.5 h-full bg-white/15 hover:bg-white/60 cursor-ew-resize shrink-0"
                      />
                      <button
                        type="button"
                        onMouseDown={(ev) => ev.stopPropagation()}
                        onClick={(ev) => {
                          ev.stopPropagation();
                          update_audio_edit(ae.id, {
                            muted: !ae.muted,
                            volume: !ae.muted ? 0 : 1,
                            label: !ae.muted ? 'Muted audio' : 'Audio 100%',
                          });
                        }}
                        title={isMutedSeg ? 'Unmute this audio segment' : 'Mute/cut this audio segment'}
                        className="p-0.5 ml-0.5 text-slate-100 hover:text-amber-200 shrink-0 cursor-pointer"
                      >
                        {isMutedSeg ? (
                          <VolumeX className="w-3 h-3 text-rose-200" />
                        ) : (
                          <Volume2 className="w-3 h-3 text-emerald-100" />
                        )}
                      </button>
                      <span className="flex-1 px-1 text-[10px] font-medium text-slate-100 truncate">
                        {isMutedSeg ? 'Muted' : `${Math.round(effGain * 100)}%`}
                      </span>
                      <button
                        type="button"
                        onMouseDown={(ev) => ev.stopPropagation()}
                        onClick={(ev) => {
                          ev.stopPropagation();
                          delete_audio_edit(ae.id);
                        }}
                        className="p-0.5 text-slate-200 hover:text-red-300 shrink-0 cursor-pointer"
                        title="Restore original audio (delete audio segment)"
                      >
                        <X className="w-3 h-3" />
                      </button>
                      <div
                        onMouseDown={(ev) =>
                          startRangeDrag(ev, ae.id, ae.start, ae.end, 'right', (ns, ne) =>
                            update_audio_edit(ae.id, { start: ns, end: ne }),
                          )
                        }
                        className="w-1.5 h-full bg-white/15 hover:bg-white/60 cursor-ew-resize shrink-0"
                      />
                    </div>
                  );
                })}
              </div>

              {/* Captions Track */}
              <div className="h-9 relative rounded-lg bg-[#0f1218]/75 border border-[#272f40]/80">
                {mappedCaptions.map((mc, idx) => {
                  const leftPx = mc.tlStart * timelineZoom;
                  const widthPx = Math.max(16, (mc.tlEnd - mc.tlStart) * timelineZoom);
                  return (
                    <div
                      key={`${mc.id}_${idx}`}
                      style={{ left: `${leftPx}px`, width: `${widthPx}px` }}
                      className={`absolute top-1 bottom-1 rounded-md border flex items-center overflow-hidden ${
                        mc.isManuallyEdited
                          ? 'bg-amber-400/20 border-amber-400/70'
                          : 'bg-purple-500/20 border-purple-400/50'
                      }`}
                    >
                      <div
                        onMouseDown={(e) =>
                          startCaptionEdgeDrag(
                            e,
                            mc.id,
                            mc.srcStart,
                            mc.srcEnd,
                            'left',
                            mc.clipSpeed,
                          )
                        }
                        className="w-1.5 h-full hover:bg-amber-400 cursor-ew-resize shrink-0"
                      />
                      <span className="flex-1 px-1.5 text-[11px] text-slate-100 truncate">
                        {mc.text}
                      </span>
                      <div
                        onMouseDown={(e) =>
                          startCaptionEdgeDrag(
                            e,
                            mc.id,
                            mc.srcStart,
                            mc.srcEnd,
                            'right',
                            mc.clipSpeed,
                          )
                        }
                        className="w-1.5 h-full hover:bg-amber-400 cursor-ew-resize shrink-0"
                      />
                    </div>
                  );
                })}

                {/* Interactive Video Audio Mute / Cut / Volume Segments */}
                {audioEdits.map((ae) => {
                  const drag =
                    rangeDragPreview && rangeDragPreview.id === ae.id ? rangeDragPreview : null;
                  const s = drag ? drag.start : ae.start;
                  const eTime = drag ? drag.end : ae.end;
                  const leftPx = s * timelineZoom;
                  const widthPx = Math.max(26, (eTime - s) * timelineZoom);
                  const active = selectedAudioEditId === ae.id;
                  const effGain = ae.muted ? 0 : ae.volume;
                  const isMutedSeg = ae.muted || effGain <= 0.001;
                  const isBoostSeg = !isMutedSeg && effGain > 1.01;
                  const isDuckSeg = !isMutedSeg && effGain < 0.99;

                  return (
                    <div
                      key={ae.id}
                      onClick={(ev) => {
                        ev.stopPropagation();
                        setSelectedAudioEditId(ae.id);
                      }}
                      onMouseDown={(ev) =>
                        startRangeDrag(ev, ae.id, ae.start, ae.end, 'move', (ns, ne) =>
                          update_audio_edit(ae.id, { start: ns, end: ne }),
                        )
                      }
                      title={`${ae.label || (isMutedSeg ? 'Muted audio' : `Audio ${Math.round(effGain * 100)}%`)} (${s.toFixed(1)}s–${eTime.toFixed(1)}s) · Drag edges to resize`}
                      style={{ left: `${leftPx}px`, width: `${widthPx}px` }}
                      className={`absolute top-0.5 bottom-0.5 rounded-md border flex items-center overflow-hidden cursor-grab z-10 ${
                        isMutedSeg
                          ? active
                            ? 'bg-rose-500/40 border-rose-300'
                            : 'bg-rose-500/25 border-rose-400/70 hover:border-rose-300'
                          : isDuckSeg
                            ? active
                              ? 'bg-amber-500/35 border-amber-300'
                              : 'bg-amber-500/20 border-amber-400/60 hover:border-amber-300'
                            : isBoostSeg
                              ? active
                                ? 'bg-cyan-500/35 border-cyan-300'
                                : 'bg-cyan-500/20 border-cyan-400/60 hover:border-cyan-300'
                              : active
                                ? 'bg-emerald-500/35 border-emerald-200'
                                : 'bg-emerald-500/20 border-emerald-300/70 hover:border-emerald-200'
                      }`}
                    >
                      <div
                        onMouseDown={(ev) =>
                          startRangeDrag(ev, ae.id, ae.start, ae.end, 'left', (ns, ne) =>
                            update_audio_edit(ae.id, { start: ns, end: ne }),
                          )
                        }
                        className="w-1.5 h-full bg-white/15 hover:bg-white/60 cursor-ew-resize shrink-0"
                      />
                      <button
                        type="button"
                        onMouseDown={(ev) => ev.stopPropagation()}
                        onClick={(ev) => {
                          ev.stopPropagation();
                          update_audio_edit(ae.id, {
                            muted: !ae.muted,
                            volume: !ae.muted ? 0 : 1,
                            label: !ae.muted ? 'Muted audio' : 'Audio 100%',
                          });
                        }}
                        title={isMutedSeg ? 'Unmute this audio segment' : 'Mute/cut this audio segment'}
                        className="p-0.5 ml-0.5 text-slate-100 hover:text-amber-200 shrink-0 cursor-pointer"
                      >
                        {isMutedSeg ? (
                          <VolumeX className="w-3 h-3 text-rose-200" />
                        ) : (
                          <Volume2 className="w-3 h-3 text-emerald-100" />
                        )}
                      </button>
                      <span className="flex-1 px-1 text-[10px] font-medium text-slate-100 truncate">
                        {isMutedSeg ? 'Muted' : `${Math.round(effGain * 100)}%`}
                      </span>
                      <button
                        type="button"
                        onMouseDown={(ev) => ev.stopPropagation()}
                        onClick={(ev) => {
                          ev.stopPropagation();
                          delete_audio_edit(ae.id);
                        }}
                        className="p-0.5 text-slate-200 hover:text-red-300 shrink-0 cursor-pointer"
                        title="Restore original audio (delete audio segment)"
                      >
                        <X className="w-3 h-3" />
                      </button>
                      <div
                        onMouseDown={(ev) =>
                          startRangeDrag(ev, ae.id, ae.start, ae.end, 'right', (ns, ne) =>
                            update_audio_edit(ae.id, { start: ns, end: ne }),
                          )
                        }
                        className="w-1.5 h-full bg-white/15 hover:bg-white/60 cursor-ew-resize shrink-0"
                      />
                    </div>
                  );
                })}
              </div>

              {/* Zooms Track */}
              <div className="h-7 relative rounded-lg bg-[#0f1218]/75 border border-[#272f40]/80">
                {zooms.map((z) => {
                  const drag =
                    rangeDragPreview && rangeDragPreview.id === z.id ? rangeDragPreview : null;
                  const s = drag ? drag.start : z.start;
                  const eTime = drag ? drag.end : z.end;
                  const leftPx = s * timelineZoom;
                  const widthPx = Math.max(24, (eTime - s) * timelineZoom);
                  const active = selectedZoomId === z.id;
                  return (
                    <div
                      key={z.id}
                      onClick={() => setSelectedZoomId(z.id)}
                      onMouseDown={(ev) =>
                        startRangeDrag(ev, z.id, z.start, z.end, 'move', (ns, ne) =>
                          update_zoom_effect(z.id, { start: ns, end: ne }),
                        )
                      }
                      style={{ left: `${leftPx}px`, width: `${widthPx}px` }}
                      className={`absolute top-0.5 bottom-0.5 rounded-md border flex items-center overflow-hidden cursor-grab ${
                        active
                          ? 'bg-sky-500/35 border-sky-300'
                          : 'bg-sky-500/20 border-sky-500/50 hover:border-sky-400'
                      }`}
                    >
                      <div
                        onMouseDown={(ev) =>
                          startRangeDrag(ev, z.id, z.start, z.end, 'left', (ns, ne) =>
                            update_zoom_effect(z.id, { start: ns, end: ne }),
                          )
                        }
                        className="w-1.5 h-full hover:bg-sky-300 cursor-ew-resize shrink-0"
                      />
                      <span className="flex-1 px-1.5 text-[10px] font-medium text-sky-100 truncate">
                        {z.kind === 'punch_in'
                          ? `Punch ${z.endScale}x`
                          : z.kind === 'zoom_out'
                            ? `Out ${z.startScale}→${z.endScale}x`
                            : `In ${z.endScale}x`}
                      </span>
                      <button
                        type="button"
                        onMouseDown={(ev) => ev.stopPropagation()}
                        onClick={(ev) => {
                          ev.stopPropagation();
                          delete_zoom_effect(z.id);
                        }}
                        className="p-0.5 text-sky-200 hover:text-red-300 shrink-0"
                        title="Delete zoom"
                      >
                        <X className="w-3 h-3" />
                      </button>
                      <div
                        onMouseDown={(ev) =>
                          startRangeDrag(ev, z.id, z.start, z.end, 'right', (ns, ne) =>
                            update_zoom_effect(z.id, { start: ns, end: ne }),
                          )
                        }
                        className="w-1.5 h-full hover:bg-sky-300 cursor-ew-resize shrink-0"
                      />
                    </div>
                  );
                })}

                {/* Interactive Video Audio Mute / Cut / Volume Segments */}
                {audioEdits.map((ae) => {
                  const drag =
                    rangeDragPreview && rangeDragPreview.id === ae.id ? rangeDragPreview : null;
                  const s = drag ? drag.start : ae.start;
                  const eTime = drag ? drag.end : ae.end;
                  const leftPx = s * timelineZoom;
                  const widthPx = Math.max(26, (eTime - s) * timelineZoom);
                  const active = selectedAudioEditId === ae.id;
                  const effGain = ae.muted ? 0 : ae.volume;
                  const isMutedSeg = ae.muted || effGain <= 0.001;
                  const isBoostSeg = !isMutedSeg && effGain > 1.01;
                  const isDuckSeg = !isMutedSeg && effGain < 0.99;

                  return (
                    <div
                      key={ae.id}
                      onClick={(ev) => {
                        ev.stopPropagation();
                        setSelectedAudioEditId(ae.id);
                      }}
                      onMouseDown={(ev) =>
                        startRangeDrag(ev, ae.id, ae.start, ae.end, 'move', (ns, ne) =>
                          update_audio_edit(ae.id, { start: ns, end: ne }),
                        )
                      }
                      title={`${ae.label || (isMutedSeg ? 'Muted audio' : `Audio ${Math.round(effGain * 100)}%`)} (${s.toFixed(1)}s–${eTime.toFixed(1)}s) · Drag edges to resize`}
                      style={{ left: `${leftPx}px`, width: `${widthPx}px` }}
                      className={`absolute top-0.5 bottom-0.5 rounded-md border flex items-center overflow-hidden cursor-grab z-10 ${
                        isMutedSeg
                          ? active
                            ? 'bg-rose-500/40 border-rose-300'
                            : 'bg-rose-500/25 border-rose-400/70 hover:border-rose-300'
                          : isDuckSeg
                            ? active
                              ? 'bg-amber-500/35 border-amber-300'
                              : 'bg-amber-500/20 border-amber-400/60 hover:border-amber-300'
                            : isBoostSeg
                              ? active
                                ? 'bg-cyan-500/35 border-cyan-300'
                                : 'bg-cyan-500/20 border-cyan-400/60 hover:border-cyan-300'
                              : active
                                ? 'bg-emerald-500/35 border-emerald-200'
                                : 'bg-emerald-500/20 border-emerald-300/70 hover:border-emerald-200'
                      }`}
                    >
                      <div
                        onMouseDown={(ev) =>
                          startRangeDrag(ev, ae.id, ae.start, ae.end, 'left', (ns, ne) =>
                            update_audio_edit(ae.id, { start: ns, end: ne }),
                          )
                        }
                        className="w-1.5 h-full bg-white/15 hover:bg-white/60 cursor-ew-resize shrink-0"
                      />
                      <button
                        type="button"
                        onMouseDown={(ev) => ev.stopPropagation()}
                        onClick={(ev) => {
                          ev.stopPropagation();
                          update_audio_edit(ae.id, {
                            muted: !ae.muted,
                            volume: !ae.muted ? 0 : 1,
                            label: !ae.muted ? 'Muted audio' : 'Audio 100%',
                          });
                        }}
                        title={isMutedSeg ? 'Unmute this audio segment' : 'Mute/cut this audio segment'}
                        className="p-0.5 ml-0.5 text-slate-100 hover:text-amber-200 shrink-0 cursor-pointer"
                      >
                        {isMutedSeg ? (
                          <VolumeX className="w-3 h-3 text-rose-200" />
                        ) : (
                          <Volume2 className="w-3 h-3 text-emerald-100" />
                        )}
                      </button>
                      <span className="flex-1 px-1 text-[10px] font-medium text-slate-100 truncate">
                        {isMutedSeg ? 'Muted' : `${Math.round(effGain * 100)}%`}
                      </span>
                      <button
                        type="button"
                        onMouseDown={(ev) => ev.stopPropagation()}
                        onClick={(ev) => {
                          ev.stopPropagation();
                          delete_audio_edit(ae.id);
                        }}
                        className="p-0.5 text-slate-200 hover:text-red-300 shrink-0 cursor-pointer"
                        title="Restore original audio (delete audio segment)"
                      >
                        <X className="w-3 h-3" />
                      </button>
                      <div
                        onMouseDown={(ev) =>
                          startRangeDrag(ev, ae.id, ae.start, ae.end, 'right', (ns, ne) =>
                            update_audio_edit(ae.id, { start: ns, end: ne }),
                          )
                        }
                        className="w-1.5 h-full bg-white/15 hover:bg-white/60 cursor-ew-resize shrink-0"
                      />
                    </div>
                  );
                })}
              </div>

              {/* Drawings Track */}
              <div className="h-7 relative rounded-lg bg-[#0f1218]/75 border border-[#272f40]/80">
                {drawings.map((d) => {
                  const drag =
                    rangeDragPreview && rangeDragPreview.id === d.id ? rangeDragPreview : null;
                  const s = drag ? drag.start : d.start;
                  const eTime = drag ? drag.end : d.end;
                  const leftPx = s * timelineZoom;
                  const widthPx = Math.max(24, (eTime - s) * timelineZoom);
                  const active = selectedDrawingId === d.id;
                  return (
                    <div
                      key={d.id}
                      onClick={() => setSelectedDrawingId(d.id)}
                      onMouseDown={(ev) =>
                        startRangeDrag(ev, d.id, d.start, d.end, 'move', (ns, ne) =>
                          update_drawing(d.id, { start: ns, end: ne }),
                        )
                      }
                      style={{ left: `${leftPx}px`, width: `${widthPx}px` }}
                      className={`absolute top-0.5 bottom-0.5 rounded-md border flex items-center overflow-hidden cursor-grab ${
                        active
                          ? 'bg-rose-500/35 border-rose-300'
                          : 'bg-rose-500/20 border-rose-500/50 hover:border-rose-400'
                      }`}
                    >
                      <div
                        onMouseDown={(ev) =>
                          startRangeDrag(ev, d.id, d.start, d.end, 'left', (ns, ne) =>
                            update_drawing(d.id, { start: ns, end: ne }),
                          )
                        }
                        className="w-1.5 h-full hover:bg-rose-300 cursor-ew-resize shrink-0"
                      />
                      <span
                        className="w-2 h-2 rounded-full ml-1.5 shrink-0"
                        style={{ backgroundColor: d.color }}
                      />
                      <span className="flex-1 px-1.5 text-[10px] font-medium text-rose-100 capitalize truncate">
                        {d.tool}
                      </span>
                      <button
                        type="button"
                        onMouseDown={(ev) => ev.stopPropagation()}
                        onClick={(ev) => {
                          ev.stopPropagation();
                          delete_drawing(d.id);
                        }}
                        className="p-0.5 text-rose-200 hover:text-red-300 shrink-0"
                        title="Delete drawing"
                      >
                        <X className="w-3 h-3" />
                      </button>
                      <div
                        onMouseDown={(ev) =>
                          startRangeDrag(ev, d.id, d.start, d.end, 'right', (ns, ne) =>
                            update_drawing(d.id, { start: ns, end: ne }),
                          )
                        }
                        className="w-1.5 h-full hover:bg-rose-300 cursor-ew-resize shrink-0"
                      />
                    </div>
                  );
                })}

                {/* Interactive Video Audio Mute / Cut / Volume Segments */}
                {audioEdits.map((ae) => {
                  const drag =
                    rangeDragPreview && rangeDragPreview.id === ae.id ? rangeDragPreview : null;
                  const s = drag ? drag.start : ae.start;
                  const eTime = drag ? drag.end : ae.end;
                  const leftPx = s * timelineZoom;
                  const widthPx = Math.max(26, (eTime - s) * timelineZoom);
                  const active = selectedAudioEditId === ae.id;
                  const effGain = ae.muted ? 0 : ae.volume;
                  const isMutedSeg = ae.muted || effGain <= 0.001;
                  const isBoostSeg = !isMutedSeg && effGain > 1.01;
                  const isDuckSeg = !isMutedSeg && effGain < 0.99;

                  return (
                    <div
                      key={ae.id}
                      onClick={(ev) => {
                        ev.stopPropagation();
                        setSelectedAudioEditId(ae.id);
                      }}
                      onMouseDown={(ev) =>
                        startRangeDrag(ev, ae.id, ae.start, ae.end, 'move', (ns, ne) =>
                          update_audio_edit(ae.id, { start: ns, end: ne }),
                        )
                      }
                      title={`${ae.label || (isMutedSeg ? 'Muted audio' : `Audio ${Math.round(effGain * 100)}%`)} (${s.toFixed(1)}s–${eTime.toFixed(1)}s) · Drag edges to resize`}
                      style={{ left: `${leftPx}px`, width: `${widthPx}px` }}
                      className={`absolute top-0.5 bottom-0.5 rounded-md border flex items-center overflow-hidden cursor-grab z-10 ${
                        isMutedSeg
                          ? active
                            ? 'bg-rose-500/40 border-rose-300'
                            : 'bg-rose-500/25 border-rose-400/70 hover:border-rose-300'
                          : isDuckSeg
                            ? active
                              ? 'bg-amber-500/35 border-amber-300'
                              : 'bg-amber-500/20 border-amber-400/60 hover:border-amber-300'
                            : isBoostSeg
                              ? active
                                ? 'bg-cyan-500/35 border-cyan-300'
                                : 'bg-cyan-500/20 border-cyan-400/60 hover:border-cyan-300'
                              : active
                                ? 'bg-emerald-500/35 border-emerald-200'
                                : 'bg-emerald-500/20 border-emerald-300/70 hover:border-emerald-200'
                      }`}
                    >
                      <div
                        onMouseDown={(ev) =>
                          startRangeDrag(ev, ae.id, ae.start, ae.end, 'left', (ns, ne) =>
                            update_audio_edit(ae.id, { start: ns, end: ne }),
                          )
                        }
                        className="w-1.5 h-full bg-white/15 hover:bg-white/60 cursor-ew-resize shrink-0"
                      />
                      <button
                        type="button"
                        onMouseDown={(ev) => ev.stopPropagation()}
                        onClick={(ev) => {
                          ev.stopPropagation();
                          update_audio_edit(ae.id, {
                            muted: !ae.muted,
                            volume: !ae.muted ? 0 : 1,
                            label: !ae.muted ? 'Muted audio' : 'Audio 100%',
                          });
                        }}
                        title={isMutedSeg ? 'Unmute this audio segment' : 'Mute/cut this audio segment'}
                        className="p-0.5 ml-0.5 text-slate-100 hover:text-amber-200 shrink-0 cursor-pointer"
                      >
                        {isMutedSeg ? (
                          <VolumeX className="w-3 h-3 text-rose-200" />
                        ) : (
                          <Volume2 className="w-3 h-3 text-emerald-100" />
                        )}
                      </button>
                      <span className="flex-1 px-1 text-[10px] font-medium text-slate-100 truncate">
                        {isMutedSeg ? 'Muted' : `${Math.round(effGain * 100)}%`}
                      </span>
                      <button
                        type="button"
                        onMouseDown={(ev) => ev.stopPropagation()}
                        onClick={(ev) => {
                          ev.stopPropagation();
                          delete_audio_edit(ae.id);
                        }}
                        className="p-0.5 text-slate-200 hover:text-red-300 shrink-0 cursor-pointer"
                        title="Restore original audio (delete audio segment)"
                      >
                        <X className="w-3 h-3" />
                      </button>
                      <div
                        onMouseDown={(ev) =>
                          startRangeDrag(ev, ae.id, ae.start, ae.end, 'right', (ns, ne) =>
                            update_audio_edit(ae.id, { start: ns, end: ne }),
                          )
                        }
                        className="w-1.5 h-full bg-white/15 hover:bg-white/60 cursor-ew-resize shrink-0"
                      />
                    </div>
                  );
                })}
              </div>

              {/* SFX Track */}
              <div className="h-7 relative rounded-lg bg-[#0f1218]/75 border border-[#272f40]/80">
                {sfxClips.map((sc) => {
                  const drag =
                    rangeDragPreview && rangeDragPreview.id === sc.id ? rangeDragPreview : null;
                  const sfxPreview =
                    sfxTrimPreview && sfxTrimPreview.id === sc.id ? sfxTrimPreview : null;
                  const matchedAsset = sfxLibrary.find((a) => a.id === sc.assetId);
                  const curTrimStart = sfxPreview
                    ? sfxPreview.trimStart
                    : Math.max(0, sc.trimStart ?? 0);
                  const sourceDur = Math.max(
                    Math.max(0, sc.trimStart ?? 0) + sc.duration,
                    sc.sourceDuration ?? 0,
                    matchedAsset?.duration ?? 0,
                  );
                  const s = drag ? drag.start : sc.start;
                  const eTime = drag ? drag.end : sc.start + sc.duration;
                  const leftPx = s * timelineZoom;
                  const widthPx = Math.max(26, (eTime - s) * timelineZoom);
                  const active = selectedSfxId === sc.id;
                  const isTrimmed = curTrimStart > 0.05 || (matchedAsset ? eTime - s < matchedAsset.duration - 0.05 : false);
                  return (
                    <div
                      key={sc.id}
                      onClick={() => setSelectedSfxId(sc.id)}
                      onMouseDown={(ev) => startSfxDrag(ev, sc, 'move', sourceDur)}
                      title={`${sc.name} · Timeline ${s.toFixed(1)}s–${eTime.toFixed(1)}s · Audio ${curTrimStart.toFixed(1)}s–${(curTrimStart + (eTime - s)).toFixed(1)}s`}
                      style={{ left: `${leftPx}px`, width: `${widthPx}px` }}
                      className={`absolute top-0.5 bottom-0.5 rounded-md border flex items-center overflow-hidden cursor-grab ${
                        active
                          ? 'bg-emerald-500/35 border-emerald-300'
                          : 'bg-emerald-500/20 border-emerald-500/50 hover:border-emerald-400'
                      }`}
                    >
                      <div
                        onMouseDown={(ev) => startSfxDrag(ev, sc, 'left', sourceDur)}
                        title="Drag to trim SFX start"
                        className="w-1.5 h-full bg-emerald-400/25 hover:bg-emerald-300 cursor-ew-resize shrink-0"
                      />
                      <Volume2 className="w-3 h-3 ml-1 text-emerald-200 shrink-0" />
                      <span className="flex-1 px-1.5 text-[10px] font-medium text-emerald-100 truncate">
                        {sc.name}
                        {isTrimmed ? ` · ${(eTime - s).toFixed(1)}s` : ''} ({Math.round(sc.volume * 100)}%)
                      </span>
                      <button
                        type="button"
                        onMouseDown={(ev) => ev.stopPropagation()}
                        onClick={(ev) => {
                          ev.stopPropagation();
                          delete_sfx_clip(sc.id);
                        }}
                        className="p-0.5 text-emerald-200 hover:text-red-300 shrink-0"
                        title="Delete SFX clip"
                      >
                        <X className="w-3 h-3" />
                      </button>
                      <div
                        onMouseDown={(ev) => startSfxDrag(ev, sc, 'right', sourceDur)}
                        title="Drag to trim SFX end / duration"
                        className="w-1.5 h-full bg-emerald-400/25 hover:bg-emerald-300 cursor-ew-resize shrink-0"
                      />
                    </div>
                  );
                })}

                {/* Interactive Video Audio Mute / Cut / Volume Segments */}
                {audioEdits.map((ae) => {
                  const drag =
                    rangeDragPreview && rangeDragPreview.id === ae.id ? rangeDragPreview : null;
                  const s = drag ? drag.start : ae.start;
                  const eTime = drag ? drag.end : ae.end;
                  const leftPx = s * timelineZoom;
                  const widthPx = Math.max(26, (eTime - s) * timelineZoom);
                  const active = selectedAudioEditId === ae.id;
                  const effGain = ae.muted ? 0 : ae.volume;
                  const isMutedSeg = ae.muted || effGain <= 0.001;
                  const isBoostSeg = !isMutedSeg && effGain > 1.01;
                  const isDuckSeg = !isMutedSeg && effGain < 0.99;

                  return (
                    <div
                      key={ae.id}
                      onClick={(ev) => {
                        ev.stopPropagation();
                        setSelectedAudioEditId(ae.id);
                      }}
                      onMouseDown={(ev) =>
                        startRangeDrag(ev, ae.id, ae.start, ae.end, 'move', (ns, ne) =>
                          update_audio_edit(ae.id, { start: ns, end: ne }),
                        )
                      }
                      title={`${ae.label || (isMutedSeg ? 'Muted audio' : `Audio ${Math.round(effGain * 100)}%`)} (${s.toFixed(1)}s–${eTime.toFixed(1)}s) · Drag edges to resize`}
                      style={{ left: `${leftPx}px`, width: `${widthPx}px` }}
                      className={`absolute top-0.5 bottom-0.5 rounded-md border flex items-center overflow-hidden cursor-grab z-10 ${
                        isMutedSeg
                          ? active
                            ? 'bg-rose-500/40 border-rose-300'
                            : 'bg-rose-500/25 border-rose-400/70 hover:border-rose-300'
                          : isDuckSeg
                            ? active
                              ? 'bg-amber-500/35 border-amber-300'
                              : 'bg-amber-500/20 border-amber-400/60 hover:border-amber-300'
                            : isBoostSeg
                              ? active
                                ? 'bg-cyan-500/35 border-cyan-300'
                                : 'bg-cyan-500/20 border-cyan-400/60 hover:border-cyan-300'
                              : active
                                ? 'bg-emerald-500/35 border-emerald-200'
                                : 'bg-emerald-500/20 border-emerald-300/70 hover:border-emerald-200'
                      }`}
                    >
                      <div
                        onMouseDown={(ev) =>
                          startRangeDrag(ev, ae.id, ae.start, ae.end, 'left', (ns, ne) =>
                            update_audio_edit(ae.id, { start: ns, end: ne }),
                          )
                        }
                        className="w-1.5 h-full bg-white/15 hover:bg-white/60 cursor-ew-resize shrink-0"
                      />
                      <button
                        type="button"
                        onMouseDown={(ev) => ev.stopPropagation()}
                        onClick={(ev) => {
                          ev.stopPropagation();
                          update_audio_edit(ae.id, {
                            muted: !ae.muted,
                            volume: !ae.muted ? 0 : 1,
                            label: !ae.muted ? 'Muted audio' : 'Audio 100%',
                          });
                        }}
                        title={isMutedSeg ? 'Unmute this audio segment' : 'Mute/cut this audio segment'}
                        className="p-0.5 ml-0.5 text-slate-100 hover:text-amber-200 shrink-0 cursor-pointer"
                      >
                        {isMutedSeg ? (
                          <VolumeX className="w-3 h-3 text-rose-200" />
                        ) : (
                          <Volume2 className="w-3 h-3 text-emerald-100" />
                        )}
                      </button>
                      <span className="flex-1 px-1 text-[10px] font-medium text-slate-100 truncate">
                        {isMutedSeg ? 'Muted' : `${Math.round(effGain * 100)}%`}
                      </span>
                      <button
                        type="button"
                        onMouseDown={(ev) => ev.stopPropagation()}
                        onClick={(ev) => {
                          ev.stopPropagation();
                          delete_audio_edit(ae.id);
                        }}
                        className="p-0.5 text-slate-200 hover:text-red-300 shrink-0 cursor-pointer"
                        title="Restore original audio (delete audio segment)"
                      >
                        <X className="w-3 h-3" />
                      </button>
                      <div
                        onMouseDown={(ev) =>
                          startRangeDrag(ev, ae.id, ae.start, ae.end, 'right', (ns, ne) =>
                            update_audio_edit(ae.id, { start: ns, end: ne }),
                          )
                        }
                        className="w-1.5 h-full bg-white/15 hover:bg-white/60 cursor-ew-resize shrink-0"
                      />
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Playhead Needle (aligned 1:1 with Ruler 0s & Tracks 0s) */}
            <div
              className="absolute top-0 bottom-0 w-0.5 bg-amber-400 pointer-events-none z-20"
              style={{ left: `${playhead * timelineZoom}px` }}
            >
              <div className="w-3 h-3 -ml-[5px] bg-amber-400 rotate-45 -mt-1 rounded-[1px]" />
            </div>
          </div>
        </div>
      </div>
      )}
    </section>
  );
};
