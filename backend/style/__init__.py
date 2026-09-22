"""
Editor style learning — "teach the AI how *you* edit".

An editor uploads finished edits together with the raw footage they were cut
from. For each pair we recover every editing decision by aligning the edit
back onto the raw clips (perceptual frame hashes), then learn from them:

    media.py     decode frames / audio with ffmpeg, perceptual hashes, borders
    features.py  per-window shot features (shared by training AND inference)
    align.py     edited → raw alignment: kept ranges, speed, transitions, color, audio
    analyzer.py  one example → analysis + labeled training samples
    learner.py   standardizer, logistic-regression selector, kNN retrieval memory
    profile.py   aggregate style across examples, human-readable summary
    planner.py   new footage + learned style → timeline rows
    service.py   async orchestration + DB persistence

Everything runs on CPU with numpy/OpenCV/ffmpeg; no GPU or cloud needed.
"""
