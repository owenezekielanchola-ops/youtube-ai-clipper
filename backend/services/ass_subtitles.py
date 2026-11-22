import math
import re
from pathlib import Path
from typing import List, Tuple

from schemas import CaptionItem, ClipItem, DrawingItem, ProjectState


NAMED_COLOR_HEX = {
    "black": "000000",
    "white": "FFFFFF",
    "yellow": "FACC15",
    "gold": "FACC15",
    "red": "EF4444",
    "crimson": "DC2626",
    "blue": "38BDF8",
    "sky": "38BDF8",
    "navy": "1E3A8A",
    "green": "22C55E",
    "lime": "84CC16",
    "emerald": "10B981",
    "orange": "F97316",
    "amber": "F59E0B",
    "pink": "EC4899",
    "rose": "F43F5E",
    "magenta": "D946EF",
    "purple": "A855F7",
    "violet": "8B5CF6",
    "cyan": "06B6D4",
    "teal": "14B8A6",
    "aqua": "06B6D4",
    "gray": "94A3B8",
    "grey": "94A3B8",
    "silver": "CBD5E1",
    "brown": "92400E",
}


def _clean_hex6(hex_color: str, default: str = "FFFFFF") -> str:
    raw = (hex_color or "").strip()
    mapped = NAMED_COLOR_HEX.get(raw.lower())
    if mapped:
        return mapped
    cleaned = raw.lstrip("#")
    if len(cleaned) == 3 and all(c in "0123456789abcdefABCDEF" for c in cleaned):
        return "".join(c * 2 for c in cleaned).upper()
    if len(cleaned) == 6 and all(c in "0123456789abcdefABCDEF" for c in cleaned):
        return cleaned.upper()
    return default


def _is_dark_color(hex_color: str) -> bool:
    c = _clean_hex6(hex_color, "FFFFFF")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) < 65.0


def _hex_to_ass_color(hex_color: str, alpha_hex: str = "00") -> str:
    """Converts #RRGGBB or named color into ASS &HAABBGGRR format."""
    cleaned = _clean_hex6(hex_color, "FFFFFF")
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
    Maps source-time captions onto the concatenated timeline of active clips
    while accounting for each clip's playback speed.
    Returns list of (timeline_start, timeline_end, caption, overlap_start, overlap_end).
    """
    mapped: List[Tuple[float, float, CaptionItem, float, float]] = []
    for clip in clips:
        clip_speed = max(0.25, min(4.0, float(getattr(clip, "speed", 1.0) or 1.0)))
        for cap in captions:
            overlap_start = max(clip.sourceStart, cap.start)
            overlap_end = min(clip.sourceEnd, cap.end)
            if overlap_end - overlap_start > 0.05:
                tl_start = clip.timelineStart + (overlap_start - clip.sourceStart) / clip_speed
                tl_end = clip.timelineStart + (overlap_end - clip.sourceStart) / clip_speed
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


def _polyline_ribbon_cmd(pts: List[Tuple[float, float]], half_w: float) -> str:
    r"""Converts a polyline into a closed ASS {\p1} ribbon polygon without overlap blotches."""
    if len(pts) < 2:
        return ""
    left_pts: List[Tuple[float, float]] = []
    right_pts: List[Tuple[float, float]] = []
    n = len(pts)
    for i, (px, py) in enumerate(pts):
        if i == 0:
            dx, dy = pts[1][0] - px, pts[1][1] - py
        elif i == n - 1:
            dx, dy = px - pts[n - 2][0], py - pts[n - 2][1]
        else:
            dx, dy = pts[i + 1][0] - pts[i - 1][0], pts[i + 1][1] - pts[i - 1][1]
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length * half_w, dx / length * half_w
        left_pts.append((px + nx, py + ny))
        right_pts.append((px - nx, py - ny))

    outline = left_pts + list(reversed(right_pts))
    parts = [f"m {int(round(outline[0][0]))} {int(round(outline[0][1]))}"]
    for vx, vy in outline[1:]:
        parts.append(f"l {int(round(vx))} {int(round(vy))}")
    return " ".join(parts)


def _rect_ring_cmd(
    x0: float, y0: float, x1: float, y1: float, stroke_px: float
) -> str:
    min_x, max_x = min(x0, x1), max(x0, x1)
    min_y, max_y = min(y0, y1), max(y0, y1)
    if max_x - min_x < 4 or max_y - min_y < 4:
        return ""
    hw = min(stroke_px / 2.0, (max_x - min_x) * 0.4, (max_y - min_y) * 0.4)
    ox0, oy0, ox1, oy1 = min_x - hw, min_y - hw, max_x + hw, max_y + hw
    ix0, iy0, ix1, iy1 = min_x + hw, min_y + hw, max_x - hw, max_y - hw
    # Outer clockwise + inner counter-clockwise to form a crisp hollow frame
    return (
        f"m {int(round(ox0))} {int(round(oy0))} "
        f"l {int(round(ox1))} {int(round(oy0))} "
        f"l {int(round(ox1))} {int(round(oy1))} "
        f"l {int(round(ox0))} {int(round(oy1))} "
        f"m {int(round(ix0))} {int(round(iy0))} "
        f"l {int(round(ix0))} {int(round(iy1))} "
        f"l {int(round(ix1))} {int(round(iy1))} "
        f"l {int(round(ix1))} {int(round(iy0))}"
    )


def _circle_ring_cmd(
    x0: float, y0: float, x1: float, y1: float, stroke_px: float, segments: int = 28
) -> str:
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    rx = max(4.0, abs(x1 - x0) / 2.0)
    ry = max(4.0, abs(y1 - y0) / 2.0)
    hw = min(stroke_px / 2.0, rx * 0.45, ry * 0.45)
    orx, ory = rx + hw, ry + hw
    irx, iry = max(1.0, rx - hw), max(1.0, ry - hw)

    outer_parts: List[str] = []
    for i in range(segments):
        ang = (2.0 * math.pi * i) / segments
        vx = int(round(cx + orx * math.cos(ang)))
        vy = int(round(cy + ory * math.sin(ang)))
        outer_parts.append(f"{'m' if i == 0 else 'l'} {vx} {vy}")

    inner_parts: List[str] = []
    for i in range(segments - 1, -1, -1):
        ang = (2.0 * math.pi * i) / segments
        vx = int(round(cx + irx * math.cos(ang)))
        vy = int(round(cy + iry * math.sin(ang)))
        inner_parts.append(f"{'m' if i == segments - 1 else 'l'} {vx} {vy}")

    return " ".join(outer_parts + inner_parts)


def _build_ass_drawing_events(
    drawings: List[DrawingItem],
    res_x: int,
    res_y: int,
    timeline_offset: float = 0.0,
    max_duration: float | None = None,
) -> List[str]:
    events: List[str] = []
    min_dim = min(res_x, res_y)
    for d in drawings:
        if len(d.points) < 2:
            continue
        s = float(d.start) - timeline_offset
        e = float(d.end) - timeline_offset
        if max_duration is not None:
            s = max(0.0, s)
            e = min(max_duration, e)
        if e <= max(0.0, s) + 0.05:
            continue

        pts = [(pt.x * res_x, pt.y * res_y) for pt in d.points]
        stroke_px = max(3.0, float(d.strokeWidth) * (min_dim / 460.0))
        half_w = stroke_px / 2.0
        cmd = ""

        if d.tool in {"freehand", "highlight"}:
            eff_hw = half_w * 2.4 if d.tool == "highlight" else half_w
            cmd = _polyline_ribbon_cmd(pts, eff_hw)
        elif d.tool == "line":
            cmd = _polyline_ribbon_cmd([pts[0], pts[-1]], half_w)
        elif d.tool == "arrow":
            x0, y0 = pts[0]
            x1, y1 = pts[-1]
            dx, dy = x1 - x0, y1 - y0
            seg_len = math.hypot(dx, dy) or 1.0
            head_len = min(seg_len * 0.45, max(24.0, half_w * 5.2))
            head_hw = head_len * 0.55
            ux, uy = dx / seg_len, dy / seg_len
            bx, by = x1 - ux * head_len, y1 - uy * head_len
            lx, ly = bx - uy * head_hw, by + ux * head_hw
            rx, ry = bx + uy * head_hw, by - ux * head_hw
            shaft_cmd = _polyline_ribbon_cmd([(x0, y0), (bx, by)], half_w)
            head_cmd = (
                f"m {int(round(x1))} {int(round(y1))} "
                f"l {int(round(lx))} {int(round(ly))} "
                f"l {int(round(rx))} {int(round(ry))}"
            )
            cmd = f"{shaft_cmd} {head_cmd}".strip()
        elif d.tool == "rect":
            cmd = _rect_ring_cmd(pts[0][0], pts[0][1], pts[-1][0], pts[-1][1], stroke_px)
        elif d.tool == "circle":
            cmd = _circle_ring_cmd(pts[0][0], pts[0][1], pts[-1][0], pts[-1][1], stroke_px)

        if not cmd:
            continue

        eff_opacity = min(d.opacity, 0.45) if d.tool == "highlight" else d.opacity
        alpha_byte = max(0, min(255, int(round((1.0 - max(0.05, min(1.0, eff_opacity))) * 255))))
        color_bgr = _hex_to_ass_color(d.color).lstrip("&H")
        color_bgr = color_bgr[2:] if len(color_bgr) == 8 else color_bgr
        events.append(
            f"Dialogue: 2,{_format_ass_time(max(0.0, s))},{_format_ass_time(e)},DrawingOverlay,,0,0,0,,"
            f"{{\\p1\\pos(0,0)\\an7\\1c&H{color_bgr}&\\1a&H{alpha_byte:02X}&}}{cmd}{{\\p0}}"
        )
    return events


def generate_ass_subtitles(
    project_state: ProjectState,
    output_path: Path,
    clips_override: List[ClipItem] | None = None,
    timeline_offset: float = 0.0,
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
    dark_text = _is_dark_color(style.textColor)
    outline_ass = "&H00FFFFFF" if dark_text else "&H00000000"
    back_ass = (
        ("&H20FFFFFF" if dark_text else "&H80000000")
        if style.preset == "minimal"
        else ("&H40FFFFFF" if dark_text else "&H40000000")
    )

    # For karaoke preset, ASS SecondaryColour is pre-highlighted color and PrimaryColour is highlighted color
    if style.preset == "karaoke":
        style_primary = highlight_ass
        style_secondary = primary_ass
    else:
        style_primary = primary_ass
        style_secondary = highlight_ass

    overlay_text = (project_state.workspaceState.overlayText or "").strip()
    overlay_style = project_state.workspaceState.overlayStyle
    overlay_is_bottom = overlay_style.position.startswith("bottom")
    alignment = {"top": 8, "middle": 5, "bottom": 2}.get(style.position, 2)
    margin_v = (
        (int(res_y * 0.11) if (overlay_text and overlay_is_bottom) else int(res_y * 0.08))
        if style.position != "middle"
        else 20
    )
    bold_flag = -1 if (style.isBold or style.preset == "bold_social") else 0
    border_style = 3 if style.preset == "minimal" else 1
    outline_px = 5 if style.preset == "bold_social" else (0 if style.preset == "minimal" else 3)
    shadow_px = 2 if style.preset in {"bold_social", "karaoke"} else 0

    overlay_style_line = ""
    if overlay_text:
        min_dim = min(res_x, res_y)
        clamped_size = max(14, min(56, int(overlay_style.fontSize)))
        overlay_font_size = max(24, int(round(clamped_size * (min_dim / 540.0))))
        overlay_pad = max(8, int(round(overlay_font_size * 0.24)))
        overlay_margin_x = max(28, int(round(min_dim * 0.035)))
        overlay_margin_v = max(26, int(round(min_dim * 0.03)))
        overlay_align = {
            "bottom-left": 1,
            "bottom-center": 2,
            "bottom-right": 3,
            "top-left": 7,
            "top-center": 8,
            "top-right": 9,
        }.get(overlay_style.position, 1)
        overlay_primary = _hex_to_ass_color(overlay_style.textColor)
        if overlay_style.showBackground:
            overlay_border_style = 3
            overlay_outline_color = _hex_to_ass_color(overlay_style.backgroundColor, "28")
            overlay_outline_px = overlay_pad
            overlay_shadow_px = 0
        else:
            overlay_border_style = 1
            overlay_outline_color = "&H00000000"
            overlay_outline_px = 4
            overlay_shadow_px = 2
        overlay_style_line = (
            f"\nStyle: CampaignOverlay,{style.fontFamily},{overlay_font_size},"
            f"{overlay_primary},{overlay_primary},{overlay_outline_color},&H80000000,"
            f"-1,0,0,0,100,100,2,0,{overlay_border_style},{overlay_outline_px},"
            f"{overlay_shadow_px},{overlay_align},{overlay_margin_x},{overlay_margin_x},{overlay_margin_v},1"
        )

    drawings = project_state.tracks.drawings
    has_drawings = len(drawings) > 0
    drawing_style_line = (
        "\nStyle: DrawingOverlay,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1"
        if has_drawings
        else ""
    )

    header = f"""[Script Info]
Title: {project_state.projectName}
ScriptType: v4.00+
PlayResX: {res_x}
PlayResY: {res_y}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style.fontFamily},{scaled_font_size},{style_primary},{style_secondary},{outline_ass},{back_ass},{bold_flag},0,0,0,100,100,0,0,{border_style},{outline_px},{shadow_px},{alignment},48,48,{margin_v},1{overlay_style_line}{drawing_style_line}

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

    total_dur = max(0.5, max((c.timelineStart + c.duration for c in clips), default=0.5))
    if overlay_text:
        dialogue_lines.append(
            f"Dialogue: 1,0:00:00.00,{_format_ass_time(total_dur)},CampaignOverlay,,0,0,0,,{_escape_ass_text(overlay_text)}"
        )

    if drawings:
        clip_limit = total_dur if clips_override is not None else None
        dialogue_lines.extend(
            _build_ass_drawing_events(
                drawings,
                res_x,
                res_y,
                timeline_offset=timeline_offset,
                max_duration=clip_limit,
            )
        )

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
