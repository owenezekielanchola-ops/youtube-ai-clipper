import React, { useEffect, useRef, useState } from 'react';
import {
  Bot,
  Loader2,
  Mic,
  MicOff,
  Palette,
  RefreshCw,
  Send,
  Sparkles,
  Undo2,
  Wand2,
} from 'lucide-react';
import { API_BASE, useEditorStore } from '../store/useEditorStore';
import type { CaptionPosition, CaptionPreset } from '../types/editor';

const QUICK_PROMPTS = [
  'Find the funniest viral moment and make a 9:16 Short with karaoke captions',
  'Find the top 3 funniest parts of this video',
  'Remove all pauses',
  'Cut first 20 seconds',
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

  const [activeTab, setActiveTab] = useState<'ai' | 'styles'>('ai');
  const lastActionMsgId = [...aiMessages]
    .reverse()
    .find((m) => m.actions && m.actions.length > 0)?.id;

  useEffect(() => {
    void fetchModelStatus();
    const intervalId = window.setInterval(() => {
      void forceRefreshModels();
    }, 24 * 60 * 60 * 1000);
    return () => window.clearInterval(intervalId);
  }, [fetchModelStatus, forceRefreshModels]);
  const [promptInput, setPromptInput] = useState('');
  const [isRecording, setIsRecording] = useState(false);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  const submitAiPrompt = async (promptText: string) => {
    const trimmed = promptText.trim();
    if (!trimmed || isAiThinking || isIngesting) return;

    if (!project.sourceVideo.videoUrl && pendingYoutubeUrl.trim()) {
      setPromptInput('');
      await ingestYoutubeUrl(pendingYoutubeUrl.trim(), trimmed);
      return;
    }

    appendAiMessage({
      id: `u_${Date.now()}`,
      role: 'user',
      content: trimmed,
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
    <aside className="w-80 border-l border-zinc-800 bg-zinc-950 flex flex-col min-h-0 shrink-0">
      {/* Tab Bar */}
      <div className="grid grid-cols-2 border-b border-zinc-800 shrink-0">
        <button
          type="button"
          onClick={() => setActiveTab('ai')}
          className={`py-2.5 text-xs font-semibold flex items-center justify-center gap-1.5 border-b-2 transition ${
            activeTab === 'ai'
              ? 'border-amber-400 text-amber-300 bg-zinc-900/60'
              : 'border-transparent text-zinc-400 hover:text-zinc-200'
          }`}
        >
          <Bot className="w-3.5 h-3.5" />
          AI Assistant
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('styles')}
          className={`py-2.5 text-xs font-semibold flex items-center justify-center gap-1.5 border-b-2 transition ${
            activeTab === 'styles'
              ? 'border-amber-400 text-amber-300 bg-zinc-900/60'
              : 'border-transparent text-zinc-400 hover:text-zinc-200'
          }`}
        >
          <Palette className="w-3.5 h-3.5" />
          Caption Styles
        </button>
      </div>

      {activeTab === 'ai' ? (
        <div className="flex-1 flex flex-col min-h-0">
          {/* AI Models & 24h Auto-Discovery Bar */}
          <div className="px-2.5 py-2 border-b border-zinc-800/80 bg-zinc-900/40 space-y-1 shrink-0">
            <div className="flex items-center justify-between gap-1.5">
              <div className="text-[10px] font-medium text-zinc-400 truncate">
                AI Models · 24h Auto-Scan
              </div>
              <button
                type="button"
                onClick={() => void forceRefreshModels()}
                disabled={isRefreshingModels}
                title="Reload .env API keys, scan live Groq/Gemini models, and reset stuck AI state"
                className="flex items-center gap-1 px-2 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50 border border-zinc-700 text-[10px] font-medium text-zinc-200 transition-colors shrink-0"
              >
                <RefreshCw className={`w-2.5 h-2.5 ${isRefreshingModels ? 'animate-spin' : ''}`} />
                {isRefreshingModels ? 'Refreshing...' : 'Force Refresh / Restart'}
              </button>
            </div>
            <div className="text-[10px] text-zinc-300 truncate" title={modelStatus?.backupToolModels.join(' -> ')}>
              <span className="text-zinc-500">Tools:</span>{' '}
              <span className="text-emerald-400 font-mono">
                {modelStatus?.primaryToolModel ?? 'openai/gpt-oss-120b'}
              </span>
              {modelStatus && modelStatus.backupToolModels.length > 0 && (
                <span className="text-zinc-500">
                  {' '}
                  (+{modelStatus.backupToolModels.length} backup
                  {modelStatus.backupToolModels.length > 1 ? 's' : ''})
                </span>
              )}
            </div>
            <div
              className="text-[10px] text-zinc-300 truncate"
              title={modelStatus?.backupRouterModels.join(' -> ')}
            >
              <span className="text-zinc-500">Router:</span>{' '}
              <span className="text-sky-400 font-mono">
                {modelStatus?.primaryRouterModel ?? 'openai/gpt-oss-20b'}
              </span>
              {modelStatus && modelStatus.backupRouterModels.length > 0 && (
                <span className="text-zinc-500">
                  {' '}
                  (+{modelStatus.backupRouterModels.length} backup
                  {modelStatus.backupRouterModels.length > 1 ? 's' : ''})
                </span>
              )}
            </div>
            <div className="flex items-center justify-between gap-2 text-[9px] text-zinc-500">
              <span className="truncate">
                STT:{' '}
                <span className="text-zinc-300 font-mono">
                  {modelStatus?.primarySttModel ?? 'whisper-large-v3-turbo'}
                </span>
                {modelStatus?.backupSttModels[0] ? ` -> ${modelStatus.backupSttModels[0]}` : ''}
              </span>
              <span className="shrink-0">
                <span className={modelStatus?.groqConfigured ? 'text-emerald-400' : 'text-amber-400'}>
                  Groq {modelStatus?.groqConfigured ? 'OK' : 'Off'}
                </span>
                {' · '}
                <span className={modelStatus?.geminiConfigured ? 'text-emerald-400' : 'text-zinc-400'}>
                  Gemini {modelStatus?.geminiConfigured ? 'OK' : 'Fallback'}
                </span>
              </span>
            </div>
            {modelStatus?.lastRefreshedIso && (
              <div className="text-[9px] text-zinc-600 flex items-center justify-between">
                <span>
                  Scanned{' '}
                  {new Date(modelStatus.lastRefreshedIso).toLocaleTimeString([], {
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </span>
                <span>
                  Live: {modelStatus.availableGroqModels.length} Groq /{' '}
                  {modelStatus.availableGeminiModels.length} Gemini
                </span>
              </div>
            )}
          </div>

          {/* Quick Prompts */}
          <div className="p-2.5 border-b border-zinc-800/80 space-y-1.5 shrink-0">
            <div className="text-[10px] font-medium text-zinc-500 flex items-center gap-1">
              <Sparkles className="w-3 h-3 text-amber-400" />
              3-Signal AI Viral Shortcuts
            </div>
            <div className="flex flex-wrap gap-1">
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
                  className="text-left px-2 py-1 rounded bg-zinc-900 hover:bg-zinc-800 disabled:opacity-40 border border-zinc-800 text-[10px] text-zinc-300 line-clamp-1"
                >
                  {qp}
                </button>
              ))}
            </div>
          </div>

          {/* Chat Feed */}
          <div className="flex-1 overflow-y-auto p-3 space-y-3">
            {aiMessages.map((msg) => (
              <div
                key={msg.id}
                className={`rounded-lg p-2.5 text-xs space-y-1.5 ${
                  msg.role === 'user'
                    ? 'bg-amber-400/15 border border-amber-400/30 text-amber-100 ml-4'
                    : 'bg-zinc-900 border border-zinc-800 text-zinc-200 mr-2'
                }`}
              >
                <div className="flex items-center justify-between text-[10px] text-zinc-500">
                  <span className="font-semibold uppercase">
                    {msg.role === 'user' ? 'You' : 'ClipForge AI'}
                  </span>
                  {msg.routeUsed && (
                    <span className="font-mono text-[9px] text-emerald-400">
                      {msg.routeUsed}
                    </span>
                  )}
                </div>
                <p className="leading-relaxed whitespace-pre-wrap">{msg.content}</p>

                {/* AI Action Log Badges with One-Click Undo */}
                {msg.actions && msg.actions.length > 0 && (
                  <div className="pt-1.5 border-t border-zinc-800 flex flex-wrap items-center gap-1">
                    {msg.actions.map((act) => (
                      <div
                        key={act.id}
                        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-emerald-500/15 border border-emerald-500/30 text-[10px] text-emerald-300"
                      >
                        <Wand2 className="w-2.5 h-2.5" />
                        <span>{act.summary}</span>
                      </div>
                    ))}
                    {msg.id === lastActionMsgId && (
                      <button
                        type="button"
                        onClick={() => useEditorStore.temporal.getState().undo()}
                        className="ml-auto inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/30 text-[10px] text-amber-300"
                        title="Undo last AI edit (Ctrl+Z)"
                      >
                        <Undo2 className="w-2.5 h-2.5" />
                        Undo
                      </button>
                    )}
                  </div>
                )}
              </div>
            ))}
            {isAiThinking && (
              <div className="flex items-center gap-2 text-xs text-zinc-400 px-2">
                <Loader2 className="w-3.5 h-3.5 animate-spin text-amber-400" />
                Running 3-Signal AI / Groq tool-calling...
              </div>
            )}
          </div>

          {/* Recent AI Action Log Strip */}
          {aiActionLog.length > 0 && (
            <div className="px-3 py-1.5 border-t border-zinc-800 bg-zinc-900/40 flex items-center justify-between text-[10px] text-zinc-400 shrink-0">
              <span className="truncate">
                Last AI tool: <strong className="text-zinc-200">{aiActionLog[0].toolName}</strong>
              </span>
              <button
                type="button"
                onClick={() => useEditorStore.temporal.getState().undo()}
                className="text-amber-400 hover:underline flex items-center gap-0.5 shrink-0"
              >
                <Undo2 className="w-3 h-3" />
                Undo
              </button>
            </div>
          )}

          {/* Prompt Input + Voice Mic */}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              submitAiPrompt(promptInput);
            }}
            className="p-2.5 border-t border-zinc-800 flex items-center gap-1.5 shrink-0"
          >
            <button
              type="button"
              onClick={toggleVoiceRecording}
              title={isRecording ? 'Stop & send voice command' : 'Record voice command'}
              className={`p-2 rounded-lg border transition shrink-0 ${
                isRecording
                  ? 'bg-red-500/20 border-red-500 text-red-400 animate-pulse'
                  : 'bg-zinc-900 border-zinc-800 text-zinc-400 hover:text-zinc-200'
              }`}
            >
              {isRecording ? (
                <MicOff className="w-4 h-4" />
              ) : (
                <Mic className="w-4 h-4" />
              )}
            </button>
            <input
              type="text"
              value={promptInput}
              onChange={(e) => setPromptInput(e.target.value)}
              placeholder={
                !project.sourceVideo.videoUrl && pendingYoutubeUrl.trim()
                  ? 'Ask which part to download first (e.g. funny part)...'
                  : 'Ask AI to clip, trim, or style...'
              }
              className="flex-1 bg-zinc-900 border border-zinc-800 focus:border-amber-400/70 rounded-lg px-2.5 py-2 text-xs text-zinc-100 placeholder:text-zinc-500 outline-none"
            />
            <button
              type="submit"
              disabled={isAiThinking || isIngesting || !promptInput.trim()}
              className="p-2 rounded-lg bg-amber-400 hover:bg-amber-300 disabled:opacity-40 text-zinc-950 transition shrink-0"
            >
              <Send className="w-4 h-4" />
            </button>
          </form>
        </div>
      ) : (
        /* Caption Style Customizer */
        <div className="flex-1 overflow-y-auto p-3 space-y-4">
          <div className="space-y-1.5">
            <label className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
              One-Click Presets
            </label>
            <div className="grid grid-cols-2 gap-2">
              {presets.map((p) => {
                const active = style.preset === p.id;
                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => change_caption_style({ preset: p.id })}
                    className={`p-2.5 rounded-lg border text-left transition ${
                      active
                        ? 'border-amber-400 bg-amber-400/10'
                        : 'border-zinc-800 bg-zinc-900/60 hover:border-zinc-700'
                    }`}
                  >
                    <div className="text-xs font-semibold text-zinc-100">{p.label}</div>
                    <div className="text-[10px] text-zinc-400 mt-0.5">{p.desc}</div>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-zinc-300">Font Family</label>
            <select
              value={style.fontFamily}
              onChange={(e) => change_caption_style({ font_family: e.target.value })}
              className="w-full bg-zinc-900 border border-zinc-800 rounded-md px-2.5 py-1.5 text-xs text-zinc-100 outline-none"
            >
              <option value="Inter">Inter</option>
              <option value="Montserrat">Montserrat</option>
              <option value="Impact">Impact</option>
              <option value="Poppins">Poppins</option>
              <option value="Arial">Arial</option>
            </select>
          </div>

          <div className="space-y-1.5">
            <div className="flex justify-between text-xs">
              <span className="text-zinc-300 font-medium">Font Size</span>
              <span className="font-mono text-amber-400">{style.fontSize}px</span>
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

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <label className="text-xs text-zinc-300">Text Color</label>
              <div className="flex items-center gap-2 bg-zinc-900 border border-zinc-800 rounded p-1.5">
                <input
                  type="color"
                  value={style.textColor}
                  onChange={(e) =>
                    change_caption_style({ text_color: e.target.value })
                  }
                  className="w-6 h-6 rounded bg-transparent cursor-pointer"
                />
                <span className="text-[11px] font-mono text-zinc-400">
                  {style.textColor}
                </span>
              </div>
            </div>
            <div className="space-y-1">
              <label className="text-xs text-zinc-300">Highlight Color</label>
              <div className="flex items-center gap-2 bg-zinc-900 border border-zinc-800 rounded p-1.5">
                <input
                  type="color"
                  value={style.highlightColor}
                  onChange={(e) =>
                    change_caption_style({ highlight_color: e.target.value })
                  }
                  className="w-6 h-6 rounded bg-transparent cursor-pointer"
                />
                <span className="text-[11px] font-mono text-zinc-400">
                  {style.highlightColor}
                </span>
              </div>
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-zinc-300">Position</label>
            <div className="grid grid-cols-3 gap-1.5">
              {positions.map((pos) => (
                <button
                  key={pos}
                  type="button"
                  onClick={() => change_caption_style({ position: pos })}
                  className={`py-1.5 rounded text-xs capitalize border transition ${
                    style.position === pos
                      ? 'bg-amber-400 text-zinc-950 border-amber-400 font-semibold'
                      : 'bg-zinc-900 border-zinc-800 text-zinc-300 hover:border-zinc-700'
                  }`}
                >
                  {pos}
                </button>
              ))}
            </div>
          </div>

          <label className="flex items-center justify-between p-2.5 rounded-lg bg-zinc-900 border border-zinc-800 cursor-pointer">
            <span className="text-xs font-medium text-zinc-200">Bold Text</span>
            <input
              type="checkbox"
              checked={style.isBold}
              onChange={(e) => change_caption_style({ is_bold: e.target.checked })}
              className="accent-amber-400 w-4 h-4"
            />
          </label>
        </div>
      )}
    </aside>
  );
};
