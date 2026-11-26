import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Captions,
  ChevronDown,
  ChevronUp,
  Flame,
  Loader2,
  Lock,
  Merge,
  Mic,
  MicOff,
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
import { API_BASE, useEditorStore } from '../store/useEditorStore';
import type { CaptionItem, SfxAsset } from '../types/editor';

function formatSec(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = (sec % 60).toFixed(1).padStart(4, '0');
  return `${m}:${s}`;
}

export const LeftMediaAndCaptions: React.FC = () => {
  const project = useEditorStore((s) => s.project);
  const runAutoCaptions = useEditorStore((s) => s.runAutoCaptions);
  const removeDetectedPauses = useEditorStore((s) => s.removeDetectedPauses);
  const isAutoCaptioning = useEditorStore((s) => s.isAutoCaptioning);
  const isRemovingPauses = useEditorStore((s) => s.isRemovingPauses);
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
  const add_audio_edit = useEditorStore((s) => s.add_audio_edit);

  const [leftTab, setLeftTab] = useState<'captions' | 'voice' | 'sfx'>('captions');
  const [showSourceDetails, setShowSourceDetails] = useState(true);
  const [sfxSearch, setSfxSearch] = useState('');
  const [playingAssetId, setPlayingAssetId] = useState<string | null>(null);
  const [tagDrafts, setTagDrafts] = useState<Record<string, string>>({});
  const [capTimeDrafts, setCapTimeDrafts] = useState<
    Record<string, { start?: string; end?: string }>
  >({});
  const [isDragOver, setIsDragOver] = useState(false);
  const [ttsLoadingCapId, setTtsLoadingCapId] = useState<string | null>(null);
  const setStatusBanner = useEditorStore((s) => s.setStatusBanner);

  // Manual Voice / TTS & Speech-to-Text Studio state
  type TtsVoiceOption = { id: string; name: string; model: string; language: string };
  const FALLBACK_TTS_VOICES: TtsVoiceOption[] = [
    { id: 'af_bella', name: 'Bella (Warm Female)', model: 'kokoro', language: 'en-US' },
    { id: 'af_heart', name: 'Heart (Expressive Female)', model: 'kokoro', language: 'en-US' },
    { id: 'af_nicole', name: 'Nicole (Clean Narration)', model: 'kokoro', language: 'en-US' },
    { id: 'am_adam', name: 'Adam (Bold Male)', model: 'kokoro', language: 'en-US' },
    { id: 'am_michael', name: 'Michael (Creator Male)', model: 'kokoro', language: 'en-US' },
    { id: 'bf_emma', name: 'Emma (UK Female)', model: 'kokoro', language: 'en-GB' },
    { id: 'bm_george', name: 'George (UK Male)', model: 'kokoro', language: 'en-GB' },
  ];
  const [ttsVoices, setTtsVoices] = useState<TtsVoiceOption[]>(FALLBACK_TTS_VOICES);
  const [ttsKeyConfigured, setTtsKeyConfigured] = useState(false);
  const [isLoadingVoices, setIsLoadingVoices] = useState(false);
  const [ttsVoice, setTtsVoice] = useState('af_bella');
  const [ttsModel, setTtsModel] = useState('kokoro');
  const [ttsSpeed, setTtsSpeed] = useState(1.05);
  const [ttsVolume, setTtsVolume] = useState(0.95);
  const [ttsScript, setTtsScript] = useState('');
  const [ttsPlacement, setTtsPlacement] = useState<'playhead' | 'start' | 'custom'>('playhead');
  const [ttsCustomStart, setTtsCustomStart] = useState('0');
  const [ttsAlsoCaption, setTtsAlsoCaption] = useState(true);
  const [ttsCapKind, setTtsCapKind] = useState<'intro_hook' | 'subtitle'>('intro_hook');
  const [ttsBgAudioMode, setTtsBgAudioMode] = useState<'keep' | 'duck' | 'mute'>('duck');
  const [isGeneratingManualTts, setIsGeneratingManualTts] = useState(false);
  const [isDictatingStt, setIsDictatingStt] = useState(false);
  const [isTranscribingStt, setIsTranscribingStt] = useState(false);

  const sttRecorderRef = useRef<MediaRecorder | null>(null);
  const sttStreamRef = useRef<MediaStream | null>(null);
  const sttChunksRef = useRef<Blob[]>([]);

  const fetchTtsVoicesList = async () => {
    setIsLoadingVoices(true);
    try {
      const resp = await fetch(`${API_BASE}/api/tts/voices`);
      if (!resp.ok) return;
      const data = await resp.json();
      if (Array.isArray(data.voices) && data.voices.length > 0) {
        setTtsVoices(data.voices as TtsVoiceOption[]);
      }
      setTtsKeyConfigured(Boolean(data.key_configured));
    } catch {
      // Keep curated fallback list if backend unreachable
    } finally {
      setIsLoadingVoices(false);
    }
  };

  const toggleSttDictation = async () => {
    if (isDictatingStt) {
      sttRecorderRef.current?.stop();
      setIsDictatingStt(false);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      sttStreamRef.current = stream;
      const preferredMime =
        typeof MediaRecorder.isTypeSupported === 'function'
          ? MediaRecorder.isTypeSupported('audio/webm')
            ? 'audio/webm'
            : MediaRecorder.isTypeSupported('audio/mp4')
              ? 'audio/mp4'
              : ''
          : '';
      const recorder = preferredMime
        ? new MediaRecorder(stream, { mimeType: preferredMime })
        : new MediaRecorder(stream);
      sttChunksRef.current = [];
      recorder.ondataavailable = (ev) => {
        if (ev.data.size > 0) sttChunksRef.current.push(ev.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        if (sttStreamRef.current === stream) {
          sttStreamRef.current = null;
        }
        const blobType = recorder.mimeType || preferredMime || 'audio/webm';
        const blob = new Blob(sttChunksRef.current, { type: blobType });
        if (blob.size === 0) return;
        const ext = blobType.includes('mp4') ? 'm4a' : blobType.includes('ogg') ? 'ogg' : 'webm';
        const formData = new FormData();
        formData.append('file', blob, `stt.${ext}`);
        setIsTranscribingStt(true);
        try {
          const resp = await fetch(`${API_BASE}/api/voice-transcribe`, {
            method: 'POST',
            body: formData,
          });
          if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            throw new Error(err.detail || 'Speech-to-Text failed');
          }
          const data = await resp.json();
          const spoken = String(data?.text || '').trim();
          if (spoken) {
            setTtsScript((prev) => (prev.trim() ? `${prev.trim()} ${spoken}` : spoken));
            setStatusBanner('Speech-to-Text transcribed into your script box.');
          }
        } catch (err) {
          setStatusBanner(
            `Speech-to-Text error: ${err instanceof Error ? err.message : String(err)}`,
          );
        } finally {
          setIsTranscribingStt(false);
        }
      };
      sttRecorderRef.current = recorder;
      recorder.start();
      setIsDictatingStt(true);
    } catch {
      setStatusBanner('Microphone access denied or unavailable.');
    }
  };

  const resolveTimelineToSource = (tlTime: number): number => {
    const clipsList = useEditorStore.getState().project.tracks.clips;
    if (clipsList.length === 0) return Math.max(0, tlTime);
    for (const c of clipsList) {
      if (tlTime >= c.timelineStart && tlTime <= c.timelineStart + c.duration + 0.01) {
        const spd = Math.max(0.25, Math.min(4.0, c.speed ?? 1));
        const off = Math.max(0, Math.min(c.duration, tlTime - c.timelineStart));
        return Number((c.sourceStart + off * spd).toFixed(2));
      }
    }
    const firstClip = clipsList[0];
    return tlTime <= 0.05 ? firstClip.sourceStart : firstClip.sourceEnd;
  };

  const handleGenerateManualVoiceover = async () => {
    const cleanText = ttsScript.trim();
    if (!cleanText || isGeneratingManualTts) return;
    const reqProjectId = useEditorStore.getState().project.projectId;
    const ph = useEditorStore.getState().project.workspaceState.playheadPosition;
    const parsedCustom = parseFloat(ttsCustomStart);
    const tlStart = Math.max(
      0,
      ttsPlacement === 'start'
        ? 0
        : ttsPlacement === 'custom' && Number.isFinite(parsedCustom)
          ? parsedCustom
          : ph,
    );

    setIsGeneratingManualTts(true);
    try {
      const resp = await fetch(`${API_BASE}/api/tts/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: cleanText,
          voice: ttsVoice,
          model: ttsModel,
          speed: ttsSpeed,
          name:
            ttsCapKind === 'intro_hook' && tlStart < 0.5
              ? 'Intro Hook TTS'
              : `Voiceover: ${cleanText.slice(0, 24)}`,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || 'TTS generation failed');
      }
      const data = await resp.json();
      const asset = data.asset as SfxAsset | undefined;
      if (!asset?.url) throw new Error('Missing generated audio asset');
      await fetchSfxLibrary();
      const liveState = useEditorStore.getState();
      if (liveState.project.projectId !== reqProjectId) return;

      liveState.add_sfx_clip({
        assetId: asset.id,
        name: asset.name,
        url: asset.url,
        start: Number(tlStart.toFixed(2)),
        duration: asset.duration,
        sourceDuration: asset.duration,
        volume: ttsVolume,
      });

      if (ttsAlsoCaption) {
        const firstClip = liveState.project.tracks.clips[0];
        const firstClipSpd = firstClip ? Math.max(0.25, Math.min(4.0, firstClip.speed ?? 1)) : 1;
        const srcStart = resolveTimelineToSource(tlStart);
        const capSpanSrc = Math.max(1.2, asset.duration * firstClipSpd);
        liveState.add_caption(
          srcStart,
          Number((srcStart + capSpanSrc).toFixed(2)),
          cleanText,
          ttsCapKind,
        );
      }

      if (ttsBgAudioMode !== 'keep') {
        liveState.add_audio_edit({
          start: Number(tlStart.toFixed(2)),
          end: Number((tlStart + Math.max(0.4, asset.duration)).toFixed(2)),
          muted: ttsBgAudioMode === 'mute',
          volume: ttsBgAudioMode === 'mute' ? 0 : 0.2,
          label: ttsBgAudioMode === 'mute' ? 'Muted under VO' : 'Ducked under VO',
        });
      }

      setStatusBanner(
        `Added TTS.ai voiceover (${ttsVoice} · ${ttsModel}) at ${tlStart.toFixed(1)}s.`,
      );
    } catch (err) {
      setStatusBanner(
        `TTS voiceover error: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setIsGeneratingManualTts(false);
    }
  };

  const handleAddTextAsCaptionOnly = () => {
    const cleanText = ttsScript.trim();
    if (!cleanText || !hasVideo) return;
    const ph = useEditorStore.getState().project.workspaceState.playheadPosition;
    const parsedCustom = parseFloat(ttsCustomStart);
    const tlStart = Math.max(
      0,
      ttsPlacement === 'start'
        ? 0
        : ttsPlacement === 'custom' && Number.isFinite(parsedCustom)
          ? parsedCustom
          : ph,
    );
    const srcStart = resolveTimelineToSource(tlStart);
    add_caption(srcStart, Number((srcStart + 3.0).toFixed(2)), cleanText, ttsCapKind);
    setStatusBanner(`Added ${ttsCapKind === 'intro_hook' ? 'intro hook ' : ''}caption at ${tlStart.toFixed(1)}s.`);
  };

  const cleanHighlightToken = (token: string) =>
    token.replace(/[^\p{L}\p{N}']/gu, '').toLowerCase();

  const handleSpeakCaptionTts = async (cap: CaptionItem) => {
    const cleanText = cap.text.trim();
    if (!cleanText || ttsLoadingCapId) return;
    const reqProjectId = useEditorStore.getState().project.projectId;
    setTtsLoadingCapId(cap.id);
    try {
      const resp = await fetch(`${API_BASE}/api/tts/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: cleanText,
          voice: ttsVoice,
          model: ttsModel,
          speed: cap.captionKind === 'intro_hook' ? ttsSpeed : 1.0,
          name: cap.captionKind === 'intro_hook' ? 'Intro Hook TTS' : undefined,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || 'TTS generation failed');
      }
      const data = await resp.json();
      const asset = data.asset as SfxAsset | undefined;
      if (!asset?.url) throw new Error('Missing generated audio asset');
      await fetchSfxLibrary();
      const liveState = useEditorStore.getState();
      if (liveState.project.projectId !== reqProjectId) return;
      const liveCap =
        liveState.project.tracks.captions.find((c) => c.id === cap.id) ?? cap;
      const clip = liveState.project.tracks.clips.find(
        (c) => c.sourceEnd >= liveCap.start && c.sourceStart <= liveCap.end,
      );
      const spd = clip ? Math.max(0.25, Math.min(4.0, clip.speed ?? 1)) : 1;
      const tlStart = clip
        ? Math.max(
            0,
            clip.timelineStart + Math.max(0, liveCap.start - clip.sourceStart) / spd,
          )
        : Math.max(0, liveCap.start);
      liveState.add_sfx_clip({
        assetId: asset.id,
        name: asset.name,
        url: asset.url,
        start: Number(tlStart.toFixed(2)),
        duration: asset.duration,
        sourceDuration: asset.duration,
        volume: 0.95,
      });
      setStatusBanner(`Generated TTS.ai voiceover '${asset.name}' at ${tlStart.toFixed(1)}s.`);
    } catch (err) {
      setStatusBanner(
        `TTS voiceover error: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setTtsLoadingCapId(null);
    }
  };

  const auditionAudioRef = useRef<HTMLAudioElement | null>(null);
  const sfxInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    void fetchSfxLibrary();
    void fetchTtsVoicesList();
    return () => {
      if (auditionAudioRef.current) {
        auditionAudioRef.current.pause();
        auditionAudioRef.current = null;
      }
      if (sttRecorderRef.current && sttRecorderRef.current.state !== 'inactive') {
        sttRecorderRef.current.onstop = null;
        sttRecorderRef.current.stop();
      }
      sttStreamRef.current?.getTracks().forEach((t) => t.stop());
      sttStreamRef.current = null;
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
    audio.onended = () => {
      if (auditionAudioRef.current === audio) {
        stopAudition();
      }
    };
    audio.onerror = () => {
      if (auditionAudioRef.current === audio) {
        stopAudition();
      }
    };
    auditionAudioRef.current = audio;
    setPlayingAssetId(asset.id);
    audio.play().catch(() => {
      if (auditionAudioRef.current === audio) {
        stopAudition();
      }
    });
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
    const clean = cleanHighlightToken(wordToken);
    if (!clean) return;
    const exists = existingHighlights.some(
      (h) => cleanHighlightToken(h) === clean,
    );
    const next = exists
      ? existingHighlights.filter((h) => cleanHighlightToken(h) !== clean)
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
                disabled={!hasVideo || isAutoCaptioning}
                onClick={() => void runAutoCaptions()}
                className="px-1.5 py-1.5 rounded-lg bg-[#1c212d] hover:bg-[#232a39] disabled:opacity-40 border border-[#272f40] text-[11px] font-medium text-slate-200 flex items-center justify-center gap-1 transition"
                title="Generate auto-captions from speech (preserves locked manual edits)"
              >
                {isAutoCaptioning ? (
                  <Loader2 className="w-3 h-3 text-amber-300 animate-spin shrink-0" />
                ) : (
                  <Captions className="w-3 h-3 text-amber-300 shrink-0" />
                )}
                <span>Captions</span>
              </button>
              <button
                type="button"
                disabled={!hasVideo || isRemovingPauses}
                onClick={() => void removeDetectedPauses(0.7)}
                className="px-1.5 py-1.5 rounded-lg bg-[#1c212d] hover:bg-[#232a39] disabled:opacity-40 border border-[#272f40] text-[11px] font-medium text-slate-200 flex items-center justify-center gap-1 transition"
                title="Automatically cut silent pauses longer than 0.7 seconds"
              >
                {isRemovingPauses ? (
                  <Loader2 className="w-3 h-3 text-emerald-300 animate-spin shrink-0" />
                ) : (
                  <Sparkles className="w-3 h-3 text-emerald-300 shrink-0" />
                )}
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

      {/* Segmented Tabs: Captions | Voice / TTS | My SFX */}
      <div className="p-2 border-b border-[#272f40] shrink-0">
        <div className="grid grid-cols-3 gap-1 bg-[#0f1218] p-1 rounded-xl border border-[#272f40]">
          <button
            type="button"
            onClick={() => {
              stopAudition();
              setLeftTab('captions');
            }}
            className={`py-1.5 px-1 rounded-lg text-[11px] font-semibold flex items-center justify-center gap-1 transition cursor-pointer ${
              leftTab === 'captions'
                ? 'bg-[#1c212d] text-amber-300 border border-[#2f394e] shadow-sm'
                : 'text-slate-400 hover:text-slate-200 border border-transparent'
            }`}
          >
            <Captions className="w-3.5 h-3.5 shrink-0" />
            <span className="truncate">Captions</span>
          </button>
          <button
            type="button"
            onClick={() => {
              stopAudition();
              setLeftTab('voice');
            }}
            className={`py-1.5 px-1 rounded-lg text-[11px] font-semibold flex items-center justify-center gap-1 transition cursor-pointer ${
              leftTab === 'voice'
                ? 'bg-[#1c212d] text-cyan-300 border border-[#2f394e] shadow-sm'
                : 'text-slate-400 hover:text-slate-200 border border-transparent'
            }`}
          >
            <Mic className="w-3.5 h-3.5 shrink-0" />
            <span className="truncate">Voice/TTS</span>
          </button>
          <button
            type="button"
            onClick={() => setLeftTab('sfx')}
            className={`py-1.5 px-1 rounded-lg text-[11px] font-semibold flex items-center justify-center gap-1 transition cursor-pointer ${
              leftTab === 'sfx'
                ? 'bg-[#1c212d] text-emerald-300 border border-[#2f394e] shadow-sm'
                : 'text-slate-400 hover:text-slate-200 border border-transparent'
            }`}
          >
            <Music className="w-3.5 h-3.5 shrink-0" />
            <span className="truncate">SFX ({sfxLibrary.length})</span>
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
                  <div className="flex flex-wrap items-center justify-between gap-1.5">
                    <div className="flex items-center gap-1">
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        value={capTimeDrafts[cap.id]?.start ?? String(cap.start)}
                        aria-label="Caption start time"
                        onChange={(e) => {
                          const val = e.target.value;
                          setCapTimeDrafts((prev) => ({
                            ...prev,
                            [cap.id]: { ...prev[cap.id], start: val },
                          }));
                        }}
                        onBlur={(e) => {
                          const parsed = parseFloat(e.target.value);
                          setCapTimeDrafts((prev) => {
                            const next = { ...prev };
                            if (next[cap.id]) {
                              const copy = { ...next[cap.id] };
                              delete copy.start;
                              if (copy.end === undefined) delete next[cap.id];
                              else next[cap.id] = copy;
                            }
                            return next;
                          });
                          if (Number.isFinite(parsed) && Math.abs(parsed - cap.start) > 0.001) {
                            adjust_caption_timing(cap.id, Math.max(0, parsed), cap.end, true);
                          }
                        }}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') e.currentTarget.blur();
                        }}
                        className="w-14 bg-[#12161f] border border-[#272f40] focus:border-amber-400/70 rounded-md px-1.5 py-1 text-[11px] font-mono tabular-nums text-slate-200 outline-none"
                      />
                      <span className="text-xs text-slate-500">→</span>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        value={capTimeDrafts[cap.id]?.end ?? String(cap.end)}
                        aria-label="Caption end time"
                        onChange={(e) => {
                          const val = e.target.value;
                          setCapTimeDrafts((prev) => ({
                            ...prev,
                            [cap.id]: { ...prev[cap.id], end: val },
                          }));
                        }}
                        onBlur={(e) => {
                          const parsed = parseFloat(e.target.value);
                          setCapTimeDrafts((prev) => {
                            const next = { ...prev };
                            if (next[cap.id]) {
                              const copy = { ...next[cap.id] };
                              delete copy.end;
                              if (copy.start === undefined) delete next[cap.id];
                              else next[cap.id] = copy;
                            }
                            return next;
                          });
                          if (Number.isFinite(parsed) && Math.abs(parsed - cap.end) > 0.001) {
                            adjust_caption_timing(
                              cap.id,
                              cap.start,
                              Math.max(cap.start + 0.1, parsed),
                              true,
                            );
                          }
                        }}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') e.currentTarget.blur();
                        }}
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
                      <button
                        type="button"
                        disabled={!cap.text.trim() || Boolean(ttsLoadingCapId)}
                        onClick={() => void handleSpeakCaptionTts(cap)}
                        title="Generate spoken TTS.ai voiceover from this caption & add to timeline"
                        className="p-1.5 rounded-md hover:bg-emerald-500/20 disabled:opacity-40 text-emerald-300 transition"
                      >
                        {ttsLoadingCapId === cap.id ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <Volume2 className="w-3.5 h-3.5" />
                        )}
                      </button>
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
                        const clean = cleanHighlightToken(w);
                        const isHl =
                          clean !== '' &&
                          cap.highlightedWords.some(
                            (hw) => cleanHighlightToken(hw) === clean,
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
      ) : leftTab === 'voice' ? (
        /* Manual Speech-to-Text (STT) & TTS.ai Voiceover Studio */
        <div className="flex-1 min-h-0 overflow-y-auto p-2.5 space-y-3">
          <div className="rounded-xl border border-[#272f40] bg-[#1c212d] p-2.5 space-y-2.5">
            <div className="flex items-center justify-between gap-2">
              <div>
                <h3 className="text-xs font-semibold text-cyan-300 flex items-center gap-1.5">
                  <Volume2 className="w-3.5 h-3.5" />
                  Voiceover &amp; Speech-to-Text
                </h3>
                <p className="text-[10px] text-slate-400 mt-0.5">
                  {ttsKeyConfigured
                    ? 'TTS.ai API Key active · up to 4,800 chars'
                    : 'Free TTS.ai Mode · up to 480 chars/req'}
                </p>
              </div>
              <button
                type="button"
                onClick={() => void fetchTtsVoicesList()}
                disabled={isLoadingVoices}
                title="Refresh TTS.ai voices"
                aria-label="Refresh TTS voices"
                className="p-1.5 rounded-lg bg-[#12161f] hover:bg-[#171c28] border border-[#272f40] text-slate-300 transition cursor-pointer"
              >
                <RefreshCw className={`w-3 h-3 ${isLoadingVoices ? 'animate-spin' : ''}`} />
              </button>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <label className="text-[10px] font-medium text-slate-300">
                  Voice to Use
                </label>
                <select
                  value={ttsVoice}
                  onChange={(e) => {
                    const nextId = e.target.value;
                    setTtsVoice(nextId);
                    const matched = ttsVoices.find((v) => v.id === nextId);
                    if (matched?.model) setTtsModel(matched.model);
                  }}
                  aria-label="TTS Voice"
                  className="w-full bg-[#12161f] border border-[#272f40] rounded-lg px-2 py-1.5 text-[11px] text-slate-100 outline-none cursor-pointer"
                >
                  {ttsVoices.map((v) => (
                    <option key={v.id} value={v.id} className="bg-[#161b26]">
                      {v.name} ({v.language})
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1">
                <label className="text-[10px] font-medium text-slate-300">
                  Engine / Model
                </label>
                <select
                  value={ttsModel}
                  onChange={(e) => setTtsModel(e.target.value)}
                  aria-label="TTS Model"
                  className="w-full bg-[#12161f] border border-[#272f40] rounded-lg px-2 py-1.5 text-[11px] text-slate-100 outline-none cursor-pointer"
                >
                  <option value="kokoro" className="bg-[#161b26]">Kokoro (Natural · Free)</option>
                  <option value="piper" className="bg-[#161b26]">Piper (Fast · Free)</option>
                  <option value="vits" className="bg-[#161b26]">VITS (Expressive · Free)</option>
                  <option value="melotts" className="bg-[#161b26]">MeloTTS (Clear · Free)</option>
                </select>
              </div>
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <label className="text-[10px] font-medium text-slate-300">
                  Script or Mic Dictation (Speech-to-Text)
                </label>
                <span className="text-[10px] font-mono tabular-nums text-slate-400">
                  {ttsScript.length}/{ttsKeyConfigured ? 4800 : 480}
                </span>
              </div>
              <textarea
                rows={3}
                value={ttsScript}
                onChange={(e) => setTtsScript(e.target.value)}
                placeholder="Type what you want spoken (e.g. 'Watch what he does to unlock GTA VI'), or click Dictate Mic below to convert your speech into text..."
                className="w-full bg-[#12161f] border border-[#272f40] focus:border-cyan-400/70 rounded-lg p-2 text-xs text-slate-100 placeholder:text-slate-500 outline-none resize-none leading-relaxed"
              />
              <div className="grid grid-cols-2 gap-1.5">
                <button
                  type="button"
                  disabled={isTranscribingStt}
                  onClick={() => void toggleSttDictation()}
                  className={`py-1.5 px-2 rounded-lg border text-[11px] font-medium flex items-center justify-center gap-1.5 transition cursor-pointer disabled:opacity-50 ${
                    isDictatingStt
                      ? 'bg-rose-500/20 border-rose-400 text-rose-200 animate-pulse'
                      : 'bg-[#12161f] hover:bg-[#171c28] border-[#272f40] text-slate-200'
                  }`}
                >
                  {isTranscribingStt ? (
                    <>
                      <Loader2 className="w-3.5 h-3.5 animate-spin text-amber-300" />
                      Transcribing...
                    </>
                  ) : isDictatingStt ? (
                    <>
                      <MicOff className="w-3.5 h-3.5 text-rose-300" />
                      Stop Mic (STT)
                    </>
                  ) : (
                    <>
                      <Mic className="w-3.5 h-3.5 text-cyan-300" />
                      Dictate Mic (STT)
                    </>
                  )}
                </button>
                <button
                  type="button"
                  disabled={!hasVideo || !ttsScript.trim()}
                  onClick={handleAddTextAsCaptionOnly}
                  title="Add the text above directly as a caption without generating TTS audio"
                  className="py-1.5 px-2 rounded-lg bg-[#12161f] hover:bg-[#171c28] disabled:opacity-40 border border-[#272f40] text-[11px] font-medium text-amber-200 flex items-center justify-center gap-1 transition cursor-pointer"
                >
                  <Captions className="w-3.5 h-3.5 text-amber-300" />
                  Add as Caption
                </button>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2.5 pt-1 border-t border-[#272f40]">
              <div className="space-y-1">
                <div className="flex justify-between text-[10px] text-slate-300">
                  <span>Voice Speed</span>
                  <span className="font-mono text-cyan-300 tabular-nums">{ttsSpeed.toFixed(2)}x</span>
                </div>
                <input
                  type="range"
                  min={0.6}
                  max={1.6}
                  step={0.05}
                  value={ttsSpeed}
                  onChange={(e) => setTtsSpeed(Number(e.target.value))}
                  aria-label="TTS speed"
                  className="w-full accent-cyan-400"
                />
              </div>
              <div className="space-y-1">
                <div className="flex justify-between text-[10px] text-slate-300">
                  <span>Voice Volume</span>
                  <span className="font-mono text-cyan-300 tabular-nums">{Math.round(ttsVolume * 100)}%</span>
                </div>
                <input
                  type="range"
                  min={0.2}
                  max={1.5}
                  step={0.05}
                  value={ttsVolume}
                  onChange={(e) => setTtsVolume(Number(e.target.value))}
                  aria-label="TTS volume"
                  className="w-full accent-cyan-400"
                />
              </div>
            </div>

            <div className="space-y-2 pt-1 border-t border-[#272f40]">
              <div className="space-y-1">
                <label className="text-[10px] font-medium text-slate-300">
                  Place on Timeline
                </label>
                <div className="grid grid-cols-3 gap-1">
                  {(
                    [
                      { id: 'playhead', label: 'Playhead' },
                      { id: 'start', label: '0.0s Intro' },
                      { id: 'custom', label: 'Custom (s)' },
                    ] as const
                  ).map((opt) => (
                    <button
                      key={opt.id}
                      type="button"
                      onClick={() => setTtsPlacement(opt.id)}
                      className={`py-1 rounded-lg text-[11px] border transition cursor-pointer ${
                        ttsPlacement === opt.id
                          ? 'bg-cyan-500/20 border-cyan-400 text-cyan-200 font-semibold'
                          : 'bg-[#12161f] border-[#272f40] text-slate-300'
                      }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
                {ttsPlacement === 'custom' && (
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    value={ttsCustomStart}
                    onChange={(e) => setTtsCustomStart(e.target.value)}
                    aria-label="Custom TTS start seconds"
                    className="w-full bg-[#12161f] border border-[#272f40] rounded-lg px-2 py-1 text-xs font-mono text-slate-200 outline-none focus:border-cyan-400"
                  />
                )}
              </div>

              <div className="flex items-center justify-between gap-2">
                <label className="flex items-center gap-1.5 text-[11px] text-slate-200 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={ttsAlsoCaption}
                    onChange={(e) => setTtsAlsoCaption(e.target.checked)}
                    className="accent-cyan-400"
                  />
                  <span>Sync Caption</span>
                </label>
                {ttsAlsoCaption && (
                  <select
                    value={ttsCapKind}
                    onChange={(e) =>
                      setTtsCapKind(e.target.value as 'intro_hook' | 'subtitle')
                    }
                    aria-label="Synced caption type"
                    className="bg-[#12161f] border border-[#272f40] rounded-md px-2 py-0.5 text-[11px] text-slate-200 outline-none"
                  >
                    <option value="intro_hook">Intro Hook</option>
                    <option value="subtitle">Subtitle</option>
                  </select>
                )}
              </div>

              <div className="space-y-1">
                <label className="text-[10px] font-medium text-slate-300">
                  Video Original Audio Under Voiceover
                </label>
                <div className="grid grid-cols-3 gap-1">
                  {(
                    [
                      { id: 'keep', label: 'Keep 100%' },
                      { id: 'duck', label: 'Duck 20%' },
                      { id: 'mute', label: 'Mute 0%' },
                    ] as const
                  ).map((m) => (
                    <button
                      key={m.id}
                      type="button"
                      onClick={() => setTtsBgAudioMode(m.id)}
                      className={`py-1 rounded-lg text-[10px] border transition cursor-pointer ${
                        ttsBgAudioMode === m.id
                          ? 'bg-emerald-500/20 border-emerald-400 text-emerald-200 font-semibold'
                          : 'bg-[#12161f] border-[#272f40] text-slate-300'
                      }`}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <button
              type="button"
              disabled={!ttsScript.trim() || isGeneratingManualTts}
              onClick={() => void handleGenerateManualVoiceover()}
              className="w-full py-2 rounded-xl bg-cyan-400 hover:bg-cyan-300 disabled:opacity-40 text-slate-950 text-xs font-semibold flex items-center justify-center gap-1.5 transition cursor-pointer shadow-sm"
            >
              {isGeneratingManualTts ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  Generating Voiceover...
                </>
              ) : (
                <>
                  <Volume2 className="w-3.5 h-3.5" />
                  Generate Voiceover &amp; Add to Timeline
                </>
              )}
            </button>
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
                  setTagDrafts((prev) => {
                    const next = { ...prev };
                    delete next[asset.id];
                    return next;
                  });
                  if (parsed.join(',') === asset.tags.join(',')) return;
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
                            sourceDuration: asset.duration,
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
