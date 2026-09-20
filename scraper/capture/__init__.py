"""Forward-capture subsystem (NEET v2, WS-1).

Continuously captures raw civic evidence (official feeds, sensors, RSS, Bluesky) into an
immutable, timestamped store so that a later test window has complete evidence.
Entry point: ``neet capture`` (see ``scraper.capture.cli``).
"""
