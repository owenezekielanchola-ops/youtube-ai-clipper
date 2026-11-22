import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowUpRight,
  Circle,
  Crop,
  Highlighter,
  Minus,
  MousePointer,
  Pause,
  PenTool,
  Play,
  SkipBack,
  SkipForward,
  Sparkles,
  Square,
  Volume2,
  VolumeX,
  ZoomIn,
  ZoomOut,
  Zap,
} from 'lucide-react';
import { DEFAULT_OVERLAY_STYLE, useEditorStore } from '../store/useEditorStore';
import type {
  ClipItem,
  DrawingPoint,
  DrawingTool,
  OverlayPosition,
  VideoCropRect,
  ZoomEffectItem,
} from '../types/editor';

const NAMED_COLOR_TO_HEX: Record<string, string> = {
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

function normalizeHexColor(raw: string, fallback = '#FFFFFF'): string {
  const trimmed = (raw || '').trim();
  if (!trimmed) return fallback;
  const named = NAMED_COLOR_TO_HEX[trimmed.toLowerCase()];
  if (named) return named;
  const hex = trimmed.replace('#', '');
  if (/^[0-9a-fA-F]{3}$/.test(hex)) {
    return `#${hex[0]}${hex[0]}${hex[1]}${hex[1]}${hex[2]}${hex[2]}`.toUpperCase();
  }
  if (/^[0-9a-fA-F]{6}$/.test(hex)) {
    return `#${hex.toUpperCase()}`;
  }
  return fallback;
}

function isDarkHexColor(raw: string): boolean {
  const hex = normalizeHexColor(raw, '#FFFFFF').replace('#', '');
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  if (Number.isNaN(r) || Number.isNaN(g) || Number.isNaN(b)) return false;
  const lum = 0.299 * r + 0.587 * g + 0.114 * b;
  return lum < 65;
}

function hexToRgba(hex: string, alpha: number): string {
  const cleaned = normalizeHexColor(hex, '#000000').replace('#', '');
  if (cleaned.length !== 6) return `rgba(0,0,0,${alpha})`;
  const r = parseInt(cleaned.slice(0, 2), 16);
  const g = parseInt(cleaned.slice(2, 4), 16);
  const b = parseInt(cleaned.slice(4, 6), 16);
  if (Number.isNaN(r) || Number.isNaN(g) || Number.isNaN(b)) {
    return `rgba(0,0,0,${alpha})`;
  }
  return `rgba(${r},${g},${b},${alpha})`;
}

const OVERLAY_POSITION_CLASSES: Record<OverlayPosition, string> = {
  'bottom-left': 'bottom-[3%] left-[3.5%]',
  'bottom-center': 'bottom-[3%] left-1/2 -translate-x-1/2',
  'bottom-right': 'bottom-[3%] right-[3.5%]',
  'top-left': 'top-[10%] left-[3.5%]',
  'top-center': 'top-[3.5%] left-1/2 -translate-x-1/2',
  'top-right': 'top-[3.5%] right-[3.5%]',
};

function formatClock(sec: number): string {
  const s = Math.max(0, sec);
  const mins = Math.floor(s / 60);
  const rem = (s % 60).toFixed(2).padStart(5, '0');
  return `${String(mins).padStart(2, '0')}:${rem}`;
}

function mapTimelineToSource(
  clips: ClipItem[],
  timelinePos: number,
): { clip: ClipItem; sourceTime: number; index: number } | null {
  if (clips.length === 0) return null;
  for (let i = 0; i < clips.length; i++) {
    const c = clips[i];
    const isLast = i === clips.length - 1;
    const endBoundary = c.timelineStart + c.duration;
    const withinUpper = isLast
      ? timelinePos <= endBoundary + 0.001
      : timelinePos < endBoundary;
    if (timelinePos >= c.timelineStart && withinUpper) {
      const speed = Math.max(0.25, Math.min(4.0, c.speed ?? 1));
      const offset = Math.max(0, Math.min(c.duration, timelinePos - c.timelineStart));
      return { clip: c, sourceTime: c.sourceStart + offset * speed, index: i };
    }
  }
  const last = clips[clips.length - 1];
  return { clip: last, sourceTime: last.sourceEnd, index: clips.length - 1 };
}

function computeActiveZoom(
  zooms: ZoomEffectItem[],
  playhead: number,
): { scale: number; focusX: number; focusY: number } {
  for (let i = zooms.length - 1; i >= 0; i--) {
    const z = zooms[i];
    if (playhead >= z.start && playhead <= z.end) {
      if (z.kind === 'punch_in' || z.easing === 'instant') {
        return { scale: z.endScale, focusX: z.focusX, focusY: z.focusY };
      }
      const span = Math.max(0.05, z.end - z.start);
      const frac = Math.max(0, Math.min(1, (playhead - z.start) / span));
      const eased =
        z.easing === 'ease_in_out' ? 0.5 - 0.5 * Math.cos(Math.PI * frac) : frac;
      return {
        scale: z.startScale + (z.endScale - z.startScale) * eased,
        focusX: z.focusX,
        focusY: z.focusY,
      };
    }
  }
  return { scale: 1, focusX: 0.5, focusY: 0.5 };
}

function renderSvgDrawing(
  key: string,
  tool: DrawingTool,
  points: DrawingPoint[],
  color: string,
  strokeWidth: number,
  opacity: number,
  isSelected = false,
) {
  if (points.length === 0) return null;
  const sw = Math.max(2, strokeWidth * 2.2);
  const p0 = { x: points[0].x * 1000, y: points[0].y * 1000 };
  const last = points[points.length - 1];
  const p1 = { x: last.x * 1000, y: last.y * 1000 };

  if (tool === 'freehand' || tool === 'highlight') {
    const ptsStr = points.map((pt) => `${(pt.x * 1000).toFixed(1)},${(pt.y * 1000).toFixed(1)}`).join(' ');
    return (
      <polyline
        key={key}
        points={ptsStr}
        fill="none"
        stroke={color}
        strokeWidth={tool === 'highlight' ? Math.max(sw * 2.5, 28) : sw}
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeOpacity={tool === 'highlight' ? Math.min(opacity, 0.45) : opacity}
        className={isSelected ? 'drop-shadow-[0_0_6px_rgba(255,255,255,0.9)]' : undefined}
      />
    );
  }

  if (tool === 'line') {
    return (
      <line
        key={key}
        x1={p0.x}
        y1={p0.y}
        x2={p1.x}
        y2={p1.y}
        stroke={color}
        strokeWidth={sw}
        strokeLinecap="round"
        strokeOpacity={opacity}
      />
    );
  }

  if (tool === 'arrow') {
    const dx = p1.x - p0.x;
    const dy = p1.y - p0.y;
    const len = Math.hypot(dx, dy) || 1;
    const headLen = Math.min(len * 0.45, Math.max(26, sw * 2.8));
    const headHalfW = headLen * 0.55;
    const ux = dx / len;
    const uy = dy / len;
    const baseX = p1.x - ux * headLen;
    const baseY = p1.y - uy * headLen;
    const leftX = baseX - uy * headHalfW;
    const leftY = baseY + ux * headHalfW;
    const rightX = baseX + uy * headHalfW;
    const rightY = baseY - ux * headHalfW;
    return (
      <g key={key} opacity={opacity}>
        <line
          x1={p0.x}
          y1={p0.y}
          x2={baseX}
          y2={baseY}
          stroke={color}
          strokeWidth={sw}
          strokeLinecap="round"
        />
        <polygon
          points={`${p1.x},${p1.y} ${leftX},${leftY} ${rightX},${rightY}`}
          fill={color}
        />
      </g>
    );
  }

  if (tool === 'circle') {
    const cx = (p0.x + p1.x) / 2;
    const cy = (p0.y + p1.y) / 2;
    const rx = Math.max(4, Math.abs(p1.x - p0.x) / 2);
    const ry = Math.max(4, Math.abs(p1.y - p0.y) / 2);
    return (
      <ellipse
        key={key}
        cx={cx}
        cy={cy}
        rx={rx}
        ry={ry}
        fill="none"
        stroke={color}
        strokeWidth={sw}
        strokeOpacity={opacity}
      />
    );
  }

  // rect
  return (
    <rect
      key={key}
      x={Math.min(p0.x, p1.x)}
      y={Math.min(p0.y, p1.y)}
      width={Math.max(8, Math.abs(p1.x - p0.x))}
      height={Math.max(8, Math.abs(p1.y - p0.y))}
      fill="none"
      stroke={color}
      strokeWidth={sw}
      strokeOpacity={opacity}
      rx={6}
    />
  );
}

export const CenterVideoPlayer: React.FC = () => {
  const project = useEditorStore((s) => s.project);
  const isPlaying = useEditorStore((s) => s.isPlaying);
  const setIsPlaying = useEditorStore((s) => s.setIsPlaying);
  const setPlayheadPosition = useEditorStore((s) => s.setPlayheadPosition);
  const activeDrawTool = useEditorStore((s) => s.activeDrawTool);
  const setActiveDrawTool = useEditorStore((s) => s.setActiveDrawTool);
  const drawColor = useEditorStore((s) => s.drawColor);
  const setDrawColor = useEditorStore((s) => s.setDrawColor);
  const drawStrokeWidth = useEditorStore((s) => s.drawStrokeWidth);
  const setDrawStrokeWidth = useEditorStore((s) => s.setDrawStrokeWidth);
  const isCropMode = useEditorStore((s) => s.isCropMode);
  const setIsCropMode = useEditorStore((s) => s.setIsCropMode);
  const set_video_crop = useEditorStore((s) => s.set_video_crop);
  const reset_video_crop = useEditorStore((s) => s.reset_video_crop);
  const selectedDrawingId = useEditorStore((s) => s.selectedDrawingId);
  const add_drawing = useEditorStore((s) => s.add_drawing);
  const add_zoom_effect = useEditorStore((s) => s.add_zoom_effect);
  const ingestYoutubeUrl = useEditorStore((s) => s.ingestYoutubeUrl);
  const isIngesting = useEditorStore((s) => s.isIngesting);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const cropContainerRef = useRef<HTMLDivElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const sfxAudiosRef = useRef<Map<string, HTMLAudioElement>>(new Map());
  const [isMuted, setIsMuted] = useState(false);
  const isMutedRef = useRef(isMuted);
  const [draftPoints, setDraftPoints] = useState<DrawingPoint[] | null>(null);
  const [draftCrop, setDraftCrop] = useState<VideoCropRect | null>(null);
  const [naturalDims, setNaturalDims] = useState<{ w: number; h: number }>({
    w: 16,
    h: 9,
  });

  const stopAllSfx = () => {
    for (const audio of sfxAudiosRef.current.values()) {
      audio.pause();
    }
    sfxAudiosRef.current.clear();
  };

  useEffect(() => {
    isMutedRef.current = isMuted;
    const sfxList = useEditorStore.getState().project.tracks.sfx ?? [];
    for (const [id, audio] of sfxAudiosRef.current.entries()) {
      const sc = sfxList.find((item) => item.id === id);
      audio.volume = isMuted ? 0 : Math.min(1, Math.max(0, sc?.volume ?? 0.85));
    }
  }, [isMuted]);

  useEffect(() => {
    return () => {
      for (const audio of sfxAudiosRef.current.values()) {
        audio.pause();
      }
      sfxAudiosRef.current.clear();
    };
  }, []);

  const clips = project.tracks.clips;
  const playhead = project.workspaceState.playheadPosition;
  const totalDuration = useMemo(
    () => clips.reduce((acc, c) => acc + c.duration, 0),
    [clips],
  );

  const activeMapping = useMemo(
    () => mapTimelineToSource(clips, playhead),
    [clips, playhead],
  );

  const currentSourceTime = activeMapping?.sourceTime ?? 0;

  // Sync <video> time when paused, scrubbed externally, or toggling crop view
  useEffect(() => {
    if (isPlaying) return;
    const video = videoRef.current;
    if (!video || !activeMapping) return;
    if (Math.abs(video.currentTime - activeMapping.sourceTime) > 0.08) {
      video.currentTime = activeMapping.sourceTime;
    }
  }, [activeMapping, isPlaying, isCropMode]);

  // Play / Pause control + Virtual Playback Controller across edited EDL clips
  useEffect(() => {
    const video = videoRef.current;
    if (!video || clips.length === 0) return;

    if (!isPlaying) {
      video.pause();
      stopAllSfx();
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      return;
    }

    const syncSfxAtTime = (headPos: number) => {
      const curSfx = useEditorStore.getState().project.tracks.sfx ?? [];
      const activeIds = new Set<string>();
      const muted = isMutedRef.current;
      for (const sc of curSfx) {
        if (headPos >= sc.start && headPos < sc.start + sc.duration - 0.03) {
          activeIds.add(sc.id);
          const trimStart = Math.max(0, sc.trimStart ?? 0);
          const offset = trimStart + Math.max(0, headPos - sc.start);
          const targetVol = muted ? 0 : Math.min(1, Math.max(0, sc.volume));
          let audio = sfxAudiosRef.current.get(sc.id);
          if (!audio) {
            audio = new Audio(sc.url);
            audio.volume = targetVol;
            sfxAudiosRef.current.set(sc.id, audio);
            if (offset > 0.03 && audio.readyState < 1) {
              const pendingAudio = audio;
              pendingAudio.addEventListener(
                'loadedmetadata',
                () => {
                  if (sfxAudiosRef.current.get(sc.id) !== pendingAudio) return;
                  const store = useEditorStore.getState();
                  if (!store.isPlaying) {
                    pendingAudio.pause();
                    sfxAudiosRef.current.delete(sc.id);
                    return;
                  }
                  const liveHead = store.project.workspaceState.playheadPosition;
                  const liveSc =
                    (store.project.tracks.sfx ?? []).find((item) => item.id === sc.id) ?? sc;
                  if (
                    liveHead < liveSc.start ||
                    liveHead >= liveSc.start + liveSc.duration - 0.03
                  ) {
                    pendingAudio.pause();
                    sfxAudiosRef.current.delete(sc.id);
                    return;
                  }
                  const liveOffset =
                    Math.max(0, liveSc.trimStart ?? 0) + Math.max(0, liveHead - liveSc.start);
                  pendingAudio.currentTime = liveOffset;
                  pendingAudio.play().catch(() => {});
                },
                { once: true },
              );
            } else {
              audio.currentTime = offset;
              audio.play().catch(() => {});
            }
          } else {
            audio.volume = targetVol;
            if (audio.readyState >= 1 && Math.abs(audio.currentTime - offset) > 0.35) {
              audio.currentTime = offset;
            }
          }
        }
      }
      for (const [id, audio] of sfxAudiosRef.current.entries()) {
        if (!activeIds.has(id)) {
          audio.pause();
          sfxAudiosRef.current.delete(id);
        }
      }
    };

    const startMap = mapTimelineToSource(clips, playhead);
    if (startMap) {
      video.playbackRate = Math.max(0.25, Math.min(4.0, startMap.clip.speed ?? 1));
      if (playhead >= totalDuration - 0.05) {
        setPlayheadPosition(0);
        video.currentTime = clips[0].sourceStart;
      } else if (Math.abs(video.currentTime - startMap.sourceTime) > 0.15) {
        video.currentTime = startMap.sourceTime;
      }
    }

    video.play().catch(() => {
      setIsPlaying(false);
    });

    const tick = () => {
      const v = videoRef.current;
      if (!v) return;
      const storeState = useEditorStore.getState();
      if (!storeState.isPlaying) return;

      if (v.seeking) {
        rafRef.current = requestAnimationFrame(tick);
        return;
      }

      const curClips = storeState.project.tracks.clips;
      const curHead = storeState.project.workspaceState.playheadPosition;
      const mapped = mapTimelineToSource(curClips, curHead);
      if (!mapped) {
        stopAllSfx();
        storeState.setIsPlaying(false);
        return;
      }

      const { clip, index } = mapped;
      const clipSpeed = Math.max(0.25, Math.min(4.0, clip.speed ?? 1));
      if (Math.abs(v.playbackRate - clipSpeed) > 0.01) {
        v.playbackRate = clipSpeed;
      }
      const srcTime = v.currentTime;

      // Recover if <video> is outside current clip bounds or playhead jumped externally (e.g. +/-2s)
      if (
        srcTime < clip.sourceStart - 0.25 ||
        srcTime > clip.sourceEnd + 0.25 ||
        Math.abs(srcTime - mapped.sourceTime) > Math.max(0.45, 0.35 * clipSpeed)
      ) {
        v.currentTime = mapped.sourceTime;
        syncSfxAtTime(curHead);
      } else if (srcTime >= clip.sourceEnd - 0.03) {
        // Reached end of current virtual clip -> jump seamlessly to next clip or stop
        const nextClip = curClips[index + 1];
        if (nextClip) {
          v.playbackRate = Math.max(0.25, Math.min(4.0, nextClip.speed ?? 1));
          v.currentTime = nextClip.sourceStart;
          storeState.setPlayheadPosition(nextClip.timelineStart);
          syncSfxAtTime(nextClip.timelineStart);
        } else {
          v.pause();
          stopAllSfx();
          storeState.setIsPlaying(false);
          storeState.setPlayheadPosition(
            curClips.reduce((acc, c) => acc + c.duration, 0),
          );
          return;
        }
      } else {
        const newHead = Number(
          (
            clip.timelineStart +
            Math.max(0, srcTime - clip.sourceStart) / clipSpeed
          ).toFixed(3),
        );
        storeState.setPlayheadPosition(newHead);
        syncSfxAtTime(newHead);
      }

      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => {
      stopAllSfx();
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [isPlaying]);

  // Active caption at current source time
  const activeCaption = useMemo(() => {
    return (
      project.tracks.captions.find(
        (c) => currentSourceTime >= c.start && currentSourceTime <= c.end,
      ) ?? null
    );
  }, [project.tracks.captions, currentSourceTime]);

  const overlayText = project.workspaceState.overlayText?.trim() || '';
  const overlayStyle = project.workspaceState.overlayStyle ?? DEFAULT_OVERLAY_STYLE;
  const overlayPos = overlayStyle.position || 'bottom-left';
  const overlayIsBottom = overlayPos.startsWith('bottom');
  const targetRatio = project.workspaceState.targetAspectRatio;
  const savedCrop: VideoCropRect = project.workspaceState.crop ?? {
    x: 0,
    y: 0,
    w: 1,
    h: 1,
  };
  const hasActiveCrop = Boolean(
    project.workspaceState.crop &&
      (savedCrop.x > 0.005 ||
        savedCrop.y > 0.005 ||
        savedCrop.w < 0.99 ||
        savedCrop.h < 0.99),
  );
  const isTimedCrop = Boolean(
    hasActiveCrop &&
      savedCrop.start != null &&
      savedCrop.end != null &&
      savedCrop.end > savedCrop.start,
  );
  const isCropActiveAtPlayhead =
    !isTimedCrop ||
    (playhead >= (savedCrop.start ?? 0) && playhead <= (savedCrop.end ?? totalDuration));
  const playbackCrop: VideoCropRect =
    hasActiveCrop && isCropActiveAtPlayhead
      ? savedCrop
      : { x: 0, y: 0, w: 1, h: 1 };
  const activeCrop = draftCrop ?? savedCrop;

  const targetAspectNum =
    targetRatio === '9:16' ? 9 / 16 : targetRatio === '1:1' ? 1 : 16 / 9;
  const natAspect = naturalDims.w / Math.max(1, naturalDims.h);
  const computeFitPct = (contentAspect: number) => {
    if (contentAspect >= targetAspectNum) {
      return { wPct: 100, hPct: (targetAspectNum / contentAspect) * 100 };
    }
    return { wPct: (contentAspect / targetAspectNum) * 100, hPct: 100 };
  };
  const uncropFit = computeFitPct(natAspect);
  const croppedAspect =
    natAspect * (Math.max(0.1, playbackCrop.w) / Math.max(0.1, playbackCrop.h));
  const croppedFit = computeFitPct(croppedAspect);

  type CropHandleMode = 'move' | 'nw' | 'ne' | 'sw' | 'se' | 'n' | 's' | 'w' | 'e';

  const startCropDrag = (e: React.MouseEvent, mode: CropHandleMode) => {
    e.stopPropagation();
    e.preventDefault();
    const container = cropContainerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;

    const startClientX = e.clientX;
    const startClientY = e.clientY;
    const orig = { ...savedCrop };
    let latest: VideoCropRect = { ...orig };
    const minSize = 0.1;

    const onMove = (ev: MouseEvent) => {
      const dx = (ev.clientX - startClientX) / rect.width;
      const dy = (ev.clientY - startClientY) / rect.height;

      let { x, y, w, h } = orig;
      if (mode === 'move') {
        x = Math.max(0, Math.min(1 - w, orig.x + dx));
        y = Math.max(0, Math.min(1 - h, orig.y + dy));
      } else {
        let left = orig.x;
        let top = orig.y;
        let right = orig.x + orig.w;
        let bottom = orig.y + orig.h;

        if (mode.includes('w')) {
          left = Math.max(0, Math.min(right - minSize, orig.x + dx));
        }
        if (mode.includes('e')) {
          right = Math.min(1, Math.max(left + minSize, orig.x + orig.w + dx));
        }
        if (mode.includes('n')) {
          top = Math.max(0, Math.min(bottom - minSize, orig.y + dy));
        }
        if (mode.includes('s')) {
          bottom = Math.min(1, Math.max(top + minSize, orig.y + orig.h + dy));
        }
        x = left;
        y = top;
        w = right - left;
        h = bottom - top;
      }

      latest = {
        x: Number(x.toFixed(4)),
        y: Number(y.toFixed(4)),
        w: Number(w.toFixed(4)),
        h: Number(h.toFixed(4)),
        ...(orig.start != null && orig.end != null
          ? { start: orig.start, end: orig.end }
          : {}),
      };
      setDraftCrop(latest);
    };

    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      setDraftCrop(null);
      set_video_crop(latest);
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  const activeZoom = useMemo(
    () => computeActiveZoom(project.tracks.zooms ?? [], playhead),
    [project.tracks.zooms, playhead],
  );

  const visibleDrawings = useMemo(
    () =>
      (project.tracks.drawings ?? []).filter(
        (d) => playhead >= d.start && playhead <= d.end,
      ),
    [project.tracks.drawings, playhead],
  );

  const getNormPoint = (clientX: number, clientY: number): DrawingPoint | null => {
    const svg = svgRef.current;
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    return {
      x: Math.max(0, Math.min(1, Number(((clientX - rect.left) / rect.width).toFixed(4)))),
      y: Math.max(0, Math.min(1, Number(((clientY - rect.top) / rect.height).toFixed(4)))),
    };
  };

  const handleQuickZoom = (kind: ZoomEffectItem['kind']) => {
    if (clips.length === 0) return;
    const endPos = Math.max(playhead + 0.6, Math.min(totalDuration, playhead + 2.5));
    add_zoom_effect({
      start: playhead,
      end: endPos,
      kind,
      startScale: kind === 'zoom_out' ? 1.45 : 1.0,
      endScale: kind === 'zoom_out' ? 1.0 : 1.45,
      focusX: 0.5,
      focusY: 0.5,
      easing: kind === 'punch_in' ? 'instant' : 'ease_in_out',
    });
  };

  const previewBoxStyle: React.CSSProperties =
    targetRatio === '9:16'
      ? {
          width: 'min(100cqw, calc(100cqh * 9 / 16))',
          height: 'min(100cqh, calc(100cqw * 16 / 9))',
        }
      : targetRatio === '1:1'
        ? {
            width: 'min(100cqw, 100cqh)',
            height: 'min(100cqw, 100cqh)',
          }
        : {
            width: 'min(100cqw, calc(100cqh * 16 / 9))',
            height: 'min(100cqh, calc(100cqw * 9 / 16))',
          };

  const style = project.captionStyle;
  const posClass =
    style.position === 'top'
      ? 'top-[8%]'
      : style.position === 'middle'
        ? 'top-1/2 -translate-y-1/2'
        : overlayText && overlayIsBottom
          ? 'bottom-[12%]'
          : 'bottom-[9%]';

  const renderCaptionContent = () => {
    if (!activeCaption) return null;

    const hlSet = new Set(
      activeCaption.highlightedWords
        .map((w) => w.replace(/[^\w']/g, '').toLowerCase())
        .filter(Boolean),
    );

    const normTextColor = normalizeHexColor(style.textColor, '#FFFFFF');
    const normHighlightColor = normalizeHexColor(style.highlightColor, '#FACC15');
    const getWordShadow = (colorHex: string) =>
      isDarkHexColor(colorHex)
        ? '0 1px 0 rgba(255,255,255,0.95), 0 -1px 0 rgba(255,255,255,0.95), 1px 0 0 rgba(255,255,255,0.95), -1px 0 0 rgba(255,255,255,0.95), 0 2px 6px rgba(255,255,255,0.75)'
        : '0 2px 6px rgba(0,0,0,0.95)';

    if (style.preset === 'karaoke' && activeCaption.words.length > 0) {
      return (
        <span className="inline-flex flex-wrap justify-center gap-x-1.5 gap-y-0.5 leading-tight">
          {activeCaption.words.map((w, idx) => {
            const clean = w.word.replace(/[^\w']/g, '').toLowerCase();
            const isManualHl = clean !== '' && hlSet.has(clean);
            const isActiveWord =
              currentSourceTime >= w.start && currentSourceTime <= w.end + 0.1;
            const wordColor =
              isActiveWord || isManualHl ? normHighlightColor : normTextColor;
            return (
              <span
                key={`${activeCaption.id}_kw_${idx}`}
                style={{
                  color: wordColor,
                  transform: isActiveWord ? 'scale(1.08)' : 'scale(1)',
                  textShadow: getWordShadow(wordColor),
                }}
                className="inline-block transition-transform duration-75"
              >
                {w.word}
              </span>
            );
          })}
        </span>
      );
    }

    const lines = activeCaption.text
      .replace(/\r\n/g, '\n')
      .replace(/\r/g, '\n')
      .split('\n');

    const renderHighlightedLines = (
      getExtraStyle?: (wordColor: string) => React.CSSProperties,
    ) => (
      <span className="inline-flex flex-col items-center">
        {lines.map((line, lineIdx) => (
          <span
            key={lineIdx}
            className="inline-flex flex-wrap justify-center gap-x-1.5"
          >
            {line
              .split(/\s+/)
              .filter(Boolean)
              .map((tok, idx) => {
                const clean = tok.replace(/[^\w']/g, '').toLowerCase();
                const isHl = clean !== '' && hlSet.has(clean);
                const wordColor = isHl ? normHighlightColor : normTextColor;
                return (
                  <span
                    key={`${lineIdx}_${idx}`}
                    style={{
                      ...(getExtraStyle ? getExtraStyle(wordColor) : {}),
                      color: wordColor,
                    }}
                  >
                    {tok}
                  </span>
                );
              })}
          </span>
        ))}
      </span>
    );

    if (style.preset === 'yellow_highlight' || style.preset === 'karaoke') {
      return (
        <span className="leading-tight">
          {renderHighlightedLines((wordColor) => ({
            textShadow: getWordShadow(wordColor),
          }))}
        </span>
      );
    }

    if (style.preset === 'bold_social') {
      return (
        <span className="uppercase tracking-tight leading-tight">
          {renderHighlightedLines((wordColor) => {
            const dark = isDarkHexColor(wordColor);
            const strokeCol = dark ? '#FFFFFF' : '#000000';
            return {
              WebkitTextStroke: `1.5px ${strokeCol}`,
              textShadow: `0 3px 0 ${strokeCol}, -2px -2px 0 ${strokeCol}, 2px -2px 0 ${strokeCol}, -2px 2px 0 ${strokeCol}, 2px 2px 0 ${strokeCol}`,
            };
          })}
        </span>
      );
    }

    // minimal
    const minimalDarkText = isDarkHexColor(normTextColor);
    return (
      <span
        className={`px-3 py-1 rounded backdrop-blur-sm leading-snug ${
          minimalDarkText ? 'bg-white/90 shadow-md' : 'bg-black/75'
        }`}
      >
        {renderHighlightedLines()}
      </span>
    );
  };

  return (
    <main className="flex-1 bg-[#0f1218] flex flex-col items-center justify-between p-3 sm:p-4 min-w-0 min-h-0 overflow-hidden">
      {/* Canvas Tools Bar: Grouped & Progressive Disclosure */}
      <div className="w-full max-w-3xl mb-2.5 shrink-0 space-y-1.5">
        <div className="bg-[#151922] border border-[#272f40] rounded-xl px-3 py-1.5 flex flex-wrap items-center justify-between gap-2">
          {/* Group 1: Frame & Crop */}
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] font-medium text-slate-400 hidden xl:inline">
              Frame
            </span>
            <div className="bg-[#0f1218] p-0.5 rounded-lg border border-[#272f40] flex items-center gap-0.5">
              <button
                type="button"
                onClick={() => {
                  setIsCropMode(false);
                  setActiveDrawTool(null);
                }}
                title="Select / Playback mode"
                className={`px-2.5 py-1 rounded-md text-xs flex items-center gap-1.5 transition cursor-pointer ${
                  activeDrawTool === null && !isCropMode
                    ? 'bg-amber-400 text-slate-950 font-semibold shadow-sm'
                    : 'text-slate-300 hover:text-slate-100 hover:bg-[#1c212d]'
                }`}
              >
                <MousePointer className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Select</span>
              </button>
              <button
                type="button"
                disabled={clips.length === 0}
                onClick={() => setIsCropMode(!isCropMode)}
                title="Crop video region (drag corners or edges on video)"
                className={`px-2.5 py-1 rounded-md text-xs flex items-center gap-1.5 transition disabled:opacity-40 cursor-pointer ${
                  isCropMode
                    ? 'bg-amber-400 text-slate-950 font-semibold shadow-sm'
                    : hasActiveCrop
                      ? 'bg-amber-500/20 text-amber-300 font-medium'
                      : 'text-slate-300 hover:text-slate-100 hover:bg-[#1c212d]'
                }`}
              >
                <Crop className="w-3.5 h-3.5" />
                <span>{isCropMode ? 'Done Crop' : 'Crop'}</span>
              </button>
            </div>
          </div>

          {/* Group 2: Quick Zooms */}
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] font-medium text-slate-400 hidden xl:inline">
              Zoom
            </span>
            <div className="bg-[#0f1218] p-0.5 rounded-lg border border-[#272f40] flex items-center gap-0.5">
              <button
                type="button"
                disabled={clips.length === 0}
                onClick={() => handleQuickZoom('punch_in')}
                title="Add 2.5s Punch-In Zoom at playhead"
                className="px-2 py-1 rounded-md hover:bg-sky-500/15 disabled:opacity-40 text-xs text-sky-300 flex items-center gap-1 transition cursor-pointer"
              >
                <Zap className="w-3.5 h-3.5" />
                Punch
              </button>
              <button
                type="button"
                disabled={clips.length === 0}
                onClick={() => handleQuickZoom('zoom_in')}
                title="Add 2.5s Smooth Zoom In at playhead"
                className="px-2 py-1 rounded-md hover:bg-sky-500/15 disabled:opacity-40 text-xs text-sky-300 flex items-center gap-1 transition cursor-pointer"
              >
                <ZoomIn className="w-3.5 h-3.5" />
                In
              </button>
              <button
                type="button"
                disabled={clips.length === 0}
                onClick={() => handleQuickZoom('zoom_out')}
                title="Add 2.5s Zoom Out at playhead"
                className="px-2 py-1 rounded-md hover:bg-sky-500/15 disabled:opacity-40 text-xs text-sky-300 flex items-center gap-1 transition cursor-pointer"
              >
                <ZoomOut className="w-3.5 h-3.5" />
                Out
              </button>
            </div>
          </div>

          {/* Group 3: Video Drawings */}
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] font-medium text-slate-400 hidden xl:inline">
              Draw
            </span>
            <div className="bg-[#0f1218] p-0.5 rounded-lg border border-[#272f40] flex items-center gap-0.5">
              {(
                [
                  { id: 'freehand', label: 'Draw', Icon: PenTool },
                  { id: 'line', label: 'Line', Icon: Minus },
                  { id: 'arrow', label: 'Arrow', Icon: ArrowUpRight },
                  { id: 'circle', label: 'Circle', Icon: Circle },
                  { id: 'rect', label: 'Box', Icon: Square },
                  { id: 'highlight', label: 'Highlight', Icon: Highlighter },
                ] as Array<{ id: DrawingTool; label: string; Icon: React.FC<{ className?: string }> }>
              ).map(({ id, label, Icon }) => (
                <button
                  key={id}
                  type="button"
                  disabled={clips.length === 0}
                  onClick={() => setActiveDrawTool(activeDrawTool === id ? null : id)}
                  title={`${label} on video`}
                  className={`px-2 py-1 rounded-md text-xs flex items-center gap-1 transition disabled:opacity-40 cursor-pointer ${
                    activeDrawTool === id
                      ? 'bg-rose-500 text-white font-semibold shadow-sm'
                      : 'text-slate-300 hover:text-slate-100 hover:bg-[#1c212d]'
                  }`}
                >
                  <Icon className="w-3.5 h-3.5" />
                  <span className="hidden lg:inline">{label}</span>
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Progressive Disclosure: Draw Color & Size Options (only shown while drawing) */}
        {activeDrawTool !== null && (
          <div className="bg-[#151922] border border-[#272f40] rounded-xl px-3 py-1.5 flex flex-wrap items-center justify-between gap-2 text-xs">
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-slate-400 font-medium">Ink</span>
              <div className="flex items-center gap-1.5">
                {['#EF4444', '#FACC15', '#22C55E', '#38BDF8', '#FFFFFF'].map((c) => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => setDrawColor(c)}
                    className={`w-4 h-4 rounded-full border transition cursor-pointer ${
                      drawColor.toLowerCase() === c.toLowerCase()
                        ? 'border-white scale-110'
                        : 'border-[#333c4e]'
                    }`}
                    style={{ backgroundColor: c }}
                    title={`Color ${c}`}
                  />
                ))}
                <input
                  type="color"
                  value={drawColor}
                  onChange={(e) => setDrawColor(e.target.value)}
                  className="w-5 h-5 rounded bg-transparent cursor-pointer"
                  title="Custom drawing color"
                />
              </div>
              <div className="w-px h-4 bg-[#272f40] mx-1" />
              <label className="flex items-center gap-1.5 text-[11px] text-slate-400">
                <span>Size</span>
                <select
                  value={drawStrokeWidth}
                  onChange={(e) => setDrawStrokeWidth(Number(e.target.value))}
                  className="bg-[#1c212d] border border-[#272f40] rounded-md px-2 py-0.5 text-xs text-slate-200 outline-none cursor-pointer"
                  title="Brush thickness"
                >
                  <option value={4}>Thin</option>
                  <option value={6}>Medium</option>
                  <option value={11}>Bold</option>
                </select>
              </label>
            </div>
            <span className="text-[11px] text-slate-400">
              Drag on video to draw · Click Select when finished
            </span>
          </div>
        )}
      </div>

      {/* Aspect-Ratio Preview Viewport (strictly contained above transport controls) */}
      <div
        className="flex-1 w-full min-h-0 flex items-center justify-center overflow-hidden"
        style={{ containerType: 'size' }}
      >
        <div
          style={previewBoxStyle}
          className="relative bg-[#0b0d12] rounded-xl overflow-hidden border border-[#272f40] shadow-2xl flex items-center justify-center"
        >
          {project.sourceVideo.videoUrl ? (
            isCropMode ? (
              <div
                ref={cropContainerRef}
                style={{ width: `${uncropFit.wPct}%`, height: `${uncropFit.hPct}%` }}
                className="relative overflow-hidden select-none"
              >
                <video
                  ref={videoRef}
                  src={project.sourceVideo.videoUrl}
                  muted={isMuted}
                  playsInline
                  preload="metadata"
                  onLoadedMetadata={(e) => {
                    const v = e.currentTarget;
                    if (v.videoWidth > 0 && v.videoHeight > 0) {
                      setNaturalDims({ w: v.videoWidth, h: v.videoHeight });
                    }
                    if (Math.abs(v.currentTime - currentSourceTime) > 0.08) {
                      v.currentTime = currentSourceTime;
                    }
                  }}
                  className="w-full h-full object-fill"
                />
                {/* Interactive Crop Frame */}
                <div
                  onMouseDown={(e) => startCropDrag(e, 'move')}
                  style={{
                    left: `${activeCrop.x * 100}%`,
                    top: `${activeCrop.y * 100}%`,
                    width: `${activeCrop.w * 100}%`,
                    height: `${activeCrop.h * 100}%`,
                  }}
                  className="absolute border-2 border-amber-400 shadow-[0_0_0_9999px_rgba(0,0,0,0.62)] cursor-move z-20"
                >
                  {/* Grid thirds */}
                  <div className="absolute inset-0 pointer-events-none grid grid-cols-3 grid-rows-3 opacity-35">
                    {Array.from({ length: 9 }).map((_, idx) => (
                      <div key={idx} className="border border-white/25" />
                    ))}
                  </div>
                  {/* Corner & edge resize handles */}
                  <div
                    onMouseDown={(e) => startCropDrag(e, 'nw')}
                    className="absolute -top-1.5 -left-1.5 w-3.5 h-3.5 bg-amber-400 border border-zinc-950 rounded-sm cursor-nwse-resize"
                  />
                  <div
                    onMouseDown={(e) => startCropDrag(e, 'ne')}
                    className="absolute -top-1.5 -right-1.5 w-3.5 h-3.5 bg-amber-400 border border-zinc-950 rounded-sm cursor-nesw-resize"
                  />
                  <div
                    onMouseDown={(e) => startCropDrag(e, 'sw')}
                    className="absolute -bottom-1.5 -left-1.5 w-3.5 h-3.5 bg-amber-400 border border-zinc-950 rounded-sm cursor-nesw-resize"
                  />
                  <div
                    onMouseDown={(e) => startCropDrag(e, 'se')}
                    className="absolute -bottom-1.5 -right-1.5 w-3.5 h-3.5 bg-amber-400 border border-zinc-950 rounded-sm cursor-nwse-resize"
                  />
                  <div
                    onMouseDown={(e) => startCropDrag(e, 'n')}
                    className="absolute -top-1 left-1/2 -translate-x-1/2 w-6 h-2 bg-amber-400 rounded-full cursor-ns-resize"
                  />
                  <div
                    onMouseDown={(e) => startCropDrag(e, 's')}
                    className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-6 h-2 bg-amber-400 rounded-full cursor-ns-resize"
                  />
                  <div
                    onMouseDown={(e) => startCropDrag(e, 'w')}
                    className="absolute top-1/2 -left-1 -translate-y-1/2 w-2 h-6 bg-amber-400 rounded-full cursor-ew-resize"
                  />
                  <div
                    onMouseDown={(e) => startCropDrag(e, 'e')}
                    className="absolute top-1/2 -right-1 -translate-y-1/2 w-2 h-6 bg-amber-400 rounded-full cursor-ew-resize"
                  />
                </div>

                {/* Crop Floating Controls */}
                <div className="absolute bottom-2.5 left-1/2 -translate-x-1/2 z-30 flex items-center gap-2 bg-[#151922]/95 border border-[#272f40] rounded-xl px-3 py-1.5 text-xs shadow-xl">
                  <span className="font-mono text-slate-300 tabular-nums">
                    {Math.round(activeCrop.w * 100)}% × {Math.round(activeCrop.h * 100)}%
                  </span>
                  <button
                    type="button"
                    onClick={() => reset_video_crop()}
                    className="px-2 py-0.5 rounded-md bg-[#1c212d] hover:bg-[#242b3b] border border-[#272f40] text-slate-200 cursor-pointer"
                  >
                    Reset
                  </button>
                  <button
                    type="button"
                    onClick={() => setIsCropMode(false)}
                    className="px-2.5 py-0.5 rounded-md bg-amber-400 hover:bg-amber-300 text-slate-950 font-semibold cursor-pointer"
                  >
                    Done
                  </button>
                </div>
              </div>
            ) : (
              <div
                style={{
                  transform: `scale(${activeZoom.scale})`,
                  transformOrigin: `${activeZoom.focusX * 100}% ${activeZoom.focusY * 100}%`,
                }}
                className="w-full h-full flex items-center justify-center transition-transform duration-75 will-change-transform"
              >
                <div
                  style={{ width: `${croppedFit.wPct}%`, height: `${croppedFit.hPct}%` }}
                  className="relative overflow-hidden"
                >
                  <video
                    ref={videoRef}
                    src={project.sourceVideo.videoUrl}
                    muted={isMuted}
                    playsInline
                    preload="metadata"
                    onLoadedMetadata={(e) => {
                      const v = e.currentTarget;
                      if (v.videoWidth > 0 && v.videoHeight > 0) {
                        setNaturalDims({ w: v.videoWidth, h: v.videoHeight });
                      }
                      if (Math.abs(v.currentTime - currentSourceTime) > 0.08) {
                        v.currentTime = currentSourceTime;
                      }
                    }}
                    onClick={() => {
                      if (!activeDrawTool) setIsPlaying(!isPlaying);
                    }}
                    style={{
                      width: `${100 / playbackCrop.w}%`,
                      height: `${100 / playbackCrop.h}%`,
                      left: `${-(playbackCrop.x / playbackCrop.w) * 100}%`,
                      top: `${-(playbackCrop.y / playbackCrop.h) * 100}%`,
                      maxWidth: 'none',
                      maxHeight: 'none',
                    }}
                    className="absolute object-fill cursor-pointer"
                  />
                </div>
              </div>
            )
          ) : (
            <div className="max-w-sm mx-auto rounded-2xl bg-[#151922]/95 border border-[#272f40] p-5 sm:p-6 text-left space-y-4 shadow-xl m-4">
              <div className="space-y-1">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
                  <Sparkles className="w-4 h-4 text-amber-400 shrink-0" />
                  Start a New Clip Edit
                </div>
                <p className="text-xs text-slate-400 leading-relaxed">
                  Import a YouTube link or try our demo clip to test captions, crop, zooms, drawings, SFX, and AI editing.
                </p>
              </div>
              <ol className="space-y-2 text-xs text-slate-300">
                <li className="flex items-start gap-2">
                  <span className="w-4 h-4 rounded-full bg-[#1c212d] border border-[#272f40] text-amber-400 font-semibold text-[10px] flex items-center justify-center shrink-0 mt-0.5">
                    1
                  </span>
                  <span>Paste any YouTube URL in the top bar and click Import</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="w-4 h-4 rounded-full bg-[#1c212d] border border-[#272f40] text-amber-400 font-semibold text-[10px] flex items-center justify-center shrink-0 mt-0.5">
                    2
                  </span>
                  <span>Adjust Crop, Zoom, Draw, Speed, or ask AI Editor on the right</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="w-4 h-4 rounded-full bg-[#1c212d] border border-[#272f40] text-amber-400 font-semibold text-[10px] flex items-center justify-center shrink-0 mt-0.5">
                    3
                  </span>
                  <span>Export your finished MP4 whenever it looks right</span>
                </li>
              </ol>
              <button
                type="button"
                disabled={isIngesting}
                onClick={() => void ingestYoutubeUrl('demo://sample')}
                className="w-full py-2 px-3 rounded-xl bg-amber-400 hover:bg-amber-300 disabled:opacity-50 text-slate-950 font-semibold text-xs flex items-center justify-center gap-1.5 transition cursor-pointer"
              >
                <Sparkles className="w-3.5 h-3.5" />
                {isIngesting ? 'Preparing Demo Clip...' : 'Try Demo Clip'}
              </button>
            </div>
          )}

          {/* Interactive SVG Drawing Overlay Layer (hidden while editing crop handles) */}
          {!isCropMode && (
            <svg
              ref={svgRef}
              viewBox="0 0 1000 1000"
              preserveAspectRatio="none"
              onPointerDown={(e) => {
                if (!activeDrawTool || clips.length === 0) return;
                const pt = getNormPoint(e.clientX, e.clientY);
                if (!pt) return;
                e.currentTarget.setPointerCapture(e.pointerId);
                setDraftPoints([pt, pt]);
              }}
              onPointerMove={(e) => {
                if (!activeDrawTool || !draftPoints) return;
                const pt = getNormPoint(e.clientX, e.clientY);
                if (!pt) return;
                if (activeDrawTool === 'freehand' || activeDrawTool === 'highlight') {
                  const lastPt = draftPoints[draftPoints.length - 1];
                  if (Math.hypot(pt.x - lastPt.x, pt.y - lastPt.y) > 0.006) {
                    setDraftPoints([...draftPoints, pt]);
                  }
                } else {
                  setDraftPoints([draftPoints[0], pt]);
                }
              }}
              onPointerUp={(e) => {
                if (!activeDrawTool || !draftPoints) return;
                if (e.currentTarget.hasPointerCapture(e.pointerId)) {
                  e.currentTarget.releasePointerCapture(e.pointerId);
                }
                const pts = draftPoints;
                setDraftPoints(null);
                if (pts.length >= 2) {
                  const span = Math.hypot(
                    pts[pts.length - 1].x - pts[0].x,
                    pts[pts.length - 1].y - pts[0].y,
                  );
                  if (span > 0.015 || pts.length > 3) {
                    const endPos = Math.max(
                      playhead + 0.5,
                      Math.min(Math.max(totalDuration, playhead + 2.5), playhead + 2.5),
                    );
                    add_drawing({
                      start: playhead,
                      end: endPos,
                      tool: activeDrawTool,
                      points: pts,
                      color: drawColor,
                      strokeWidth:
                        activeDrawTool === 'highlight'
                          ? Math.max(drawStrokeWidth * 2, 16)
                          : drawStrokeWidth,
                      opacity: activeDrawTool === 'highlight' ? 0.45 : 1.0,
                    });
                  }
                }
              }}
              className={`absolute inset-0 w-full h-full z-10 ${
                activeDrawTool
                  ? 'pointer-events-auto cursor-crosshair touch-none'
                  : 'pointer-events-none'
              }`}
            >
              {visibleDrawings.map((d) =>
                renderSvgDrawing(
                  d.id,
                  d.tool,
                  d.points,
                  d.color,
                  d.strokeWidth,
                  d.opacity,
                  d.id === selectedDrawingId,
                ),
              )}
              {draftPoints &&
                activeDrawTool &&
                renderSvgDrawing(
                  'draft_stroke',
                  activeDrawTool,
                  draftPoints,
                  drawColor,
                  activeDrawTool === 'highlight'
                    ? Math.max(drawStrokeWidth * 2, 16)
                    : drawStrokeWidth,
                  activeDrawTool === 'highlight' ? 0.45 : 1.0,
                )}
            </svg>
          )}

          {/* Aspect Ratio Badge */}
          <div className="absolute top-2.5 left-2.5 px-2 py-0.5 rounded bg-black/75 border border-white/15 text-[10px] font-mono text-zinc-200 pointer-events-none">
            {project.workspaceState.targetAspectRatio}
            {hasActiveCrop ? ' • Crop' : ''}
          </div>

          {/* Campaign On-Screen Overlay Preview */}
          {!isCropMode && overlayText && (
            <div
              className={`absolute ${OVERLAY_POSITION_CLASSES[overlayPos] ?? OVERLAY_POSITION_CLASSES['bottom-left']} z-10 pointer-events-none select-none uppercase tracking-widest font-black leading-none transition-all ${
                overlayStyle.showBackground
                  ? 'border border-white/25 rounded-md px-2.5 py-1 shadow-lg'
                  : ''
              }`}
              style={{
                fontFamily: style.fontFamily,
                color: overlayStyle.textColor,
                backgroundColor: overlayStyle.showBackground
                  ? hexToRgba(overlayStyle.backgroundColor, 0.85)
                  : 'transparent',
                fontSize: `${Math.max(
                  11,
                  Math.round(overlayStyle.fontSize * (targetRatio === '9:16' ? 0.54 : 0.65)),
                )}px`,
                textShadow: overlayStyle.showBackground
                  ? '0 1px 2px rgba(0,0,0,0.9)'
                  : '0 2px 4px rgba(0,0,0,0.95), -1.5px -1.5px 0 #000, 1.5px -1.5px 0 #000, -1.5px 1.5px 0 #000, 1.5px 1.5px 0 #000',
              }}
            >
              {overlayText}
            </div>
          )}

          {/* 60fps Real-Time CSS Caption Overlay */}
          {!isCropMode && activeCaption && (
            <div
              className={`absolute left-4 right-4 ${posClass} flex justify-center text-center pointer-events-none px-2`}
              style={{
                fontFamily: style.fontFamily,
                fontSize: `${Math.max(14, Math.round(style.fontSize * 0.72))}px`,
                fontWeight: style.isBold || style.preset === 'bold_social' ? 800 : 500,
              }}
            >
              {renderCaptionContent()}
            </div>
          )}
        </div>
      </div>

      {/* Transport Controls */}
      <div className="w-full max-w-2xl mt-2.5 bg-[#151922] border border-[#272f40] rounded-xl px-4 py-2 flex items-center justify-between gap-4 shrink-0">
        <div className="flex items-center gap-2 font-mono text-xs tabular-nums">
          <span className="text-amber-400 font-semibold">{formatClock(playhead)}</span>
          <span className="text-slate-600">/</span>
          <span className="text-slate-300">{formatClock(totalDuration)}</span>
          {activeMapping && (
            <span className="ml-1.5 text-[11px] text-slate-400 hidden sm:inline">
              src {formatClock(currentSourceTime)}
            </span>
          )}
          {activeMapping && activeMapping.clip.speed != null && activeMapping.clip.speed !== 1 && (
            <span className="px-1.5 py-0.5 rounded bg-indigo-500/15 border border-indigo-400/30 text-indigo-300 text-[11px] font-sans font-medium">
              {activeMapping.clip.speed}x
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={clips.length === 0}
            onClick={() => setPlayheadPosition(Math.max(0, playhead - 2))}
            className="p-1.5 rounded-lg hover:bg-[#1c212d] disabled:opacity-40 text-slate-300 hover:text-slate-100 transition cursor-pointer"
            title="Back 2s"
          >
            <SkipBack className="w-4 h-4" />
          </button>
          <button
            type="button"
            disabled={clips.length === 0}
            onClick={() => setIsPlaying(!isPlaying)}
            className="w-9 h-9 rounded-full bg-amber-400 hover:bg-amber-300 disabled:opacity-40 text-slate-950 flex items-center justify-center transition shadow-sm cursor-pointer"
            title="Play / Pause (Space)"
          >
            {isPlaying ? (
              <Pause className="w-4 h-4 fill-current" />
            ) : (
              <Play className="w-4 h-4 fill-current ml-0.5" />
            )}
          </button>
          <button
            type="button"
            disabled={clips.length === 0}
            onClick={() => setPlayheadPosition(Math.min(totalDuration, playhead + 2))}
            className="p-1.5 rounded-lg hover:bg-[#1c212d] disabled:opacity-40 text-slate-300 hover:text-slate-100 transition cursor-pointer"
            title="Forward 2s"
          >
            <SkipForward className="w-4 h-4" />
          </button>
        </div>

        <button
          type="button"
          onClick={() => setIsMuted(!isMuted)}
          className="px-2 py-1.5 rounded-lg hover:bg-[#1c212d] text-slate-300 hover:text-slate-100 flex items-center gap-1.5 text-xs transition cursor-pointer"
          title={isMuted ? 'Unmute audio' : 'Mute audio'}
        >
          {isMuted ? (
            <>
              <VolumeX className="w-4 h-4 text-rose-400" />
              <span className="hidden sm:inline text-rose-300">Muted</span>
            </>
          ) : (
            <>
              <Volume2 className="w-4 h-4" />
              <span className="hidden sm:inline text-slate-400">Audio</span>
            </>
          )}
        </button>
      </div>
    </main>
  );
};
