import json
import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app import config, diff, github_client, reviewer
from app.security import valid_signature

config.check()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pr-auditor")

# no public API docs page, github is the only real caller
app = FastAPI(title="PR Auditor", docs_url=None, redoc_url=None, openapi_url=None)

# pull_request actions that mean there's new code to look at
REVIEW_ACTIONS = {"opened", "reopened", "synchronize", "ready_for_review"}
# people with a role in the repo. keeps strangers from spending our claude credits with a PR
TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}


@app.get("/")
def home():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request, background: BackgroundTasks):
    # check the signature on the raw bytes before trusting anything in the body
    body = await request.body()
    if not valid_signature(body, request.headers.get("X-Hub-Signature-256"), config.GITHUB_WEBHOOK_SECRET):
        raise HTTPException(401, "bad signature")

    event = request.headers.get("X-GitHub-Event")
    if event == "ping":
        return {"msg": "pong"}
    if event != "pull_request":
        return {"msg": f"ignoring {event} event"}

    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "body isn't JSON, set the webhook content type to application/json")

    action = payload.get("action")
    pr = payload["pull_request"]
    if action not in REVIEW_ACTIONS:
        return {"msg": f"ignoring action {action}"}
    if pr.get("draft"):
        return {"msg": "ignoring draft PR"}
    if pr.get("author_association") not in TRUSTED:
        return {"msg": "ignoring PR from outside the repo"}

    repo_name = payload["repository"]["full_name"]
    # github gives up after 10 seconds, so answer now and review afterwards
    # TODO: skip redelivered events (same X-GitHub-Delivery) and overlapping reviews of one PR
    background.add_task(review_pr, repo_name, pr["number"])
    return JSONResponse({"msg": f"reviewing {repo_name}#{pr['number']}"}, status_code=202)


def review_pr(repo_name, number):
    # plain def, so fastapi runs it in a worker thread and the slow calls don't block the server
    try:
        pr = github_client.get_pull(repo_name, number)
        sha = pr.head.sha  # the exact version we're reviewing, so comments land on the right code
        diff_text, commentable, skipped = diff.build(pr.get_files())
        if not diff_text:
            log.info("%s#%s: nothing to review (left out: %s)", repo_name, number, ", ".join(skipped) or "none")
            return
        log.info("%s#%s: asking claude to review %d files", repo_name, number, len(commentable))
        result = reviewer.review(pr.title, pr.body, diff_text, skipped)
        inline, general = diff.split_findings(result.findings, commentable)
        for f in inline + general:
            log.info("  [%s] %s:%s %s (%s)", f.severity, f.path, f.line, f.title, f.category)
        posted = github_client.post_review(pr, sha, result.summary, inline, general, skipped)
        log.info("%s#%s: %d findings, %d new line comments posted", repo_name, number, len(result.findings), posted)
    except reviewer.ReviewError as e:
        log.warning("%s#%s: review failed: %s", repo_name, number, e)
        github_client.post_note(pr, f"Couldn't run the automatic security review this time: {e}. Worth giving this one a manual look.")
    except Exception:
        log.exception("review of %s#%s failed", repo_name, number)
