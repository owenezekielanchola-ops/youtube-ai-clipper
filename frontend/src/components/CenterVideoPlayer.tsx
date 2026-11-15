import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Pause, Play, SkipBack, SkipForward, Volume2, VolumeX } from 'lucide-react';
import { useEditorStore } from '../store/useEditorStore';
import type { ClipItem } from '../types/editor';

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
      const offset = Math.max(0, Math.min(c.duration, timelinePos - c.timelineStart));
      return { clip: c, sourceTime: c.sourceStart + offset, index: i };
    }
  }
  const last = clips[clips.length - 1];
  return { clip: last, sourceTime: last.sourceEnd, index: clips.length - 1 };
}

export const CenterVideoPlayer: React.FC = () => {
  const project = useEditorStore((s) => s.project);
  const isPlaying = useEditorStore((s) => s.isPlaying);
  const setIsPlaying = useEditorStore((s) => s.setIsPlaying);
  const setPlayheadPosition = useEditorStore((s) => s.setPlayheadPosition);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const [isMuted, setIsMuted] = useState(false);

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

  // Sync <video> time when paused or scrubbed externally
  useEffect(() => {
    if (isPlaying) return;
    const video = videoRef.current;
    if (!video || !activeMapping) return;
    if (Math.abs(video.currentTime - activeMapping.sourceTime) > 0.08) {
      video.currentTime = activeMapping.sourceTime;
    }
  }, [activeMapping, isPlaying]);

  // Play / Pause control + Virtual Playback Controller across edited EDL clips
  useEffect(() => {
    const video = videoRef.current;
    if (!video || clips.length === 0) return;

    if (!isPlaying) {
      video.pause();
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      return;
    }

    const startMap = mapTimelineToSource(clips, playhead);
    if (startMap) {
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
        storeState.setIsPlaying(false);
        return;
      }

      const { clip, index } = mapped;
      const srcTime = v.currentTime;

      // Recover if <video> is outside current clip bounds or playhead jumped externally (e.g. +/-2s)
      if (
        srcTime < clip.sourceStart - 0.25 ||
        srcTime > clip.sourceEnd + 0.25 ||
        Math.abs(srcTime - mapped.sourceTime) > 0.4
      ) {
        v.currentTime = mapped.sourceTime;
      } else if (srcTime >= clip.sourceEnd - 0.03) {
        // Reached end of current virtual clip -> jump seamlessly to next clip or stop
        const nextClip = curClips[index + 1];
        if (nextClip) {
          v.currentTime = nextClip.sourceStart;
          storeState.setPlayheadPosition(nextClip.timelineStart);
        } else {
          v.pause();
          storeState.setIsPlaying(false);
          storeState.setPlayheadPosition(
            curClips.reduce((acc, c) => acc + c.duration, 0),
          );
          return;
        }
      } else {
        const newHead = clip.timelineStart + Math.max(0, srcTime - clip.sourceStart);
        storeState.setPlayheadPosition(Number(newHead.toFixed(3)));
      }

      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => {
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

  const aspectClass =
    project.workspaceState.targetAspectRatio === '9:16'
      ? 'aspect-[9/16] h-full max-h-[420px]'
      : project.workspaceState.targetAspectRatio === '1:1'
        ? 'aspect-square h-full max-h-[420px]'
        : 'aspect-video w-full max-w-[740px]';

  const style = project.captionStyle;
  const posClass =
    style.position === 'top'
      ? 'top-[8%]'
      : style.position === 'middle'
        ? 'top-1/2 -translate-y-1/2'
        : 'bottom-[9%]';

  const renderCaptionContent = () => {
    if (!activeCaption) return null;

    const hlSet = new Set(
      activeCaption.highlightedWords
        .map((w) => w.replace(/[^\w']/g, '').toLowerCase())
        .filter(Boolean),
    );

    if (style.preset === 'karaoke' && activeCaption.words.length > 0) {
      return (
        <span className="inline-flex flex-wrap justify-center gap-x-1.5 gap-y-0.5 leading-tight">
          {activeCaption.words.map((w, idx) => {
            const spoken = currentSourceTime >= w.start;
            const clean = w.word.replace(/[^\w']/g, '').toLowerCase();
            const isManualHl = clean !== '' && hlSet.has(clean);
            const isActiveWord =
              currentSourceTime >= w.start && currentSourceTime <= w.end + 0.08;
            return (
              <span
                key={`${activeCaption.id}_kw_${idx}`}
                style={{
                  color: spoken || isManualHl ? style.highlightColor : style.textColor,
                  transform: isActiveWord ? 'scale(1.08)' : 'scale(1)',
                  textShadow: '0 2px 6px rgba(0,0,0,0.95)',
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

    const renderHighlightedLines = (extraStyle?: React.CSSProperties) => (
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
                return (
                  <span
                    key={`${lineIdx}_${idx}`}
                    style={{
                      ...extraStyle,
                      color: isHl ? style.highlightColor : style.textColor,
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
          {renderHighlightedLines({
            textShadow: '0 2px 6px rgba(0,0,0,0.95)',
          })}
        </span>
      );
    }

    if (style.preset === 'bold_social') {
      return (
        <span className="uppercase tracking-tight leading-tight">
          {renderHighlightedLines({
            WebkitTextStroke: '1.5px #000',
            textShadow:
              '0 3px 0 #000, -2px -2px 0 #000, 2px -2px 0 #000, -2px 2px 0 #000, 2px 2px 0 #000',
          })}
        </span>
      );
    }

    // minimal
    return (
      <span className="px-3 py-1 rounded bg-black/75 backdrop-blur-sm leading-snug">
        {renderHighlightedLines()}
      </span>
    );
  };

  return (
    <main className="flex-1 bg-zinc-900/40 flex flex-col items-center justify-between p-4 min-w-0 min-h-0">
      {/* Aspect-Ratio Preview Viewport */}
      <div className="flex-1 w-full flex items-center justify-center min-h-0">
        <div
          className={`relative ${aspectClass} bg-black rounded-xl overflow-hidden border border-zinc-800 shadow-2xl flex items-center justify-center`}
        >
          {project.sourceVideo.videoUrl ? (
            <video
              ref={videoRef}
              src={project.sourceVideo.videoUrl}
              muted={isMuted}
              playsInline
              preload="metadata"
              onClick={() => setIsPlaying(!isPlaying)}
              className="w-full h-full object-cover cursor-pointer"
            />
          ) : (
            <div className="text-center px-6 space-y-2">
              <p className="text-sm font-medium text-zinc-300">
                Ready for YouTube Video Ingest
              </p>
              <p className="text-xs text-zinc-500 max-w-xs mx-auto">
                Paste a YouTube link in the top bar or click <strong className="text-amber-400">Demo</strong> to generate a local sample clip.
              </p>
            </div>
          )}

          {/* Aspect Ratio Badge */}
          <div className="absolute top-2.5 left-2.5 px-2 py-0.5 rounded bg-black/70 border border-white/10 text-[10px] font-mono text-zinc-300 pointer-events-none">
            {project.workspaceState.targetAspectRatio}
          </div>

          {/* 60fps Real-Time CSS Caption Overlay */}
          {activeCaption && (
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
      <div className="w-full max-w-2xl mt-3 bg-zinc-950 border border-zinc-800 rounded-xl px-4 py-2 flex items-center justify-between gap-4 shrink-0">
        <div className="flex items-center gap-2 font-mono text-xs">
          <span className="text-amber-400 font-semibold">{formatClock(playhead)}</span>
          <span className="text-zinc-600">/</span>
          <span className="text-zinc-400">{formatClock(totalDuration)}</span>
          {activeMapping && (
            <span className="ml-2 text-[10px] text-zinc-500 hidden sm:inline">
              src {formatClock(currentSourceTime)}
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={clips.length === 0}
            onClick={() => setPlayheadPosition(Math.max(0, playhead - 2))}
            className="p-1.5 rounded-lg hover:bg-zinc-800 disabled:opacity-40 text-zinc-300"
            title="Back 2s"
          >
            <SkipBack className="w-4 h-4" />
          </button>
          <button
            type="button"
            disabled={clips.length === 0}
            onClick={() => setIsPlaying(!isPlaying)}
            className="w-9 h-9 rounded-full bg-amber-400 hover:bg-amber-300 disabled:opacity-40 text-zinc-950 flex items-center justify-center transition"
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
            className="p-1.5 rounded-lg hover:bg-zinc-800 disabled:opacity-40 text-zinc-300"
            title="Forward 2s"
          >
            <SkipForward className="w-4 h-4" />
          </button>
        </div>

        <button
          type="button"
          onClick={() => setIsMuted(!isMuted)}
          className="p-1.5 rounded-lg hover:bg-zinc-800 text-zinc-300"
          title={isMuted ? 'Unmute' : 'Mute'}
        >
          {isMuted ? (
            <VolumeX className="w-4 h-4 text-red-400" />
          ) : (
            <Volume2 className="w-4 h-4" />
          )}
        </button>
      </div>
    </main>
  );
};
