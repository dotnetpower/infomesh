# Search Peer ID Output and BGM Stability

## Summary

Plain-text distributed search output now shows a `Peer:` line when a result comes from a known peer. Dashboard BGM playback now prefers `mpv` with gapless looping and audio buffering, and crawl idle auto-stop is opt-in by default to avoid stop/start interruptions during intermittent crawl activity.

## User Impact

- Remote search results are easier to trace back to the serving peer.
- BGM is less likely to stutter or restart when both `mpv` and `ffplay` are installed.
- Users who want BGM to pause when crawling is idle can set `bgm_idle_stop = true`.

## Verification

- Added formatter coverage for peer ID display.
- Added dashboard BGM coverage for `mpv` preference, gapless buffering flags, and the idle-stop default.
