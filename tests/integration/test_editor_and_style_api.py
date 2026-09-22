"""
End-to-end check of the manual editor + editor-style learning against a RUNNING
API server (LOCAL_MODE) using the clips in test_clips/.

    API_URL=http://localhost:8000 python -m pytest tests/integration -s

Skipped automatically when no server is reachable.
"""
import os
import time
from pathlib import Path

import httpx
import pytest

API = os.environ.get("API_URL", "http://localhost:8000")
TC = Path(__file__).resolve().parents[2] / "test_clips"


def _server_up() -> bool:
    try:
        return httpx.get(f"{API}/health", timeout=3).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _server_up() or not TC.exists(), reason="API server or test_clips missing")


@pytest.fixture(scope="module")
def c():
    client = httpx.Client(base_url=API, timeout=600)
    tok = client.post("/api/v1/auth/register", json={
        "email": f"editor_{int(time.time() * 1000)}@test.com", "password": "password123"}).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {tok}"
    return client


def _ok(r, code=200):
    assert r.status_code == code, f"{r.request.method} {r.request.url} → {r.status_code}: {r.text[:500]}"
    return r.json() if r.content else None


def _upload(c, path, url):
    with open(path, "rb") as f:
        return c.post(url, files={"file": (path.name, f, "video/mp4")})


def test_manual_editor_flow(c):
    pid = _ok(c.post("/api/v1/projects", json={"title": "Manual", "output_formats": ["reels"]}), 201)["id"]
    base = f"/api/v1/projects/{pid}"

    # import clips mid-edit → instantly ingested (no AI pipeline)
    clips = [_ok(_upload(c, TC / f"raw_0{i}.mp4", f"{base}/media/import"), 201) for i in (1, 2, 3)]
    assert all(cl["duration_ms"] and cl["source_url"] for cl in clips)
    segs = _ok(c.get(f"{base}/segments"))
    assert segs and all(s["source_url"] for s in segs)

    # whole clip + a detected segment on the timeline
    e1 = _ok(c.post(f"{base}/story/clip-range", json={"clip_id": clips[0]["id"]}), 201)
    _ok(c.post(f"{base}/story", json={"segment_id": segs[-1]["id"]}), 201)
    tl = _ok(c.get(f"{base}/story"))
    assert len(tl) == 2 and tl[0]["source_url"]

    # razor split, then stitch back
    mid = (e1["trim_start_ms"] + e1["trim_end_ms"]) // 2
    tl = _ok(c.post(f"{base}/story/{e1['id']}/split", json={"at_ms": mid}))
    assert len(tl) == 3 and tl[0]["trim_end_ms"] == mid and tl[1]["trim_start_ms"] == mid
    _ok(c.post(f"{base}/story/{tl[0]['id']}/split", json={"at_ms": tl[0]["trim_start_ms"]}), 400)  # edge
    tl = _ok(c.post(f"{base}/story/{tl[0]['id']}/join-next"))
    assert len(tl) == 2 and tl[0]["trim_end_ms"] == e1["trim_end_ms"]
    _ok(c.post(f"{base}/story/{tl[0]['id']}/join-next"), 400)  # different source clips

    # duplicate + effects + transition
    snapshot = [{k: e[k] for k in ("segment_id", "narrative_role", "transition_in", "trim_start_ms",
                                   "trim_end_ms", "effects")} for e in tl]
    tl = _ok(c.post(f"{base}/story/{tl[1]['id']}/duplicate"))
    assert len(tl) == 3
    e = _ok(c.patch(f"{base}/story/{tl[1]['id']}", json={
        "effects": {"speed": 9, "saturation": 1.3, "filter": "warm"}, "transition_in": "dissolve"}))
    assert e["effects"]["speed"] == 4.0 and e["effects"]["filter"] == "warm" and e["transition_in"] == "dissolve"
    e = _ok(c.patch(f"{base}/story/{tl[1]['id']}", json={"trim_start_ms": -5000}))
    assert e["trim_start_ms"] == e["start_ms"]  # clamped to the segment

    # versions + undo via whole-timeline replace
    v = _ok(c.post(f"{base}/versions", json={"label": "three clips"}), 201)
    tl = _ok(c.put(f"{base}/story", json={"rows": snapshot}))
    assert len(tl) == 2
    tl = _ok(c.post(f"{base}/versions/{v['id']}/restore"))
    assert len(tl) == 3 and tl[1]["effects"]["filter"] == "warm"
    assert any("Before restoring" in x["label"] for x in _ok(c.get(f"{base}/versions")))

    # music bed (raw_05 has an audio track) + text overlay
    with open(TC / "raw_05.mp4", "rb") as f:
        track = _ok(c.post(f"{base}/audio", files={"file": ("song.mp4", f, "video/mp4")}), 201)
    track = _ok(c.patch(f"{base}/audio/{track['id']}", json={"volume": 0.5, "duck_original": 0.3, "loop": True}))
    assert track["loop"] and track["duck_original"] == 0.3
    with open(TC / "raw_01.mp4", "rb") as f:  # silent video → rejected as audio
        _ok(c.post(f"{base}/audio", files={"file": ("silent.mp4", f, "video/mp4")}), 400)
    _ok(c.post(f"{base}/text", json={"text": "Hello: world", "start_ms": 0, "end_ms": 2000}), 201)
    _ok(c.post(f"{base}/text", json={"text": "bad", "start_ms": 3000, "end_ms": 1000}), 400)

    # real render honoring everything above
    r = _ok(c.post(f"{base}/render", json={}))
    assert not r["any_failed"], r
    assert r["warnings"] == [], r["warnings"]
    out = _ok(c.get(f"{base}/outputs"))[0]
    assert out["download_url"] and out["render_metadata"]["real_render"]


def test_style_training_and_apply(c):
    prof = _ok(c.post("/api/v1/styles", json={"name": "Pipeline cut", "description": "test"}), 201)
    sid = prof["id"]
    raws = sorted(TC.glob("raw_*.mp4"))
    files = [("edited", ("FINAL_youtube.mp4", open(TC / "FINAL_youtube.mp4", "rb"), "video/mp4"))]
    files += [("raw", (p.name, open(p, "rb"), "video/mp4")) for p in raws]
    try:
        ex = _ok(c.post(f"/api/v1/styles/{sid}/examples", files=files, data={"title": "Trip"}), 201)
    finally:
        for _, (_, fh, _) in files:
            fh.close()
    assert len(ex["assets"]) == 1 + len(raws) and ex["assets"][0]["kind"] == "edited"

    _ok(c.post(f"/api/v1/styles/{sid}/train"), 202)
    _ok(c.post(f"/api/v1/styles/{sid}/train"), 409)  # already running
    for _ in range(300):
        prof = _ok(c.get(f"/api/v1/styles/{sid}"))
        if prof["status"] in ("ready", "failed"):
            break
        time.sleep(1)
    assert prof["status"] == "ready", prof.get("error_message")
    assert prof["style"]["alignment_coverage"] > 0.8
    assert prof["summary"] and "Pacing" in prof["summary"]
    assert prof["examples"][0]["analysis"]["shots"] and "samples" not in prof["examples"][0]["analysis"]
    print("\nLEARNED STYLE:\n" + prof["summary"])

    # apply the learned style to a fresh project
    pid = _ok(c.post("/api/v1/projects", json={"title": "Styled", "output_formats": ["youtube"]}), 201)["id"]
    base = f"/api/v1/projects/{pid}"
    for i in (6, 7, 8, 9):
        _ok(_upload(c, TC / f"raw_0{i}.mp4", f"{base}/media/import"), 201)
    tl = _ok(c.post(f"{base}/apply-style", json={"profile_id": sid}))
    assert tl and all("Style 'Pipeline cut'" in e["edit_reasoning"] for e in tl)
    assert tl[0]["transition_in"] == "cut"
    proj = _ok(c.get(base))
    assert proj["target_style"]["style_profile_id"] == sid
    r = _ok(c.post(f"{base}/render", json={}))
    assert not r["any_failed"]


def test_pipeline_uses_learned_style(c):
    profs = _ok(c.get("/api/v1/styles"))
    ready = [p for p in profs if p["status"] == "ready"]
    if not ready:
        pytest.skip("run test_style_training_and_apply first")
    pid = _ok(c.post("/api/v1/projects", json={
        "title": "AI + style", "output_formats": ["reels"],
        "target_style": {"style_profile_id": ready[0]["id"]}}), 201)["id"]
    base = f"/api/v1/projects/{pid}"
    for i in (1, 2, 3, 4):
        _ok(_upload(c, TC / f"raw_0{i}.mp4", f"{base}/uploads/local"))
    _ok(c.post(f"{base}/process", json={}))
    for _ in range(600):
        st = _ok(c.get(f"{base}/pipeline"))["project_status"]
        if st in ("completed", "failed"):
            break
        time.sleep(1)
    assert st == "completed"
    story = _ok(c.get(f"{base}/story"))
    assert story and "Style '" in story[0]["edit_reasoning"]
