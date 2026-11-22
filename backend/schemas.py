from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class WordTimestamp(BaseModel):
    word: str
    start: float
    end: float


class CaptionItem(BaseModel):
    id: str
    start: float
    end: float
    text: str
    isManuallyEdited: bool = False
    highlightedWords: List[str] = Field(default_factory=list)
    words: List[WordTimestamp] = Field(default_factory=list)


class ClipItem(BaseModel):
    id: str
    title: str
    sourceStart: float
    sourceEnd: float
    timelineStart: float
    duration: float
    speed: float = 1.0
    viralityScore: Optional[float] = None
    reason: Optional[str] = None


class ZoomEffectItem(BaseModel):
    id: str
    start: float
    end: float
    kind: Literal["zoom_in", "zoom_out", "punch_in"] = "punch_in"
    startScale: float = 1.0
    endScale: float = 1.35
    focusX: float = 0.5
    focusY: float = 0.5
    easing: Literal["instant", "linear", "ease_in_out"] = "ease_in_out"


class DrawingPoint(BaseModel):
    x: float
    y: float


class DrawingItem(BaseModel):
    id: str
    start: float
    end: float
    tool: Literal["freehand", "line", "arrow", "circle", "rect", "highlight"] = "arrow"
    points: List[DrawingPoint] = Field(default_factory=list)
    color: str = "#EF4444"
    strokeWidth: int = 6
    opacity: float = 1.0


class SfxAsset(BaseModel):
    id: str
    name: str
    filename: str
    url: str
    duration: float
    tags: List[str] = Field(default_factory=list)
    description: str = ""


class SfxUpdateRequest(BaseModel):
    name: Optional[str] = None
    tags: Optional[List[str]] = None
    description: Optional[str] = None


class SfxClipItem(BaseModel):
    id: str
    assetId: str
    name: str
    url: str
    start: float
    duration: float
    trimStart: float = 0.0
    volume: float = 0.85


class HeatmapPoint(BaseModel):
    start_time: float
    end_time: float
    value: float


class SourceVideo(BaseModel):
    videoUrl: str
    title: str
    thumbnail: str
    originalDuration: float
    heatmap: List[HeatmapPoint] = Field(default_factory=list)
    audioPeaks: List[float] = Field(default_factory=list)


class TimeRange(BaseModel):
    start: float
    end: float


class VideoCropRect(BaseModel):
    x: float = 0.0
    y: float = 0.0
    w: float = 1.0
    h: float = 1.0
    start: Optional[float] = None
    end: Optional[float] = None


class OverlayStyle(BaseModel):
    position: Literal[
        "bottom-left",
        "bottom-center",
        "bottom-right",
        "top-left",
        "top-center",
        "top-right",
    ] = "bottom-left"
    textColor: str = "#FFFFFF"
    backgroundColor: str = "#000000"
    showBackground: bool = True
    fontSize: int = 24


class WorkspaceState(BaseModel):
    playheadPosition: float = 0.0
    selectedClipIds: List[str] = Field(default_factory=list)
    selectedTimeRange: Optional[TimeRange] = None
    targetAspectRatio: Literal["16:9", "9:16", "1:1"] = "16:9"
    activeCampaignId: Optional[str] = None
    overlayText: Optional[str] = None
    overlayStyle: OverlayStyle = Field(default_factory=OverlayStyle)
    crop: Optional[VideoCropRect] = None


class TracksState(BaseModel):
    clips: List[ClipItem] = Field(default_factory=list)
    captions: List[CaptionItem] = Field(default_factory=list)
    zooms: List[ZoomEffectItem] = Field(default_factory=list)
    drawings: List[DrawingItem] = Field(default_factory=list)
    sfx: List[SfxClipItem] = Field(default_factory=list)


class CaptionStyle(BaseModel):
    preset: Literal["karaoke", "bold_social", "yellow_highlight", "minimal"] = "karaoke"
    fontFamily: str = "Inter"
    fontSize: int = 28
    textColor: str = "#FFFFFF"
    highlightColor: str = "#FACC15"
    position: Literal["top", "middle", "bottom"] = "bottom"
    isBold: bool = True


class ProjectState(BaseModel):
    projectId: str
    projectName: str = "Untitled Project"
    youtubeUrl: str
    sourceVideo: SourceVideo
    workspaceState: WorkspaceState = Field(default_factory=WorkspaceState)
    tracks: TracksState = Field(default_factory=TracksState)
    captionStyle: CaptionStyle = Field(default_factory=CaptionStyle)


class ToolCallSchema(BaseModel):
    id: str
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    reason: Optional[str] = None


class IngestRequest(BaseModel):
    youtube_url: str
    project_name: Optional[str] = None
    initial_prompt: Optional[str] = None
    clip_only: bool = True
    campaign_id: Optional[str] = None
    campaign_rules: List[str] = Field(default_factory=list)
    overlay_text: Optional[str] = None
    overlay_style: Optional[OverlayStyle] = None


class IngestResponse(BaseModel):
    project: ProjectState
    transcript: Dict[str, Any]
    ai_message: Optional[str] = None
    route_used: Optional[str] = None
    storage_saved_pct: Optional[int] = None


class AiEditRequest(BaseModel):
    prompt: str
    project_state: ProjectState
    history: List[Dict[str, str]] = Field(default_factory=list)
    campaign_id: Optional[str] = None
    campaign_rules: List[str] = Field(default_factory=list)
    deep_think: bool = False


class AiEditResponse(BaseModel):
    message: str
    tool_calls: List[ToolCallSchema] = Field(default_factory=list)
    auto_captions: Optional[List[CaptionItem]] = None
    route_used: str = "groq"


class AutoCaptionsRequest(BaseModel):
    project_id: str
    start_time: Optional[float] = None
    end_time: Optional[float] = None


class DetectPausesRequest(BaseModel):
    project_id: str
    min_gap: float = 0.7


class ExportRequest(BaseModel):
    project_state: ProjectState
    export_mode: Literal["combined", "individual"] = "combined"


class ExportResponse(BaseModel):
    status: str
    files: List[Dict[str, str]] = Field(default_factory=list)


class SubtitleExportRequest(BaseModel):
    project_state: ProjectState
    format: Literal["srt", "vtt", "ass"] = "srt"


class ProjectSaveRequest(BaseModel):
    project_state: ProjectState


class ReferenceStyleRequest(BaseModel):
    reference_url: str
    project_state: ProjectState
    custom_instruction: Optional[str] = None
    deep_think: bool = False


class ReferenceStyleResponse(BaseModel):
    profile: Dict[str, Any]
    message: str
    tool_calls: List[ToolCallSchema] = Field(default_factory=list)
    auto_captions: Optional[List[CaptionItem]] = None
    route_used: str = "reference-style"


# ==============================================================================
# 21 SHARED TOOLS DEFINITIONS (OpenAI / Groq Tool Calling & Gemini Schema)
# ==============================================================================

SHARED_TOOLS_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "isolate_highlight_clips",
            "description": (
                "Replaces the current clip track with one or more viral/funny highlight clips "
                "and recalculates timelineStart sequentially."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "clips": {
                        "type": "array",
                        "description": "List of highlight clips to place on the timeline.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source_start": {"type": "number", "description": "Start timestamp in source video (seconds)."},
                                "source_end": {"type": "number", "description": "End timestamp in source video (seconds)."},
                                "title": {"type": "string", "description": "Short catchy title for the clip."},
                                "reason": {"type": "string", "description": "Why this moment is funny/viral."},
                                "virality_score": {"type": "number", "description": "Estimated virality score from 1.0 to 10.0."}
                            },
                            "required": ["source_start", "source_end", "title"]
                        }
                    }
                },
                "required": ["clips"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "trim_clip",
            "description": "Adjusts the source start and end points of a specific clip on the timeline.",
            "parameters": {
                "type": "object",
                "properties": {
                    "clip_id": {"type": "string"},
                    "new_source_start": {"type": "number"},
                    "new_source_end": {"type": "number"}
                },
                "required": ["clip_id", "new_source_start", "new_source_end"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "split_clip",
            "description": "Splits one clip into two separate clips at the given timeline or source timestamp.",
            "parameters": {
                "type": "object",
                "properties": {
                    "clip_id": {"type": "string"},
                    "split_timestamp": {
                        "type": "number",
                        "description": "Timeline timestamp (seconds) where the clip should be split."
                    }
                },
                "required": ["clip_id", "split_timestamp"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_clips",
            "description": "Deletes specified clips from the timeline and closes the gap.",
            "parameters": {
                "type": "object",
                "properties": {
                    "clip_ids": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": ["clip_ids"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "merge_clips",
            "description": "Combines two adjacent clips if their source timestamps are contiguous, or groups them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "clip_id_1": {"type": "string"},
                    "clip_id_2": {"type": "string"}
                },
                "required": ["clip_id_1", "clip_id_2"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reorder_clips",
            "description": "Moves a clip to a new zero-based index position in the timeline sequence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "clip_id": {"type": "string"},
                    "new_index": {"type": "integer"}
                },
                "required": ["clip_id", "new_index"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remove_time_ranges",
            "description": (
                "Cuts out specified source time ranges across the timeline "
                "(e.g., 'Remove the first 20 seconds' or 'Remove unnecessary pauses')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ranges": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "start": {"type": "number"},
                                "end": {"type": "number"}
                            },
                            "required": ["start", "end"]
                        }
                    }
                },
                "required": ["ranges"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_auto_captions",
            "description": (
                "Populates tracks.captions from the cached Groq Whisper transcript for the active timeline clips "
                "or selected source time range, preserving any caption where isManuallyEdited == true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_time": {"type": "number"},
                    "end_time": {"type": "number"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_caption",
            "description": "Adds a new manual caption block at the specified source time range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "text": {"type": "string"}
                },
                "required": ["start", "end", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_caption",
            "description": (
                "Updates caption text and optional highlighted words. "
                "NEVER target captions with isManuallyEdited=true unless explicitly asked by the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "caption_id": {"type": "string"},
                    "new_text": {"type": "string"},
                    "highlighted_words": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": ["caption_id", "new_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "adjust_caption_timing",
            "description": "Changes start and end timestamps of a specific caption.",
            "parameters": {
                "type": "object",
                "properties": {
                    "caption_id": {"type": "string"},
                    "new_start": {"type": "number"},
                    "new_end": {"type": "number"}
                },
                "required": ["caption_id", "new_start", "new_end"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "change_caption_style",
            "description": (
                "Updates captionStyle properties (preset, font_family, font_size, text_color, highlight_color, position, is_bold). "
                "Always pass 6-digit hex color codes for text_color and highlight_color (e.g. '#000000' for black, '#FFFFFF' for white, '#EF4444' for red, '#FACC15' for yellow)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "preset": {
                        "type": "string",
                        "enum": ["karaoke", "bold_social", "yellow_highlight", "minimal"]
                    },
                    "font_family": {"type": "string"},
                    "font_size": {"type": "integer"},
                    "text_color": {"type": "string", "description": "Hex color code for caption text (e.g. '#000000' for black, '#FFFFFF' for white)."},
                    "highlight_color": {"type": "string", "description": "Hex color code for active/highlighted words (e.g. '#FACC15' for yellow)."},
                    "position": {
                        "type": "string",
                        "enum": ["top", "middle", "bottom"]
                    },
                    "is_bold": {"type": "boolean"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "change_video_format",
            "description": "Changes the preview and export aspect ratio.",
            "parameters": {
                "type": "object",
                "properties": {
                    "aspect_ratio": {
                        "type": "string",
                        "enum": ["16:9", "9:16", "1:1"]
                    }
                },
                "required": ["aspect_ratio"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_caption",
            "description": "Deletes a specific caption block by its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "caption_id": {"type": "string"}
                },
                "required": ["caption_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "combine_captions",
            "description": "Combines two adjacent caption blocks into a single caption block.",
            "parameters": {
                "type": "object",
                "properties": {
                    "caption_id_1": {"type": "string"},
                    "caption_id_2": {"type": "string"}
                },
                "required": ["caption_id_1", "caption_id_2"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_clip_speed",
            "description": (
                "Changes playback speed (0.25x to 4.0x) of a specific clip or a timeline "
                "[start_time, end_time] range (auto-splitting if needed)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "speed": {
                        "type": "number",
                        "description": "Playback speed multiplier from 0.25 to 4.0 (e.g. 1.5 for faster, 0.5 for slow-mo)."
                    },
                    "clip_id": {
                        "type": "string",
                        "description": "Optional specific clip ID to change speed on."
                    },
                    "start_time": {
                        "type": "number",
                        "description": "Optional timeline start time in seconds for range speed change."
                    },
                    "end_time": {
                        "type": "number",
                        "description": "Optional timeline end time in seconds for range speed change."
                    }
                },
                "required": ["speed"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_zoom_effect",
            "description": "Adds a keyframed zoom effect (punch_in, zoom_in, zoom_out) over a timeline interval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "number", "description": "Timeline start timestamp (seconds)."},
                    "end": {"type": "number", "description": "Timeline end timestamp (seconds)."},
                    "kind": {
                        "type": "string",
                        "enum": ["punch_in", "zoom_in", "zoom_out"]
                    },
                    "start_scale": {"type": "number", "description": "Starting scale (1.0 to 3.0)."},
                    "end_scale": {"type": "number", "description": "Ending scale (1.0 to 3.0)."},
                    "focus_x": {"type": "number", "description": "Normalized horizontal focus target (0.0 to 1.0)."},
                    "focus_y": {"type": "number", "description": "Normalized vertical focus target (0.0 to 1.0)."},
                    "easing": {
                        "type": "string",
                        "enum": ["instant", "linear", "ease_in_out"]
                    }
                },
                "required": ["start", "end"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_zoom_effect",
            "description": "Deletes an existing zoom effect by its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "zoom_id": {"type": "string"}
                },
                "required": ["zoom_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_drawing",
            "description": "Draws a vector line, arrow, circle, rectangle, freehand, or highlight overlay onto the video for a timeline interval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "number", "description": "Timeline start timestamp (seconds)."},
                    "end": {"type": "number", "description": "Timeline end timestamp (seconds)."},
                    "tool": {
                        "type": "string",
                        "enum": ["freehand", "line", "arrow", "circle", "rect", "highlight"]
                    },
                    "points": {
                        "type": "array",
                        "description": "Normalized 0.0 to 1.0 screen coordinates defining the drawing.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "x": {"type": "number"},
                                "y": {"type": "number"}
                            },
                            "required": ["x", "y"]
                        }
                    },
                    "color": {"type": "string", "description": "Hex color code such as #EF4444 or #FACC15."},
                    "stroke_width": {"type": "integer", "description": "Stroke width from 2 to 24."},
                    "opacity": {"type": "number", "description": "Opacity from 0.1 to 1.0."}
                },
                "required": ["start", "end", "tool", "points"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_drawing",
            "description": "Deletes an existing drawing overlay by its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "drawing_id": {"type": "string"}
                },
                "required": ["drawing_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_sfx_clip",
            "description": "Places an uploaded custom MP3 sound effect onto the timeline SFX track.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "number", "description": "Timeline start timestamp (seconds)."},
                    "asset_id": {"type": "string", "description": "Optional ID from the Available Custom MP3 SFX Library."},
                    "sfx_name": {"type": "string", "description": "Optional name or tag search query to match an uploaded SFX."},
                    "trim_start": {"type": "number", "description": "Optional source audio start offset in seconds (>= 0) to skip leading audio."},
                    "duration": {"type": "number", "description": "Optional trimmed playback duration override in seconds."},
                    "volume": {"type": "number", "description": "Volume multiplier from 0.1 to 2.0 (default 0.85)."}
                },
                "required": ["start"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "trim_sfx_clip",
            "description": "Trims an existing sound effect clip on the timeline by setting its source trim_start offset and/or playback duration.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sfx_id": {"type": "string", "description": "Optional SFX clip ID from Active SFX Clips."},
                    "sfx_name": {"type": "string", "description": "Optional SFX name to match if sfx_id is omitted."},
                    "trim_start": {"type": "number", "description": "Source audio start offset in seconds (>= 0) to trim the beginning."},
                    "duration": {"type": "number", "description": "Trimmed playback duration in seconds (>= 0.1)."},
                    "start": {"type": "number", "description": "Optional new timeline start timestamp in seconds."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_sfx_clip",
            "description": "Deletes an SFX clip from the timeline by its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sfx_id": {"type": "string"}
                },
                "required": ["sfx_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_video_crop",
            "description": (
                "Crops the video frame to a normalized 0.0-1.0 rectangle (e.g. creator facecam or reaction region). "
                "Pass optional start and end (timeline seconds) if the crop should only apply for a while / during a specific funny part or reaction; "
                "omit start and end to crop the entire video. Pass x=0, y=0, w=1, h=1 to reset to full frame."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "number", "description": "Left crop edge (0.0 to 0.9)."},
                    "y": {"type": "number", "description": "Top crop edge (0.0 to 0.9)."},
                    "w": {"type": "number", "description": "Normalized crop width (0.1 to 1.0)."},
                    "h": {"type": "number", "description": "Normalized crop height (0.1 to 1.0)."},
                    "start": {
                        "type": "number",
                        "description": "Optional timeline start time in seconds when cropping only for a specific moment/while."
                    },
                    "end": {
                        "type": "number",
                        "description": "Optional timeline end time in seconds when cropping only for a specific moment/while."
                    }
                },
                "required": ["x", "y", "w", "h"]
            }
        }
    }
]
