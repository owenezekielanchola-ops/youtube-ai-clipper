import { Info, X } from 'lucide-react';
import { BottomTimeline } from './components/BottomTimeline';
import { CenterVideoPlayer } from './components/CenterVideoPlayer';
import { LeftMediaAndCaptions } from './components/LeftMediaAndCaptions';
import { RightAiAndStyles } from './components/RightAiAndStyles';
import { TopNavbar } from './components/TopNavbar';
import { useEditorStore } from './store/useEditorStore';

export function App() {
  const statusBanner = useEditorStore((s) => s.statusBanner);
  const setStatusBanner = useEditorStore((s) => s.setStatusBanner);

  return (
    <div className="h-screen w-screen bg-[#0f1218] text-slate-100 flex flex-col overflow-hidden">
      <TopNavbar />

      {statusBanner && (
        <div
          role="status"
          className="bg-[#1b2230] border-b border-amber-400/25 px-4 py-2 flex items-center justify-between gap-3 text-xs text-amber-100 shrink-0"
        >
          <div className="flex items-center gap-2 min-w-0">
            <Info className="w-3.5 h-3.5 text-amber-300 shrink-0" />
            <span className="break-words" title={statusBanner}>
              {statusBanner}
            </span>
          </div>
          <button
            type="button"
            onClick={() => setStatusBanner(null)}
            aria-label="Dismiss notification"
            className="p-1 rounded-md text-slate-400 hover:text-slate-100 hover:bg-white/5 transition shrink-0"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      <div className="flex-1 flex min-h-0 overflow-hidden">
        <LeftMediaAndCaptions />
        <CenterVideoPlayer />
        <RightAiAndStyles />
      </div>

      <BottomTimeline />
    </div>
  );
}

export default App;
