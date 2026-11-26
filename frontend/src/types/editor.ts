export type AspectRatio = '16:9' | '9:16' | '1:1';
export type CaptionPreset = 'karaoke' | 'bold_social' | 'yellow_highlight' | 'minimal';
export type CaptionPosition = 'top' | 'middle' | 'bottom';
export type CaptionKind = 'subtitle' | 'intro_hook';
export type OverlayPosition =
  | 'bottom-left'
  | 'bottom-center'
  | 'bottom-right'
  | 'top-left'
  | 'top-center'
  | 'top-right';

export interface OverlayStyle {
  position: OverlayPosition;
  textColor: string;
  backgroundColor: string;
  showBackground: boolean;
  fontSize: number;
}

export interface WordTimestamp {
  word: string;
  start: number;
  end: number;
}

export interface CaptionItem {
  id: string;
  start: number;
  end: number;
  text: string;
  isManuallyEdited: boolean;
  captionKind?: CaptionKind;
  highlightedWords: string[];
  words: WordTimestamp[];
}

export interface ClipItem {
  id: string;
  title: string;
  sourceStart: number;
  sourceEnd: number;
  timelineStart: number;
  duration: number;
  speed?: number;
  viralityScore?: number;
  reason?: string;
}

export type ZoomKind = 'zoom_in' | 'zoom_out' | 'punch_in';
export type ZoomEasing = 'instant' | 'linear' | 'ease_in_out';

export interface ZoomEffectItem {
  id: string;
  start: number;
  end: number;
  kind: ZoomKind;
  startScale: number;
  endScale: number;
  focusX: number;
  focusY: number;
  easing: ZoomEasing;
}

export type DrawingTool =
  | 'freehand'
  | 'line'
  | 'arrow'
  | 'circle'
  | 'rect'
  | 'highlight';

export interface DrawingPoint {
  x: number;
  y: number;
}

export interface DrawingItem {
  id: string;
  start: number;
  end: number;
  tool: DrawingTool;
  points: DrawingPoint[];
  color: string;
  strokeWidth: number;
  opacity: number;
}

export interface SfxAsset {
  id: string;
  name: string;
  filename: string;
  url: string;
  duration: number;
  tags: string[];
  description?: string;
}

export interface SfxClipItem {
  id: string;
  assetId: string;
  name: string;
  url: string;
  start: number;
  duration: number;
  trimStart?: number;
  volume: number;
  sourceDuration?: number;
}

export interface AudioEditItem {
  id: string;
  start: number;
  end: number;
  muted: boolean;
  volume: number;
  label?: string;
}

export interface HeatmapPoint {
  start_time: number;
  end_time: number;
  value: number;
}

export interface SourceVideo {
  videoUrl: string;
  title: string;
  thumbnail: string;
  originalDuration: number;
  heatmap: HeatmapPoint[];
  audioPeaks: number[];
}

export interface TimeRange {
  start: number;
  end: number;
}

export interface VideoCropRect {
  x: number;
  y: number;
  w: number;
  h: number;
  start?: number;
  end?: number;
}

export interface WorkspaceState {
  playheadPosition: number;
  selectedClipIds: string[];
  selectedTimeRange: TimeRange | null;
  targetAspectRatio: AspectRatio;
  activeCampaignId?: string | null;
  overlayText?: string | null;
  overlayStyle?: OverlayStyle;
  crop?: VideoCropRect | null;
}

export interface TracksState {
  clips: ClipItem[];
  captions: CaptionItem[];
  zooms?: ZoomEffectItem[];
  drawings?: DrawingItem[];
  sfx?: SfxClipItem[];
  audioEdits?: AudioEditItem[];
}

export interface CaptionStyle {
  preset: CaptionPreset;
  fontFamily: string;
  fontSize: number;
  textColor: string;
  highlightColor: string;
  position: CaptionPosition;
  isBold: boolean;
}

export interface ProjectState {
  projectId: string;
  projectName: string;
  youtubeUrl: string;
  sourceVideo: SourceVideo;
  workspaceState: WorkspaceState;
  tracks: TracksState;
  captionStyle: CaptionStyle;
}

export interface CachedTranscript {
  text: string;
  words: WordTimestamp[];
  segments: Array<{ start: number; end: number; text: string }>;
  source?: string;
}

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  reason?: string;
}

export interface AiActionLogItem {
  id: string;
  toolName: string;
  summary: string;
  timestamp: string;
}

export interface AiPendingProposal {
  projectId?: string;
  toolCalls: ToolCall[];
  autoCaptions: CaptionItem[] | null;
  generatedAssets?: SfxAsset[];
  status: 'pending' | 'accepted' | 'dismissed';
}

export interface AiChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  routeUsed?: string;
  campaignLabel?: string;
  copyableCaption?: string;
  actions?: AiActionLogItem[];
  generatedAssets?: SfxAsset[];
  pendingProposal?: AiPendingProposal;
}

export interface HighlightClipInput {
  source_start: number;
  source_end: number;
  title: string;
  reason?: string;
  virality_score?: number;
}

export interface ChangeCaptionStyleArgs {
  preset?: CaptionPreset;
  font_family?: string;
  font_size?: number;
  text_color?: string;
  highlight_color?: string;
  position?: CaptionPosition;
  is_bold?: boolean;
}

export interface ModelStatus {
  primaryToolModel: string | null;
  backupToolModels: string[];
  primaryRouterModel: string | null;
  backupRouterModels: string[];
  primarySttModel: string | null;
  backupSttModels: string[];
  primaryViralModel: string | null;
  backupViralModels: string[];
  primaryNvidiaFastModel: string | null;
  backupNvidiaFastModels: string[];
  primaryNvidiaVisionModel: string | null;
  backupNvidiaVisionModels: string[];
  availableGroqModels: string[];
  availableGeminiModels: string[];
  availableNvidiaModels: string[];
  lastRefreshedIso: string | null;
  nextRefreshInSeconds: number;
  groqConfigured: boolean;
  geminiConfigured: boolean;
  nvidiaConfigured: boolean;
  lastRaceWinner: {
    provider?: string;
    model?: string;
    latency_ms?: number;
    task?: string;
  } | null;
}

