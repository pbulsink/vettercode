import pytest
import responses

from vettercode.github import GithubClient, GithubError, PER_PAGE, parse_github_time

API = "https://api.github.com"


def repo_item(owner: str, name: str, fork: bool = False) -> dict:
    return {"owner": {"login": owner}, "name": name, "fork": fork}


def issue_item(number: int, updated: str) -> dict:
    return {"number": number, "title": f"t{number}", "body": "b", "updated_at": updated}


@responses.activate
def test_list_repos_paginates_and_includes_forks():
    page1 = [repo_item("me", f"repo{i}") for i in range(PER_PAGE)]
    page2 = [repo_item("me", "last", fork=True), repo_item("me", "final")]
    responses.add(responses.GET, f"{API}/users/me/repos", json=page1, status=200)
    responses.add(responses.GET, f"{API}/users/me/repos", json=page2, status=200)

    repos = GithubClient(token="t").list_repos("me")
    assert len(repos) == PER_PAGE + 2
    assert "me/last" in repos and "me/final" in repos

    responses.add(responses.GET, f"{API}/users/me/repos", json=page1, status=200)
    responses.add(responses.GET, f"{API}/users/me/repos", json=page2, status=200)
    no_forks = GithubClient(token="t").list_repos("me", include_forks=False)
    # page1 (100 non-forks) + "me/final" from page2; fork "me/last" excluded
    assert len(no_forks) == PER_PAGE + 1
    assert "me/last" not in no_forks
    assert "me/final" in no_forks


@responses.activate
def test_list_issues_excludes_prs_and_filters_since():
    items = [
        issue_item(1, "2026-01-01T00:00:00Z"),
        issue_item(2, "2026-09-01T00:00:00Z"),
        {"number": 9, "title": "a PR", "updated_at": "2026-09-02T00:00:00Z", "pull_request": {}},
    ]
    responses.add(responses.GET, f"{API}/repos/o/n/issues", json=items, status=200)

    client = GithubClient(token="tok")
    all_issues = client.list_issues("o", "n")
    assert [i["number"] for i in all_issues] == [1, 2]

    responses.add(responses.GET, f"{API}/repos/o/n/issues", json=items, status=200)
    since = client.list_issues("o", "n", since_iso="2026-08-01T00:00:00+00:00")
    assert [i["number"] for i in since] == [2]


@responses.activate
def test_list_comments_honors_limit():
    comments = [{"user": {"login": f"u{i}"}, "body": f"c{i}"} for i in range(5)]
    responses.add(responses.GET, f"{API}/repos/o/n/issues/3/comments", json=comments, status=200)
    out = GithubClient(token="t").list_comments("o", "n", 3, limit=2)
    assert len(out) == 2

    responses.add(responses.GET, f"{API}/repos/o/n/issues/3/comments", json=comments, status=200)
    assert len(GithubClient(token="t").list_comments("o", "n", 3)) == 5


@responses.activate
def test_post_comment_sends_body():
    responses.add(
        responses.POST,
        f"{API}/repos/o/n/issues/3/comments",
        json={"id": 7},
        status=201,
    )
    out = GithubClient(token="t").post_comment("o", "n", 3, "hello")
    assert out == {"id": 7}
    import json as _json

    sent = _json.loads(responses.calls[0].request.body)
    assert sent == {"body": "hello"}


@responses.activate
def test_error_status_raises_github_error():
    responses.add(responses.GET, f"{API}/repos/o/n/issues", json={"message": "Nope"}, status=404)
    with pytest.raises(GithubError) as excinfo:
        GithubClient(token="t").list_issues("o", "n")
    assert excinfo.value.status == 404


@responses.activate
def test_headers_include_token_only_when_set():
    responses.add(responses.GET, f"{API}/user", json={"login": "me"}, status=200)
    GithubClient(token="sekrit").get_user_login()
    assert responses.calls[0].request.headers.get("Authorization") == "Bearer sekrit"

    responses.add(responses.GET, f"{API}/user", json={"login": "me"}, status=200)
    GithubClient(token=None).get_user_login()
    assert "Authorization" not in responses.calls[1].request.headers


def test_parse_github_time():
    assert parse_github_time("2026-01-02T03:04:05Z").tzinfo is not None
    assert parse_github_time(None) is None
    assert parse_github_time("garbage") is None


@responses.activate
def test_request_with_empty_body_returns_none():
    responses.add(responses.POST, f"{API}/repos/o/n/issues/3/comments", body="", status=204)
    out = GithubClient(token="t").post_comment("o", "n", 3, "hello")
    assert out == {}


def test_utc_now_iso_has_timezone():
    from vettercode.github import GithubClient

    value = GithubClient().utc_now_iso()
    assert value.endswith("+00:00") or value.endswith("Z")
