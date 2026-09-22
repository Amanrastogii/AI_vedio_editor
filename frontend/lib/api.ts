export const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ── Token management ──────────────────────────────────────────────────────────
const TOKEN_KEY = "av_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string) {
  localStorage.setItem(TOKEN_KEY, t);
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ── Types ─────────────────────────────────────────────────────────────────────
export interface HealthStatus {
  status: string;
  version: string;
  db_ready: boolean;
}
export interface Project {
  id: string;
  title: string;
  status: string;
  target_duration_sec: number | null;
  target_style?: Record<string, any> | null;
  output_formats: string[] | null;
  created_at: string;
  completed_at: string | null;
  error_message: string | null;
}
export interface Clip {
  id: string;
  filename: string;
  original_filename: string;
  duration_ms: number | null;
  width: number | null;
  height: number | null;
  file_size_bytes: number | null;
  upload_order: number;
  is_ingested: boolean;
  thumbnail_url?: string | null;
  source_url?: string | null;
}
export interface AgentTaskStatus {
  agent: string;
  status: string;
  progress_pct: number | null;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  current_message?: string | null;
  result_metadata?: Record<string, any> | null;
}
export interface PipelineStatus {
  project_id: string;
  project_status: string;
  agents: AgentTaskStatus[];
}
export interface Effects {
  speed?: number;
  volume?: number;
  muted?: boolean;
  brightness?: number;
  contrast?: number;
  saturation?: number;
  filter?: string;
}
export interface StoryEntry {
  id: string;
  position: number;
  narrative_role: string;
  segment_id: string | null;
  clip_id: string | null;
  thumbnail_url: string | null;
  start_ms: number | null;
  end_ms: number | null;
  trim_start_ms: number | null;
  trim_end_ms: number | null;
  transition_in: string;
  edit_reasoning: string | null;
  effects?: Effects | null;
  reframe_params?: ReframeParams | null;
  source_url?: string | null;
}
export interface ReframeParams {
  mode?: "smart" | "center" | "fit" | "manual";
  x?: number;
}
export interface TimelineRow {
  segment_id: string;
  narrative_role?: string;
  transition_in?: string;
  trim_start_ms?: number | null;
  trim_end_ms?: number | null;
  effects?: Effects | null;
  reframe_params?: ReframeParams | null;
  edit_reasoning?: string | null;
}
export interface CaptionSettings {
  enabled: boolean;
  preset: "classic" | "bold" | "karaoke" | "minimal";
  position: "top" | "center" | "bottom";
  font_size: number;
  uppercase: boolean;
  max_chars: number;
  max_lines: number;
  highlight_color: string;
  text_color: string;
  hide_fillers: boolean;
}
export interface EditorSettings {
  captions: CaptionSettings;
  reframe: { mode: "smart" | "center" | "fit" };
}
export interface CaptionWord {
  text: string;
  start_ms: number;
  end_ms: number;
  id: string | null;
}
export interface CaptionCue {
  start_ms: number;
  end_ms: number;
  text: string;
  words: CaptionWord[];
}
export interface TranscriptionStatus {
  available: boolean;
  model: string;
  clips: number;
  transcribed: number;
  words: number;
}
export interface Job {
  id: string;
  kind: string;
  status: "running" | "done" | "failed";
  progress: number;
  message: string;
  result: any;
  error: string | null;
}
export interface CleanupStats {
  removed_ms: number;
  silences_removed: number;
  fillers_removed: number;
  cuts_added: number;
  clips_skipped_no_speech: number;
  removed_words: string[];
  clip_count: number;
}
export interface SubjectTrack {
  source: string;
  points: [number, number, number][];
}
export interface AudioTrack {
  id: string;
  original_filename: string;
  duration_ms: number | null;
  start_ms: number;
  source_offset_ms: number;
  length_ms: number | null;
  volume: number;
  fade_in_ms: number;
  fade_out_ms: number;
  loop: boolean;
  duck_original: number;
  url: string | null;
  analysis?: { bpm: number; beats_ms: number[] } | null;
}
export interface TextOverlay {
  id: string;
  text: string;
  start_ms: number;
  end_ms: number;
  position: "top" | "center" | "bottom";
  font_size: number;
  color: string;
  box: boolean;
}
export interface TimelineVersion {
  id: string;
  label: string;
  entry_count: number;
  created_at: string;
}
export interface StyleAsset {
  id: string;
  kind: "edited" | "raw";
  original_filename: string;
  duration_ms: number | null;
  width: number | null;
  height: number | null;
}
export interface StyleExample {
  id: string;
  title: string;
  status: string;
  analysis: Record<string, any> | null;
  error_message: string | null;
  assets: StyleAsset[];
  created_at: string;
}
export interface StyleProfile {
  id: string;
  name: string;
  description: string | null;
  status: "draft" | "analyzing" | "training" | "ready" | "failed";
  style: Record<string, any> | null;
  metrics: Record<string, any> | null;
  summary: string | null;
  error_message: string | null;
  trained_at: string | null;
  created_at: string;
  example_count: number;
  examples?: StyleExample[] | null;
}
export interface Segment {
  id: string;
  clip_id: string;
  start_ms: number;
  end_ms: number;
  segment_type: string;
  quality_score: number | null;
  engagement_score: number | null;
  has_face: boolean;
  keyframe_url: string | null;
  on_timeline: boolean;
  source_url?: string | null;
}
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  action_json: Record<string, any> | null;
  created_at: string;
}
export interface Output {
  id: string;
  format: string;
  aspect_ratio: string;
  width: number | null;
  height: number | null;
  duration_ms: number | null;
  file_size_bytes: number | null;
  quality_score: number | null;
  download_url: string | null;
  render_metadata: Record<string, any> | null;
}

// ── Health ──────────────────────────────────────────────────────────────────
export async function getHealth(): Promise<HealthStatus | null> {
  try {
    const res = await fetch(`${API_URL}/health`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as HealthStatus;
  } catch {
    return null;
  }
}

// ── Auth ──────────────────────────────────────────────────────────────────────
export async function register(email: string, password: string, fullName?: string) {
  const res = await fetch(`${API_URL}/api/v1/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, full_name: fullName }),
  });
  return handle<{ access_token: string }>(res);
}
export async function login(email: string, password: string) {
  const res = await fetch(`${API_URL}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return handle<{ access_token: string }>(res);
}

// ── Projects ──────────────────────────────────────────────────────────────────
export async function listProjects() {
  const res = await fetch(`${API_URL}/api/v1/projects`, {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handle<Project[]>(res);
}
export async function getProject(id: string) {
  const res = await fetch(`${API_URL}/api/v1/projects/${id}`, {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handle<Project>(res);
}
export async function createProject(title: string, outputFormats: string[], styleProfileId?: string) {
  const res = await fetch(`${API_URL}/api/v1/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      title,
      output_formats: outputFormats,
      ...(styleProfileId ? { target_style: { style_profile_id: styleProfileId } } : {}),
    }),
  });
  return handle<Project>(res);
}
export async function updateProject(
  id: string,
  patch: Partial<{ title: string; target_duration_sec: number | null; target_style: Record<string, any> | null; output_formats: string[] }>
) {
  const res = await fetch(`${API_URL}/api/v1/projects/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(patch),
  });
  return handle<Project>(res);
}
export async function deleteProject(id: string) {
  const res = await fetch(`${API_URL}/api/v1/projects/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  return handle<void>(res);
}

// ── Uploads ─────────────────────────────────────────────────────────────────
export async function uploadClip(projectId: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/uploads/local`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  return handle<Clip>(res);
}
export async function listClips(projectId: string) {
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/uploads`, {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handle<Clip[]>(res);
}

// ── Processing ──────────────────────────────────────────────────────────────
export async function startProcessing(projectId: string, outputFormats?: string[]) {
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/process`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(outputFormats ? { output_formats: outputFormats } : {}),
  });
  return handle<{ project_id: string; celery_task_id: string; message: string }>(res);
}
export async function getPipeline(projectId: string) {
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/pipeline`, {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handle<PipelineStatus>(res);
}
export async function getStory(projectId: string) {
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/story`, {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handle<StoryEntry[]>(res);
}

// ── Manual editing (timeline) ─────────────────────────────────────────────────
export async function getSegments(projectId: string) {
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/segments`, {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handle<Segment[]>(res);
}
export async function addStoryEntry(projectId: string, segmentId: string) {
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/story`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ segment_id: segmentId }),
  });
  return handle<StoryEntry>(res);
}
export async function updateStoryEntry(
  projectId: string,
  entryId: string,
  patch: Partial<{
    trim_start_ms: number;
    trim_end_ms: number;
    transition_in: string;
    effects: Effects | null;
    narrative_role: string;
    reframe_params: ReframeParams | null;
  }>
) {
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/story/${entryId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(patch),
  });
  return handle<StoryEntry>(res);
}
export async function deleteStoryEntry(projectId: string, entryId: string) {
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/story/${entryId}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  return handle<void>(res);
}
export async function reorderStory(projectId: string, entryIds: string[]) {
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/story/reorder`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ entry_ids: entryIds }),
  });
  return handle<StoryEntry[]>(res);
}
export async function renderTimeline(projectId: string, outputFormats?: string[]) {
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/render`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(outputFormats ? { output_formats: outputFormats } : {}),
  });
  return handle<{ total_duration_ms: number; any_failed: boolean; qa_passed: boolean; warnings: string[] }>(res);
}

// ── Editor: advanced timeline ops ─────────────────────────────────────────────
async function jsonCall<T>(method: string, path: string, body?: unknown) {
  const res = await fetch(`${API_URL}/api/v1${path}`, {
    method,
    headers: { ...(body !== undefined ? { "Content-Type": "application/json" } : {}), ...authHeaders() },
    body: body !== undefined ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  return handle<T>(res);
}
async function uploadCall<T>(path: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_URL}/api/v1${path}`, { method: "POST", headers: authHeaders(), body: form });
  return handle<T>(res);
}

export const splitEntry = (pid: string, entryId: string, atMs: number) =>
  jsonCall<StoryEntry[]>("POST", `/projects/${pid}/story/${entryId}/split`, { at_ms: Math.round(atMs) });
export const duplicateEntry = (pid: string, entryId: string) =>
  jsonCall<StoryEntry[]>("POST", `/projects/${pid}/story/${entryId}/duplicate`);
export const joinWithNext = (pid: string, entryId: string) =>
  jsonCall<StoryEntry[]>("POST", `/projects/${pid}/story/${entryId}/join-next`);
export const replaceTimeline = (pid: string, rows: TimelineRow[]) =>
  jsonCall<StoryEntry[]>("PUT", `/projects/${pid}/story`, { rows });
export const addClipRange = (pid: string, clipId: string, startMs?: number, endMs?: number) =>
  jsonCall<StoryEntry>("POST", `/projects/${pid}/story/clip-range`, { clip_id: clipId, start_ms: startMs, end_ms: endMs });
export const importClip = (pid: string, file: File) => uploadCall<Clip>(`/projects/${pid}/media/import`, file);
export const ingestUploaded = (pid: string) =>
  jsonCall<{ clips: number; segments: number }>("POST", `/projects/${pid}/media/ingest`);

export const listVersions = (pid: string) => jsonCall<TimelineVersion[]>("GET", `/projects/${pid}/versions`);
export const saveVersion = (pid: string, label: string) =>
  jsonCall<TimelineVersion>("POST", `/projects/${pid}/versions`, { label });
export const restoreVersion = (pid: string, vid: string) =>
  jsonCall<StoryEntry[]>("POST", `/projects/${pid}/versions/${vid}/restore`);
export const deleteVersion = (pid: string, vid: string) => jsonCall<void>("DELETE", `/projects/${pid}/versions/${vid}`);

export const listAudio = (pid: string) => jsonCall<AudioTrack[]>("GET", `/projects/${pid}/audio`);
export const uploadAudio = (pid: string, file: File) => uploadCall<AudioTrack>(`/projects/${pid}/audio`, file);
export const updateAudio = (pid: string, id: string, patch: Partial<AudioTrack>) =>
  jsonCall<AudioTrack>("PATCH", `/projects/${pid}/audio/${id}`, patch);
export const deleteAudio = (pid: string, id: string) => jsonCall<void>("DELETE", `/projects/${pid}/audio/${id}`);

export const listText = (pid: string) => jsonCall<TextOverlay[]>("GET", `/projects/${pid}/text`);
export const addText = (pid: string, body: Omit<TextOverlay, "id">) =>
  jsonCall<TextOverlay>("POST", `/projects/${pid}/text`, body);
export const updateText = (pid: string, id: string, patch: Partial<TextOverlay>) =>
  jsonCall<TextOverlay>("PATCH", `/projects/${pid}/text/${id}`, patch);
export const deleteText = (pid: string, id: string) => jsonCall<void>("DELETE", `/projects/${pid}/text/${id}`);

export const applyStyle = (pid: string, profileId: string, targetDurationSec?: number) =>
  jsonCall<StoryEntry[]>("POST", `/projects/${pid}/apply-style`, {
    profile_id: profileId,
    ...(targetDurationSec ? { target_duration_sec: targetDurationSec } : {}),
  });

// ── Editing assists: captions, cleanup, beats, reframing ──────────────────────
export const getEditorSettings = (pid: string) => jsonCall<EditorSettings>("GET", `/projects/${pid}/editor-settings`);
export const patchEditorSettings = (
  pid: string,
  patch: { captions?: Partial<CaptionSettings>; reframe?: Partial<EditorSettings["reframe"]> }
) => jsonCall<EditorSettings>("PATCH", `/projects/${pid}/editor-settings`, patch);
export const startTranscribe = (pid: string, force = false) =>
  jsonCall<Job>("POST", `/projects/${pid}/transcribe${force ? "?force=true" : ""}`);
export const getJob = (pid: string, jobId: string) => jsonCall<Job>("GET", `/projects/${pid}/jobs/${jobId}`);
export const listJobs = (pid: string) => jsonCall<Job[]>("GET", `/projects/${pid}/jobs`);
export const getCaptions = (pid: string) =>
  jsonCall<{ cues: CaptionCue[]; status: TranscriptionStatus; settings: CaptionSettings }>("GET", `/projects/${pid}/captions`);
export const editCaptionWords = (pid: string, wordIds: string[], text: string) =>
  jsonCall<{ words: number }>("PUT", `/projects/${pid}/captions/words`, { word_ids: wordIds, text });
export async function downloadCaptions(pid: string, fmt: "srt" | "vtt") {
  const res = await fetch(`${API_URL}/api/v1/projects/${pid}/captions.${fmt}`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`${res.status}`);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = `captions.${fmt}`;
  a.click();
  URL.revokeObjectURL(url);
}
export const runCleanup = (
  pid: string,
  opts: { remove_silence: boolean; min_silence_ms: number; padding_ms: number; remove_fillers: boolean; dry_run: boolean }
) =>
  jsonCall<{ stats: CleanupStats; applied: boolean; timeline?: StoryEntry[]; transcription: TranscriptionStatus }>(
    "POST", `/projects/${pid}/cleanup`, opts
  );
export const analyzeBeats = (pid: string, trackId: string) =>
  jsonCall<{ bpm: number; beat_count: number; downbeat_phase: number }>("POST", `/projects/${pid}/audio/${trackId}/beats`);
export const getBeatGrid = (pid: string, trackId: string, every = 1) =>
  jsonCall<{ beats_ms: number[] }>("GET", `/projects/${pid}/audio/${trackId}/beat-grid?every=${every}`);
export const runBeatSync = (pid: string, trackId: string, every: number | null, dryRun: boolean) =>
  jsonCall<{ stats: { synced: number; unsynced: number; every: number; mean_error_ms: number | null }; bpm: number; applied: boolean; timeline?: StoryEntry[] }>(
    "POST", `/projects/${pid}/beat-sync`, { track_id: trackId, every, dry_run: dryRun }
  );
export const getReframeTracks = (pid: string) =>
  jsonCall<Record<string, SubjectTrack>>("GET", `/projects/${pid}/reframe/tracks`);

// ── Style profiles ────────────────────────────────────────────────────────────
export const listStyles = () => jsonCall<StyleProfile[]>("GET", `/styles`);
export const getStyle = (id: string) => jsonCall<StyleProfile>("GET", `/styles/${id}`);
export const createStyle = (name: string, description?: string) =>
  jsonCall<StyleProfile>("POST", `/styles`, { name, description });
export const deleteStyle = (id: string) => jsonCall<void>("DELETE", `/styles/${id}`);
export const trainStyle = (id: string, reanalyze = false) =>
  jsonCall<StyleProfile>("POST", `/styles/${id}/train${reanalyze ? "?reanalyze=true" : ""}`);
export const deleteStyleExample = (id: string, exampleId: string) =>
  jsonCall<void>("DELETE", `/styles/${id}/examples/${exampleId}`);

/** Upload one training example with progress (XHR — fetch has no upload progress). */
export function addStyleExample(
  id: string,
  edited: File,
  raws: File[],
  title: string,
  onProgress?: (pct: number) => void
): Promise<StyleExample> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("edited", edited);
    raws.forEach((r) => form.append("raw", r));
    if (title) form.append("title", title);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_URL}/api/v1/styles/${id}/examples`);
    const t = getToken();
    if (t) xhr.setRequestHeader("Authorization", `Bearer ${t}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => {
      let body: any = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as StyleExample);
      else reject(new Error(body?.detail || `${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error("Network error during upload"));
    xhr.send(form);
  });
}

// ── Chat ──────────────────────────────────────────────────────────────────────
export async function getChatHistory(projectId: string) {
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/chat`, {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handle<ChatMessage[]>(res);
}
export async function sendChatMessage(projectId: string, message: string) {
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ message }),
  });
  return handle<ChatMessage>(res);
}

// ── Outputs ───────────────────────────────────────────────────────────────────
export async function listOutputs(projectId: string) {
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/outputs`, {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handle<Output[]>(res);
}

// ── WebSocket ─────────────────────────────────────────────────────────────────
export function openPipelineSocket(projectId: string): WebSocket | null {
  const token = getToken();
  if (!token) return null;
  const wsBase = API_URL.replace(/^http/, "ws");
  return new WebSocket(`${wsBase}/ws/projects/${projectId}?token=${token}`);
}
