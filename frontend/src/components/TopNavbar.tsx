import React, { useEffect, useRef, useState } from 'react';
import {
  Check,
  Download,
  Film,
  FolderOpen,
  Link2,
  Loader2,
  Plus,
  Redo2,
  Save,
  ShieldCheck,
  Sparkles,
  Undo2,
  Upload,
  X,
} from 'lucide-react';
import { useStore } from 'zustand';
import { getCampaignById, LACY_CAMPAIGN } from '../config/campaigns';
import { API_BASE, useEditorStore } from '../store/useEditorStore';
import type { AspectRatio, ProjectState } from '../types/editor';
import { CampaignUploadGuide } from './CampaignUploadGuide';

interface SavedProjectMeta {
  projectId: string;
  projectName: string;
  youtubeUrl: string;
  duration: number;
}

interface ExportedFile {
  filename: string;
  title: string;
  url: string;
}

export const TopNavbar: React.FC = () => {
  const project = useEditorStore((s) => s.project);
  const isIngesting = useEditorStore((s) => s.isIngesting);
  const pendingYoutubeUrl = useEditorStore((s) => s.pendingYoutubeUrl);
  const setPendingYoutubeUrl = useEditorStore((s) => s.setPendingYoutubeUrl);
  const ingestPrompt = useEditorStore((s) => s.ingestPrompt);
  const setIngestPrompt = useEditorStore((s) => s.setIngestPrompt);
  const ingestYoutubeUrl = useEditorStore((s) => s.ingestYoutubeUrl);
  const setProjectName = useEditorStore((s) => s.setProjectName);
  const change_video_format = useEditorStore((s) => s.change_video_format);
  const loadProjectState = useEditorStore((s) => s.loadProjectState);
  const setStatusBanner = useEditorStore((s) => s.setStatusBanner);
  const setActiveCampaign = useEditorStore((s) => s.setActiveCampaign);

  const activeCampaign = getCampaignById(project.workspaceState.activeCampaignId);
  const isLacyActive = activeCampaign?.id === LACY_CAMPAIGN.id;
  const hasVideo = Boolean(project.sourceVideo.videoUrl);

  const pastStates = useStore(useEditorStore.temporal, (s) => s.pastStates);
  const futureStates = useStore(useEditorStore.temporal, (s) => s.futureStates);

  const [showImportBar, setShowImportBar] = useState(true);
  const prevHasVideoRef = useRef(hasVideo);
  const [showLoadModal, setShowLoadModal] = useState(false);
  const [savedProjects, setSavedProjects] = useState<SavedProjectMeta[]>([]);
  const [showExportModal, setShowExportModal] = useState(false);
  const [exportMode, setExportMode] = useState<'combined' | 'individual'>('combined');
  const [isExporting, setIsExporting] = useState(false);
  const [exportProgress, setExportProgress] = useState(0);
  const [exportStatus, setExportStatus] = useState('Ready to export');
  const [exportedFiles, setExportedFiles] = useState<ExportedFile[]>([]);

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!prevHasVideoRef.current && hasVideo) {
      setShowImportBar(false);
    }
    prevHasVideoRef.current = hasVideo;
  }, [hasVideo]);

  useEffect(() => {
    setExportProgress(0);
    setExportStatus('Ready to export');
    setExportedFiles([]);
  }, [project.projectId]);

  useEffect(() => {
    if (!showLoadModal && !showExportModal) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setShowLoadModal(false);
        setShowExportModal(false);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [showLoadModal, showExportModal]);

  useEffect(() => {
    if (!showExportModal) return;
    const ws = new WebSocket('ws://localhost:8000/ws/progress');
    wsRef.current = ws;
    ws.onmessage = (evt) => {
      try {
        const data = JSON.parse(evt.data);
        if (
          data.projectId &&
          data.projectId !== useEditorStore.getState().project.projectId
        ) {
          return;
        }
        if (typeof data.progress === 'number') setExportProgress(data.progress);
        if (typeof data.status === 'string') setExportStatus(data.status);
        if (Array.isArray(data.files)) setExportedFiles(data.files);
      } catch {
        // Ignore malformed frame
      }
    };
    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [showExportModal]);

  const handleIngest = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!pendingYoutubeUrl.trim() || isIngesting) return;
    await ingestYoutubeUrl(pendingYoutubeUrl.trim(), ingestPrompt.trim());
  };

  const handleLoadSample = async () => {
    if (isIngesting) return;
    setPendingYoutubeUrl('demo://sample');
    await ingestYoutubeUrl('demo://sample');
  };

  const handleSaveProject = async () => {
    try {
      await fetch(`${API_BASE}/api/projects/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_state: project }),
      });
    } catch {
      // Allow local JSON download even if backend isn't reachable
    }

    const blob = new Blob([JSON.stringify(project, null, 2)], {
      type: 'application/json',
    });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${project.projectId || 'project'}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
    setStatusBanner('Project saved to workspace_data/projects/ and downloaded JSON.');
  };

  const handleOpenLoadModal = async () => {
    setShowLoadModal(true);
    try {
      const resp = await fetch(`${API_BASE}/api/projects`);
      if (resp.ok) {
        const data = await resp.json();
        setSavedProjects(data.projects || []);
      }
    } catch {
      setSavedProjects([]);
    }
  };

  const handleLoadFromBackend = async (projectId: string) => {
    const resp = await fetch(`${API_BASE}/api/projects/${projectId}`);
    if (resp.ok) {
      const proj: ProjectState = await resp.json();
      let loadedTranscript = null;
      try {
        const tResp = await fetch(`${API_BASE}/api/projects/${projectId}/transcript`);
        if (tResp.ok) {
          const tData = await tResp.json();
          loadedTranscript = tData?.transcript ?? null;
        }
      } catch {
        // Ignore missing transcript cache
      }
      loadProjectState(proj, loadedTranscript);
      useEditorStore.temporal.getState().clear();
      setShowLoadModal(false);
    }
  };

  const handleUploadJson = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result)) as ProjectState;
        loadProjectState(parsed);
        useEditorStore.temporal.getState().clear();
        setShowLoadModal(false);
      } catch {
        setStatusBanner('Invalid project JSON file.');
      }
    };
    reader.readAsText(file);
  };

  const handleStartExport = async () => {
    setIsExporting(true);
    setExportProgress(2);
    setExportStatus('Starting FFmpeg low-RAM render...');
    setExportedFiles([]);
    try {
      const resp = await fetch(`${API_BASE}/api/export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_state: project,
          export_mode: exportMode,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || 'Export failed');
      }
      const data = await resp.json();
      setExportProgress(100);
      setExportStatus('Export complete!');
      setExportedFiles(data.files || []);
    } catch (err) {
      setExportStatus(
        `Error: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setIsExporting(false);
    }
  };

  const handleDownloadSubtitles = async (format: 'srt' | 'vtt') => {
    try {
      const resp = await fetch(`${API_BASE}/api/subtitles`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_state: project, format }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || `Failed to export .${format.toUpperCase()}`);
      }
      const text = await resp.text();
      const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${project.projectId || 'captions'}.${format}`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (err) {
      setStatusBanner(
        `Subtitle export error: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  };

  const ratios: Array<{ id: AspectRatio; label: string }> = [
    { id: '16:9', label: '16:9 Clip' },
    { id: '9:16', label: '9:16 Short' },
    { id: '1:1', label: '1:1 Square' },
  ];

  return (
    <>
      <div className="shrink-0">
        {/* Row 1: Calm Main Studio Header */}
        <header className="h-12 border-b border-[#272f40] bg-[#151922] px-3.5 flex items-center justify-between gap-3">
          {/* Left: Brand, Project Name, & Import Toggle */}
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-amber-400 to-orange-500 flex items-center justify-center text-slate-950 font-bold shrink-0 shadow-sm">
              <Film className="w-4 h-4" />
            </div>
            <input
              type="text"
              value={project.projectName}
              onChange={(e) => setProjectName(e.target.value)}
              aria-label="Project name"
              className="bg-transparent border border-transparent hover:border-[#272f40] focus:border-amber-400/60 focus:bg-[#1c212d] rounded-lg px-2.5 py-1 text-sm font-semibold text-slate-100 w-44 lg:w-52 truncate outline-none transition"
            />
            {hasVideo && (
              <button
                type="button"
                onClick={() => setShowImportBar((v) => !v)}
                aria-expanded={showImportBar}
                title="Load or clip another YouTube video"
                className={`px-2.5 py-1 rounded-lg border text-xs font-medium flex items-center gap-1.5 transition shrink-0 ${
                  showImportBar
                    ? 'bg-amber-400/15 border-amber-400/40 text-amber-200'
                    : 'bg-[#1c212d] hover:bg-[#232a39] border-[#272f40] text-slate-200'
                }`}
              >
                <Plus className="w-3.5 h-3.5 text-amber-300" />
                New Video
              </button>
            )}
          </div>

          {/* Center: Aspect Ratio & Campaign Toggle */}
          <div className="flex items-center gap-2.5 shrink-0">
            <div className="flex items-center bg-[#0f1218] border border-[#272f40] rounded-lg p-0.5">
              {ratios.map((r) => {
                const active = project.workspaceState.targetAspectRatio === r.id;
                return (
                  <button
                    key={r.id}
                    type="button"
                    onClick={() => change_video_format(r.id)}
                    className={`px-2.5 py-1 rounded-md text-xs font-medium transition ${
                      active
                        ? 'bg-amber-400 text-slate-950 font-semibold shadow-sm'
                        : 'text-slate-400 hover:text-slate-100'
                    }`}
                  >
                    {r.label}
                  </button>
                );
              })}
            </div>

            <button
              type="button"
              onClick={() => setActiveCampaign(isLacyActive ? null : LACY_CAMPAIGN.id)}
              aria-pressed={isLacyActive}
              title={`Toggle Lacy Campaign rules:\n• ${LACY_CAMPAIGN.editingRules.join('\n• ')}`}
              className={`px-3 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1.5 transition shrink-0 ${
                isLacyActive
                  ? 'bg-violet-500/20 border-violet-400/70 text-violet-100 shadow-sm'
                  : 'bg-[#1c212d] hover:bg-[#232a39] border-[#272f40] text-slate-300 hover:text-slate-100'
              }`}
            >
              {isLacyActive ? (
                <Check className="w-3.5 h-3.5 text-violet-300" />
              ) : (
                <ShieldCheck className="w-3.5 h-3.5 text-violet-300" />
              )}
              <span>Lacy Campaign</span>
            </button>
          </div>

          {/* Right: History, Save/Load, Export */}
          <div className="flex items-center gap-2 shrink-0">
            <div className="flex items-center bg-[#1c212d] border border-[#272f40] rounded-lg p-0.5">
              <button
                type="button"
                onClick={() => useEditorStore.temporal.getState().undo()}
                disabled={pastStates.length === 0}
                title="Undo (Ctrl+Z)"
                aria-label="Undo"
                className="p-1.5 rounded-md hover:bg-white/5 disabled:opacity-30 text-slate-200 transition"
              >
                <Undo2 className="w-3.5 h-3.5" />
              </button>
              <button
                type="button"
                onClick={() => useEditorStore.temporal.getState().redo()}
                disabled={futureStates.length === 0}
                title="Redo (Ctrl+Y)"
                aria-label="Redo"
                className="p-1.5 rounded-md hover:bg-white/5 disabled:opacity-30 text-slate-200 transition"
              >
                <Redo2 className="w-3.5 h-3.5" />
              </button>
            </div>

            <button
              type="button"
              onClick={handleSaveProject}
              title="Save Project JSON"
              className="px-2.5 py-1.5 rounded-lg bg-[#1c212d] hover:bg-[#232a39] border border-[#272f40] text-slate-200 text-xs font-medium flex items-center gap-1.5 transition"
            >
              <Save className="w-3.5 h-3.5 text-slate-300" />
              Save
            </button>
            <button
              type="button"
              onClick={handleOpenLoadModal}
              title="Load Project JSON"
              className="px-2.5 py-1.5 rounded-lg bg-[#1c212d] hover:bg-[#232a39] border border-[#272f40] text-slate-200 text-xs font-medium flex items-center gap-1.5 transition"
            >
              <FolderOpen className="w-3.5 h-3.5 text-slate-300" />
              Load
            </button>

            <button
              type="button"
              onClick={() => setShowExportModal(true)}
              disabled={project.tracks.clips.length === 0}
              className="px-3.5 py-1.5 rounded-lg bg-emerald-500 hover:bg-emerald-400 disabled:opacity-40 text-slate-950 font-semibold text-xs flex items-center gap-1.5 transition shadow-sm"
            >
              <Download className="w-3.5 h-3.5" />
              Export
            </button>
          </div>
        </header>

        {/* Row 2: Spacious YouTube Import Bar */}
        {(!hasVideo || showImportBar) && (
          <div className="border-b border-[#272f40] bg-[#12161f] px-4 py-2">
            <form
              onSubmit={handleIngest}
              className="max-w-5xl mx-auto flex items-center gap-2"
            >
              <div className="relative flex-1 min-w-0">
                <Link2 className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  value={pendingYoutubeUrl}
                  onChange={(e) => setPendingYoutubeUrl(e.target.value)}
                  placeholder="Paste YouTube video link here..."
                  className="w-full bg-[#1c212d] border border-[#272f40] focus:border-amber-400/70 rounded-lg pl-8 pr-3 py-1.5 text-xs text-slate-100 placeholder:text-slate-400 outline-none transition"
                />
              </div>
              <input
                type="text"
                value={ingestPrompt}
                onChange={(e) => setIngestPrompt(e.target.value)}
                placeholder="Clip prompt (e.g. 'find the interesting part' — needed for 1h–24h VODs)"
                title="AI scans captions/heatmap first and downloads ONLY the matching clip(s) — works for 12–24h ended streams without downloading the full video"
                className="w-64 lg:w-80 bg-[#1c212d] border border-[#272f40] focus:border-emerald-400/70 rounded-lg px-3 py-1.5 text-xs text-slate-100 placeholder:text-slate-400 outline-none shrink-0 transition"
              />
              <button
                type="submit"
                disabled={isIngesting || !pendingYoutubeUrl.trim()}
                className="px-3.5 py-1.5 rounded-lg bg-amber-400 hover:bg-amber-300 disabled:opacity-40 text-slate-950 font-semibold text-xs flex items-center gap-1.5 transition shrink-0"
              >
                {isIngesting ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    Loading...
                  </>
                ) : ingestPrompt.trim() ? (
                  'Clip & Load'
                ) : (
                  'Load Video'
                )}
              </button>
              <button
                type="button"
                onClick={handleLoadSample}
                disabled={isIngesting}
                title="Generate a local 45s demo video with captions & heatmap"
                className="px-3 py-1.5 rounded-lg bg-[#1c212d] hover:bg-[#232a39] disabled:opacity-40 border border-[#272f40] text-slate-200 text-xs font-medium flex items-center gap-1.5 transition shrink-0"
              >
                <Sparkles className="w-3.5 h-3.5 text-amber-300" />
                Try Demo
              </button>
              {hasVideo && (
                <button
                  type="button"
                  onClick={() => setShowImportBar(false)}
                  title="Hide import bar"
                  aria-label="Hide import bar"
                  className="p-1.5 rounded-lg text-slate-400 hover:text-slate-100 hover:bg-white/5 transition shrink-0"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </form>
          </div>
        )}
      </div>

      {/* Load Project Modal */}
      {showLoadModal && (
        <div
          className="fixed inset-0 z-50 bg-black/65 backdrop-blur-sm flex items-center justify-center p-4"
          onClick={() => setShowLoadModal(false)}
        >
          <div
            className="bg-[#161b26] border border-[#2b3447] rounded-2xl shadow-2xl w-full max-w-md p-5 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-100">Load Saved Project</h2>
              <button
                type="button"
                onClick={() => setShowLoadModal(false)}
                aria-label="Close modal"
                className="p-1 rounded-lg text-slate-400 hover:text-slate-100 hover:bg-white/5"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="space-y-2 max-h-60 overflow-y-auto pr-1">
              {savedProjects.length === 0 ? (
                <p className="text-xs text-slate-400 py-6 text-center">
                  No saved projects found in <span className="font-mono">workspace_data/projects/</span> yet.
                </p>
              ) : (
                savedProjects.map((sp) => (
                  <button
                    key={sp.projectId}
                    type="button"
                    onClick={() => handleLoadFromBackend(sp.projectId)}
                    className="w-full text-left p-3 rounded-xl bg-[#1c212d] hover:bg-[#232a39] border border-[#272f40] flex items-center justify-between transition"
                  >
                    <div className="min-w-0">
                      <div className="text-xs font-semibold text-slate-100 truncate">
                        {sp.projectName}
                      </div>
                      <div className="text-xs text-slate-400 truncate mt-0.5 tabular-nums">
                        {sp.projectId} · {sp.duration.toFixed(1)}s
                      </div>
                    </div>
                  </button>
                ))
              )}
            </div>

            <div className="pt-3 border-t border-[#272f40] flex items-center justify-between">
              <input
                ref={fileInputRef}
                type="file"
                accept=".json"
                onChange={handleUploadJson}
                className="hidden"
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="px-3 py-1.5 rounded-lg bg-[#1c212d] hover:bg-[#232a39] border border-[#272f40] text-xs font-medium text-slate-200 flex items-center gap-1.5 transition"
              >
                <Upload className="w-3.5 h-3.5" />
                Import .json File
              </button>
              <button
                type="button"
                onClick={() => setShowLoadModal(false)}
                className="px-3 py-1.5 rounded-lg bg-[#1c212d] hover:bg-[#232a39] border border-[#272f40] text-xs text-slate-300 transition"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Export Modal */}
      {showExportModal && (
        <div
          className="fixed inset-0 z-50 bg-black/65 backdrop-blur-sm flex items-center justify-center p-4"
          onClick={() => setShowExportModal(false)}
        >
          <div
            className="bg-[#161b26] border border-[#2b3447] rounded-2xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto p-5 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold text-slate-100">
                  Export Video &amp; Subtitles
                </h2>
                <p className="text-xs text-slate-400 mt-0.5">
                  Renders speed, crop, zooms, drawings, custom SFX &amp; captions ({project.workspaceState.targetAspectRatio})
                </p>
              </div>
              <button
                type="button"
                onClick={() => setShowExportModal(false)}
                aria-label="Close export modal"
                className="p-1 rounded-lg text-slate-400 hover:text-slate-100 hover:bg-white/5"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="grid grid-cols-2 gap-2.5">
              <button
                type="button"
                onClick={() => setExportMode('combined')}
                className={`p-3 rounded-xl border text-left transition ${
                  exportMode === 'combined'
                    ? 'border-emerald-400/70 bg-emerald-500/10'
                    : 'border-[#272f40] bg-[#1c212d] hover:bg-[#232a39]'
                }`}
              >
                <div className="text-xs font-semibold text-slate-100">
                  Combined Timeline MP4
                </div>
                <div className="text-xs text-slate-400 mt-1">
                  Joins {project.tracks.clips.length} clip(s) with captions &amp; all effects
                </div>
              </button>
              <button
                type="button"
                onClick={() => setExportMode('individual')}
                className={`p-3 rounded-xl border text-left transition ${
                  exportMode === 'individual'
                    ? 'border-emerald-400/70 bg-emerald-500/10'
                    : 'border-[#272f40] bg-[#1c212d] hover:bg-[#232a39]'
                }`}
              >
                <div className="text-xs font-semibold text-slate-100">
                  Individual Clips MP4
                </div>
                <div className="text-xs text-slate-400 mt-1">
                  Exports each timeline clip as its own MP4 file
                </div>
              </button>
            </div>

            <div className="flex items-center gap-2 pt-1">
              <button
                type="button"
                onClick={() => handleDownloadSubtitles('srt')}
                className="px-3 py-1.5 rounded-lg bg-[#1c212d] hover:bg-[#232a39] border border-[#272f40] text-xs text-slate-200 transition"
              >
                Download .SRT
              </button>
              <button
                type="button"
                onClick={() => handleDownloadSubtitles('vtt')}
                className="px-3 py-1.5 rounded-lg bg-[#1c212d] hover:bg-[#232a39] border border-[#272f40] text-xs text-slate-200 transition"
              >
                Download .VTT
              </button>
              <div className="flex-1" />
              <button
                type="button"
                onClick={handleStartExport}
                disabled={isExporting}
                className="px-4 py-2 rounded-lg bg-emerald-500 hover:bg-emerald-400 disabled:opacity-50 text-slate-950 font-semibold text-xs flex items-center gap-1.5 transition"
              >
                {isExporting ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    Rendering...
                  </>
                ) : (
                  <>
                    <Download className="w-3.5 h-3.5" />
                    Render MP4
                  </>
                )}
              </button>
            </div>

            {/* Progress Bar */}
            <div className="space-y-2 bg-[#12161f] border border-[#272f40] rounded-xl p-3">
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-300 truncate">{exportStatus}</span>
                <span className="font-mono tabular-nums text-emerald-400">{exportProgress}%</span>
              </div>
              <div className="w-full h-2 bg-[#1c212d] rounded-full overflow-hidden">
                <div
                  className="h-full bg-emerald-400 transition-all duration-200"
                  style={{ width: `${exportProgress}%` }}
                />
              </div>
            </div>

            {/* Pre-export Campaign Notice */}
            {activeCampaign && exportedFiles.length === 0 && (
              <div className="rounded-xl bg-violet-500/10 border border-violet-400/25 px-3.5 py-2.5 text-xs text-violet-200 flex items-center gap-2.5 leading-relaxed">
                <ShieldCheck className="w-4 h-4 text-violet-300 shrink-0" />
                <span>
                  <strong>{activeCampaign.label}</strong> active: &ldquo;{activeCampaign.overlayText}&rdquo; overlay will be burned in, and manual upload instructions + copyable #LACY caption will appear after export.
                </span>
              </div>
            )}

            {/* Download Results */}
            {exportedFiles.length > 0 && (
              <div className="space-y-2 max-h-40 overflow-y-auto">
                {exportedFiles.map((f) => (
                  <a
                    key={f.filename}
                    href={f.url}
                    download={f.filename}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center justify-between p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/30 hover:bg-emerald-500/20 text-xs text-emerald-300 transition"
                  >
                    <span className="truncate font-medium">{f.filename}</span>
                    <span className="flex items-center gap-1 font-semibold shrink-0">
                      <Download className="w-3.5 h-3.5" />
                      Download
                    </span>
                  </a>
                ))}
              </div>
            )}

            {/* Post-Export Campaign Upload Instructions & Ready-to-Copy Caption */}
            {activeCampaign && exportedFiles.length > 0 && (
              <CampaignUploadGuide
                campaign={activeCampaign}
                projectName={project.projectName}
                exportedFiles={exportedFiles}
              />
            )}
          </div>
        </div>
      )}
    </>
  );
};
