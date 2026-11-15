export type AspectRatio = '16:9' | '9:16' | '1:1';
export type CaptionPreset = 'karaoke' | 'bold_social' | 'yellow_highlight' | 'minimal';
export type CaptionPosition = 'top' | 'middle' | 'bottom';

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
  viralityScore?: number;
  reason?: string;
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

export interface WorkspaceState {
  playheadPosition: number;
  selectedClipIds: string[];
  selectedTimeRange: TimeRange | null;
  targetAspectRatio: AspectRatio;
}

export interface TracksState {
  clips: ClipItem[];
  captions: CaptionItem[];
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

export interface AiChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  routeUsed?: string;
  actions?: AiActionLogItem[];
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
  availableGroqModels: string[];
  availableGeminiModels: string[];
  lastRefreshedIso: string | null;
  nextRefreshInSeconds: number;
  groqConfigured: boolean;
  geminiConfigured: boolean;
}

