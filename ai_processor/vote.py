"""The vote that names the model of an AI step (BE-I-04, shared).

One home for the rule. BE-I-04's grading run (`ai_processor/grading_run.py`)
and the other steps' runs (`ai_processor/step_run.py`) both call this, so
they cannot drift apart.

The rule: the model with the most votes wins. On a tie, the main model if it
is among the leaders; else the first by plain alphabetical order of the
provider's exact text; a model the provider did not name (None) loses every
tie to a named one. It depends only on the counts, never on the order of
arrival.

Imports nothing from the project.
"""


def majority_model(votes, main):
    """The winner among `votes` ({model or None: count}), or None.

    None means there is no named winner: no votes at all, or every leader
    is an unnamed model. A model with no votes (a zero or negative count)
    is not a leader. The caller decides which word None reads as.
    """
    counts = {model: count for model, count in votes.items() if count > 0}
    if not counts:
        return None
    most = max(counts.values())
    leaders = [model for model, count in counts.items() if count == most]
    named = sorted(model for model in leaders if model is not None)
    if main in named:
        return main
    if named:
        return named[0]
    return None
