import React, { useEffect, useRef, useState } from 'react';
import {
  Download,
  Film,
  FolderOpen,
  Link2,
  Loader2,
  Redo2,
  Save,
  Sparkles,
  Undo2,
  Upload,
  X,
} from 'lucide-react';
import { useStore } from 'zustand';
import { API_BASE, useEditorStore } from '../store/useEditorStore';
import type { AspectRatio, ProjectState } from '../types/editor';

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

  const pastStates = useStore(useEditorStore.temporal, (s) => s.pastStates);
  const futureStates = useStore(useEditorStore.temporal, (s) => s.futureStates);

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
    const resp = await fetch(`${API_BASE}/api/subtitles`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_state: project, format }),
    });
    if (!resp.ok) return;
    const text = await resp.text();
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${project.projectId || 'captions'}.${format}`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const ratios: Array<{ id: AspectRatio; label: string }> = [
    { id: '16:9', label: '16:9 Clip' },
    { id: '9:16', label: '9:16 Short' },
    { id: '1:1', label: '1:1 Square' },
  ];

  return (
    <>
      <header className="h-14 border-b border-zinc-800 bg-zinc-950 px-3 flex items-center justify-between gap-3 shrink-0">
        {/* Brand & Project Name */}
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-amber-400 to-orange-500 flex items-center justify-center text-zinc-950 font-bold shrink-0">
            <Film className="w-4 h-4" />
          </div>
          <input
            type="text"
            value={project.projectName}
            onChange={(e) => setProjectName(e.target.value)}
            aria-label="Project name"
            className="bg-transparent border border-transparent hover:border-zinc-800 focus:border-amber-400/60 focus:bg-zinc-900 rounded px-2 py-1 text-sm font-semibold text-zinc-100 w-44 truncate outline-none"
          />
        </div>

        {/* YouTube URL Ingest Bar */}
        <form onSubmit={handleIngest} className="flex items-center gap-1.5 flex-1 max-w-2xl">
          <div className="relative flex-1 min-w-0">
            <Link2 className="w-3.5 h-3.5 text-zinc-500 absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={pendingYoutubeUrl}
              onChange={(e) => setPendingYoutubeUrl(e.target.value)}
              placeholder="Paste YouTube URL (max 1080p)..."
              className="w-full bg-zinc-900 border border-zinc-800 focus:border-amber-400/70 rounded-md pl-8 pr-3 py-1.5 text-xs text-zinc-100 placeholder:text-zinc-500 outline-none"
            />
          </div>
          <input
            type="text"
            value={ingestPrompt}
            onChange={(e) => setIngestPrompt(e.target.value)}
            placeholder="Only download part (e.g. funny, serious)..."
            title="Optional: AI finds this part from audio first and downloads ONLY matching clip(s) to save storage"
            className="w-48 lg:w-60 bg-zinc-900 border border-zinc-800 focus:border-emerald-400/70 rounded-md px-2.5 py-1.5 text-xs text-zinc-100 placeholder:text-zinc-500 outline-none shrink-0"
          />
          <button
            type="submit"
            disabled={isIngesting || !pendingYoutubeUrl.trim()}
            className="px-3 py-1.5 rounded-md bg-amber-400 hover:bg-amber-300 disabled:opacity-40 text-zinc-950 font-semibold text-xs flex items-center gap-1.5 transition shrink-0"
          >
            {isIngesting ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                Ingesting...
              </>
            ) : ingestPrompt.trim() ? (
              'Clip & Load'
            ) : (
              'Load URL'
            )}
          </button>
          <button
            type="button"
            onClick={handleLoadSample}
            disabled={isIngesting}
            title="Generate a local 45s demo video with word timestamps & heatmap"
            className="px-2.5 py-1.5 rounded-md bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 text-zinc-200 text-xs font-medium flex items-center gap-1 transition shrink-0"
          >
            <Sparkles className="w-3.5 h-3.5 text-amber-400" />
            Demo
          </button>
        </form>

        {/* Controls: Undo/Redo, Save/Load, Aspect Ratio, Export */}
        <div className="flex items-center gap-2 shrink-0">
          {/* Undo / Redo */}
          <div className="flex items-center bg-zinc-900 border border-zinc-800 rounded-md p-0.5">
            <button
              type="button"
              onClick={() => useEditorStore.temporal.getState().undo()}
              disabled={pastStates.length === 0}
              title="Undo (Ctrl+Z)"
              className="p-1.5 rounded hover:bg-zinc-800 disabled:opacity-30 text-zinc-300"
            >
              <Undo2 className="w-3.5 h-3.5" />
            </button>
            <button
              type="button"
              onClick={() => useEditorStore.temporal.getState().redo()}
              disabled={futureStates.length === 0}
              title="Redo (Ctrl+Y)"
              className="p-1.5 rounded hover:bg-zinc-800 disabled:opacity-30 text-zinc-300"
            >
              <Redo2 className="w-3.5 h-3.5" />
            </button>
          </div>

          {/* Save / Load */}
          <button
            type="button"
            onClick={handleSaveProject}
            title="Save Project JSON"
            className="px-2.5 py-1.5 rounded-md bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-zinc-200 text-xs flex items-center gap-1"
          >
            <Save className="w-3.5 h-3.5" />
            Save
          </button>
          <button
            type="button"
            onClick={handleOpenLoadModal}
            title="Load Project JSON"
            className="px-2.5 py-1.5 rounded-md bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-zinc-200 text-xs flex items-center gap-1"
          >
            <FolderOpen className="w-3.5 h-3.5" />
            Load
          </button>

          {/* Aspect Ratio Switcher */}
          <div className="flex items-center bg-zinc-900 border border-zinc-800 rounded-md p-0.5">
            {ratios.map((r) => {
              const active = project.workspaceState.targetAspectRatio === r.id;
              return (
                <button
                  key={r.id}
                  type="button"
                  onClick={() => change_video_format(r.id)}
                  className={`px-2 py-1 rounded text-[11px] font-medium transition ${
                    active
                      ? 'bg-amber-400 text-zinc-950 font-semibold'
                      : 'text-zinc-400 hover:text-zinc-200'
                  }`}
                >
                  {r.label}
                </button>
              );
            })}
          </div>

          {/* Export Button */}
          <button
            type="button"
            onClick={() => setShowExportModal(true)}
            disabled={project.tracks.clips.length === 0}
            className="px-3 py-1.5 rounded-md bg-emerald-500 hover:bg-emerald-400 disabled:opacity-40 text-zinc-950 font-semibold text-xs flex items-center gap-1.5 transition"
          >
            <Download className="w-3.5 h-3.5" />
            Export
          </button>
        </div>
      </header>

      {/* Load Project Modal */}
      {showLoadModal && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl w-full max-w-md p-4 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-zinc-100">Load Saved Project</h2>
              <button
                type="button"
                onClick={() => setShowLoadModal(false)}
                className="text-zinc-400 hover:text-zinc-200"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="space-y-2 max-h-60 overflow-y-auto">
              {savedProjects.length === 0 ? (
                <p className="text-xs text-zinc-500 py-4 text-center">
                  No projects saved in workspace_data/projects/ yet.
                </p>
              ) : (
                savedProjects.map((sp) => (
                  <button
                    key={sp.projectId}
                    type="button"
                    onClick={() => handleLoadFromBackend(sp.projectId)}
                    className="w-full text-left p-2.5 rounded-lg bg-zinc-950 hover:bg-zinc-800 border border-zinc-800 flex items-center justify-between"
                  >
                    <div className="min-w-0">
                      <div className="text-xs font-medium text-zinc-100 truncate">
                        {sp.projectName}
                      </div>
                      <div className="text-[11px] text-zinc-500 truncate">
                        {sp.projectId} • {sp.duration.toFixed(1)}s
                      </div>
                    </div>
                  </button>
                ))
              )}
            </div>

            <div className="pt-2 border-t border-zinc-800 flex items-center justify-between">
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
                className="px-3 py-1.5 rounded-md bg-zinc-800 hover:bg-zinc-700 text-xs text-zinc-200 flex items-center gap-1.5"
              >
                <Upload className="w-3.5 h-3.5" />
                Import .json File
              </button>
              <button
                type="button"
                onClick={() => setShowLoadModal(false)}
                className="px-3 py-1.5 rounded-md bg-zinc-800 text-xs text-zinc-300"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Export Modal */}
      {showExportModal && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl w-full max-w-lg p-5 space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold text-zinc-100">
                  Export Video & Subtitles
                </h2>
                <p className="text-[11px] text-zinc-400">
                  8GB RAM Low-Memory Sequential FFmpeg Renderer ({project.workspaceState.targetAspectRatio})
                </p>
              </div>
              <button
                type="button"
                onClick={() => setShowExportModal(false)}
                className="text-zinc-400 hover:text-zinc-200"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setExportMode('combined')}
                className={`p-3 rounded-lg border text-left transition ${
                  exportMode === 'combined'
                    ? 'border-emerald-400 bg-emerald-500/10'
                    : 'border-zinc-800 bg-zinc-950 hover:border-zinc-700'
                }`}
              >
                <div className="text-xs font-semibold text-zinc-100">
                  Combined Timeline MP4
                </div>
                <div className="text-[11px] text-zinc-400 mt-0.5">
                  Concatenates {project.tracks.clips.length} clip(s) + burns styled captions (.ass)
                </div>
              </button>
              <button
                type="button"
                onClick={() => setExportMode('individual')}
                className={`p-3 rounded-lg border text-left transition ${
                  exportMode === 'individual'
                    ? 'border-emerald-400 bg-emerald-500/10'
                    : 'border-zinc-800 bg-zinc-950 hover:border-zinc-700'
                }`}
              >
                <div className="text-xs font-semibold text-zinc-100">
                  Individual Clips MP4
                </div>
                <div className="text-[11px] text-zinc-400 mt-0.5">
                  Exports each timeline clip as a separate MP4
                </div>
              </button>
            </div>

            <div className="flex items-center gap-2 pt-1">
              <button
                type="button"
                onClick={() => handleDownloadSubtitles('srt')}
                className="px-3 py-1.5 rounded-md bg-zinc-800 hover:bg-zinc-700 text-xs text-zinc-200"
              >
                Download .SRT
              </button>
              <button
                type="button"
                onClick={() => handleDownloadSubtitles('vtt')}
                className="px-3 py-1.5 rounded-md bg-zinc-800 hover:bg-zinc-700 text-xs text-zinc-200"
              >
                Download .VTT
              </button>
              <div className="flex-1" />
              <button
                type="button"
                onClick={handleStartExport}
                disabled={isExporting}
                className="px-4 py-2 rounded-md bg-emerald-500 hover:bg-emerald-400 disabled:opacity-50 text-zinc-950 font-semibold text-xs flex items-center gap-1.5"
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
            <div className="space-y-1.5 bg-zinc-950 border border-zinc-800 rounded-lg p-3">
              <div className="flex items-center justify-between text-xs">
                <span className="text-zinc-300 truncate">{exportStatus}</span>
                <span className="font-mono text-emerald-400">{exportProgress}%</span>
              </div>
              <div className="w-full h-2 bg-zinc-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-emerald-400 transition-all duration-200"
                  style={{ width: `${exportProgress}%` }}
                />
              </div>
            </div>

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
                    className="flex items-center justify-between p-2.5 rounded-lg bg-emerald-500/10 border border-emerald-500/30 hover:bg-emerald-500/20 text-xs text-emerald-300"
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
          </div>
        </div>
      )}
    </>
  );
};
