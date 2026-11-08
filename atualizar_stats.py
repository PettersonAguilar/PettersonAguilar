#!/usr/bin/env python3
"""Gera as estatisticas do perfil e escreve no README.md.

Roda dentro do GitHub Actions, usa apenas a biblioteca padrao do Python
e a API oficial do GitHub. Nao depende de servicos de terceiros.
"""

import json
import math
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

USER = os.environ.get("GH_USER", "PettersonAguilar")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
README = os.environ.get("README_PATH", "README.md")
API = "https://api.github.com"

START = "<!--STATS_START-->"
END = "<!--STATS_END-->"


# ----------------------------------------------------------------- API
def _req(url, payload=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "readme-stats-bot",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def paginated(url):
    items, page = [], 1
    while page <= 10:
        sep = "&" if "?" in url else "?"
        chunk = _req(f"{url}{sep}per_page=100&page={page}")
        if not chunk:
            break
        items.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return items


def fetch_user():
    return _req(f"{API}/users/{USER}")


def fetch_repos():
    repos = paginated(f"{API}/users/{USER}/repos?type=owner")
    return [r for r in repos if not r.get("fork")]


def fetch_languages(repos):
    totals = {}
    for repo in repos[:60]:
        try:
            langs = _req(repo["languages_url"])
        except Exception as exc:  # um repo com erro nao derruba tudo
            print(f"aviso: linguagens de {repo.get('name')}: {exc}")
            continue
        for lang, size in langs.items():
            totals[lang] = totals.get(lang, 0) + size
    return totals


QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      totalCommitContributions
      totalIssueContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def fetch_contributions():
    data = _req(f"{API}/graphql", {"query": QUERY, "variables": {"login": USER}})
    return data["data"]["user"]["contributionsCollection"]


# ------------------------------------------------------------- calculos
def streaks(days):
    """days: lista de (data, contribuicoes) em ordem cronologica."""
    longest = run = 0
    for _, count in days:
        run = run + 1 if count > 0 else 0
        longest = max(longest, run)
    current = 0
    for idx, (_, count) in enumerate(reversed(days)):
        if count > 0:
            current += 1
        elif idx == 0:
            continue  # hoje ainda sem commit nao quebra a sequencia
        else:
            break
    return current, longest


def sparkline(weeks, size=26):
    totals = [sum(d["contributionCount"] for d in w["contributionDays"]) for w in weeks]
    totals = totals[-size:]
    blocks = "▁▂▃▄▅▆▇█"
    top = max(totals) if totals else 0
    if top == 0:
        return blocks[0] * len(totals)
    return "".join(blocks[min(7, int(v / top * 7 + 0.5))] for v in totals)


def bar(pct, size=20):
    filled = max(0, min(size, round(size * pct / 100)))
    return "█" * filled + "░" * (size - filled)


def num(n):
    return f"{n:,}".replace(",", ".")


def level_info(xp):
    level = int(math.sqrt(xp / 50)) + 1
    floor_xp = 50 * (level - 1) ** 2
    next_xp = 50 * level ** 2
    pct = int((xp - floor_xp) / (next_xp - floor_xp) * 100)
    if level < 3:
        title = "Novato"
    elif level < 6:
        title = "Aprendiz"
    elif level < 10:
        title = "Desenvolvedor"
    elif level < 15:
        title = "Veterano"
    else:
        title = "Mestre"
    return level, title, pct


# --------------------------------------------------------------- render
def render(s):
    out = ['<div align="center">', "", f"### Nível {s['level']} · {s['title']}", ""]
    out.append(
        f"`{bar(s['pct'])}` **{s['pct']}%** para o nível {s['level'] + 1} "
        f"· **{num(s['xp'])} XP**"
    )
    out += ["", "</div>", ""]

    out += [
        "| Estatística | Valor |",
        "|:--|--:|",
        f"| Repositórios públicos | **{num(s['repos'])}** |",
        f"| Commits no último ano | **{num(s['commits'])}** |",
        f"| Sequência atual | **{s['cur_streak']} dias** |",
        f"| Maior sequência (último ano) | **{s['best_streak']} dias** |",
        f"| Pull requests | **{num(s['prs'])}** |",
        f"| Issues abertas | **{num(s['issues'])}** |",
        f"| Estrelas recebidas | **{num(s['stars'])}** |",
        f"| Seguidores | **{num(s['followers'])}** |",
        f"| No GitHub desde | **{s['since']}** |",
        "",
        f"**Atividade nas últimas 26 semanas:** `{s['spark']}`",
        "",
    ]

    if s["langs"]:
        out += ["#### Linguagens mais usadas", "", "| Linguagem | Uso | % |", "|:--|:--|--:|"]
        for name, pct in s["langs"]:
            out.append(f"| {name} | `{bar(pct)}` | {pct:.1f}% |")
        out.append("")

    out += ["#### Conquistas do GitHub", "", "| Conquista | Status |", "|:--|:--:|"]
    for name, done in s["achievements"]:
        out.append(f"| {name} | {'Desbloqueada' if done else 'Bloqueada'} |")
    out += ["", f"<sub>Atualizado automaticamente em {s['updated']} (Brasília)</sub>"]
    return "\n".join(out)


def build_stats():
    user = fetch_user()  # se falhar, o script para e o README nao e alterado
    repos = fetch_repos()
    stars = sum(r.get("stargazers_count", 0) for r in repos)

    try:
        contrib = fetch_contributions()
        cal = contrib["contributionCalendar"]
        days = [
            (d["date"], d["contributionCount"])
            for w in cal["weeks"]
            for d in w["contributionDays"]
        ]
        cur, best = streaks(days)
        spark = sparkline(cal["weeks"])
        commits = contrib["totalCommitContributions"]
        prs = contrib["totalPullRequestContributions"]
        issues = contrib["totalIssueContributions"]
        reviews = contrib["totalPullRequestReviewContributions"]
    except Exception as exc:
        print(f"aviso: contribuicoes indisponiveis: {exc}")
        cur = best = commits = prs = issues = reviews = 0
        spark = "▁" * 26

    try:
        totals = fetch_languages(repos)
    except Exception as exc:
        print(f"aviso: linguagens indisponiveis: {exc}")
        totals = {}
    grand = sum(totals.values())
    langs = []
    if grand:
        ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:5]
        langs = [(name, size / grand * 100) for name, size in ranked]

    followers = user.get("followers", 0)
    n_repos = user.get("public_repos", len(repos))
    xp = commits * 10 + prs * 30 + issues * 10 + reviews * 15 + n_repos * 50 + stars * 20 + followers * 10
    level, title, pct = level_info(xp)

    achievements = [
        ("Criou o primeiro repositório", n_repos >= 1),
        ("5 repositórios públicos", n_repos >= 5),
        ("10 commits no último ano", commits >= 10),
        ("100 commits no último ano", commits >= 100),
        ("7 dias seguidos de commits", best >= 7),
        ("Abriu um pull request", prs >= 1),
        ("Ganhou a primeira estrela", stars >= 1),
        ("10 seguidores", followers >= 10),
    ]

    brasilia = timezone(timedelta(hours=-3))
    return {
        "level": level, "title": title, "pct": pct, "xp": xp,
        "repos": n_repos, "commits": commits, "cur_streak": cur,
        "best_streak": best, "prs": prs, "issues": issues, "stars": stars,
        "followers": followers, "since": user.get("created_at", "????")[:4],
        "spark": spark, "langs": langs, "achievements": achievements,
        "updated": datetime.now(brasilia).strftime("%d/%m/%Y %H:%M"),
    }


def update_readme(block):
    with open(README, encoding="utf-8") as fh:
        text = fh.read()
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pattern.search(text):
        sys.exit(f"Marcadores {START} e {END} nao encontrados no {README}")
    new = pattern.sub(lambda _m: f"{START}\n{block}\n{END}", text)
    with open(README, "w", encoding="utf-8") as fh:
        fh.write(new)


if __name__ == "__main__":
    update_readme(render(build_stats()))
    print("README atualizado.")
