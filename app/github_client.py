from github import Auth, Github, GithubException

from app import config

# small line under the summary so teammates know a bot wrote it, not you by hand
FOOTER = "<sub>Automated review by pr-auditor</sub>"


def connect():
    return Github(auth=Auth.Token(config.GITHUB_TOKEN))


def get_pull(repo_name, number):
    return connect().get_repo(repo_name, lazy=True).get_pull(number)


def comment_text(f):
    text = f.explanation
    if f.suggestion:
        # github shows this as a "Commit suggestion" button that swaps in the new code
        text += f"\n\n```suggestion\n{f.suggestion.rstrip()}\n```"
    if f.also_needed:
        text += f"\n\n{f.also_needed}"
    return text


def as_comment(f):
    c = {"path": f.path, "line": f.line, "side": "RIGHT", "body": comment_text(f)}
    if f.start_line < f.line:
        c["start_line"] = f.start_line
        c["start_side"] = "RIGHT"
    return c


def summary_text(summary, general, skipped):
    parts = [summary]
    if general:
        parts.append("A couple more things that didn't fit on a specific line:\n"
                     + "\n".join(f"- `{f.path}` (line {f.line}): {f.explanation}" for f in general))
    if skipped:
        parts.append("I didn't look at " + ", ".join(f"`{p}`" for p in skipped) + " (deleted, binary, generated or too big).")
    parts.append(FOOTER)
    return "\n\n".join(parts)


def post_review(pr, sha, summary, inline, general, skipped):
    # skip lines we already commented on, so a new push doesn't repeat the same comments
    me = connect().get_user().login
    done = {(c.path, c.line) for c in pr.get_review_comments() if c.user.login == me and c.line}
    new = [f for f in inline if (f.path, f.line) not in done]
    if inline and not new and not general:
        return 0

    commit = pr.base.repo.get_commit(sha)
    # just comments. a bot shouldn't approve or block a PR, and github refuses both on your own PR anyway
    try:
        pr.create_review(commit=commit, body=summary_text(summary, general, skipped), event="COMMENT",
                         comments=[as_comment(f) for f in new])
    except GithubException as e:
        if e.status != 422 or not new:
            raise
        # github turned down one of the lines (maybe a new push landed meanwhile), so post everything as one comment
        pr.create_review(commit=commit, body=summary_text(summary, general + new, skipped), event="COMMENT")
    return len(new)


def post_note(pr, text):
    pr.create_issue_comment(f"{text}\n\n{FOOTER}")
