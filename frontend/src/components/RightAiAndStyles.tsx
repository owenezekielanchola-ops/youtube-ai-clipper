import React, { useEffect, useRef, useState } from 'react';
import {
  Bot,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  Crop,
  Link2,
  Loader2,
  Maximize2,
  Mic,
  MicOff,
  Minimize2,
  Palette,
  Play,
  RefreshCw,
  Send,
  ShieldCheck,
  Sparkles,
  Square,
  Undo2,
  Volume2,
  Wand2,
} from 'lucide-react';
import { getCampaignById } from '../config/campaigns';
import { API_BASE, DEFAULT_OVERLAY_STYLE, useEditorStore } from '../store/useEditorStore';
import type { CaptionPosition, CaptionPreset, OverlayPosition } from '../types/editor';

const QUICK_PROMPTS = [
  'Find the funniest viral moment and make a 9:16 Short with karaoke captions',
  'Zoom to the creator when he gets a reaction',
  'What are they doing? List the funny parts',
  'Crop to the creator webcam',
  'Speed up this part to 1.5x',
  'Add a sound effect here',
];

export const RightAiAndStyles: React.FC = () => {
  const project = useEditorStore((s) => s.project);
  const aiMessages = useEditorStore((s) => s.aiMessages);
  const aiActionLog = useEditorStore((s) => s.aiActionLog);
  const isAiThinking = useEditorStore((s) => s.isAiThinking);
  const setIsAiThinking = useEditorStore((s) => s.setIsAiThinking);
  const appendAiMessage = useEditorStore((s) => s.appendAiMessage);
  const applyAiToolCalls = useEditorStore((s) => s.applyAiToolCalls);
  const change_caption_style = useEditorStore((s) => s.change_caption_style);
  const setStatusBanner = useEditorStore((s) => s.setStatusBanner);
  const modelStatus = useEditorStore((s) => s.modelStatus);
  const isRefreshingModels = useEditorStore((s) => s.isRefreshingModels);
  const pendingYoutubeUrl = useEditorStore((s) => s.pendingYoutubeUrl);
  const isIngesting = useEditorStore((s) => s.isIngesting);
  const ingestYoutubeUrl = useEditorStore((s) => s.ingestYoutubeUrl);
  const fetchModelStatus = useEditorStore((s) => s.fetchModelStatus);
  const forceRefreshModels = useEditorStore((s) => s.forceRefreshModels);
  const setActiveCampaign = useEditorStore((s) => s.setActiveCampaign);
  const setOverlayText = useEditorStore((s) => s.setOverlayText);
  const updateOverlayStyle = useEditorStore((s) => s.updateOverlayStyle);
  const selectedZoomId = useEditorStore((s) => s.selectedZoomId);
  const selectedDrawingId = useEditorStore((s) => s.selectedDrawingId);
  const selectedSfxId = useEditorStore((s) => s.selectedSfxId);
  const update_zoom_effect = useEditorStore((s) => s.update_zoom_effect);
  const delete_zoom_effect = useEditorStore((s) => s.delete_zoom_effect);
  const update_drawing = useEditorStore((s) => s.update_drawing);
  const delete_drawing = useEditorStore((s) => s.delete_drawing);
  const update_sfx_clip = useEditorStore((s) => s.update_sfx_clip);
  const delete_sfx_clip = useEditorStore((s) => s.delete_sfx_clip);
  const sfxLibrary = useEditorStore((s) => s.sfxLibrary);
  const isCropMode = useEditorStore((s) => s.isCropMode);
  const setIsCropMode = useEditorStore((s) => s.setIsCropMode);
  const set_video_crop = useEditorStore((s) => s.set_video_crop);
  const reset_video_crop = useEditorStore((s) => s.reset_video_crop);

  const activeCampaign = getCampaignById(project.workspaceState.activeCampaignId);
  const overlayStyle = project.workspaceState.overlayStyle ?? DEFAULT_OVERLAY_STYLE;
  const activeCrop = project.workspaceState.crop ?? { x: 0, y: 0, w: 1, h: 1 };
  const hasActiveCrop = Boolean(
    project.workspaceState.crop &&
      (activeCrop.x > 0.005 ||
        activeCrop.y > 0.005 ||
        activeCrop.w < 0.99 ||
        activeCrop.h < 0.99),
  );
  const selectedZoom = (project.tracks.zooms ?? []).find((z) => z.id === selectedZoomId) ?? null;
  const selectedDrawing =
    (project.tracks.drawings ?? []).find((d) => d.id === selectedDrawingId) ?? null;
  const selectedSfx =
    (project.tracks.sfx ?? []).find((s) => s.id === selectedSfxId) ?? null;
  const selectedSfxAsset = selectedSfx
    ? sfxLibrary.find((a) => a.id === selectedSfx.assetId) ?? null
    : null;
  const sfxTrimStart = Math.max(0, selectedSfx?.trimStart ?? 0);
  const sfxSourceDur = selectedSfx
    ? Math.max(
        sfxTrimStart + selectedSfx.duration,
        selectedSfxAsset?.duration ?? sfxTrimStart + selectedSfx.duration,
      )
    : 1.5;
  const [copiedMsgId, setCopiedMsgId] = useState<string | null>(null);
  const [showModels, setShowModels] = useState(false);
  const [isRefOpen, setIsRefOpen] = useState(false);
  const [refUrl, setRefUrl] = useState('');
  const [refInstruction, setRefInstruction] = useState('');
  const [isCloningStyle, setIsCloningStyle] = useState(false);
  const [isPreviewingSfx, setIsPreviewingSfx] = useState(false);

  const [activeTab, setActiveTab] = useState<'ai' | 'styles'>('ai');
  const lastActionMsgId = [...aiMessages]
    .reverse()
    .find((m) => m.actions && m.actions.length > 0)?.id;

  const sfxPreviewAudioRef = useRef<HTMLAudioElement | null>(null);
  const sfxPreviewTimerRef = useRef<number | null>(null);

  const stopPreviewSfx = () => {
    if (sfxPreviewTimerRef.current !== null) {
      window.clearTimeout(sfxPreviewTimerRef.current);
      sfxPreviewTimerRef.current = null;
    }
    if (sfxPreviewAudioRef.current) {
      sfxPreviewAudioRef.current.pause();
      sfxPreviewAudioRef.current = null;
    }
    setIsPreviewingSfx(false);
  };

  useEffect(() => {
    stopPreviewSfx();
  }, [selectedSfxId]);

  useEffect(() => {
    void fetchModelStatus();
    const intervalId = window.setInterval(() => {
      void forceRefreshModels();
    }, 24 * 60 * 60 * 1000);
    return () => {
      window.clearInterval(intervalId);
      stopPreviewSfx();
    };
  }, [fetchModelStatus, forceRefreshModels]);

  const togglePreviewTrimmedSfx = () => {
    if (!selectedSfx) return;
    if (isPreviewingSfx) {
      stopPreviewSfx();
      return;
    }
    stopPreviewSfx();
    const audio = new Audio(selectedSfx.url);
    audio.volume = Math.min(1, Math.max(0, selectedSfx.volume));
    sfxPreviewAudioRef.current = audio;
    setIsPreviewingSfx(true);
    audio.onended = () => stopPreviewSfx();
    audio.onerror = () => stopPreviewSfx();

    const startOffset = sfxTrimStart;
    const playDurMs = Math.max(150, Math.round(selectedSfx.duration * 1000));
    const startSlicePlayback = () => {
      if (sfxPreviewAudioRef.current !== audio) return;
      audio.currentTime = startOffset;
      audio
        .play()
        .then(() => {
          if (sfxPreviewAudioRef.current !== audio) return;
          sfxPreviewTimerRef.current = window.setTimeout(() => {
            stopPreviewSfx();
          }, playDurMs);
        })
        .catch(() => stopPreviewSfx());
    };

    if (startOffset > 0.02 && audio.readyState < 1) {
      audio.addEventListener('loadedmetadata', startSlicePlayback, { once: true });
    } else {
      startSlicePlayback();
    }
  };
  const [promptInput, setPromptInput] = useState('');
  const [isRecording, setIsRecording] = useState(false);
  const [isChatExpanded, setIsChatExpanded] = useState(false);
  const [showQuickPrompts, setShowQuickPrompts] = useState(false);
  const [copiedTextId, setCopiedTextId] = useState<string | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const chatScrollRef = useRef<HTMLDivElement | null>(null);
  const chatEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (activeTab !== 'ai') return;
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [aiMessages.length, isAiThinking, activeTab]);

  const submitAiPrompt = async (promptText: string) => {
    const trimmed = promptText.trim();
    if (!trimmed || isAiThinking || isIngesting) return;

    if (!project.sourceVideo.videoUrl && pendingYoutubeUrl.trim()) {
      setPromptInput('');
      await ingestYoutubeUrl(pendingYoutubeUrl.trim(), trimmed);
      return;
    }

    const liveState = useEditorStore.getState();
    const active = getCampaignById(liveState.project.workspaceState.activeCampaignId);

    appendAiMessage({
      id: `u_${Date.now()}`,
      role: 'user',
      content: trimmed,
      campaignLabel: active?.label,
    });
    setPromptInput('');
    setIsAiThinking(true);

    try {
      const history = aiMessages.slice(-6).map((m) => ({
        role: m.role,
        content: m.content,
      }));
      const resp = await fetch(`${API_BASE}/api/ai-edit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt: trimmed,
          project_state: useEditorStore.getState().project,
          history,
          campaign_id: active?.id,
          campaign_rules: active?.editingRules ?? [],
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || 'AI edit failed');
      }
      const data = await resp.json();
      const executed = applyAiToolCalls(
        data.tool_calls || [],
        data.auto_captions || null,
      );
      appendAiMessage({
        id: `a_${Date.now()}`,
        role: 'assistant',
        content: data.message,
        routeUsed: data.route_used,
        actions: executed,
      });
    } catch (err) {
      appendAiMessage({
        id: `err_${Date.now()}`,
        role: 'assistant',
        content: `AI error: ${err instanceof Error ? err.message : String(err)}`,
      });
    } finally {
      setIsAiThinking(false);
    }
  };

  const submitReferenceStyle = async () => {
    const cleanRef = refUrl.trim();
    if (!cleanRef || isCloningStyle || isAiThinking || isIngesting) return;
    if (!project.sourceVideo.videoUrl) {
      setStatusBanner('Load or clip your source video first before cloning a reference style.');
      return;
    }

    appendAiMessage({
      id: `u_ref_${Date.now()}`,
      role: 'user',
      content: `Edit like this YouTube reference: ${cleanRef}${
        refInstruction.trim() ? ` (${refInstruction.trim()})` : ''
      }`,
    });
    setIsCloningStyle(true);

    try {
      const resp = await fetch(`${API_BASE}/api/reference-style`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          reference_url: cleanRef,
          project_state: useEditorStore.getState().project,
          custom_instruction: refInstruction.trim() || undefined,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || 'Reference style analysis failed');
      }
      const data = await resp.json();
      const executed = applyAiToolCalls(
        data.tool_calls || [],
        data.auto_captions || null,
      );
      appendAiMessage({
        id: `a_ref_${Date.now()}`,
        role: 'assistant',
        content: data.message,
        routeUsed: data.route_used,
        actions: executed,
      });
      setRefInstruction('');
    } catch (err) {
      appendAiMessage({
        id: `err_ref_${Date.now()}`,
        role: 'assistant',
        content: `Edit Like This error: ${err instanceof Error ? err.message : String(err)}`,
      });
    } finally {
      setIsCloningStyle(false);
    }
  };

  const toggleVoiceRecording = async () => {
    if (isRecording) {
      mediaRecorderRef.current?.stop();
      setIsRecording(false);
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      audioChunksRef.current = [];

      recorder.ondataavailable = (ev) => {
        if (ev.data.size > 0) audioChunksRef.current.push(ev.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
        const formData = new FormData();
        formData.append('file', blob, 'voice.webm');
        try {
          const resp = await fetch(`${API_BASE}/api/voice-transcribe`, {
            method: 'POST',
            body: formData,
          });
          if (resp.ok) {
            const data = await resp.json();
            if (data.text) {
              await submitAiPrompt(data.text);
            }
          } else {
            const err = await resp.json().catch(() => ({ detail: 'Voice error' }));
            setStatusBanner(err.detail || 'Voice transcription failed');
          }
        } catch (err) {
          setStatusBanner(
            `Voice error: ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      };

      mediaRecorderRef.current = recorder;
      recorder.start();
      setIsRecording(true);
    } catch {
      setStatusBanner('Microphone access denied or unavailable.');
    }
  };

  const style = project.captionStyle;
  const presets: Array<{ id: CaptionPreset; label: string; desc: string }> = [
    { id: 'karaoke', label: 'Karaoke', desc: 'Word-by-word highlight' },
    { id: 'bold_social', label: 'Bold Social', desc: 'Uppercase + thick outline' },
    { id: 'yellow_highlight', label: 'Yellow Highlight', desc: 'Keyword color pop' },
    { id: 'minimal', label: 'Minimal', desc: 'Clean dark subtitle box' },
  ];

  const positions: CaptionPosition[] = ['top', 'middle', 'bottom'];

  return (
    <aside
      className={`${
        isChatExpanded ? 'w-[520px]' : 'w-[360px] xl:w-[400px]'
      } border-l border-[#272f40] bg-[#151922] flex flex-col min-h-0 shrink-0 transition-[width] duration-150`}
    >
      {/* Segmented Header Tabs + Panel Width Toggle */}
      <div className="p-2 border-b border-[#272f40] flex items-center gap-1.5 shrink-0">
        <div className="flex-1 grid grid-cols-2 gap-1 bg-[#0f1218] p-1 rounded-xl border border-[#272f40]">
          <button
            type="button"
            onClick={() => setActiveTab('ai')}
            className={`py-1.5 px-2 rounded-lg text-xs font-semibold flex items-center justify-center gap-1.5 transition cursor-pointer ${
              activeTab === 'ai'
                ? 'bg-[#1c212d] text-amber-300 shadow-sm border border-[#272f40]'
                : 'text-slate-400 hover:text-slate-200 border border-transparent'
            }`}
          >
            <Bot className="w-3.5 h-3.5" />
            AI Editor
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('styles')}
            className={`py-1.5 px-2 rounded-lg text-xs font-semibold flex items-center justify-center gap-1.5 transition cursor-pointer ${
              activeTab === 'styles'
                ? 'bg-[#1c212d] text-amber-300 shadow-sm border border-[#272f40]'
                : 'text-slate-400 hover:text-slate-200 border border-transparent'
            }`}
          >
            <Palette className="w-3.5 h-3.5" />
            <span>Styles &amp; Tools</span>
            {(selectedZoom || selectedDrawing || selectedSfx || hasActiveCrop) && (
              <span
                className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0"
                title="Active selection or crop settings available"
              />
            )}
          </button>
        </div>
        <button
          type="button"
          onClick={() => setIsChatExpanded((prev) => !prev)}
          title={isChatExpanded ? 'Standard AI panel width' : 'Widen AI panel for easier reading'}
          aria-label={isChatExpanded ? 'Shrink AI panel' : 'Widen AI panel'}
          className="p-2 rounded-xl bg-[#0f1218] hover:bg-[#1c212d] border border-[#272f40] text-slate-300 hover:text-amber-300 transition shrink-0 cursor-pointer"
        >
          {isChatExpanded ? (
            <Minimize2 className="w-3.5 h-3.5" />
          ) : (
            <Maximize2 className="w-3.5 h-3.5" />
          )}
        </button>
      </div>

      {activeTab === 'ai' ? (
        <div className="flex-1 flex flex-col min-h-0">
          {/* Unified Compact Toolbar: AI Status + Edit Like This Reference + Refresh */}
          <div className="px-3 py-1.5 border-b border-[#272f40] bg-[#12161f] shrink-0">
            <div className="flex items-center justify-between gap-1.5">
              <button
                type="button"
                onClick={() => {
                  setShowModels((prev) => !prev);
                  setIsRefOpen(false);
                }}
                className="flex items-center gap-1.5 min-w-0 text-left group cursor-pointer"
                title="Click to inspect live Groq & Gemini model configuration"
              >
                <span
                  className={`w-2 h-2 rounded-full shrink-0 ${
                    modelStatus?.groqConfigured ? 'bg-emerald-400' : 'bg-amber-400'
                  }`}
                />
                <span className="text-[11px] font-medium text-slate-200 group-hover:text-white truncate">
                  {modelStatus?.groqConfigured && modelStatus?.geminiConfigured
                    ? 'AI Ready · Groq + Gemini'
                    : modelStatus?.groqConfigured
                      ? 'AI Ready · Groq Active'
                      : 'AI Models · Check Keys'}
                </span>
                {showModels ? (
                  <ChevronUp className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                ) : (
                  <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                )}
              </button>

              <div className="flex items-center gap-1 shrink-0">
                <button
                  type="button"
                  onClick={() => {
                    setIsRefOpen((prev) => !prev);
                    setShowModels(false);
                  }}
                  title="Clone zooms, pacing & style from a reference YouTube video"
                  className={`flex items-center gap-1 px-2 py-1 rounded-lg border text-[11px] font-medium transition cursor-pointer ${
                    isRefOpen
                      ? 'bg-sky-500/15 border-sky-400/50 text-sky-200'
                      : 'bg-[#1c212d] hover:bg-[#242b3b] border-[#272f40] text-sky-300'
                  }`}
                >
                  <Link2 className="w-3 h-3 shrink-0" />
                  <span>Edit Like This</span>
                </button>
                <button
                  type="button"
                  onClick={() => void forceRefreshModels()}
                  disabled={isRefreshingModels}
                  title="Reload .env API keys, scan live Groq/Gemini models, and reset stuck AI state"
                  aria-label="Refresh AI models"
                  className="p-1.5 rounded-lg bg-[#1c212d] hover:bg-[#242b3b] disabled:opacity-50 border border-[#272f40] text-slate-300 transition shrink-0 cursor-pointer"
                >
                  <RefreshCw className={`w-3 h-3 ${isRefreshingModels ? 'animate-spin' : ''}`} />
                </button>
              </div>
            </div>

            {showModels && (
              <div className="mt-2 p-2.5 rounded-xl bg-[#1c212d] border border-[#272f40] space-y-1.5 text-[11px] max-h-60 overflow-y-auto">
                <div
                  className="text-slate-300 truncate"
                  title={modelStatus?.backupToolModels.join(' -> ')}
                >
                  <span className="text-slate-400">Tools:</span>{' '}
                  <span className="text-emerald-300 font-mono">
                    {modelStatus?.primaryToolModel ?? 'openai/gpt-oss-120b'}
                  </span>
                  {modelStatus && modelStatus.backupToolModels.length > 0 && (
                    <span className="text-slate-500">
                      {' '}
                      (+{modelStatus.backupToolModels.length} backup
                      {modelStatus.backupToolModels.length > 1 ? 's' : ''})
                    </span>
                  )}
                </div>
                <div
                  className="text-slate-300 truncate"
                  title={modelStatus?.backupRouterModels.join(' -> ')}
                >
                  <span className="text-slate-400">Router:</span>{' '}
                  <span className="text-sky-300 font-mono">
                    {modelStatus?.primaryRouterModel ?? 'openai/gpt-oss-20b'}
                  </span>
                  {modelStatus && modelStatus.backupRouterModels.length > 0 && (
                    <span className="text-slate-500">
                      {' '}
                      (+{modelStatus.backupRouterModels.length} backup
                      {modelStatus.backupRouterModels.length > 1 ? 's' : ''})
                    </span>
                  )}
                </div>
                <div
                  className="text-slate-300 truncate"
                  title={modelStatus?.backupViralModels.join(' -> ')}
                >
                  <span className="text-slate-400">Vision Planner:</span>{' '}
                  <span className="text-violet-300 font-mono">
                    {modelStatus?.primaryViralModel ?? 'gemini-2.5-flash'}
                  </span>
                  {modelStatus && modelStatus.backupViralModels.length > 0 && (
                    <span className="text-slate-500">
                      {' '}
                      (+{modelStatus.backupViralModels.length} backup
                      {modelStatus.backupViralModels.length > 1 ? 's' : ''})
                    </span>
                  )}
                </div>
                <div className="flex items-center justify-between gap-2 text-[11px] text-slate-400 pt-0.5">
                  <span className="truncate">
                    STT:{' '}
                    <span className="text-slate-200 font-mono">
                      {modelStatus?.primarySttModel ?? 'whisper-large-v3-turbo'}
                    </span>
                  </span>
                  <span className="shrink-0">
                    <span className={modelStatus?.groqConfigured ? 'text-emerald-400' : 'text-amber-400'}>
                      Groq {modelStatus?.groqConfigured ? 'OK' : 'Off'}
                    </span>
                    {' · '}
                    <span className={modelStatus?.geminiConfigured ? 'text-emerald-400' : 'text-slate-400'}>
                      Gemini {modelStatus?.geminiConfigured ? 'OK' : 'Fallback'}
                    </span>
                  </span>
                </div>
                {modelStatus?.lastRefreshedIso && (
                  <div className="pt-1 border-t border-[#272f40] text-[10px] text-slate-500 flex items-center justify-between">
                    <span>
                      Scanned{' '}
                      {new Date(modelStatus.lastRefreshedIso).toLocaleTimeString([], {
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </span>
                    <span>
                      {modelStatus.availableGroqModels.length} Groq ·{' '}
                      {modelStatus.availableGeminiModels.length} Gemini
                    </span>
                  </div>
                )}
              </div>
            )}

            {isRefOpen && (
              <div className="mt-2 p-2.5 rounded-xl bg-[#1c212d] border border-[#272f40] space-y-2 max-h-60 overflow-y-auto">
                <p className="text-[11px] text-slate-400 leading-relaxed">
                  Paste a reference YouTube Short/video. Gemini breaks down its funny beats, pacing, zooms &amp; captions and matches them onto your clip.
                </p>
                <input
                  type="url"
                  value={refUrl}
                  onChange={(e) => setRefUrl(e.target.value)}
                  placeholder="Paste reference YouTube / Shorts link..."
                  className="w-full bg-[#12161f] border border-[#272f40] focus:border-sky-400 rounded-lg px-2.5 py-1.5 text-xs text-slate-100 placeholder:text-slate-500 outline-none"
                />
                <input
                  type="text"
                  value={refInstruction}
                  onChange={(e) => setRefInstruction(e.target.value)}
                  placeholder="Optional note (e.g. copy zooms & fast captions)"
                  className="w-full bg-[#12161f] border border-[#272f40] focus:border-sky-400 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 placeholder:text-slate-500 outline-none"
                />
                <button
                  type="button"
                  disabled={!refUrl.trim() || !project.sourceVideo.videoUrl || isCloningStyle}
                  onClick={() => void submitReferenceStyle()}
                  className="w-full py-2 rounded-lg bg-sky-500 hover:bg-sky-400 disabled:opacity-40 text-slate-950 text-xs font-semibold flex items-center justify-center gap-1.5 transition cursor-pointer"
                >
                  {isCloningStyle ? (
                    <>
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      Analyzing Reference Style...
                    </>
                  ) : (
                    <>
                      <Wand2 className="w-3.5 h-3.5" />
                      Clone Style onto My Clip
                    </>
                  )}
                </button>
              </div>
            )}
          </div>

          {/* Chat Feed */}
          <div ref={chatScrollRef} className="flex-1 min-h-0 overflow-y-auto px-3 py-3 space-y-3">
            {aiMessages.map((msg) => (
              <div
                key={msg.id}
                className={`rounded-2xl p-3.5 space-y-2 ${
                  msg.role === 'user'
                    ? 'bg-amber-400/12 border border-amber-400/30 text-amber-50 ml-4'
                    : 'bg-[#1c212d] border border-[#2b3447] text-slate-100 shadow-sm'
                }`}
              >
                <div className="flex items-center justify-between gap-2 text-[11px] text-slate-400">
                  <div className="flex items-center gap-1.5 min-w-0">
                    {msg.role === 'assistant' && (
                      <Bot className="w-3.5 h-3.5 text-amber-300 shrink-0" />
                    )}
                    <span
                      className={`font-semibold shrink-0 ${
                        msg.role === 'user' ? 'text-amber-300' : 'text-slate-200'
                      }`}
                    >
                      {msg.role === 'user' ? 'You' : 'ClipForge AI'}
                    </span>
                    {msg.campaignLabel && (
                      <span className="px-1.5 py-0.5 rounded-md bg-violet-500/20 border border-violet-400/30 text-[10px] text-violet-200 font-medium shrink-0">
                        {msg.campaignLabel}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 min-w-0">
                    {msg.routeUsed && (
                      <span
                        title={msg.routeUsed}
                        className="font-mono text-[10px] text-emerald-300 truncate max-w-[150px]"
                      >
                        {msg.routeUsed}
                      </span>
                    )}
                    {msg.role === 'assistant' && msg.content && (
                      <button
                        type="button"
                        onClick={() => {
                          void navigator.clipboard?.writeText(msg.content);
                          setCopiedTextId(msg.id);
                          window.setTimeout(() => setCopiedTextId(null), 1500);
                        }}
                        title="Copy AI reply"
                        aria-label="Copy AI reply"
                        className="p-1 rounded hover:bg-white/10 text-slate-400 hover:text-slate-100 transition shrink-0 cursor-pointer"
                      >
                        {copiedTextId === msg.id ? (
                          <Check className="w-3 h-3 text-emerald-400" />
                        ) : (
                          <Copy className="w-3 h-3" />
                        )}
                      </button>
                    )}
                  </div>
                </div>
                <p className="text-[13px] leading-[1.55] text-slate-100 whitespace-pre-wrap break-words">
                  {msg.content}
                </p>

                {msg.copyableCaption && (
                  <div className="pt-1 flex items-center justify-end">
                    <button
                      type="button"
                      onClick={() => {
                        void navigator.clipboard?.writeText(msg.copyableCaption || '');
                        setCopiedMsgId(msg.id);
                        window.setTimeout(() => setCopiedMsgId(null), 2000);
                      }}
                      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-semibold transition cursor-pointer ${
                        copiedMsgId === msg.id
                          ? 'bg-emerald-500 text-slate-950'
                          : 'bg-amber-400 hover:bg-amber-300 text-slate-950'
                      }`}
                    >
                      {copiedMsgId === msg.id ? (
                        <>
                          <Check className="w-3 h-3" />
                          Copied #LACY Caption!
                        </>
                      ) : (
                        <>
                          <Copy className="w-3 h-3" />
                          Copy #LACY Caption
                        </>
                      )}
                    </button>
                  </div>
                )}

                {/* AI Action Log Badges with One-Click Undo */}
                {msg.actions && msg.actions.length > 0 && (
                  <div className="pt-2 border-t border-[#272f40] flex flex-wrap items-center gap-1.5">
                    {msg.actions.map((act) => (
                      <div
                        key={act.id}
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-emerald-500/15 border border-emerald-500/30 text-[11px] text-emerald-300"
                      >
                        <Wand2 className="w-3 h-3 shrink-0" />
                        <span>{act.summary}</span>
                      </div>
                    ))}
                    {msg.id === lastActionMsgId && (
                      <button
                        type="button"
                        onClick={() => useEditorStore.temporal.getState().undo()}
                        className="ml-auto inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/30 text-[11px] text-amber-300 cursor-pointer"
                        title="Undo last AI edit (Ctrl+Z)"
                      >
                        <Undo2 className="w-3 h-3" />
                        Undo
                      </button>
                    )}
                  </div>
                )}
              </div>
            ))}
            {isAiThinking && (
              <div className="flex items-center gap-2 text-xs text-slate-300 bg-[#1c212d] border border-[#272f40] rounded-xl p-2.5">
                <Loader2 className="w-3.5 h-3.5 animate-spin text-amber-400 shrink-0" />
                <span>Planning visuals &amp; executing timeline edits...</span>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>

          {/* Recent AI Action Log Strip */}
          {aiActionLog.length > 0 && (
            <div className="px-3 py-1 border-t border-[#272f40] bg-[#12161f] flex items-center justify-between text-[11px] text-slate-400 shrink-0">
              <span className="truncate">
                Last edit: <strong className="text-slate-200">{aiActionLog[0].toolName}</strong>
              </span>
              <button
                type="button"
                onClick={() => useEditorStore.temporal.getState().undo()}
                className="text-amber-300 hover:text-amber-200 flex items-center gap-1 shrink-0 cursor-pointer"
              >
                <Undo2 className="w-3 h-3" />
                Undo
              </button>
            </div>
          )}

          {/* Active Campaign Rules Banner */}
          {activeCampaign && (
            <div className="px-3 py-1.5 border-t border-[#272f40] bg-violet-500/10 flex items-center justify-between gap-2 text-[11px] text-violet-200 shrink-0">
              <div className="flex items-center gap-1.5 min-w-0">
                <ShieldCheck className="w-3.5 h-3.5 text-violet-300 shrink-0" />
                <span className="truncate">
                  <strong>{activeCampaign.label}:</strong> {activeCampaign.editingRules.length} rules · &ldquo;{activeCampaign.overlayText}&rdquo;
                </span>
              </div>
              <button
                type="button"
                onClick={() => setActiveCampaign(null)}
                className="text-violet-300 hover:text-white underline shrink-0 cursor-pointer"
              >
                Off
              </button>
            </div>
          )}

          {/* Bottom Composer: Collapsible Quick Suggestions + Auto-Expanding Prompt Box */}
          <div className="px-3 py-2.5 border-t border-[#272f40] bg-[#12161f] space-y-2 shrink-0">
            {(showQuickPrompts || aiMessages.length <= 1) && (
              <div className="flex items-center gap-1.5 overflow-x-auto pb-1">
                {QUICK_PROMPTS.map((qp) => (
                  <button
                    key={qp}
                    type="button"
                    disabled={
                      isAiThinking ||
                      isIngesting ||
                      !(project.sourceVideo.videoUrl || pendingYoutubeUrl.trim())
                    }
                    onClick={() => submitAiPrompt(qp)}
                    title={qp}
                    className="shrink-0 max-w-[205px] truncate px-2.5 py-1 rounded-full bg-[#1c212d] hover:bg-[#242b3b] disabled:opacity-40 border border-[#272f40] text-[11px] text-slate-300 hover:text-slate-100 transition cursor-pointer"
                  >
                    {qp}
                  </button>
                ))}
              </div>
            )}

            <form
              onSubmit={(e) => {
                e.preventDefault();
                submitAiPrompt(promptInput);
              }}
              className="flex items-end gap-1.5"
            >
              <button
                type="button"
                onClick={() => setShowQuickPrompts((prev) => !prev)}
                title={showQuickPrompts ? 'Hide quick prompt suggestions' : 'Show quick prompt suggestions'}
                aria-label="Toggle quick prompt suggestions"
                className={`p-2 rounded-xl border transition shrink-0 cursor-pointer ${
                  showQuickPrompts || aiMessages.length <= 1
                    ? 'bg-amber-400/15 border-amber-400/40 text-amber-300'
                    : 'bg-[#1c212d] border-[#272f40] text-slate-400 hover:text-slate-100 hover:bg-[#242b3b]'
                }`}
              >
                <Sparkles className="w-4 h-4" />
              </button>
              <button
                type="button"
                onClick={toggleVoiceRecording}
                title={isRecording ? 'Stop & send voice command' : 'Record voice command'}
                className={`p-2 rounded-xl border transition shrink-0 cursor-pointer ${
                  isRecording
                    ? 'bg-rose-500/20 border-rose-500 text-rose-300 animate-pulse'
                    : 'bg-[#1c212d] border-[#272f40] text-slate-300 hover:text-slate-100 hover:bg-[#242b3b]'
                }`}
              >
                {isRecording ? (
                  <MicOff className="w-4 h-4" />
                ) : (
                  <Mic className="w-4 h-4" />
                )}
              </button>
              <textarea
                rows={2}
                value={promptInput}
                onChange={(e) => setPromptInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    submitAiPrompt(promptInput);
                  }
                }}
                placeholder={
                  !project.sourceVideo.videoUrl && pendingYoutubeUrl.trim()
                    ? 'Ask which part to clip first (e.g. funny part)...'
                    : 'Ask AI to clip, crop, zoom, or style...'
                }
                className="flex-1 bg-[#1c212d] border border-[#272f40] focus:border-amber-400/70 rounded-xl px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 outline-none resize-none max-h-24 leading-snug"
              />
              <button
                type="submit"
                disabled={isAiThinking || isIngesting || !promptInput.trim()}
                className="p-2 rounded-xl bg-amber-400 hover:bg-amber-300 disabled:opacity-40 text-slate-950 transition shrink-0 cursor-pointer"
                title="Send prompt"
              >
                <Send className="w-4 h-4" />
              </button>
            </form>
          </div>
        </div>
      ) : (
        /* Caption, Crop, Zoom, Drawing, SFX & Overlay Customizer */
        <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-3">
          {/* Selected Zoom Effect Inspector */}
          {selectedZoom && (
            <div className="p-3 rounded-xl bg-[#1c212d] border border-sky-400/35 space-y-2.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-sky-300">
                  Selected Zoom Effect
                </span>
                <button
                  type="button"
                  onClick={() => delete_zoom_effect(selectedZoom.id)}
                  className="text-[11px] text-rose-400 hover:text-rose-300 underline cursor-pointer"
                >
                  Delete
                </button>
              </div>
              <div className="grid grid-cols-3 gap-1">
                {(['punch_in', 'zoom_in', 'zoom_out'] as const).map((k) => (
                  <button
                    key={k}
                    type="button"
                    onClick={() =>
                      update_zoom_effect(selectedZoom.id, {
                        kind: k,
                        easing: k === 'punch_in' ? 'instant' : 'ease_in_out',
                        startScale: k === 'zoom_out' ? 1.45 : 1.0,
                        endScale: k === 'zoom_out' ? 1.0 : 1.45,
                      })
                    }
                    className={`py-1.5 rounded-lg text-[11px] border capitalize transition cursor-pointer ${
                      selectedZoom.kind === k
                        ? 'bg-sky-500 text-slate-950 border-sky-400 font-semibold'
                        : 'bg-[#12161f] border-[#272f40] text-slate-300 hover:border-[#333c4e]'
                    }`}
                  >
                    {k.replace('_', ' ')}
                  </button>
                ))}
              </div>
              <div className="grid grid-cols-2 gap-2.5">
                <div className="space-y-1">
                  <div className="flex justify-between text-[11px] text-slate-300">
                    <span>Start Scale</span>
                    <span className="font-mono text-sky-300 tabular-nums">
                      {selectedZoom.startScale.toFixed(2)}x
                    </span>
                  </div>
                  <input
                    type="range"
                    min={1.0}
                    max={2.5}
                    step={0.05}
                    value={selectedZoom.startScale}
                    onChange={(e) =>
                      update_zoom_effect(selectedZoom.id, {
                        startScale: Number(e.target.value),
                      })
                    }
                    className="w-full accent-sky-400"
                  />
                </div>
                <div className="space-y-1">
                  <div className="flex justify-between text-[11px] text-slate-300">
                    <span>End Scale</span>
                    <span className="font-mono text-sky-300 tabular-nums">
                      {selectedZoom.endScale.toFixed(2)}x
                    </span>
                  </div>
                  <input
                    type="range"
                    min={1.0}
                    max={2.5}
                    step={0.05}
                    value={selectedZoom.endScale}
                    onChange={(e) =>
                      update_zoom_effect(selectedZoom.id, {
                        endScale: Number(e.target.value),
                      })
                    }
                    className="w-full accent-sky-400"
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2.5">
                <div className="space-y-1">
                  <div className="flex justify-between text-[11px] text-slate-300">
                    <span>Focus X</span>
                    <span className="font-mono text-sky-300 tabular-nums">
                      {Math.round(selectedZoom.focusX * 100)}%
                    </span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={selectedZoom.focusX}
                    onChange={(e) =>
                      update_zoom_effect(selectedZoom.id, {
                        focusX: Number(e.target.value),
                      })
                    }
                    className="w-full accent-sky-400"
                  />
                </div>
                <div className="space-y-1">
                  <div className="flex justify-between text-[11px] text-slate-300">
                    <span>Focus Y</span>
                    <span className="font-mono text-sky-300 tabular-nums">
                      {Math.round(selectedZoom.focusY * 100)}%
                    </span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={selectedZoom.focusY}
                    onChange={(e) =>
                      update_zoom_effect(selectedZoom.id, {
                        focusY: Number(e.target.value),
                      })
                    }
                    className="w-full accent-sky-400"
                  />
                </div>
              </div>
            </div>
          )}

          {/* Selected Drawing Inspector */}
          {selectedDrawing && (
            <div className="p-3 rounded-xl bg-[#1c212d] border border-rose-400/35 space-y-2.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold capitalize text-rose-300">
                  Selected {selectedDrawing.tool} Drawing
                </span>
                <button
                  type="button"
                  onClick={() => delete_drawing(selectedDrawing.id)}
                  className="text-[11px] text-rose-400 hover:text-rose-300 underline cursor-pointer"
                >
                  Delete
                </button>
              </div>
              <div className="grid grid-cols-2 gap-2.5">
                <div className="space-y-1">
                  <label className="text-[11px] text-slate-300">Color</label>
                  <div className="flex items-center gap-2 bg-[#12161f] border border-[#272f40] rounded-lg p-1.5">
                    <input
                      type="color"
                      value={selectedDrawing.color}
                      onChange={(e) =>
                        update_drawing(selectedDrawing.id, { color: e.target.value })
                      }
                      className="w-5 h-5 rounded bg-transparent cursor-pointer"
                    />
                    <span className="text-[11px] font-mono text-slate-300 truncate">
                      {selectedDrawing.color}
                    </span>
                  </div>
                </div>
                <div className="space-y-1">
                  <div className="flex justify-between text-[11px] text-slate-300">
                    <span>Stroke</span>
                    <span className="font-mono text-rose-300 tabular-nums">
                      {selectedDrawing.strokeWidth}px
                    </span>
                  </div>
                  <input
                    type="range"
                    min={2}
                    max={24}
                    value={selectedDrawing.strokeWidth}
                    onChange={(e) =>
                      update_drawing(selectedDrawing.id, {
                        strokeWidth: Number(e.target.value),
                      })
                    }
                    className="w-full accent-rose-400"
                  />
                </div>
              </div>
              <div className="space-y-1">
                <div className="flex justify-between text-[11px] text-slate-300">
                  <span>Opacity</span>
                  <span className="font-mono text-rose-300 tabular-nums">
                    {Math.round(selectedDrawing.opacity * 100)}%
                  </span>
                </div>
                <input
                  type="range"
                  min={0.15}
                  max={1}
                  step={0.05}
                  value={selectedDrawing.opacity}
                  onChange={(e) =>
                    update_drawing(selectedDrawing.id, {
                      opacity: Number(e.target.value),
                    })
                  }
                  className="w-full accent-rose-400"
                />
              </div>
            </div>
          )}

          {/* Selected SFX Clip Inspector */}
          {selectedSfx && (
            <div className="p-3 rounded-xl bg-[#1c212d] border border-emerald-400/35 space-y-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-semibold text-emerald-300 flex items-center gap-1.5 truncate">
                  <Volume2 className="w-3.5 h-3.5 shrink-0" />
                  SFX: {selectedSfx.name}
                </span>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    type="button"
                    onClick={togglePreviewTrimmedSfx}
                    title={isPreviewingSfx ? 'Stop SFX preview' : 'Preview trimmed SFX slice'}
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium border transition cursor-pointer ${
                      isPreviewingSfx
                        ? 'bg-emerald-400 text-slate-950 border-emerald-300'
                        : 'bg-[#12161f] hover:bg-white/5 text-emerald-200 border-[#272f40]'
                    }`}
                  >
                    {isPreviewingSfx ? (
                      <>
                        <Square className="w-2.5 h-2.5 fill-current" />
                        Stop
                      </>
                    ) : (
                      <>
                        <Play className="w-2.5 h-2.5 fill-current" />
                        Preview
                      </>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => delete_sfx_clip(selectedSfx.id)}
                    className="text-[11px] text-rose-400 hover:text-rose-300 underline cursor-pointer"
                  >
                    Delete
                  </button>
                </div>
              </div>

              <div className="space-y-1">
                <div className="flex justify-between text-[11px] text-slate-300">
                  <span>Volume</span>
                  <span className="font-mono text-emerald-300 tabular-nums">
                    {Math.round(selectedSfx.volume * 100)}%
                  </span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={2}
                  step={0.05}
                  value={selectedSfx.volume}
                  onChange={(e) =>
                    update_sfx_clip(selectedSfx.id, {
                      volume: Number(e.target.value),
                    })
                  }
                  className="w-full accent-emerald-400"
                />
              </div>

              <div className="grid grid-cols-3 gap-2">
                <div className="space-y-1">
                  <label className="text-[11px] text-slate-300">Timeline (s)</label>
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    value={selectedSfx.start}
                    onChange={(e) =>
                      update_sfx_clip(selectedSfx.id, {
                        start: parseFloat(e.target.value) || 0,
                      })
                    }
                    className="w-full bg-[#12161f] border border-[#272f40] rounded-lg px-2 py-1.5 text-xs font-mono text-slate-200 outline-none focus:border-emerald-400"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-[11px] text-slate-300">Trim In (s)</label>
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    max={Math.max(0, Number((sfxSourceDur - 0.15).toFixed(2)))}
                    value={sfxTrimStart}
                    onChange={(e) => {
                      const nextIn = Math.max(
                        0,
                        Math.min(Math.max(0, sfxSourceDur - 0.15), parseFloat(e.target.value) || 0),
                      );
                      const deltaTrim = nextIn - sfxTrimStart;
                      update_sfx_clip(selectedSfx.id, {
                        trimStart: nextIn,
                        duration: Math.max(
                          0.15,
                          Number((selectedSfx.duration - deltaTrim).toFixed(2)),
                        ),
                      });
                    }}
                    className="w-full bg-[#12161f] border border-[#272f40] rounded-lg px-2 py-1.5 text-xs font-mono text-slate-200 outline-none focus:border-emerald-400"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-[11px] text-slate-300">Duration (s)</label>
                  <input
                    type="number"
                    step="0.1"
                    min="0.15"
                    max={Number(Math.max(0.15, sfxSourceDur - sfxTrimStart).toFixed(2))}
                    value={selectedSfx.duration}
                    onChange={(e) =>
                      update_sfx_clip(selectedSfx.id, {
                        duration: parseFloat(e.target.value) || 0.5,
                      })
                    }
                    className="w-full bg-[#12161f] border border-[#272f40] rounded-lg px-2 py-1.5 text-xs font-mono text-slate-200 outline-none focus:border-emerald-400"
                  />
                </div>
              </div>

              <div className="space-y-1 pt-0.5">
                <div className="flex justify-between text-[10px] text-slate-400 font-mono tabular-nums">
                  <span>
                    Slice: {sfxTrimStart.toFixed(1)}s →{' '}
                    {(sfxTrimStart + selectedSfx.duration).toFixed(1)}s of{' '}
                    {sfxSourceDur.toFixed(1)}s
                  </span>
                  {(sfxTrimStart > 0.02 ||
                    Math.abs(selectedSfx.duration - sfxSourceDur) > 0.05) && (
                    <button
                      type="button"
                      onClick={() =>
                        update_sfx_clip(selectedSfx.id, {
                          trimStart: 0,
                          duration: sfxSourceDur,
                        })
                      }
                      className="text-emerald-300 hover:underline font-sans cursor-pointer"
                    >
                      Reset Trim
                    </button>
                  )}
                </div>
                <input
                  type="range"
                  min={0.15}
                  max={Math.max(0.2, Number((sfxSourceDur - sfxTrimStart).toFixed(2)))}
                  step={0.05}
                  value={Math.min(
                    Math.max(0.15, selectedSfx.duration),
                    Math.max(0.2, sfxSourceDur - sfxTrimStart),
                  )}
                  onChange={(e) =>
                    update_sfx_clip(selectedSfx.id, {
                      duration: Number(e.target.value),
                    })
                  }
                  aria-label="Trimmed SFX duration"
                  className="w-full accent-emerald-400"
                />
              </div>
            </div>
          )}

          {/* Video Crop Framing */}
          <div
            className={`p-3 rounded-xl bg-[#1c212d] border space-y-2.5 ${
              isCropMode || hasActiveCrop ? 'border-amber-400/35' : 'border-[#272f40]'
            }`}
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-amber-300 flex items-center gap-1.5">
                <Crop className="w-3.5 h-3.5" />
                Video Crop Framing
              </span>
              {hasActiveCrop && (
                <button
                  type="button"
                  onClick={() => reset_video_crop()}
                  className="text-[11px] text-slate-400 hover:text-slate-200 underline cursor-pointer"
                >
                  Reset
                </button>
              )}
            </div>

            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                disabled={project.tracks.clips.length === 0}
                onClick={() => setIsCropMode(!isCropMode)}
                className={`py-1.5 px-2.5 rounded-lg text-xs font-medium border flex items-center justify-center gap-1.5 transition disabled:opacity-40 cursor-pointer ${
                  isCropMode
                    ? 'bg-amber-400 text-slate-950 border-amber-300 font-semibold'
                    : 'bg-[#12161f] hover:bg-[#171c28] text-amber-200 border-[#272f40]'
                }`}
              >
                <Crop className="w-3.5 h-3.5" />
                {isCropMode ? 'Done Cropping' : 'Drag Crop Box'}
              </button>
              <button
                type="button"
                disabled={!hasActiveCrop}
                onClick={() => reset_video_crop()}
                className="py-1.5 px-2.5 rounded-lg text-xs bg-[#12161f] hover:bg-[#171c28] disabled:opacity-40 border border-[#272f40] text-slate-300 cursor-pointer"
              >
                Full Frame
              </button>
            </div>

            <div className="grid grid-cols-3 gap-1.5">
              {[
                { label: 'Top-Left Cam', rect: { x: 0.0, y: 0.0, w: 0.36, h: 0.42 } },
                { label: 'Center Focus', rect: { x: 0.2, y: 0.05, w: 0.6, h: 0.9 } },
                { label: 'Top-Right Cam', rect: { x: 0.64, y: 0.0, w: 0.36, h: 0.42 } },
              ].map((preset) => (
                <button
                  key={preset.label}
                  type="button"
                  disabled={project.tracks.clips.length === 0}
                  onClick={() => set_video_crop(preset.rect)}
                  className="py-1.5 px-1 rounded-lg text-[11px] bg-[#12161f] hover:bg-[#171c28] disabled:opacity-40 border border-[#272f40] text-slate-300 cursor-pointer"
                >
                  {preset.label}
                </button>
              ))}
            </div>

            <div className="grid grid-cols-4 gap-1.5 text-[11px] font-mono text-slate-400 text-center tabular-nums">
              <div className="bg-[#12161f] border border-[#272f40] rounded-lg py-1">
                X {Math.round(activeCrop.x * 100)}%
              </div>
              <div className="bg-[#12161f] border border-[#272f40] rounded-lg py-1">
                Y {Math.round(activeCrop.y * 100)}%
              </div>
              <div className="bg-[#12161f] border border-[#272f40] rounded-lg py-1">
                W {Math.round(activeCrop.w * 100)}%
              </div>
              <div className="bg-[#12161f] border border-[#272f40] rounded-lg py-1">
                H {Math.round(activeCrop.h * 100)}%
              </div>
            </div>

            {hasActiveCrop && (
              <div className="pt-1 border-t border-[#272f40] space-y-2">
                <div className="flex items-center justify-between text-[11px]">
                  <span className="text-slate-300 font-medium">
                    {activeCrop.start != null && activeCrop.end != null
                      ? `Timed Crop (${activeCrop.start.toFixed(1)}s – ${activeCrop.end.toFixed(1)}s)`
                      : 'Duration: Entire Clip'}
                  </span>
                  {activeCrop.start != null && activeCrop.end != null ? (
                    <button
                      type="button"
                      onClick={() =>
                        set_video_crop({
                          x: activeCrop.x,
                          y: activeCrop.y,
                          w: activeCrop.w,
                          h: activeCrop.h,
                        })
                      }
                      className="text-amber-300 hover:underline cursor-pointer"
                    >
                      Make Whole Clip
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => {
                        const ph = project.workspaceState.playheadPosition;
                        set_video_crop({
                          x: activeCrop.x,
                          y: activeCrop.y,
                          w: activeCrop.w,
                          h: activeCrop.h,
                          start: Number(ph.toFixed(1)),
                          end: Number((ph + 3.5).toFixed(1)),
                        });
                      }}
                      className="text-amber-300 hover:underline cursor-pointer"
                    >
                      Only 3.5s Here
                    </button>
                  )}
                </div>

                {activeCrop.start != null && activeCrop.end != null && (
                  <div className="grid grid-cols-2 gap-2">
                    <div className="space-y-0.5">
                      <label className="text-[10px] text-slate-400">Start (s)</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        value={activeCrop.start}
                        onChange={(e) => {
                          const s = Math.max(0, parseFloat(e.target.value) || 0);
                          set_video_crop({
                            ...activeCrop,
                            start: s,
                            end: Math.max(s + 0.5, activeCrop.end ?? s + 3.0),
                          });
                        }}
                        className="w-full bg-[#12161f] border border-[#272f40] rounded-lg px-2 py-1 text-xs font-mono text-slate-200 outline-none focus:border-amber-400"
                      />
                    </div>
                    <div className="space-y-0.5">
                      <label className="text-[10px] text-slate-400">End (s)</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0.5"
                        value={activeCrop.end}
                        onChange={(e) => {
                          const s = activeCrop.start ?? 0;
                          const en = Math.max(s + 0.5, parseFloat(e.target.value) || s + 1.0);
                          set_video_crop({
                            ...activeCrop,
                            start: s,
                            end: en,
                          });
                        }}
                        className="w-full bg-[#12161f] border border-[#272f40] rounded-lg px-2 py-1 text-xs font-mono text-slate-200 outline-none focus:border-amber-400"
                      />
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* On-Screen Overlay (LACY / Custom Watermark) */}
          <div className="p-3 rounded-xl bg-[#1c212d] border border-[#272f40] space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-violet-300">
                On-Screen Overlay (LACY)
              </span>
              {project.workspaceState.overlayText && (
                <button
                  type="button"
                  onClick={() => setOverlayText(null)}
                  className="text-[11px] text-slate-400 hover:text-slate-200 underline cursor-pointer"
                >
                  Clear
                </button>
              )}
            </div>

            <div className="space-y-1">
              <label className="text-[11px] text-slate-300">Overlay Text</label>
              <input
                type="text"
                value={project.workspaceState.overlayText ?? ''}
                onChange={(e) => setOverlayText(e.target.value || null)}
                placeholder="e.g. LACY"
                className="w-full bg-[#12161f] border border-[#272f40] focus:border-violet-400/70 rounded-lg px-2.5 py-1.5 text-xs text-slate-100 placeholder:text-slate-500 outline-none"
              />
            </div>

            <div className="space-y-1">
              <label className="text-[11px] text-slate-300">Location</label>
              <div className="grid grid-cols-3 gap-1.5">
                {(
                  [
                    { id: 'top-left', label: 'Top Left' },
                    { id: 'top-center', label: 'Top Mid' },
                    { id: 'top-right', label: 'Top Right' },
                    { id: 'bottom-left', label: 'Bot Left' },
                    { id: 'bottom-center', label: 'Bot Mid' },
                    { id: 'bottom-right', label: 'Bot Right' },
                  ] as Array<{ id: OverlayPosition; label: string }>
                ).map((loc) => (
                  <button
                    key={loc.id}
                    type="button"
                    onClick={() => updateOverlayStyle({ position: loc.id })}
                    className={`py-1.5 px-1 rounded-lg text-[11px] border transition cursor-pointer ${
                      overlayStyle.position === loc.id
                        ? 'bg-violet-500 text-white border-violet-400 font-semibold'
                        : 'bg-[#12161f] border-[#272f40] text-slate-300 hover:border-[#333c4e]'
                    }`}
                  >
                    {loc.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2.5">
              <div className="space-y-1">
                <label className="text-[11px] text-slate-300">Text Color</label>
                <div className="flex items-center gap-2 bg-[#12161f] border border-[#272f40] rounded-lg p-1.5">
                  <input
                    type="color"
                    value={overlayStyle.textColor}
                    onChange={(e) => updateOverlayStyle({ textColor: e.target.value })}
                    className="w-5 h-5 rounded bg-transparent cursor-pointer"
                  />
                  <span className="text-[11px] font-mono text-slate-300 truncate">
                    {overlayStyle.textColor}
                  </span>
                </div>
              </div>
              <div className="space-y-1">
                <label className="text-[11px] text-slate-300">Box Color</label>
                <div className="flex items-center gap-2 bg-[#12161f] border border-[#272f40] rounded-lg p-1.5">
                  <input
                    type="color"
                    value={overlayStyle.backgroundColor}
                    onChange={(e) => updateOverlayStyle({ backgroundColor: e.target.value })}
                    className="w-5 h-5 rounded bg-transparent cursor-pointer"
                  />
                  <span className="text-[11px] font-mono text-slate-300 truncate">
                    {overlayStyle.backgroundColor}
                  </span>
                </div>
              </div>
            </div>

            <div className="space-y-1">
              <div className="flex justify-between text-[11px]">
                <span className="text-slate-300">Overlay Size</span>
                <span className="font-mono text-violet-300 tabular-nums">
                  {overlayStyle.fontSize}px
                </span>
              </div>
              <input
                type="range"
                min={14}
                max={56}
                value={overlayStyle.fontSize}
                onChange={(e) =>
                  updateOverlayStyle({ fontSize: Number(e.target.value) })
                }
                className="w-full accent-violet-400"
              />
            </div>

            <label className="flex items-center justify-between p-2.5 rounded-lg bg-[#12161f] border border-[#272f40] cursor-pointer">
              <span className="text-xs text-slate-200">Show Background Box</span>
              <input
                type="checkbox"
                checked={overlayStyle.showBackground}
                onChange={(e) =>
                  updateOverlayStyle({ showBackground: e.target.checked })
                }
                className="accent-violet-400 w-4 h-4"
              />
            </label>
          </div>

          {/* Caption Presets & Typography Card */}
          <div className="p-3 rounded-xl bg-[#1c212d] border border-[#272f40] space-y-3">
            <div className="space-y-2">
              <label className="text-xs font-semibold text-amber-300 block">
                Caption Style
              </label>
              <div className="grid grid-cols-2 gap-2">
                {presets.map((p) => {
                  const active = style.preset === p.id;
                  return (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => change_caption_style({ preset: p.id })}
                      className={`p-2.5 rounded-xl border text-left transition cursor-pointer ${
                        active
                          ? 'border-amber-400 bg-amber-400/12'
                          : 'border-[#272f40] bg-[#12161f] hover:border-[#333c4e]'
                      }`}
                    >
                      <div className="text-xs font-semibold text-slate-100">{p.label}</div>
                      <div className="text-[11px] text-slate-400 mt-0.5 leading-snug">
                        {p.desc}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="space-y-1">
              <label className="text-[11px] font-medium text-slate-300">Font Family</label>
              <select
                value={style.fontFamily}
                onChange={(e) => change_caption_style({ font_family: e.target.value })}
                className="w-full bg-[#12161f] border border-[#272f40] rounded-lg px-2.5 py-1.5 text-xs text-slate-100 outline-none cursor-pointer"
              >
                <option value="Inter">Inter</option>
                <option value="Montserrat">Montserrat</option>
                <option value="Impact">Impact</option>
                <option value="Poppins">Poppins</option>
                <option value="Arial">Arial</option>
              </select>
            </div>

            <div className="space-y-1">
              <div className="flex justify-between text-xs">
                <span className="text-slate-300 font-medium">Font Size</span>
                <span className="font-mono text-amber-300 tabular-nums">{style.fontSize}px</span>
              </div>
              <input
                type="range"
                min={16}
                max={48}
                value={style.fontSize}
                onChange={(e) =>
                  change_caption_style({ font_size: Number(e.target.value) })
                }
                className="w-full accent-amber-400"
              />
            </div>

            <div className="grid grid-cols-2 gap-2.5">
              <div className="space-y-1">
                <label className="text-[11px] text-slate-300">Text Color</label>
                <div className="flex items-center gap-2 bg-[#12161f] border border-[#272f40] rounded-lg p-1.5">
                  <input
                    type="color"
                    value={style.textColor}
                    onChange={(e) =>
                      change_caption_style({ text_color: e.target.value })
                    }
                    className="w-5 h-5 rounded bg-transparent cursor-pointer"
                  />
                  <span className="text-[11px] font-mono text-slate-300 truncate">
                    {style.textColor}
                  </span>
                </div>
              </div>
              <div className="space-y-1">
                <label className="text-[11px] text-slate-300">Highlight Color</label>
                <div className="flex items-center gap-2 bg-[#12161f] border border-[#272f40] rounded-lg p-1.5">
                  <input
                    type="color"
                    value={style.highlightColor}
                    onChange={(e) =>
                      change_caption_style({ highlight_color: e.target.value })
                    }
                    className="w-5 h-5 rounded bg-transparent cursor-pointer"
                  />
                  <span className="text-[11px] font-mono text-slate-300 truncate">
                    {style.highlightColor}
                  </span>
                </div>
              </div>
            </div>

            <div className="space-y-1">
              <label className="text-[11px] font-medium text-slate-300">Position</label>
              <div className="grid grid-cols-3 gap-1.5">
                {positions.map((pos) => (
                  <button
                    key={pos}
                    type="button"
                    onClick={() => change_caption_style({ position: pos })}
                    className={`py-1.5 rounded-lg text-xs capitalize border transition cursor-pointer ${
                      style.position === pos
                        ? 'bg-amber-400 text-slate-950 border-amber-400 font-semibold'
                        : 'bg-[#12161f] border-[#272f40] text-slate-300 hover:border-[#333c4e]'
                    }`}
                  >
                    {pos}
                  </button>
                ))}
              </div>
            </div>

            <label className="flex items-center justify-between p-2.5 rounded-lg bg-[#12161f] border border-[#272f40] cursor-pointer">
              <span className="text-xs font-medium text-slate-200">Bold Text</span>
              <input
                type="checkbox"
                checked={style.isBold}
                onChange={(e) => change_caption_style({ is_bold: e.target.checked })}
                className="accent-amber-400 w-4 h-4"
              />
            </label>
          </div>
        </div>
      )}
    </aside>
  );
};
