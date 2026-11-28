"""The shared writing rules every pre-scan prompt carries.

The owner reads these analyses as a smart private investor, not an industry
specialist. Output written for a specialist (unexplained
abbreviations, lines crammed with numbers) is unreadable to him, so every
prompt in streamlit_app.DEFAULT_AI_PROMPTS and the card modules includes this
block after its context and before its output template.
"""

HOW_TO_WRITE = """HOW TO WRITE
- Reader: a smart private investor who is not an expert in this industry.
- Call brands, products and segments by their own names; the reader
  knows them.
- No unexplained abbreviations. Explain a necessary term in a few words
  the first time; otherwise leave it out.
- At most one or two numbers per line; pick the one that proves the point
  and round it ($8.4B, about a third).
- Answer the section's question first, in plain words; the bullets say WHY.
- Don't repeat facts owned by another section unless they change this answer.
- Label every figure's basis and period (GAAP/adjusted, FY/quarter).
- If the framework doesn't fit this business, say so in one line; don't force it.
"""
