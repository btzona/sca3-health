#!/usr/bin/env python3
"""Gather SCA3 / Machado-Joseph research into data/articles.json.

Sources that fail are logged and skipped. If a run collects nothing, the
existing ledger is left in place and the process exits 0.

Successful runs merge into the ledger instead of replacing it. Duplicates
collapse on DOI, trial id, or title. The file is capped, and the cap keeps
room for items from the first half of 2026 so the shelf still reaches
January 2026.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "articles.json"
USER_AGENT = (
    "AtaxinLedger/1.0 (+https://github.com/btzona/sca3-health; "
    "mailto:btzona@users.noreply.github.com)"
)
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
TIMEOUT = 30
HISTORY_CAP = 240
# Items on or before this date are reserved when the cap trims the file,
# so a busy month cannot erase the start of the archive.
HISTORY_FLOOR = datetime(2026, 6, 30, tzinfo=timezone.utc)
ARCHIVE_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
TODAY = datetime.now(timezone.utc).date()

DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s\"<>]+)", re.I)
STRONG_RE = re.compile(
    r"spinocerebellar ataxia type[-\s]3(?!\d)|machado[-\s]joseph|\bsca3\b(?!\d)|ataxin[-\s]?3\b",
    re.I,
)
ATXN3_RE = re.compile(r"\bATXN3\b", re.I)
ATAXIA_RE = re.compile(
    r"ataxia|polyglutamine|\bpolyq\b|cerebell|CAG repeat|spinocerebellar",
    re.I,
)
DRUG_RE = re.compile(
    r"\bVO659\b|troriluzole|\bVYGLXIA\b|\bS233107\b|stemchymal",
    re.I,
)
OTHER_TITLE_RE = re.compile(
    r"multiple system atrophy|\bMSA-?[A-Z]?\b|Parkinson|Alzheimer|carcinoma|"
    r"lung adenocarcinoma|hepatocellular|breast cancer|ovarian cancer",
    re.I,
)
MARKET_RE = re.compile(r"\b(market size|market forecast|CAGR)\b", re.I)
PREPRINT_DOI_RE = re.compile(r"^10\.(1101|64898|21203)/", re.I)

TAG_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("Clinical trial", re.compile(
        r"clinical trial|\bphase\s*[123]\b|\bNCT\d{8}\b|placebo-controlled|"
        r"first-in-human|recruiting|randomized|efficacy and safety",
        re.I,
    )),
    ("Therapy/ASO", re.compile(
        r"\bASO\b|antisense|oligonucleotide|\bVO659\b|troriluzole|\bS233107\b|"
        r"stemchymal|splice-switching|gene silencing|lowering",
        re.I,
    )),
    ("Gene therapy", re.compile(
        r"gene therapy|\bAAV\b|\bCRISPR\b|\bRNAi\b|\bsiRNA\b|viral vector|"
        r"\bmiRNA\b",
        re.I,
    )),
    ("Biomarkers", re.compile(
        r"biomarker|neurofilament|\bNfL\b|\bMRI\b|imaging biomarker|fluid biomarker",
        re.I,
    )),
    ("Natural history", re.compile(
        r"natural history|disease progression|\bSARA\b|age at onset|prevalence|"
        r"survival|cohort",
        re.I,
    )),
    ("Genetics/Reproduction", re.compile(
        r"\bCAG\b|preimplantation|\bPGT\b|prenatal|anticipation|genetic counsel|"
        r"repeat expansion|repeat instability|transmission",
        re.I,
    )),
    ("Care/Rehab", re.compile(
        r"rehabilitat|physical therapy|physiotherapy|\bspeech\b|swallow|dysphagia|"
        r"exercise|occupational therapy|fall prevention|palliative|quality of life|"
        r"\bgait\b",
        re.I,
    )),
    ("Review", re.compile(
        r"\breview\b|meta-analysis|systematic review",
        re.I,
    )),
]

NEWS_FEEDS = [
    {
        "id": "hdbuzz",
        "name": "HDBuzz",
        "url": "https://en.hdbuzz.net/rss",
    },
    {
        "id": "neurologylive",
        "name": "NeurologyLive",
        "url": "https://www.neurologylive.com/rss",
    },
    {
        "id": "biopharmadive",
        "name": "BioPharma Dive",
        "url": "https://www.biopharmadive.com/feeds/news/",
    },
    {
        "id": "stat",
        "name": "STAT",
        "url": "https://www.statnews.com/feed/",
    },
    {
        "id": "sciencedaily",
        "name": "ScienceDaily",
        "url": "https://www.sciencedaily.com/rss/health_medicine.xml",
    },
    {
        "id": "nature-neurodeg",
        "name": "Nature (Neurodegeneration)",
        "url": "https://www.nature.com/subjects/neurodegeneration.rss",
    },
    {
        "id": "biohaven",
        "name": "Biohaven",
        "url": "https://www.biohaven.com/feed/",
    },
    {
        "id": "prnewswire",
        "name": "PR Newswire",
        "url": "https://www.prnewswire.com/rss/news-releases-list.rss",
    },
]

NEWS_HOSTS = {
    "ataxia.org",
    "hdbuzz.net",
    "neurologylive.com",
    "biopharmadive.com",
    "statnews.com",
    "nature.com",
    "science.org",
    "sciencemag.org",
    "thelancet.com",
    "nih.gov",
    "fda.gov",
    "clinicaltrials.gov",
    "prnewswire.com",
    "businesswire.com",
    "reprocell.com",
    "vicotx.com",
    "cureraredisease.org",
    "biohaven.com",
    "endpointsnews.com",
    "fiercebiotech.com",
    "clinicaltrialsarena.com",
    "newswire.ca",
    "medscape.com",
    "reuters.com",
    "apnews.com",
    "bbc.co.uk",
    "bbc.com",
    "npr.org",
    "bostonglobe.com",
    "sciencedaily.com",
    "eurekalert.org",
    "the-scientist.com",
    "alzforum.org",
    "cell.com",
    "nejm.org",
    "bmj.com",
    "theguardian.com",
    "washingtonpost.com",
    "nytimes.com",
    "news-medical.net",
}

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def strip_html(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<jats:[^>]+>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def truncate(text: str, n: int = 320) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    cut = text[: n - 1].rsplit(" ", 1)[0]
    return cut.rstrip(".,;:") + "…"


def fetch_url(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict | None = None,
    timeout: int = TIMEOUT,
) -> tuple[bytes | None, str | None]:
    req_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        req_headers.update(headers)
    last_error = "empty"
    for attempt in range(3):
        req = urllib.request.Request(url, data=data, headers=req_headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
                return resp.read(), None
        except urllib.error.HTTPError as exc:
            last_error = f"HTTPError: {exc}"
            if exc.code in {429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(1.4 * (attempt + 1))
                continue
            return None, last_error
        except Exception as exc:  # noqa: BLE001
            return None, f"{type(exc).__name__}: {exc}"
    return None, last_error


def parse_date(raw: str) -> str | None:
    if not raw:
        return None
    raw = str(raw).strip()
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            s = raw.replace("Z", "+0000") if fmt.endswith("%z") and raw.endswith("Z") else raw
            if fmt.endswith("%z") and re.search(r"[+-]\d{2}:\d{2}$", s):
                s = s[:-3] + s[-2:]
            dt = datetime.strptime(s[:26], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            continue
    match = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if match:
        return f"{match.group(1)}T12:00:00+00:00"
    return None


def publication_date(raw: str) -> str | None:
    raw = (raw or "").strip()
    # Noon UTC keeps a date-only value on the same civil day in US time zones.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return f"{raw}T12:00:00+00:00"
    if re.fullmatch(r"\d{4}-\d{2}", raw):
        return f"{raw}-01T12:00:00+00:00"
    if re.fullmatch(r"\d{4}", raw):
        return None
    return parse_date(raw)


def article_id(url: str, title: str) -> str:
    base = (url or title).strip().lower()
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def extract_doi(text: str) -> str | None:
    if not text:
        return None
    match = DOI_RE.search(text)
    if not match:
        return None
    doi = match.group(1).rstrip(".,);]").lower()
    doi = re.sub(r"</?jats:[^>]+>", "", doi)
    return doi


def title_key(title: str) -> str:
    text = strip_html(title).lower().replace("’", "'").replace("–", "-").replace("—", "-")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def is_relevant(title: str, summary: str = "") -> bool:
    blob = f"{title} {summary}"
    if MARKET_RE.search(blob):
        return False
    # A paper about a different disease that only cites SCA3 in passing stays out.
    if OTHER_TITLE_RE.search(title) and not STRONG_RE.search(title):
        return False
    if STRONG_RE.search(blob):
        return True
    if ATXN3_RE.search(blob) and ATAXIA_RE.search(blob):
        return True
    if DRUG_RE.search(blob) and ATAXIA_RE.search(blob):
        return True
    return False


def assign_tags(title: str, summary: str, *, kind: str) -> list[str]:
    blob = f"{title} {summary}"
    tags: list[str] = []
    if kind == "trial":
        tags.append("Clinical trial")
    for name, pattern in TAG_RULES:
        if name not in tags and pattern.search(blob):
            tags.append(name)
    if kind == "trial" and "Clinical trial" in tags:
        # Registry records are trials first; keep other matches after that.
        tags = ["Clinical trial"] + [tag for tag in tags if tag != "Clinical trial"]
    return tags or ["Research"]


def in_archive(date_iso: str | None) -> bool:
    if not date_iso:
        return False
    try:
        dt = datetime.fromisoformat(date_iso)
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= ARCHIVE_START


def make_article(
    *,
    title: str,
    link: str,
    summary: str,
    source: str,
    source_id: str,
    date_iso: str | None,
    kind: str,
) -> dict | None:
    title = strip_html(title).strip().rstrip(".")
    source = strip_html(source).strip() or source
    link = (link or "").strip()
    if len(title) < 12 or not link.startswith("http"):
        return None
    summary = truncate(strip_html(summary), 320)
    if not is_relevant(title, summary):
        return None
    if not in_archive(date_iso):
        return None
    doi = extract_doi(link)
    if doi and "doi.org" not in link:
        link = f"https://doi.org/{doi}"
    return {
        "id": article_id(link, title),
        "title": title,
        "link": link,
        "summary": summary or source,
        "source": source[:80],
        "source_id": source_id,
        "date": date_iso,
        "tags": assign_tags(title, summary, kind=kind),
        "kind": kind,
    }


def canonical_keys(article: dict) -> list[str]:
    keys: list[str] = []
    doi = extract_doi(article.get("link") or "")
    if doi:
        keys.append("doi:" + doi.lower())
    nct = re.search(r"\b(NCT\d{8})\b", f"{article.get('link')} {article.get('title')}", re.I)
    if nct and article.get("kind") == "trial":
        keys.append("nct:" + nct.group(1).upper())
    title = title_key(article.get("title") or "")
    if len(title) >= 18:
        keys.append("title:" + title)
    if not keys:
        keys.append("id:" + (article.get("id") or article.get("link") or ""))
    return keys


def prefer_date(old: str | None, new: str | None) -> str | None:
    """Keep a real day over a year-only placeholder stored as 1 January."""
    if not old:
        return new
    if not new:
        return old

    def january_first(value: str) -> bool:
        return value[5:10] == "01-01"

    if january_first(old) and not january_first(new):
        return new
    if january_first(new) and not january_first(old):
        return old
    return old if article_timestamp({"date": old}) <= article_timestamp({"date": new}) else new


def article_timestamp(article: dict) -> float:
    try:
        return datetime.fromisoformat(article.get("date") or "").timestamp()
    except ValueError:
        return 0.0


def prefer_article(old: dict, new: dict) -> dict:
    chosen = dict(old)
    if len(new.get("summary") or "") > len(old.get("summary") or "") + 40:
        chosen["summary"] = new.get("summary") or chosen.get("summary")
    chosen["date"] = prefer_date(old.get("date"), new.get("date"))
    tags: list[str] = []
    for tag in list(old.get("tags") or []) + list(new.get("tags") or []):
        if tag != "Research" and tag not in tags:
            tags.append(tag)
    chosen["tags"] = tags or ["Research"]
    old_doi = extract_doi(old.get("link") or "") or ""
    new_doi = extract_doi(new.get("link") or "") or ""
    if new_doi and (not old_doi or (PREPRINT_DOI_RE.match(old_doi) and not PREPRINT_DOI_RE.match(new_doi))):
        chosen["link"] = f"https://doi.org/{new_doi}"
        chosen["id"] = article_id(chosen["link"], chosen.get("title") or "")
        if new.get("source") and new.get("kind") == "paper":
            chosen["source"] = new["source"]
            chosen["source_id"] = new.get("source_id") or chosen.get("source_id")
            chosen["kind"] = "paper"
    if old.get("kind") == "preprint" and new.get("kind") == "paper":
        chosen["kind"] = "paper"
    return chosen


def load_previous_articles() -> list[dict]:
    if not OUT.exists():
        return []
    try:
        previous = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    articles = previous.get("articles") if isinstance(previous, dict) else None
    if not isinstance(articles, list):
        return []
    return [item for item in articles if isinstance(item, dict)]


def merge_articles(previous: list[dict], fresh: list[dict]) -> list[dict]:
    records: list[dict] = []
    index: dict[str, int] = {}

    def add(article: dict) -> None:
        keys = canonical_keys(article)
        found = next((index[key] for key in keys if key in index), None)
        if found is None:
            records.append(dict(article))
            found = len(records) - 1
        else:
            records[found] = prefer_article(records[found], article)
        for key in canonical_keys(records[found]):
            index[key] = found

    for article in list(previous) + list(fresh):
        if (article.get("title") or "").strip() and str(article.get("link") or "").startswith("http"):
            add(article)
    return records


def cap_history(items: list[dict]) -> list[dict]:
    items = [
        item
        for item in items
        if str(item.get("link") or "").startswith("http")
        and in_archive(item.get("date"))
        and is_relevant(item.get("title") or "", item.get("summary") or "")
    ]
    items.sort(key=lambda item: -article_timestamp(item))
    if len(items) <= HISTORY_CAP:
        return items
    floor_ts = HISTORY_FLOOR.timestamp()
    early = [item for item in items if 0 < article_timestamp(item) <= floor_ts]
    early_keep = early[:48]
    kept = {id(item) for item in early_keep}
    rest = [item for item in items if id(item) not in kept]
    selected = early_keep + rest[: max(0, HISTORY_CAP - len(early_keep))]
    selected.sort(key=lambda item: -article_timestamp(item))
    return selected


def write_ledger(payload: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def journal_name(rec: dict) -> str:
    info = rec.get("journalInfo") or {}
    journal = info.get("journal") if isinstance(info, dict) else None
    name = ""
    if isinstance(journal, dict):
        name = journal.get("title") or journal.get("medlineAbbreviation") or ""
    name = strip_html(str(name)).strip()
    if not name:
        name = strip_html(str(rec.get("bookOrReportDetails", {}).get("publisher") if isinstance(rec.get("bookOrReportDetails"), dict) else ""))
    return name or "Europe PMC"


def europepmc_search(query: str, page_size: int = 100) -> tuple[list[dict], str | None]:
    articles: list[dict] = []
    cursor = "*"
    errors: list[str] = []
    for _ in range(8):
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(
            {
                "query": query,
                "format": "json",
                "pageSize": str(page_size),
                "resultType": "core",
                "cursorMark": cursor,
                "sort": "P_PDATE_D desc",
            }
        )
        raw, err = fetch_url(url)
        if err or not raw:
            errors.append(err or "empty Europe PMC response")
            break
        head = raw[:160].lstrip().lower()
        if head.startswith(b"<!doctype") or head.startswith(b"<html"):
            errors.append("Europe PMC returned HTML")
            break
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"parse: {exc}")
            break
        results = (payload.get("resultList") or {}).get("result") or []
        if isinstance(results, dict):
            results = [results]
        if not results:
            break
        for rec in results:
            if not isinstance(rec, dict):
                continue
            title = rec.get("title") or ""
            doi = extract_doi(rec.get("doi") or "")
            pmid = str(rec.get("pmid") or "").strip()
            if doi:
                link = f"https://doi.org/{doi}"
            elif pmid:
                link = f"https://europepmc.org/article/MED/{pmid}"
            else:
                continue
            journal = journal_name(rec)
            kind = "paper"
            if PREPRINT_DOI_RE.match(doi or "") or re.search(r"biorxiv|medrxiv|research square", journal, re.I):
                kind = "preprint"
            date_iso = publication_date(rec.get("firstPublicationDate") or rec.get("pubYear") or "")
            article = make_article(
                title=title,
                link=link,
                summary=rec.get("abstractText") or journal,
                source=journal,
                source_id="europepmc",
                date_iso=date_iso,
                kind=kind,
            )
            if article:
                articles.append(article)
        nxt = payload.get("nextCursorMark")
        if not nxt or nxt == cursor:
            break
        cursor = nxt
        time.sleep(0.2)
    return articles, "; ".join(errors[:3]) if errors and not articles else None


def month_spans() -> list[tuple[str, str]]:
    spans: list[tuple[str, str]] = []
    year, month = 2026, 1
    while (year, month) <= (TODAY.year, TODAY.month):
        start = f"{year:04d}-{month:02d}-01"
        if year == TODAY.year and month == TODAY.month:
            end = TODAY.isoformat()
        elif month == 2:
            end = f"{year:04d}-02-28"
        elif month in {4, 6, 9, 11}:
            end = f"{year:04d}-{month:02d}-30"
        else:
            end = f"{year:04d}-{month:02d}-31"
        spans.append((start, end))
        month += 1
        if month == 13:
            month = 1
            year += 1
    return spans


def fetch_europepmc() -> tuple[list[dict], str | None]:
    end = TODAY.isoformat()
    core = (
        '(TITLE:"spinocerebellar ataxia type 3" OR ABSTRACT:"spinocerebellar ataxia type 3" '
        'OR TITLE:"Machado-Joseph" OR ABSTRACT:"Machado-Joseph" OR TITLE:SCA3 OR ABSTRACT:SCA3 '
        'OR TITLE:ATXN3 OR ABSTRACT:ATXN3 OR TITLE:"ataxin-3" OR ABSTRACT:"ataxin-3") '
        f"AND FIRST_PDATE:[2026-01-01 TO {end}]"
    )
    found, err = europepmc_search(core)
    more, err2 = europepmc_search(f"(SRC:PPR) AND {core}", page_size=50)
    found.extend(more)
    # Month slices catch papers a single sorted cursor can skip.
    month_errors: list[str] = []
    for start, stop in month_spans():
        query = (
            '(TITLE:"spinocerebellar ataxia type 3" OR TITLE:"Machado-Joseph" OR TITLE:"ataxin-3" '
            f"OR TITLE:ATXN3 OR TITLE:SCA3) AND FIRST_PDATE:[{start} TO {stop}]"
        )
        batch, month_err = europepmc_search(query, page_size=25)
        found.extend(batch)
        if month_err:
            month_errors.append(f"{start[:7]}: {month_err}")
        time.sleep(0.15)
    warning = "; ".join(part for part in (err, err2, *month_errors[:2]) if part) or None
    return found, warning


def crossref_date(item: dict) -> str | None:
    for key in ("published-online", "published", "published-print", "issued"):
        block = item.get(key) or {}
        parts = block.get("date-parts") if isinstance(block, dict) else None
        if parts and parts[0]:
            nums = parts[0]
            # A bare year is not a publication day. Leaving it as 1 January
            # piles unrelated records into the first month of the archive.
            if len(nums) < 2:
                continue
            year = int(nums[0])
            month = int(nums[1])
            day = int(nums[2]) if len(nums) > 2 else 1
            try:
                return datetime(year, month, day, 12, tzinfo=timezone.utc).isoformat()
            except ValueError:
                return f"{year:04d}-{month:02d}-01T00:00:00+00:00"
    return None


def fetch_crossref() -> tuple[list[dict], str | None]:
    articles: list[dict] = []
    queries = [
        '"spinocerebellar ataxia type 3"',
        '"Machado-Joseph"',
        "ATXN3 ataxia",
    ]
    errors: list[str] = []
    for query in queries:
        for offset in (0, 100, 200):
            url = "https://api.crossref.org/works?" + urllib.parse.urlencode(
                {
                    "query.bibliographic": query,
                    "filter": f"from-pub-date:2026-01-01,until-pub-date:{TODAY.isoformat()}",
                    "rows": "100",
                    "offset": str(offset),
                    "select": "DOI,title,abstract,container-title,type,published,published-online,published-print,issued,URL",
                }
            )
            raw, err = fetch_url(url, headers={"User-Agent": USER_AGENT})
            if err or not raw:
                errors.append(err or "empty Crossref response")
                break
            try:
                message = json.loads(raw.decode("utf-8")).get("message") or {}
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                errors.append(f"parse: {exc}")
                break
            items = message.get("items") or []
            if not items:
                break
            for item in items:
                titles = item.get("title") or []
                title = titles[0] if titles else ""
                doi = extract_doi(item.get("DOI") or "")
                if not doi:
                    continue
                container = item.get("container-title") or []
                journal = container[0] if container else "Crossref"
                kind = "preprint" if item.get("type") == "posted-content" or PREPRINT_DOI_RE.match(doi) else "paper"
                article = make_article(
                    title=title,
                    link=f"https://doi.org/{doi}",
                    summary=item.get("abstract") or journal,
                    source=journal,
                    source_id="crossref",
                    date_iso=crossref_date(item),
                    kind=kind,
                )
                if article:
                    articles.append(article)
            if len(items) < 100:
                break
            time.sleep(0.25)
    warning = "; ".join(errors[:3]) if errors and not articles else ("; ".join(errors[:2]) if errors else None)
    if errors and articles:
        return articles, "; ".join(errors[:2])
    if errors and not articles:
        return [], "; ".join(errors[:3])
    return articles, None


def preprint_window() -> tuple[str, str]:
    explicit = os.environ.get("SCA3_PREPRINT_FROM", "").strip()
    if explicit:
        start = explicit
    else:
        # Recent window only. Europe PMC carries preprints back to January 2026.
        # A full-server scan of every 2026 preprint is too large for a daily run.
        start_dt = datetime.now(timezone.utc) - timedelta(days=3)
        if start_dt < ARCHIVE_START:
            start_dt = ARCHIVE_START
        start = start_dt.date().isoformat()
    return start, TODAY.isoformat()


def preprint_spans(start: str, end: str) -> list[tuple[str, str]]:
    start_d = datetime.fromisoformat(start).date()
    end_d = datetime.fromisoformat(end).date()
    if (end_d - start_d).days <= 40:
        return [(start_d.isoformat(), end_d.isoformat())]
    spans: list[tuple[str, str]] = []
    cursor = start_d.replace(day=1)
    while cursor <= end_d:
        if cursor.month == 12:
            nxt = cursor.replace(year=cursor.year + 1, month=1, day=1)
        else:
            nxt = cursor.replace(month=cursor.month + 1, day=1)
        span_end = min(end_d, nxt - timedelta(days=1))
        span_start = max(start_d, cursor)
        spans.append((span_start.isoformat(), span_end.isoformat()))
        cursor = nxt
    return spans


def fetch_preprint_span(server: str, start: str, end: str) -> tuple[list[dict], str | None]:
    articles: list[dict] = []
    cursor = 0
    pages = 0
    while pages < 40:
        url = f"https://api.biorxiv.org/details/{server}/{start}/{end}/{cursor}"
        raw, err = fetch_url(url, timeout=25)
        if err or not raw:
            return articles, err or "empty preprint response"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return articles, f"parse: {exc}"
        collection = payload.get("collection") or []
        if not collection:
            break
        for rec in collection:
            doi = extract_doi(rec.get("doi") or "")
            if not doi:
                continue
            article = make_article(
                title=rec.get("title") or "",
                link=f"https://doi.org/{doi}",
                summary=rec.get("abstract") or server,
                source=server,
                source_id=server,
                date_iso=publication_date(rec.get("date") or ""),
                kind="preprint",
            )
            if article:
                articles.append(article)
        messages = payload.get("messages") or [{}]
        meta = messages[0] if messages else {}
        total = int(meta.get("total") or 0)
        cursor += len(collection)
        pages += 1
        if pages == 1 or pages % 10 == 0:
            print(f"    {server} page {pages}, {cursor} of {total or '?'}", flush=True)
        if not total or cursor >= total or len(collection) < 1:
            break
        if pages >= 40 and total and cursor < total:
            print(f"    {server} stopped early at {cursor} of {total}", flush=True)
            break
        time.sleep(0.05)
    return articles, None


def fetch_preprints() -> tuple[list[dict], str | None]:
    found: list[dict] = []
    errors: list[str] = []
    start, end = preprint_window()
    for server in ("biorxiv", "medrxiv"):
        for span_start, span_end in preprint_spans(start, end):
            print(f"  {server} {span_start} → {span_end}", flush=True)
            batch, err = fetch_preprint_span(server, span_start, span_end)
            if err and not batch:
                errors.append(f"{server} {span_start}: {err}")
                print(f"  FAIL {server} {span_start}: {err}")
                continue
            found.extend(batch)
            if err:
                errors.append(f"{server} {span_start}: {err}")
            print(f"  {server} {span_start[:7]}: {len(batch)} kept")
    return found, "; ".join(errors[:4]) if errors else None


def fetch_trials() -> tuple[list[dict], str | None]:
    articles: list[dict] = []
    token = None
    for _ in range(6):
        params = {
            "query.term": 'SCA3 OR "Machado-Joseph" OR ATXN3 OR "spinocerebellar ataxia type 3"',
            "pageSize": "100",
        }
        if token:
            params["pageToken"] = token
        url = "https://clinicaltrials.gov/api/v2/studies?" + urllib.parse.urlencode(params)
        raw, err = fetch_url(url)
        if err or not raw:
            return articles, err or "empty ClinicalTrials.gov response"
        head = raw[:120].lstrip().lower()
        if head.startswith(b"<!doctype") or head.startswith(b"<html"):
            return articles, "ClinicalTrials.gov returned HTML"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return articles, f"parse: {exc}"
        for study in payload.get("studies") or []:
            proto = study.get("protocolSection") or {}
            ident = proto.get("identificationModule") or {}
            status = proto.get("statusModule") or {}
            desc = proto.get("descriptionModule") or {}
            cond = proto.get("conditionsModule") or {}
            nct = ident.get("nctId") or ""
            title = ident.get("briefTitle") or ident.get("officialTitle") or ""
            conditions = " ".join(cond.get("conditions") or [])
            summary = desc.get("briefSummary") or ""
            blob = f"{title} {ident.get('officialTitle') or ''} {conditions} {summary}"
            if not is_relevant(title, blob):
                continue
            date_iso = publication_date((status.get("lastUpdatePostDateStruct") or {}).get("date") or "")
            overall = (status.get("overallStatus") or "See registry").replace("_", " ").title()
            condition_line = f" Conditions: {conditions}." if conditions else ""
            article = make_article(
                title=title,
                link=f"https://clinicaltrials.gov/study/{nct}" if nct else "",
                summary=f"Status: {overall}.{condition_line} {summary}",
                source="ClinicalTrials.gov",
                source_id="clinicaltrials",
                date_iso=date_iso,
                kind="trial",
            )
            if article:
                articles.append(article)
        token = payload.get("nextPageToken")
        if not token:
            break
        time.sleep(0.2)
    return articles, None


def local_text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return strip_html("".join(el.itertext()))


def parse_rss_items(root: ET.Element) -> list[dict]:
    items: list[dict] = []
    for item in root.findall("./channel/item"):
        title = local_text(item.find("title"))
        link = local_text(item.find("link"))
        summary = local_text(item.find("description"))
        if not summary:
            encoded = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
            summary = local_text(encoded)
        date_raw = local_text(item.find("pubDate")) or local_text(
            item.find("{http://purl.org/dc/elements/1.1/}date")
        )
        source_el = item.find("source")
        source_name = local_text(source_el)
        items.append(
            {
                "title": title,
                "link": link,
                "summary": summary,
                "date_raw": date_raw,
                "source_name": source_name,
            }
        )
    for entry in root.findall("{http://www.w3.org/2005/Atom}entry"):
        title = local_text(entry.find("{http://www.w3.org/2005/Atom}title"))
        link = ""
        for link_el in entry.findall("{http://www.w3.org/2005/Atom}link"):
            href = link_el.get("href") or ""
            rel = link_el.get("rel") or "alternate"
            if href and rel in ("alternate", ""):
                link = href
                break
        summary = local_text(entry.find("{http://www.w3.org/2005/Atom}summary"))
        date_raw = local_text(entry.find("{http://www.w3.org/2005/Atom}updated")) or local_text(
            entry.find("{http://www.w3.org/2005/Atom}published")
        )
        items.append(
            {"title": title, "link": link, "summary": summary, "date_raw": date_raw, "source_name": ""}
        )
    return items


def articles_from_feed(parsed: list[dict], source: str, source_id: str, kind: str = "news") -> list[dict]:
    articles: list[dict] = []
    for item in parsed:
        link = (item.get("link") or "").strip()
        link = re.sub(r"^https?://web\.archive\.org/web/\d+(?:id_)?/", "", link)
        article = make_article(
            title=item.get("title") or "",
            link=link,
            summary=item.get("summary") or "",
            source=item.get("source_name") or source,
            source_id=source_id,
            date_iso=parse_date(item.get("date_raw") or ""),
            kind=kind,
        )
        if article:
            articles.append(article)
    return articles


def fetch_rss(feed: dict, kind: str = "news") -> tuple[list[dict], str | None]:
    raw, err = fetch_url(feed["url"])
    if err or not raw:
        return [], err or "empty feed"
    head = raw[:200].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        return [], "HTML response, not a feed"
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return [], f"parse: {exc}"
    return articles_from_feed(parse_rss_items(root), feed["name"], feed["id"], kind), None


def fetch_naf() -> tuple[list[dict], str | None]:
    live, live_err = fetch_rss(
        {"id": "naf", "name": "National Ataxia Foundation", "url": "https://www.ataxia.org/feed/"},
        kind="news",
    )
    snapshots = wayback_naf_feeds()
    found = list(live)
    errors: list[str] = []
    if live_err:
        errors.append(f"live: {live_err}")
    if snapshots[1] and not snapshots[0]:
        errors.append(snapshots[1])
    found.extend(snapshots[0])
    if not found and snapshots[1]:
        return [], "; ".join(errors[:3]) or snapshots[1]
    if not found and live_err:
        return [], "note: live feed blocked; archived RSS had no matching SCA3 items"
    warning = "; ".join(errors[:2]) if errors else None
    return found, warning


def wayback_naf_feeds() -> tuple[list[dict], str | None]:
    cdx = (
        "https://web.archive.org/cdx/search/cdx?url=www.ataxia.org/feed/"
        "&output=json&fl=timestamp,statuscode&filter=statuscode:200&from=20260101"
    )
    raw, err = fetch_url(cdx, timeout=45)
    if err or not raw:
        return [], err or "Wayback CDX unavailable"
    try:
        rows = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [], f"CDX parse: {exc}"
    stamps: dict[str, str] = {}
    for row in rows[1:]:
        if len(row) < 2 or row[1] != "200":
            continue
        stamps[row[0][:6]] = row[0]
    articles: list[dict] = []
    failures = 0
    for stamp in sorted(stamps.values()):
        url = f"https://web.archive.org/web/{stamp}id_/https://www.ataxia.org/feed/"
        body, fetch_err = fetch_url(url, timeout=45)
        if fetch_err or not body or not body.lstrip().startswith(b"<?xml"):
            failures += 1
            continue
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            failures += 1
            continue
        articles.extend(
            articles_from_feed(
                parse_rss_items(root),
                "National Ataxia Foundation",
                "naf",
                "news",
            )
        )
        time.sleep(0.15)
    if not articles and failures:
        return [], "Wayback snapshots of the NAF feed were unreadable"
    return articles, None


def host_allowed(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    host = host.lower().removeprefix("www.")
    return any(host == allowed or host.endswith("." + allowed) for allowed in NEWS_HOSTS)


def google_article_id(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.hostname == "news.google.com" and len(parts) >= 2 and parts[-2] == "articles":
        return parts[-1]
    return None


def decode_google_news(url: str) -> str | None:
    art_id = google_article_id(url)
    if not art_id:
        return None
    page_url = (
        f"https://news.google.com/rss/articles/{art_id}"
        "?hl=en-US&gl=US&ceid=US%3Aen"
    )
    raw, err = fetch_url(page_url, headers={"User-Agent": BROWSER_UA, "Accept": "text/html"})
    if err or not raw:
        return None
    text = raw.decode("utf-8", "replace")
    sg = re.search(r'data-n-a-sg="([^"]+)"', text)
    ts = re.search(r'data-n-a-ts="([^"]+)"', text)
    if not sg or not ts:
        return None
    ctx = [
        ["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
        "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0,
    ]
    inner = ["garturlreq", ctx, art_id, int(ts.group(1)) if ts.group(1).isdigit() else ts.group(1), sg.group(1)]
    envelopes = [[["Fbv4je", json.dumps(inner, separators=(",", ":")), None, "0"]]]
    body = "f.req=" + urllib.parse.quote(json.dumps(envelopes, separators=(",", ":")))
    raw2, err2 = fetch_url(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute",
        data=body.encode(),
        headers={
            "User-Agent": BROWSER_UA,
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        },
    )
    if err2 or not raw2:
        return None
    match = re.search(r"https?://[^\"\\]+", raw2.decode("utf-8", "replace"))
    if not match:
        return None
    decoded = match.group(0).replace("\\u0026", "&")
    if "news.google.com" in decoded:
        return None
    return decoded


def fetch_google_news() -> tuple[list[dict], str | None]:
    query = (
        '"spinocerebellar ataxia type 3" OR "Machado-Joseph" OR SCA3 OR "ataxin-3" '
        "OR VO659 OR troriluzole OR Stemchymal OR S233107"
    )
    queries = [
        query + " when:1y",
        'site:ataxia.org (SCA3 OR "Machado-Joseph" OR "ataxin-3")',
    ]
    articles: list[dict] = []
    seen: set[str] = set()
    decoded = 0
    errors: list[str] = []
    for query_text in queries:
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": query_text, "hl": "en-US", "gl": "US", "ceid": "US:en"}
        )
        raw, err = fetch_url(url, headers={"User-Agent": BROWSER_UA, "Accept": "application/rss+xml"})
        if err or not raw:
            errors.append(err or "empty Google News response")
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            errors.append(f"parse: {exc}")
            continue
        for item in parse_rss_items(root):
            title = item.get("title") or ""
            clean_title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title).strip()
            summary = item.get("summary") or ""
            if not is_relevant(clean_title, f"{title} {summary}"):
                continue
            if not in_archive(parse_date(item.get("date_raw") or "")):
                continue
            if decoded >= 40:
                break
            publisher = decode_google_news(item.get("link") or "")
            decoded += 1
            time.sleep(0.12)
            if not publisher or not host_allowed(publisher) or publisher in seen:
                continue
            seen.add(publisher)
            source = item.get("source_name") or "News"
            source_id = "google-news"
            if urllib.parse.urlparse(publisher).hostname and "ataxia.org" in urllib.parse.urlparse(publisher).hostname:
                source = "National Ataxia Foundation"
                source_id = "naf"
            article = make_article(
                title=clean_title,
                link=publisher,
                summary=summary or source,
                source=source,
                source_id=source_id,
                date_iso=parse_date(item.get("date_raw") or ""),
                kind="news",
            )
            if article:
                articles.append(article)
    if errors and not articles:
        return [], "; ".join(errors[:2])
    return articles, "; ".join(errors[:2]) if errors else None


def collect_source(status: list[dict], source_id: str, name: str, fn) -> list[dict]:
    print(f"Fetching {name} …", flush=True)
    try:
        articles, err = fn()
    except Exception as exc:  # noqa: BLE001
        status.append({"id": source_id, "name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        print(f"  FAIL: {exc}")
        return []
    if err and err.startswith("note:"):
        note = err.removeprefix("note:").strip()
        status.append({"id": source_id, "name": name, "ok": True, "items": len(articles), "warning": note})
        print(f"  OK with warning: {len(articles)} kept ({note})")
        return articles
    if err and not articles:
        status.append({"id": source_id, "name": name, "ok": False, "error": err})
        print(f"  FAIL: {err}")
        return []
    row: dict = {"id": source_id, "name": name, "ok": True, "items": len(articles)}
    if err:
        row["warning"] = err
        print(f"  OK with warning: {len(articles)} kept ({err})")
    else:
        print(f"  OK: {len(articles)} kept")
    status.append(row)
    return articles


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    feed_status: list[dict] = []
    collected: list[dict] = []

    collected.extend(collect_source(feed_status, "europepmc", "Europe PMC", fetch_europepmc))
    collected.extend(collect_source(feed_status, "crossref", "Crossref", fetch_crossref))
    collected.extend(collect_source(feed_status, "preprints", "bioRxiv and medRxiv", fetch_preprints))
    collected.extend(collect_source(feed_status, "clinicaltrials", "ClinicalTrials.gov", fetch_trials))
    collected.extend(collect_source(feed_status, "naf", "National Ataxia Foundation", fetch_naf))
    collected.extend(collect_source(feed_status, "google-news", "News search", fetch_google_news))

    for feed in NEWS_FEEDS:
        def run(feed=feed):
            return fetch_rss(feed)

        collected.extend(collect_source(feed_status, feed["id"], feed["name"], run))
        time.sleep(0.15)

    previous = load_previous_articles()
    if not collected:
        if previous:
            print(f"No articles collected. Keeping last good {OUT} ({len(previous)} articles).")
            return 0
        write_ledger(
            {
                "site": "Ataxin-3",
                "subtitle": "A public ledger of SCA3 and Machado-Joseph research",
                "updated": datetime.now(timezone.utc).isoformat(),
                "article_count": 0,
                "feeds": feed_status,
                "articles": [],
            }
        )
        print("No articles collected and no previous ledger. Wrote an empty ledger.")
        return 0

    selected = cap_history(merge_articles(previous, collected))
    dated = [article["date"] for article in selected if article.get("date")]
    payload = {
        "site": "Ataxin-3",
        "subtitle": "A public ledger of SCA3 and Machado-Joseph research",
        "updated": datetime.now(timezone.utc).isoformat(),
        "article_count": len(selected),
        "feeds": feed_status,
        "articles": selected,
    }
    write_ledger(payload)
    span = f"{min(dated)[:10]} → {max(dated)[:10]}" if dated else "undated"
    print(f"\nWrote {len(selected)} articles ({span}) → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
