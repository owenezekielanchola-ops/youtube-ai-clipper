import re
from pathlib import Path
from typing import List, Tuple

from schemas import CaptionItem, ClipItem, ProjectState


def _hex_to_ass_color(hex_color: str, alpha_hex: str = "00") -> str:
    """Converts #RRGGBB into ASS &HAABBGGRR format."""
    cleaned = hex_color.strip().lstrip("#")
    if len(cleaned) != 6:
        cleaned = "FFFFFF"
    r, g, b = cleaned[0:2], cleaned[2:4], cleaned[4:6]
    return f"&H{alpha_hex.upper()}{b.upper()}{g.upper()}{r.upper()}"


def _format_ass_time(seconds: float) -> str:
    total_cs = max(0, int(round(seconds * 100)))
    cs = total_cs % 100
    total_secs = total_cs // 100
    secs = total_secs % 60
    mins = (total_secs // 60) % 60
    hrs = total_secs // 3600
    return f"{hrs}:{mins:02d}:{secs:02d}.{cs:02d}"


def _format_srt_time(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    ms = total_ms % 1000
    total_secs = total_ms // 1000
    secs = total_secs % 60
    mins = (total_secs // 60) % 60
    hrs = total_secs // 3600
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{ms:03d}"


def _format_vtt_time(seconds: float) -> str:
    return _format_srt_time(seconds).replace(",", ".")


def _map_captions_to_clips(
    captions: List[CaptionItem], clips: List[ClipItem]
) -> List[Tuple[float, float, CaptionItem, float, float]]:
    """
    Maps source-time captions onto the concatenated timeline of active clips.
    Returns list of (timeline_start, timeline_end, caption, overlap_start, overlap_end).
    """
    mapped: List[Tuple[float, float, CaptionItem, float, float]] = []
    for clip in clips:
        for cap in captions:
            overlap_start = max(clip.sourceStart, cap.start)
            overlap_end = min(clip.sourceEnd, cap.end)
            if overlap_end - overlap_start > 0.05:
                tl_start = clip.timelineStart + (overlap_start - clip.sourceStart)
                tl_end = clip.timelineStart + (overlap_end - clip.sourceStart)
                mapped.append((tl_start, tl_end, cap, overlap_start, overlap_end))
    mapped.sort(key=lambda item: item[0])
    return mapped


def _escape_ass_text(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("{", "｛")
        .replace("}", "｝")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\N")
    )


def _style_tokens_with_highlights(
    text: str,
    highlighted_words: List[str],
    highlight_ass: str,
    primary_ass: str,
    uppercase: bool = False,
) -> str:
    """Applies ASS highlight colors to matching tokens while preserving line breaks."""
    hl_set = {
        re.sub(r"[^\w']", "", hw.lower())
        for hw in highlighted_words
        if hw.strip()
    }
    raw = text.upper() if uppercase else text
    if not hl_set:
        return _escape_ass_text(raw)

    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    rendered_lines: List[str] = []
    for line in lines:
        styled_tokens: List[str] = []
        for tok in line.split():
            clean_tok = re.sub(r"[^\w']", "", tok.lower())
            escaped = _escape_ass_text(tok)
            if clean_tok and clean_tok in hl_set:
                styled_tokens.append(f"{{\\c{highlight_ass}}}{escaped}{{\\c{primary_ass}}}")
            else:
                styled_tokens.append(escaped)
        rendered_lines.append(" ".join(styled_tokens))
    return "\\N".join(rendered_lines)


def generate_ass_subtitles(
    project_state: ProjectState,
    output_path: Path,
    clips_override: List[ClipItem] | None = None,
) -> Path:
    """
    Generates an Advanced SubStation Alpha (.ass) file matching the project's
    captionStyle (karaoke, bold_social, yellow_highlight, minimal) and targetAspectRatio.
    """
    style = project_state.captionStyle
    ratio = project_state.workspaceState.targetAspectRatio
    clips = clips_override if clips_override is not None else project_state.tracks.clips

    if ratio == "9:16":
        res_x, res_y = 1080, 1920
        font_scale = 2.05
    elif ratio == "1:1":
        res_x, res_y = 1080, 1080
        font_scale = 1.6
    else:
        res_x, res_y = 1920, 1080
        font_scale = 1.75

    scaled_font_size = max(24, int(round(style.fontSize * font_scale)))
    primary_ass = _hex_to_ass_color(style.textColor)
    highlight_ass = _hex_to_ass_color(style.highlightColor)
    outline_ass = "&H00000000"
    back_ass = "&H80000000" if style.preset == "minimal" else "&H40000000"

    # For karaoke preset, ASS SecondaryColour is pre-highlighted color and PrimaryColour is highlighted color
    if style.preset == "karaoke":
        style_primary = highlight_ass
        style_secondary = primary_ass
    else:
        style_primary = primary_ass
        style_secondary = highlight_ass

    alignment = {"top": 8, "middle": 5, "bottom": 2}.get(style.position, 2)
    margin_v = int(res_y * 0.08) if style.position != "middle" else 20
    bold_flag = -1 if (style.isBold or style.preset == "bold_social") else 0
    border_style = 3 if style.preset == "minimal" else 1
    outline_px = 5 if style.preset == "bold_social" else (0 if style.preset == "minimal" else 3)
    shadow_px = 2 if style.preset in {"bold_social", "karaoke"} else 0

    header = f"""[Script Info]
Title: {project_state.projectName}
ScriptType: v4.00+
PlayResX: {res_x}
PlayResY: {res_y}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style.fontFamily},{scaled_font_size},{style_primary},{style_secondary},{outline_ass},{back_ass},{bold_flag},0,0,0,100,100,0,0,{border_style},{outline_px},{shadow_px},{alignment},48,48,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    mapped = _map_captions_to_clips(project_state.tracks.captions, clips)
    dialogue_lines: List[str] = []

    for tl_start, tl_end, cap, overlap_start, overlap_end in mapped:
        start_str = _format_ass_time(tl_start)
        end_str = _format_ass_time(tl_end)
        hl_set = {
            re.sub(r"[^\w']", "", hw.lower())
            for hw in cap.highlightedWords
            if hw.strip()
        }

        if style.preset == "karaoke":
            parts: List[str] = []
            if cap.words:
                visible_words = [
                    w for w in cap.words if w.end > overlap_start and w.start < overlap_end
                ] or cap.words
                if visible_words and visible_words[0].start > overlap_start + 0.05:
                    lead_cs = max(1, int(round((visible_words[0].start - overlap_start) * 100)))
                    parts.append(f"{{\\k{lead_cs}}}")
                for i, w in enumerate(visible_words):
                    w_start = max(overlap_start, w.start)
                    w_end = (
                        min(overlap_end, visible_words[i + 1].start)
                        if i + 1 < len(visible_words)
                        else min(overlap_end, w.end)
                    )
                    dur_cs = max(8, int(round((w_end - w_start) * 100)))
                    word_txt = _escape_ass_text(w.word)
                    clean_w = re.sub(r"[^\w']", "", w.word.lower())
                    if clean_w and clean_w in hl_set:
                        parts.append(f"{{\\k{dur_cs}\\2c{highlight_ass}}}{word_txt}{{\\2c{style_secondary}}}")
                    else:
                        parts.append(f"{{\\k{dur_cs}}}{word_txt}")
            else:
                tokens = cap.text.split()
                if tokens:
                    total_cs = max(len(tokens) * 8, int(round((tl_end - tl_start) * 100)))
                    per_cs = max(8, total_cs // len(tokens))
                    for tok in tokens:
                        escaped = _escape_ass_text(tok)
                        clean_tok = re.sub(r"[^\w']", "", tok.lower())
                        if clean_tok and clean_tok in hl_set:
                            parts.append(f"{{\\k{per_cs}\\2c{highlight_ass}}}{escaped}{{\\2c{style_secondary}}}")
                        else:
                            parts.append(f"{{\\k{per_cs}}}{escaped}")
            rendered_text = " ".join(parts) if parts else _escape_ass_text(cap.text)
        elif style.preset == "bold_social":
            rendered_text = _style_tokens_with_highlights(
                cap.text,
                cap.highlightedWords,
                highlight_ass,
                primary_ass,
                uppercase=True,
            )
        else:
            rendered_text = _style_tokens_with_highlights(
                cap.text,
                cap.highlightedWords,
                highlight_ass,
                primary_ass,
                uppercase=False,
            )

        dialogue_lines.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{rendered_text}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(header + "\n".join(dialogue_lines) + "\n", encoding="utf-8")
    return output_path


def generate_srt_content(project_state: ProjectState) -> str:
    mapped = _map_captions_to_clips(project_state.tracks.captions, project_state.tracks.clips)
    blocks: List[str] = []
    for idx, (tl_start, tl_end, cap, _, _) in enumerate(mapped, start=1):
        clean_text = re.sub(
            r"\n{2,}", "\n", cap.text.replace("\r\n", "\n").replace("\r", "\n").strip()
        )
        blocks.append(
            f"{idx}\n{_format_srt_time(tl_start)} --> {_format_srt_time(tl_end)}\n{clean_text}\n"
        )
    return "\n".join(blocks)


def generate_vtt_content(project_state: ProjectState) -> str:
    mapped = _map_captions_to_clips(project_state.tracks.captions, project_state.tracks.clips)
    lines: List[str] = ["WEBVTT\n"]
    for idx, (tl_start, tl_end, cap, _, _) in enumerate(mapped, start=1):
        clean_text = re.sub(
            r"\n{2,}", "\n", cap.text.replace("\r\n", "\n").replace("\r", "\n").strip()
        )
        lines.append(
            f"{idx}\n{_format_vtt_time(tl_start)} --> {_format_vtt_time(tl_end)}\n{clean_text}\n"
        )
    return "\n".join(lines)
