"""
End-to-end: captions, silence/filler removal, beat sync, smart reframing and chat
commands against a RUNNING API server (LOCAL_MODE), using generated media with
known ground truth (tests/fixtures/make_media.py).

    API_URL=http://localhost:8000 python -m pytest tests/integration/test_assist_api.py -s
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.fixtures.make_media import make_all  # noqa: E402

API = os.environ.get("API_URL", "http://localhost:8000")
GEN = ROOT / "test_clips" / "generated"


def _server_up() -> bool:
    try:
        return httpx.get(f"{API}/health", timeout=3).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _server_up(), reason="API server not running")


@pytest.fixture(scope="module")
def media():
    return make_all(GEN)


@pytest.fixture(scope="module")
def c():
    client = httpx.Client(base_url=f"{API}/api/v1", timeout=900)
    tok = client.post("/auth/register", json={"email": f"assist_{int(time.time() * 1000)}@test.com",
                                              "password": "password123"}).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {tok}"
    return client


def ok(r, code=200):
    assert r.status_code == code, f"{r.request.method} {r.request.url} → {r.status_code}: {r.text[:600]}"
    return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else r.text


def upload(c, path, url, name=None):
    with open(path, "rb") as f:
        return c.post(url, files={"file": (name or path.name, f, "application/octet-stream")})


def wait_job(c, pid, job, timeout=600):
    for _ in range(timeout):
        j = ok(c.get(f"/projects/{pid}/jobs/{job['id']}"))
        if j["status"] != "running":
            return j
        time.sleep(1)
    raise AssertionError("job timed out")


@pytest.fixture(scope="module")
def project(c, media):
    if not media["speech"]:
        pytest.skip("no text-to-speech engine available to generate speech")
    pid = ok(c.post("/projects", json={"title": "Assist", "output_formats": ["reels"]}), 201)["id"]
    speech = ok(upload(c, media["speech"], f"/projects/{pid}/media/import"), 201)
    moving = ok(upload(c, media["moving"], f"/projects/{pid}/media/import"), 201)
    ok(c.post(f"/projects/{pid}/story/clip-range", json={"clip_id": speech["id"]}), 201)
    ok(c.post(f"/projects/{pid}/story/clip-range", json={"clip_id": moving["id"]}), 201)
    return {"pid": pid, "speech": speech, "moving": moving}


def test_captions_from_speech(c, project):
    pid = project["pid"]
    job = ok(c.post(f"/projects/{pid}/transcribe"), 202)
    j = wait_job(c, pid, job)
    assert j["status"] == "done", j
    cap = ok(c.get(f"/projects/{pid}/captions"))
    text = " ".join(q["text"] for q in cap["cues"]).lower()
    assert "welcome back to the channel" in text and "lake" in text
    assert " um" not in f" {text}"                                        # fillers hidden by default
    assert cap["status"]["transcribed"] == 2
    # fix a word: "channel." → "vlog."
    first = cap["cues"][0]
    word = next(w for w in first["words"] if w["text"].lower().startswith("channel"))
    ok(c.put(f"/projects/{pid}/captions/words", json={"word_ids": [word["id"]], "text": "vlog."}))
    assert "vlog." in ok(c.get(f"/projects/{pid}/captions"))["cues"][0]["text"]
    assert "-->" in ok(c.get(f"/projects/{pid}/captions.srt"))
    s = ok(c.patch(f"/projects/{pid}/editor-settings", json={"captions": {"enabled": True, "preset": "bold"}}))
    assert s["captions"]["enabled"] and s["reframe"]["mode"] == "smart"


def test_silence_and_filler_removal(c, project):
    pid = project["pid"]
    before = ok(c.get(f"/projects/{pid}/story"))
    dry = ok(c.post(f"/projects/{pid}/cleanup", json={"dry_run": True}))["stats"]
    assert dry["fillers_removed"] >= 2 and dry["silences_removed"] >= 2
    assert dry["removed_ms"] > 3500
    assert dry["clips_skipped_no_speech"] >= 1                           # the silent moving-box clip is untouched
    applied = ok(c.post(f"/projects/{pid}/cleanup", json={"dry_run": False}))
    assert applied["applied"] and len(applied["timeline"]) > len(before)
    text = " ".join(q["text"] for q in ok(c.get(f"/projects/{pid}/captions"))["cues"]).lower()
    assert "hiking" in text and "amazing" in text                        # speech survived, captions follow cuts
    versions = ok(c.get(f"/projects/{pid}/versions"))
    assert any("silences" in v["label"] for v in versions)


def test_beat_sync(c, project, media):
    pid = project["pid"]
    track = ok(upload(c, media["click"], f"/projects/{pid}/audio", "beat.wav"), 201)
    beats = ok(c.post(f"/projects/{pid}/audio/{track['id']}/beats"))
    assert abs(beats["bpm"] - 120) < 1.5
    grid = ok(c.get(f"/projects/{pid}/audio/{track['id']}/beat-grid"))["beats_ms"]
    assert grid and abs(grid[1] - grid[0] - 500) < 25
    res = ok(c.post(f"/projects/{pid}/beat-sync", json={"track_id": track["id"], "every": 1, "dry_run": False}))
    assert res["applied"] and res["stats"]["synced"] >= 2
    assert res["stats"]["mean_error_ms"] is not None and res["stats"]["mean_error_ms"] < 40


def test_chat_commands(c, project):
    pid = project["pid"]
    n = len(ok(c.get(f"/projects/{pid}/story")))
    r = ok(c.post(f"/projects/{pid}/chat", json={"message": "make the last clip black and white"}))
    assert "black & white" in r["content"] and r["action_json"]
    assert ok(c.get(f"/projects/{pid}/story"))[-1]["effects"]["filter"] == "bw"
    r = ok(c.post(f"/projects/{pid}/chat", json={"message": "speed up clip 1 to 1.5x; use crossfade transitions everywhere"}))
    story = ok(c.get(f"/projects/{pid}/story"))
    assert story[0]["effects"]["speed"] == 1.5 and all(e["transition_in"] == "cross_fade" for e in story[1:])
    r = ok(c.post(f"/projects/{pid}/chat", json={"message": "karaoke captions"}))
    assert ok(c.get(f"/projects/{pid}/editor-settings"))["captions"]["preset"] == "karaoke"
    r = ok(c.post(f"/projects/{pid}/chat", json={"message": "remove clip 99"}))
    assert "no clip 99" in r["content"].lower() and len(ok(c.get(f"/projects/{pid}/story"))) == n
    r = ok(c.post(f"/projects/{pid}/chat", json={"message": "what's the weather"}))
    assert "didn't understand" in r["content"].lower()


def test_render_with_reframe_and_captions(c, project):
    pid = project["pid"]
    ok(c.post(f"/projects/{pid}/chat", json={"message": "bold captions"}))
    tracks = ok(c.get(f"/projects/{pid}/reframe/tracks"))
    mv = tracks[project["moving"]["id"]]
    assert mv["source"] == "interest" and mv["points"][0][1] < 0.3 < 0.7 < mv["points"][-1][1]
    r = ok(c.post(f"/projects/{pid}/render", json={}))
    assert not r["any_failed"] and r["warnings"] == [], r
    out = ok(c.get(f"/projects/{pid}/outputs"))[0]
    assert out["width"] == 1080 and out["height"] == 1920 and out["download_url"]
