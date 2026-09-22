"""
Editing intelligence on top of the timeline (all pure planners + thin services):

    layout.py     sequence timing of timeline entries (mirrors the renderer exactly)
    settings.py   per-project editor settings (captions style, reframing)
    captions.py   transcript words → timed caption cues → SRT / VTT / styled ASS
    cleanup.py    silence + filler-word removal plan
    beatsync.py   snap cut points to the music's beat grid
    reframe.py    subject tracking + moving crop for aspect-ratio changes
    commands.py   natural-language editing commands (rules, or Claude when configured)
    service.py    async orchestration + DB persistence for the above
"""
