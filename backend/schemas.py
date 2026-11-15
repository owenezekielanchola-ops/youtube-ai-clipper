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
    viralityScore: Optional[float] = None
    reason: Optional[str] = None


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


class WorkspaceState(BaseModel):
    playheadPosition: float = 0.0
    selectedClipIds: List[str] = Field(default_factory=list)
    selectedTimeRange: Optional[TimeRange] = None
    targetAspectRatio: Literal["16:9", "9:16", "1:1"] = "16:9"


class TracksState(BaseModel):
    clips: List[ClipItem] = Field(default_factory=list)
    captions: List[CaptionItem] = Field(default_factory=list)


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


# ==============================================================================
# 13 SHARED TOOLS DEFINITIONS (OpenAI / Groq Tool Calling & Gemini Schema)
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
            "description": "Updates captionStyle properties (preset, font, size, colors, position, bold).",
            "parameters": {
                "type": "object",
                "properties": {
                    "preset": {
                        "type": "string",
                        "enum": ["karaoke", "bold_social", "yellow_highlight", "minimal"]
                    },
                    "font_family": {"type": "string"},
                    "font_size": {"type": "integer"},
                    "text_color": {"type": "string"},
                    "highlight_color": {"type": "string"},
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
    }
]
