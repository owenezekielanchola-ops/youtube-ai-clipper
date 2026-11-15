import { X } from 'lucide-react';
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
    <div className="h-screen w-screen bg-zinc-950 text-zinc-100 flex flex-col overflow-hidden">
      <TopNavbar />

      {statusBanner && (
        <div className="bg-amber-500/15 border-b border-amber-500/30 px-4 py-1.5 flex items-center justify-between text-xs text-amber-200 shrink-0">
          <span>{statusBanner}</span>
          <button
            type="button"
            onClick={() => setStatusBanner(null)}
            className="text-amber-300 hover:text-white"
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
