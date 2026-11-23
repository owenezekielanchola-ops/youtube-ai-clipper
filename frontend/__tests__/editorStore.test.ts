/**
 * Jest unit tests for the ClipForge shared editing engine (Zustand store).
 * Targets the 13 shared tools + undo/redo. Node env; store logic is DOM-free.
 */
import { describe, it, expect, beforeEach } from '@jest/globals';
import { useEditorStore } from '../src/store/useEditorStore';
import type { ProjectState, ClipItem } from '../src/types/editor';

const clip = (id: string, sStart: number, sEnd: number): ClipItem => ({
  id, title: id, sourceStart: sStart, sourceEnd: sEnd,
  timelineStart: 0, duration: sEnd - sStart, speed: 1,
});

function projectWith(clips: ClipItem[]): ProjectState {
  return {
    projectId: 'proj_test', projectName: 'Test', youtubeUrl: '',
    sourceVideo: { videoUrl: '', title: 't', thumbnail: '', originalDuration: 600, heatmap: [], audioPeaks: [] },
    workspaceState: {
      playheadPosition: 0, selectedClipIds: [], selectedTimeRange: null,
      targetAspectRatio: '16:9', activeCampaignId: null, overlayText: null,
      overlayStyle: { position: 'bottom-left', textColor: '#fff', backgroundColor: '#000', showBackground: true, fontSize: 24 },
      crop: null,
    },
    tracks: { clips, captions: [], zooms: [], drawings: [], sfx: [] },
    captionStyle: { preset: 'karaoke', fontFamily: 'Inter', fontSize: 28, textColor: '#fff', highlightColor: '#FACC15', position: 'bottom', isBold: true },
  } as ProjectState;
}

const S = () => useEditorStore.getState();
const clips = () => S().project.tracks.clips;

beforeEach(() => {
  useEditorStore.temporal.getState().clear();
  S().loadProjectState(projectWith([clip('c1', 0, 30), clip('c2', 30, 60)]));
});

describe('trim_clip', () => {
  it('applies a valid trim', () => {
    S().trim_clip('c1', 5, 20);
    const c = clips().find((x) => x.id === 'c1')!;
    expect(c.sourceStart).toBe(5);
    expect(c.sourceEnd).toBe(20);
  });
  it('clamps start >= 0', () => {
    S().trim_clip('c1', -100, 20);
    expect(clips().find((x) => x.id === 'c1')!.sourceStart).toBeGreaterThanOrEqual(0);
  });
  it('clamps end to source duration (600)', () => {
    S().trim_clip('c1', 0, 99999);
    expect(clips().find((x) => x.id === 'c1')!.sourceEnd).toBeLessThanOrEqual(600);
  });
  it('enforces end > start (min 0.2)', () => {
    S().trim_clip('c1', 50, 10);
    const c = clips().find((x) => x.id === 'c1')!;
    expect(c.sourceEnd).toBeGreaterThan(c.sourceStart);
  });
  it('recalculates contiguous timeline (no gaps)', () => {
    S().trim_clip('c1', 0, 10);
    const cs = clips();
    expect(cs[0].timelineStart).toBe(0);
    expect(cs[1].timelineStart).toBeCloseTo(cs[0].duration, 2);
  });
});

describe('split_clip', () => {
  it('splits mid-clip into two clips', () => {
    const before = clips().length;
    S().split_clip('c1', 15); // timeline 15 is inside c1 (0..30)
    expect(clips().length).toBe(before + 1);
  });
  it('no-op when split at the very edge', () => {
    const before = clips().length;
    S().split_clip('c1', 0.05);
    expect(clips().length).toBe(before);
  });
  it('split halves preserve total source coverage', () => {
    S().split_clip('c1', 15);
    const parts = clips().filter((c) => c.sourceStart < 30 && c.sourceEnd <= 30.001);
    const covered = parts.reduce((a, c) => a + (c.sourceEnd - c.sourceStart), 0);
    expect(covered).toBeCloseTo(30, 1);
  });
});

describe('delete_clips', () => {
  it('removes a clip', () => {
    S().delete_clips(['c1']);
    expect(clips().map((c) => c.id)).toEqual(['c2']);
  });
  it('ignores unknown ids', () => {
    S().delete_clips(['nope']);
    expect(clips().length).toBe(2);
  });
});

describe('merge_clips', () => {
  it('merges adjacent contiguous clips (30|30 -> 1)', () => {
    S().merge_clips('c1', 'c2'); // c1 end=30, c2 start=30 -> contiguous
    expect(clips().length).toBe(1);
    expect(clips()[0].sourceStart).toBe(0);
    expect(clips()[0].sourceEnd).toBe(60);
  });
  it('rejects non-adjacent clips', () => {
    S().loadProjectState(projectWith([clip('a', 0, 10), clip('b', 10, 20), clip('d', 20, 30)]));
    S().merge_clips('a', 'd');
    expect(clips().length).toBe(3); // unchanged
  });
  it('rejects non-contiguous source (gap > 0.5s)', () => {
    S().loadProjectState(projectWith([clip('a', 0, 10), clip('b', 100, 110)]));
    S().merge_clips('a', 'b');
    expect(clips().length).toBe(2); // unchanged
  });
});

describe('captions', () => {
  it('add_caption appends a caption', () => {
    S().add_caption(1, 3, 'hello');
    const caps = S().project.tracks.captions;
    expect(caps.some((c) => c.text === 'hello')).toBe(true);
  });
  it('delete_caption removes it', () => {
    S().add_caption(1, 3, 'bye');
    const id = S().project.tracks.captions.find((c) => c.text === 'bye')!.id;
    S().delete_caption(id);
    expect(S().project.tracks.captions.some((c) => c.id === id)).toBe(false);
  });
});

describe('change_video_format', () => {
  it('updates target aspect ratio', () => {
    S().change_video_format('9:16');
    expect(S().project.workspaceState.targetAspectRatio).toBe('9:16');
  });
});

describe('undo / redo (zundo temporal)', () => {
  it('undo reverts the last edit', () => {
    S().delete_clips(['c1']);
    expect(clips().length).toBe(1);
    useEditorStore.temporal.getState().undo();
    expect(clips().length).toBe(2);
  });
  it('redo re-applies after undo', () => {
    S().delete_clips(['c1']);
    useEditorStore.temporal.getState().undo();
    useEditorStore.temporal.getState().redo();
    expect(clips().length).toBe(1);
  });
});
