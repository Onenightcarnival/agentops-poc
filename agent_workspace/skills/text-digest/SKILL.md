---
name: text-digest
metadata:
  id: agentops.text-digest
  version: "1.0.0"
description: Count characters and whitespace-separated words in a supplied text.
---

Follow this workflow for the original text supplied by the user:

1. Identify the exact text to inspect; do not include surrounding quotation marks,
   explanatory punctuation, or the user's instructions unless they are part of it.
2. Count characters, including spaces. Count words separated by whitespace.
3. Report the original text, character count, and word count concisely.
4. If the text is missing or ambiguous, ask for clarification instead of guessing.

This is an instruction-based demo for short text. Follow the steps directly using
the native agent workflow; there is no separate skill dispatcher. Do not claim
that a program produced or verified the counts.
