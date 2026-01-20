"""
Shared WS mutation freeze flag.

Purpose:
- Signal router sets this to True during order placement
- WS engine reads this flag to avoid WS control mutations
"""

WS_MUTATION_FROZEN = False
