"""Optional enrichment of codemap's own graph from another engine's index.

codemap's symbols and keys stay the source of truth: an engine here only *adds*
call links (and route links) between symbols codemap already knows, so nothing
the course skill authored against a key ever moves.
"""
