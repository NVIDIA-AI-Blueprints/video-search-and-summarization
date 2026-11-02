# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Opt-in person-image versus scene/motion selection for raw benchmarks."""

import re

_PERSON_VIEWPOINT = re.compile(
    r"(?:(?:seen|viewed|shown|captured|visible) )?"
    r"(?:(?:from |in |a |the )*(?:front|back|rear|side)(?: view| profile| facing)"
    r"|from (?:behind|(?:the )?(?:front|back|side)))",
    re.IGNORECASE,
)


# Normalize only inflections of the five already supported states. A phrase
# such as "walking a dog" remains unknown and cannot lose its interaction.
_STATE_FORMS = {
    form: base
    for base, forms in {
        "stand": ("stand", "stands", "standing"),
        "sit": ("sit", "sits", "sitting"),
        "lie": ("lie", "lies", "lying"),
        "walk": ("walk", "walks", "walking"),
        "run": ("run", "runs", "running"),
    }.items()
    for form in forms
}


_ACTION_QUERY_WORDS = frozenset(_STATE_FORMS) | frozenset(
    [
        "kneel",
        "kneels",
        "kneeling",
        "crouch",
        "crouches",
        "crouching",
        "bend",
        "bends",
        "bending",
        "lean",
        "leans",
        "leaning",
    ]
)
_ACTION_QUERY_GRAMMAR = _ACTION_QUERY_WORDS | frozenset(
    [
        "person",
        "someone",
        "somebody",
        "individual",
        "man",
        "woman",
        "boy",
        "girl",
        "child",
        "human",
        "male",
        "female",
        "adult",
        "group",
        "crowd",
        "people",
        "persons",
        "men",
        "women",
        "boys",
        "girls",
        "children",
        "individuals",
        "two",
        "three",
        "four",
        "five",
        "several",
        "multiple",
        "many",
        "both",
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "being",
        "who",
        "that",
        "just",
        "only",
        "still",
        "up",
        "down",
        "around",
        "forward",
        "forwards",
        "backward",
        "backwards",
        "stationary",
        "slowly",
        "quickly",
        "fast",
        "in",
        "place",
        "together",
    ]
)


def _action_only_query(query: str) -> bool:
    # Absence from an appearance dictionary is not evidence of an action-only
    # request: unknown garments, body traits, numerals and non-English text
    # must retain the existing expert decision.
    if re.search(r"[^A-Za-z\s.!?]", query):
        return False
    words = set(re.findall(r"\w+", query.casefold()))
    return bool(words & _ACTION_QUERY_WORDS) and words <= _ACTION_QUERY_GRAMMAR


def person_state_route(decomposition, original_query: str | None = None):
    """Validate the extraction contract and choose an expert without GT inputs.

    The person-image expert handles an isolated person's appearance and five
    ordinary states. When original text is available, recognized action-only
    requests use the scene/motion expert. Unknown source requirements retain
    the existing decision. External relations, other subjects and unknown
    action verbs still use embedding.
    """
    if type(decomposition.get("person_subject")) is not bool:
        raise ValueError("person-state requires boolean person_subject")
    for field in ("actions", "scene_relations"):
        value = decomposition.get(field)
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise ValueError(
                f"person-state requires {field} as nonempty strings in an array"
            )
    caption = decomposition.get("attribute_query")
    if not isinstance(caption, str) or not caption.strip():
        raise ValueError("person-state requires nonempty attribute_query")
    # The model sometimes puts camera viewpoint into scene_relations. A whole
    # viewpoint phrase is person appearance; preserve any actual external
    # context. This never removes viewpoint words from the retrieval query.
    has_external_scene = any(
        not _PERSON_VIEWPOINT.fullmatch(" ".join(item.strip().rstrip(".").split()))
        for item in decomposition["scene_relations"]
    )
    return (
        "attribute"
        if decomposition["person_subject"]
        and not has_external_scene
        and all(
            action.strip().lower() in _STATE_FORMS
            for action in decomposition["actions"]
        )
        and not (original_query is not None and _action_only_query(original_query))
        else "embed"
    )
