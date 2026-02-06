from collections import defaultdict


# FIXME: It still tries to analyze submissions that has not been graded
def analyze_question_difficulty(submissions):
    question_scores = defaultdict(list)

    for submission in submissions:
        if not submission.answers:
            continue

        for q_id, q_data in submission.answers.items():
            score = q_data.get("score")
            if score is not None:
                question_scores[q_id].append(score)

    if not question_scores:
        return [], []

    question_averages = {
        q: sum(scores) / len(scores) for q, scores in question_scores.items()
    }

    hardest = sorted(question_averages.items(), key=lambda x: x[1])[:2]
    easiest = sorted(question_averages.items(), key=lambda x: x[1], reverse=True)[:2]

    return hardest, easiest
