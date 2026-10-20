"""Conservative checks for reactions that contain no instruction or replacement.

These catch a narrow, explicit pattern; they are not a general intent classifier.
"""
import re


def reaction_without_request(text: str | None) -> bool:
    words = (text or '').casefold().replace('’', "'")
    reaction = re.search(r"\b(?:again|seriously|really)\s*[!?]", words)
    request = re.search(r"\b(?:add|record|note|save|log|put|mark|set|change|switch|replace|vote|choose|pick|order|bring|remove|"
                        r"let'?s|how about|what if|we could|maybe|can (?:you|u|we)|could (?:you|u|we)|please|"
                        r"i(?:'ll| will)|i(?:'d| would) (?:like|prefer|rather)|i (?:prefer|want)|instead)\b", words)
    return bool(reaction and not request)
