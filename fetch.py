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


UNPAYWALL_EMAIL = "btzona@users.noreply.github.com"
CC_LICENSE_RE = re.compile(r"creativecommons\.org/(?:licenses|publicdomain)/", re.I)
# 10.1101 is the long-standing bioRxiv and medRxiv prefix. 10.64898 is the
# prefix those servers use on 2026 preprints in this ledger.
SERVER_PREPRINT_RE = re.compile(r"biorxiv|medrxiv", re.I)


def is_server_preprint(article: dict) -> bool:
    blob = f"{article.get('source') or ''} {article.get('link') or ''}"
    if SERVER_PREPRINT_RE.search(blob):
        return True
    doi = extract_doi(article.get("link") or "") or ""
    return doi.startswith("10.1101/")


def preprint_url(article: dict) -> str | None:
    if not is_server_preprint(article):
        return None
    doi = extract_doi(article.get("link") or "")
    if doi:
        return f"https://doi.org/{doi}"
    link = str(article.get("link") or "")
    return link if link.startswith("http") else None


def epmc_is_free(record: dict) -> bool:
    if str(record.get("isOpenAccess") or "").upper() == "Y":
        return True
    if str(record.get("hasPDF") or "").upper() == "Y":
        return True
    urls = ((record.get("fullTextUrlList") or {}).get("fullTextUrl") or [])
    if isinstance(urls, dict):
        urls = [urls]
    for item in urls:
        if not isinstance(item, dict):
            continue
        code = str(item.get("availabilityCode") or "").upper()
        availability = str(item.get("availability") or "").lower()
        if code == "F" or availability in {"free", "open access"}:
            return True
    return False


def epmc_full_url(record: dict) -> str | None:
    pmcid = str(record.get("pmcid") or "").strip()
    if pmcid:
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/"
    urls = ((record.get("fullTextUrlList") or {}).get("fullTextUrl") or [])
    if isinstance(urls, dict):
        urls = [urls]
    html_url = ""
    for item in urls:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not url.startswith("http"):
            continue
        if str(item.get("documentStyle") or "").lower() == "html":
            html_url = url
            break
        html_url = html_url or url
    return html_url or None


def epmc_abstract_url(record: dict, doi: str) -> str:
    source = str(record.get("source") or "")
    ident = str(record.get("id") or "")
    if source and ident:
        return f"https://europepmc.org/article/{source}/{ident}"
    if doi:
        return f"https://doi.org/{doi}"
    return ""


def unpaywall_url(record: dict) -> str | None:
    location = record.get("best_oa_location") or {}
    if not isinstance(location, dict):
        return None
    for key in ("url_for_pdf", "url"):
        url = str(location.get(key) or "")
        if url.startswith("http"):
            return url
    return None


def crossref_is_oa(message: dict) -> bool:
    for item in message.get("license") or []:
        if isinstance(item, dict) and CC_LICENSE_RE.search(str(item.get("URL") or "")):
            return True
    return False


def crossref_has_abstract(message: dict) -> bool:
    return bool(strip_html(str(message.get("abstract") or "")).strip())


def decide_access(
    article: dict,
    *,
    epmc: dict | None,
    unpay: dict | None,
    xref: dict | None,
) -> tuple[str, str, str]:
    """Return access level, url, and which source supplied the link."""
    doi = extract_doi(article.get("link") or "") or ""
    if epmc and epmc_is_free(epmc):
        url = epmc_full_url(epmc) or (f"https://doi.org/{doi}" if doi else "")
        if url:
            return "full", url, "europepmc"
    if unpay and unpay.get("is_oa") and unpaywall_url(unpay):
        return "full", unpaywall_url(unpay) or "", "unpaywall"
    if xref and crossref_is_oa(xref) and doi:
        return "full", f"https://doi.org/{doi}", "crossref"
    preprint = preprint_url(article)
    if preprint:
        return "full", preprint, "preprint"
    if epmc and strip_html(str(epmc.get("abstractText") or "")).strip():
        return "abstract", epmc_abstract_url(epmc, doi), "europepmc"
    if xref and crossref_has_abstract(xref) and doi:
        return "abstract", f"https://doi.org/{doi}", "crossref"
    return "closed", "", ""


def fetch_json(url: str) -> tuple[dict | None, str | None, bool]:
    """Return payload, error, and whether the identifier was simply unknown."""
    raw, err = fetch_url(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    if err or not raw:
        missing = bool(err and "404" in err)
        return None, err or "empty response", missing
    if raw[:1] not in (b"{", b"["):
        return None, "not json", False
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"parse: {exc}", False
    if not isinstance(payload, dict):
        return None, "unexpected json", False
    return payload, None, False


def europepmc_record_for_doi(doi: str, results: list[dict]) -> dict | None:
    """Pick the Europe PMC hit for one DOI search.

    PMC-hosted records often omit the DOI field, so a single-hit search is
    still that paper. A DOI printed on the record wins when several hits return.
    """
    matched = [
        record
        for record in results
        if extract_doi(str(record.get("doi") or "")) == doi
    ]
    pool = matched or (results if len(results) == 1 else [])
    if not pool and results:
        # Several records and none repeat the DOI. Keep an open-access hit only.
        pool = [record for record in results if epmc_is_free(record)][:1]
    if not pool:
        return None
    pool.sort(key=lambda record: (0 if epmc_is_free(record) else 1, 0 if record.get("pmcid") else 1))
    return pool[0]


def europepmc_by_doi(dois: list[str]) -> tuple[dict[str, dict], set[str]]:
    found: dict[str, dict] = {}
    failed: set[str] = set()
    for index, doi in enumerate(dois):
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(
            {
                "query": f'DOI:"{doi}"',
                "format": "json",
                "resultType": "core",
                "pageSize": "5",
            }
        )
        payload, err, _missing = fetch_json(url)
        if err or not payload:
            failed.add(doi)
            continue
        results = ((payload.get("resultList") or {}).get("result") or [])
        if isinstance(results, dict):
            results = [results]
        record = europepmc_record_for_doi(doi, [item for item in results if isinstance(item, dict)])
        if record:
            found[doi] = record
        if index % 25 == 24:
            print(f"  Europe PMC {index + 1}/{len(dois)}")
        time.sleep(0.05)
    if failed:
        print(f"  Europe PMC access lookup failed for {len(failed)} DOI(s).")
    return found, failed


def annotate_access(articles: list[dict]) -> dict[str, int]:
    """Set access, access_url, and access_via from Unpaywall, Europe PMC, and Crossref.

    A source that fails leaves that article's previous values in place.
    bioRxiv and medRxiv preprints count as free full text even if a lookup fails.
    """
    dois: list[str] = []
    seen: set[str] = set()
    for article in articles:
        doi = extract_doi(article.get("link") or "")
        if doi and doi not in seen:
            seen.add(doi)
            dois.append(doi)

    epmc, epmc_failed = europepmc_by_doi(dois)

    unpay: dict[str, dict | None] = {}
    unpay_failed: set[str] = set()
    xref: dict[str, dict | None] = {}
    xref_failed: set[str] = set()
    for index, doi in enumerate(dois):
        payload, err, missing = fetch_json(
            "https://api.unpaywall.org/v2/"
            + urllib.parse.quote(doi, safe="/")
            + "?"
            + urllib.parse.urlencode({"email": UNPAYWALL_EMAIL})
        )
        if payload and not err:
            unpay[doi] = payload
        elif missing:
            unpay[doi] = None
        else:
            unpay_failed.add(doi)
        time.sleep(0.08)
        payload, err, missing = fetch_json("https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="/"))
        message = (payload or {}).get("message") if payload else None
        if isinstance(message, dict) and not err:
            xref[doi] = message
        elif missing:
            xref[doi] = None
        else:
            xref_failed.add(doi)
        if index % 25 == 24:
            print(f"  Access lookups {index + 1}/{len(dois)}")
        time.sleep(0.08)

    counts = {"full": 0, "abstract": 0, "closed": 0, "kept": 0}
    evidence_counts: dict[str, int] = {}
    for article in articles:
        doi = extract_doi(article.get("link") or "") or ""
        looked_up = bool(doi)
        failed = doi in unpay_failed or doi in xref_failed or doi in epmc_failed
        record = epmc.get(doi) if doi else None
        # A preprint still gets a full-text badge when the lookups fail.
        if looked_up and failed and not is_server_preprint(article) and doi not in epmc and doi not in unpay and doi not in xref:
            if article.get("access") in {"full", "abstract", "closed"}:
                counts["kept"] += 1
            apply_reading(article, None, failed=True)
            evidence_counts[article.get("evidence") or "Other"] = evidence_counts.get(article.get("evidence") or "Other", 0) + 1
            continue
        if not looked_up and not is_server_preprint(article):
            level, url, via = "closed", "", ""
        else:
            level, url, via = decide_access(
                article,
                epmc=record,
                unpay=unpay.get(doi) if doi in unpay else None,
                xref=xref.get(doi) if doi in xref else None,
            )
            if looked_up and failed and level == "closed" and article.get("access") in {"full", "abstract"}:
                counts["kept"] += 1
                apply_reading(article, record, failed=True)
                evidence_counts[article.get("evidence") or "Other"] = evidence_counts.get(article.get("evidence") or "Other", 0) + 1
                continue
        article["access"] = level
        article["access_url"] = url
        article["access_via"] = via
        counts[level] = counts.get(level, 0) + 1
        apply_reading(article, record, failed=bool(doi) and doi in epmc_failed and not record)
        evidence_counts[article.get("evidence") or "Other"] = evidence_counts.get(article.get("evidence") or "Other", 0) + 1
    print("Evidence labels: " + ", ".join(f"{count} {label}" for label, count in sorted(evidence_counts.items())))
    print(
        "Access badges: "
        f"{counts['full']} free full text, {counts['abstract']} free abstract, "
        f"{counts['closed']} no badge"
        + (f", {counts['kept']} kept previous" if counts["kept"] else "")
    )
    return counts


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


TRIALS_OUT = ROOT / "data" / "trials.json"
SERVIER_URL = (
    "https://clinicaltrials.servier.com/trial/"
    "a-first-in-human-study-of-s233107-in-participants-with-spinocerebellar-ataxia-type-3/"
)
EU_CT_URL = "https://euclinicaltrials.eu/ctis-public/view/2025-525111-17-00"
EU_CT_API = "https://euclinicaltrials.eu/ctis-public-api/retrieve/2025-525111-17-00"
OPEN_STATUSES = {"RECRUITING", "NOT_YET_RECRUITING"}


def human_status(raw: str) -> str:
    return (raw or "").replace("_", " ").strip().title() or "See registry"


def phase_label(phases: list) -> str:
    nums: list[str] = []
    for phase in phases or []:
        text = str(phase or "").upper()
        if text in {"", "NA", "N/A"}:
            continue
        nums.append(text.replace("PHASE", "").strip())
    if not nums:
        return "Phase not listed"
    return "Phase " + "/".join(nums)


def age_label(minimum: str, maximum: str) -> str:
    def clean(value: str) -> str:
        value = (value or "").strip()
        value = re.sub(r"\s*years?$", "", value, flags=re.I).strip()
        return value

    low, high = clean(minimum), clean(maximum)
    if low and high:
        return f"{low}–{high} years"
    if low:
        return f"{low} years and older"
    if high:
        return f"Up to {high} years"
    return "Age not listed"


def population_label(criteria: str) -> str:
    # Read the inclusion block. Exclusion lines often name the groups a trial refuses.
    inclusion = re.split(r"exclusion criteria", criteria or "", maxsplit=1, flags=re.I)[0]
    pre = re.search(
        r"pre-?symptomatic|pre-?manifest|pre-?ataxic|SARA\s*(?:<|less than)\s*4",
        inclusion,
        re.I,
    )
    symptomatic = re.search(
        r"symptomatic|mild to moderate|clinical symptoms|ataxia severity|\bSARA\b|diagnos",
        inclusion,
        re.I,
    )
    if pre and symptomatic:
        return "Symptomatic and premanifest"
    if pre:
        return "Premanifest"
    if symptomatic:
        return "Symptomatic"
    return "See registry criteria"


def sca3_trial_text(title: str, conditions: list, criteria: str) -> bool:
    # The summary alone is not enough: an unrelated trial can mention SCA3 in passing.
    blob = " ".join([title, " ".join(conditions or []), criteria or ""])
    if STRONG_RE.search(blob):
        return True
    return bool(ATXN3_RE.search(blob) and ATAXIA_RE.search(blob))


def study_to_trial(study: dict) -> dict | None:
    proto = study.get("protocolSection") or {}
    ident = proto.get("identificationModule") or {}
    status = proto.get("statusModule") or {}
    design = proto.get("designModule") or {}
    desc = proto.get("descriptionModule") or {}
    cond = proto.get("conditionsModule") or {}
    elig = proto.get("eligibilityModule") or {}
    contacts = proto.get("contactsLocationsModule") or {}
    sponsor = (proto.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}
    arms = proto.get("armsInterventionsModule") or {}
    nct = ident.get("nctId") or ""
    title = ident.get("briefTitle") or ident.get("officialTitle") or ""
    conditions = cond.get("conditions") or []
    summary = desc.get("briefSummary") or ""
    criteria = elig.get("eligibilityCriteria") or ""
    overall = status.get("overallStatus") or ""
    if overall not in OPEN_STATUSES:
        return None
    if (design.get("studyType") or "").upper() != "INTERVENTIONAL":
        return None
    if not sca3_trial_text(title, conditions, criteria):
        return None
    sites = []
    seen = set()
    for loc in contacts.get("locations") or []:
        facility_raw = loc.get("facility")
        if isinstance(facility_raw, dict):
            facility = facility_raw.get("name") or ""
        else:
            facility = facility_raw or ""
        city = loc.get("city") or ""
        country = loc.get("country") or ""
        key = (facility.lower(), city.lower(), country.lower())
        if key in seen:
            continue
        seen.add(key)
        sites.append(
            {
                "facility": facility,
                "city": city,
                "country": country,
                "status": human_status(loc.get("status") or ""),
            }
        )
    countries = []
    for site in sites:
        if site["country"] and site["country"] not in countries:
            countries.append(site["country"])
    interventions = []
    for item in arms.get("interventions") or []:
        name = (item.get("name") or "").strip()
        kind = (item.get("type") or "").replace("_", " ").strip().title()
        if name:
            interventions.append(f"{kind}: {name}" if kind else name)
    updated = ((status.get("lastUpdatePostDateStruct") or {}).get("date") or "")
    return {
        "id": nct,
        "nct_id": nct,
        "eu_ct": "",
        "title": strip_html(title),
        "sponsor": sponsor.get("name") or "",
        "phase": phase_label(design.get("phases") or []),
        "status": human_status(overall),
        "status_key": "recruiting" if overall == "RECRUITING" else "not_yet",
        "study_type": "Interventional",
        "ages": age_label(elig.get("minimumAge") or "", elig.get("maximumAge") or ""),
        "population": population_label(criteria),
        "countries": countries,
        "sites": sites,
        "interventions": interventions,
        "summary": strip_html(summary).strip(),
        "eligibility": strip_html(criteria).strip(),
        "links": [
            {
                "label": "ClinicalTrials.gov",
                "url": f"https://clinicaltrials.gov/study/{nct}",
            }
        ],
        "registry_updated": updated,
        "checked_registry": "ClinicalTrials.gov",
        "checked_date": updated,
        "checked_label": checked_label("ClinicalTrials.gov", updated),
        "source": "ClinicalTrials.gov API v2",
    }


def fetch_open_ctgov() -> tuple[list[dict], str | None]:
    found: list[dict] = []
    token = None
    for _ in range(8):
        params = {
            "query.term": 'SCA3 OR "Machado-Joseph" OR ATXN3 OR "spinocerebellar ataxia type 3"',
            "filter.overallStatus": "RECRUITING,NOT_YET_RECRUITING",
            "pageSize": "100",
        }
        if token:
            params["pageToken"] = token
        url = "https://clinicaltrials.gov/api/v2/studies?" + urllib.parse.urlencode(params)
        raw, err = fetch_url(url, headers={"Accept": "application/json"})
        if err or not raw:
            return found, err or "empty ClinicalTrials.gov response"
        head = raw[:120].lstrip().lower()
        if head.startswith(b"<!doctype") or head.startswith(b"<html"):
            return found, "ClinicalTrials.gov returned HTML"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return found, f"parse: {exc}"
        for study in payload.get("studies") or []:
            trial = study_to_trial(study)
            if trial:
                found.append(trial)
        token = payload.get("nextPageToken")
        if not token:
            break
        time.sleep(0.2)
    return found, None


def servier_registry_is_open() -> tuple[bool | None, str]:
    """Read the public CTIS record for S233107.

    The public trial page shows this as ctStatus. On 17 October 2026 that page
    said "End of trial 11/09/2026" and marked Denmark, France, Spain, and
    Portugal Ended, reason Sponsor Decision. Servier's own page still said
    not yet recruiting. An unreadable registry is not treated as permission
    to list the study as open.
    """
    raw, err = fetch_url(
        EU_CT_API,
        headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
    )
    if err or not raw:
        return None, err or "empty EU CTIS response"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"EU CTIS parse: {exc}"
    status = str(payload.get("ctStatus") or "").strip()
    end = str(payload.get("endDateEU") or "").strip()
    if status.lower() == "ended":
        when = f", end date {end}" if end else ""
        return False, f"EU CTIS lists this trial as Ended{when}"
    if status.lower() in {"authorised", "authorized", "ongoing"}:
        return True, status
    if not status:
        return None, "EU CTIS response had no trial status"
    return False, f"EU CTIS status {status} is not an open trial"


def fetch_servier_trial() -> tuple[dict | None, str | None]:
    open_now, reason = servier_registry_is_open()
    if open_now is not True:
        return None, reason
    raw, err = fetch_url(SERVIER_URL, headers={"User-Agent": BROWSER_UA, "Accept": "text/html"})
    if err or not raw:
        return None, err or "empty Servier page"
    text = html.unescape(raw.decode("utf-8", "replace"))
    if "S233107" not in text or ("NOT_YET_RECRUITING" not in text and "Not yet recruiting" not in text):
        return None, "Servier page did not contain the expected trial text"
    sites = []
    seen = set()
    for match in re.finditer(
        r'"name":"(?P<name>(?:\\.|[^"\\])*)","latitude":"[^"]*","longitude":"[^"]*",'
        r'"city":"(?P<city>(?:\\.|[^"\\])*)","zip":"[^"]*","country":"(?P<country>(?:\\.|[^"\\])*)",'
        r'"status":"(?P<status>[A-Z_]+)"',
        text,
    ):
        name = json.loads(f'"{match.group("name")}"')
        city = json.loads(f'"{match.group("city")}"')
        country = json.loads(f'"{match.group("country")}"')
        key = (name.lower(), city.lower(), country.lower())
        if key in seen:
            continue
        seen.add(key)
        sites.append(
            {
                "facility": name,
                "city": city,
                "country": country,
                "status": human_status(match.group("status")),
            }
        )
    countries = []
    for site in sites:
        if site["country"] not in countries:
            countries.append(site["country"])
    status_key = "not_yet" if "Not yet recruiting" in text or "NOT_YET_RECRUITING" in text else "see_registry"
    status = "Not Yet Recruiting" if status_key == "not_yet" else "See registry"
    note = ""
    if "More centres in the US and Japan will be added soon." in text:
        note = "The sponsor page says more centres in the US and Japan will be added soon."
    trial = {
        "id": "EU-CT-2025-525111-17-00",
        "nct_id": "",
        "eu_ct": "2025-525111-17-00",
        "title": "A first-in-human study of S233107 in participants with spinocerebellar ataxia type 3",
        "sponsor": "Servier",
        "phase": "Phase 1b/2a",
        "status": status,
        "status_key": status_key,
        "study_type": "Interventional",
        "ages": "18–65 years",
        "population": "Symptomatic",
        "countries": countries,
        "sites": sites,
        "interventions": ["Drug: S233107", "Drug: Placebo"],
        "summary": (
            "Servier’s page describes a randomized, placebo-controlled, multiple-ascending-dose study "
            "of S233107 given into the spinal fluid, followed by an open-label extension. "
            "The drug is described as designed to reduce the faulty ataxin-3 protein. "
            "Inclusion on the page: a genetic diagnosis of SCA3, age 18 to 65, and the ability to walk without support. "
            "Healthy volunteers are not accepted. The page’s age field also lists 18 to 64 years."
            + ((" " + note) if note else "")
        ),
        "eligibility": (
            "Inclusion, as published on the sponsor page: confirmed diagnosis of SCA3 by genetic test; "
            "adult aged 18 to 65 years; able to walk without support. "
            "Exclusion: other types of brain disorders, major reduced kidney function, or a history or presence of significant suicide risk. "
            "The page does not describe a premanifest group."
        ),
        "links": [
            {"label": "Servier trial page", "url": SERVIER_URL},
            {"label": "EU CTIS", "url": EU_CT_URL},
        ],
        "registry_updated": "",
        "source": "Servier public trial page; EU CT 2025-525111-17-00",
    }
    return trial, None


def previous_servier_trial() -> dict | None:
    if not TRIALS_OUT.exists():
        return None
    try:
        payload = json.loads(TRIALS_OUT.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    for trial in payload.get("trials") or []:
        if trial.get("eu_ct") == "2025-525111-17-00" or "S233107" in (trial.get("title") or ""):
            return trial
    return None


def refresh_open_trials() -> None:
    """Write data/trials.json. A failed source keeps the last good file."""
    TRIALS_OUT.parent.mkdir(parents=True, exist_ok=True)
    ctgov, ct_err = fetch_open_ctgov()
    servier, servier_err = fetch_servier_trial()
    if ct_err and not ctgov and servier is None:
        print(f"Open trials refresh skipped ({ct_err}; Servier: {servier_err}). Keeping {TRIALS_OUT.name}.")
        return
    trials = list(ctgov)
    ids = {trial.get("nct_id") for trial in trials}
    titles = " ".join(trial.get("title") or "" for trial in trials)
    ended = bool(servier_err and "lists this trial as Ended" in servier_err)
    if servier and "S233107" not in titles and servier["id"] not in ids:
        trials.append(servier)
    elif ended:
        print(f"Servier trial left off the open list: {servier_err}")
    elif servier is None:
        kept = previous_servier_trial()
        if kept:
            trials.append(kept)
            print(f"Servier trial page skipped ({servier_err}). Kept the previous Servier record.")
        else:
            print(f"Servier trial page skipped: {servier_err}")
    if not trials:
        print("Open trials refresh found nothing. Keeping the previous file.")
        return
    order = {"recruiting": 0, "not_yet": 1}
    trials.sort(key=lambda trial: (order.get(trial.get("status_key"), 9), trial.get("title") or ""))
    payload = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "query": "SCA3 OR Machado-Joseph OR ATXN3, interventional, recruiting or not yet recruiting",
        "sources": [
            {
                "id": "clinicaltrials",
                "ok": ct_err is None,
                "error": ct_err,
                "items": len(ctgov),
            },
            {
                "id": "servier-eu",
                "ok": servier is not None,
                "error": servier_err,
                "items": 1 if servier else 0,
            },
        ],
        "trial_count": len(trials),
        "trials": trials,
    }
    TRIALS_OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(trials)} open trials → {TRIALS_OUT}")


STOCKS_OUT = ROOT / "data" / "stocks.json"
# Public companies on the Who's working page. Stooq is tried first.
# Yahoo's chart endpoint is the fallback when Stooq does not return CSV.
STOCK_SYMBOLS = [
    {"id": "BHVN", "stooq": "bhvn.us", "yahoo": "BHVN", "exchange": "NYSE", "currency": "USD"},
    {"id": "BIIB", "stooq": "biib.us", "yahoo": "BIIB", "exchange": "NasdaqGS", "currency": "USD"},
    {"id": "SRPT", "stooq": "srpt.us", "yahoo": "SRPT", "exchange": "NasdaqGS", "currency": "USD"},
    {"id": "4978.T", "stooq": "4978.jp", "yahoo": "4978.T", "exchange": "Tokyo", "currency": "JPY"},
]


def load_previous_stocks() -> dict:
    if not STOCKS_OUT.exists():
        return {}
    try:
        payload = json.loads(STOCKS_OUT.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def round_price(value: float, currency: str) -> float:
    if currency == "JPY":
        return round(value, 1) if abs(value - round(value)) >= 0.05 else float(round(value))
    return round(value, 2)


def quote_from_closes(closes: list[tuple[str, float]], currency: str) -> dict | None:
    usable = [(day, price) for day, price in closes if price is not None and price > 0]
    if len(usable) < 2:
        return None
    prev_day, previous = usable[-2]
    day, close = usable[-1]
    change = (close - previous) / previous * 100
    return {
        "close": round_price(close, currency),
        "previous_close": round_price(previous, currency),
        "change_percent": round(change, 2),
        "as_of": day,
        "previous_as_of": prev_day,
    }


def parse_stooq_csv(raw: bytes, currency: str) -> dict | None:
    text = raw.decode("utf-8", "replace").strip()
    if not text or text.lstrip().startswith("<") or "javascript" in text[:400].lower():
        return None
    closes: list[tuple[str, float]] = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 5 or not re.match(r"\d{4}-\d{2}-\d{2}", parts[0]):
            continue
        try:
            closes.append((parts[0], float(parts[4])))
        except ValueError:
            continue
    return quote_from_closes(closes, currency)


def parse_yahoo_chart(raw: bytes, fallback_exchange: str, fallback_currency: str) -> dict | None:
    try:
        payload = json.loads(raw.decode("utf-8"))
        result = (payload.get("chart") or {}).get("result") or []
        if not result:
            return None
        row = result[0]
        meta = row.get("meta") or {}
        timestamps = row.get("timestamp") or []
        quote = ((row.get("indicators") or {}).get("quote") or [{}])[0]
        closes_raw = quote.get("close") or []
    except (UnicodeDecodeError, json.JSONDecodeError, IndexError, TypeError):
        return None
    closes: list[tuple[str, float]] = []
    for stamp, price in zip(timestamps, closes_raw):
        if price is None:
            continue
        day = datetime.fromtimestamp(int(stamp), timezone.utc).date().isoformat()
        try:
            closes.append((day, float(price)))
        except (TypeError, ValueError):
            continue
    currency = str(meta.get("currency") or fallback_currency)
    parsed = quote_from_closes(closes, currency)
    if not parsed:
        return None
    parsed["exchange"] = str(meta.get("fullExchangeName") or fallback_exchange)
    parsed["currency"] = currency
    parsed["source"] = "yahoo-chart"
    return parsed


def fetch_stock_quote(spec: dict) -> tuple[dict | None, str | None]:
    stooq_url = "https://stooq.com/q/d/l/?s=" + urllib.parse.quote(spec["stooq"]) + "&i=d"
    raw, err = fetch_url(stooq_url, headers={"User-Agent": BROWSER_UA, "Accept": "text/csv,*/*"})
    parsed = parse_stooq_csv(raw, spec["currency"]) if raw else None
    if parsed:
        parsed["exchange"] = spec["exchange"]
        parsed["currency"] = spec["currency"]
        parsed["source"] = "stooq"
        parsed["symbol"] = spec["id"]
        return parsed, None
    yahoo_url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(spec["yahoo"])
        + "?interval=1d&range=1mo"
    )
    raw, yahoo_err = fetch_url(
        yahoo_url,
        headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
    )
    parsed = parse_yahoo_chart(raw, spec["exchange"], spec["currency"]) if raw else None
    if parsed:
        parsed["symbol"] = spec["id"]
        return parsed, None
    return None, yahoo_err or err or "no daily closes"


def refresh_stocks() -> None:
    """Write data/stocks.json. A symbol that fails keeps its last good quote."""
    STOCKS_OUT.parent.mkdir(parents=True, exist_ok=True)
    previous = load_previous_stocks()
    quotes = dict(previous.get("quotes") or {})
    errors: list[str] = []
    refreshed = 0
    for spec in STOCK_SYMBOLS:
        quote, err = fetch_stock_quote(spec)
        if quote:
            quotes[spec["id"]] = quote
            refreshed += 1
            print(
                f"  {spec['id']} {quote['close']} {quote['currency']} "
                f"({quote['change_percent']:+.2f}%) as of {quote['as_of']} via {quote['source']}"
            )
        else:
            errors.append(f"{spec['id']}: {err}")
            if spec["id"] in quotes:
                print(f"  {spec['id']} failed ({err}). Kept the previous quote.")
            else:
                print(f"  {spec['id']} failed ({err}).")
        time.sleep(0.2)
    if refreshed == 0 and previous.get("quotes"):
        print(f"Stock refresh failed. Keeping last good {STOCKS_OUT.name}.")
        return
    payload = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "Delayed last close from a public daily price history.",
        "quotes": quotes,
        "errors": errors,
    }
    STOCKS_OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(quotes)} stock quotes → {STOCKS_OUT}")


REVIEW_PUB_RE = re.compile(r"\breview\b|meta-analysis", re.I)
TRIAL_PUB_RE = re.compile(
    r"clinical trial|randomized controlled|controlled clinical|clinical study|"
    r"pragmatic clinical|adaptive clinical|equivalence trial",
    re.I,
)
OBS_PUB_RE = re.compile(r"observational study", re.I)
ANIMAL_RE = re.compile(
    r"\b(mouse|mice|murine|rats?|drosophila|zebrafish|macaque|c\.?\s*elegans|knock-?in)\b",
    re.I,
)
CELL_RE = re.compile(
    r"\b(in vitro|cell lines?|fibroblasts?|iPSCs?|induced pluripotent|organoids?|"
    r"HEK293|cultured neurons|primary neurons)\b",
    re.I,
)
OBS_RE = re.compile(r"\b(cohort|cross-sectional|case-control|observational|natural history)\b", re.I)
HUMAN_RE = re.compile(r"\b(patients?|participants?|people with|subjects)\b", re.I)
TRIAL_WORD_RE = re.compile(r"\b(randomi[sz]ed|placebo-controlled|phase\s*[0-4]|clinical trial)\b", re.I)
REVIEW_TITLE_RE = re.compile(
    r"\b(systematic review|meta-analysis|literature review|narrative review|scoping review)\b|"
    r"^\s*(a\s+)?review\b",
    re.I,
)
RESULT_RE = re.compile(
    r"\b(found|showed|shown|suggests?|(?<!-)associated|observed|concludes?|concluded|"
    r"indicates?|indicated|reported|(?<!-)reduced|(?<!-)increased|identified|demonstrated)\b",
    re.I,
)
METHODS_RE = re.compile(
    r"^(background|methods|objective|objectives|aim|purpose)\b|"
    r"\b(we (describe|present|aimed|sought|investigated|conducted|tested|measured|quantified|performed|used)|here we)\b",
    re.I,
)
HYPE_RE = re.compile(
    r"\b(breakthrough|revolutionary|game[- ]changing|miracle|cures?|promising|exciting|first-ever)\b",
    re.I,
)
EVIDENCE_LABELS = (
    "Review of past research",
    "Treatment trial in people",
    "Study in people",
    "Animal study",
    "Lab study (cells)",
    "Early, not yet peer-reviewed",
    "Research paper",
    "News",
)

FRIENDLY_EVIDENCE = {
    "Review": "Review of past research",
    "Human trial": "Treatment trial in people",
    "Human observational study": "Study in people",
    "Animal study": "Animal study",
    "Cell/lab study": "Lab study (cells)",
    "Preprint (not peer-reviewed)": "Early, not yet peer-reviewed",
    "Other": "Research paper",
    "News": "News",
}

def as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def epmc_pub_types(record: dict | None) -> list[str]:
    if not isinstance(record, dict):
        return []
    block = record.get("pubTypeList") or {}
    if not isinstance(block, dict):
        return []
    return [str(item).strip() for item in as_list(block.get("pubType")) if str(item).strip()]


def epmc_mesh(record: dict | None) -> list[str]:
    if not isinstance(record, dict):
        return []
    block = record.get("meshHeadingList") or {}
    if not isinstance(block, dict):
        return []
    terms: list[str] = []
    for head in as_list(block.get("meshHeading")):
        if isinstance(head, dict):
            name = str(head.get("descriptorName") or "").strip()
        else:
            name = str(head).strip()
        if name:
            terms.append(name)
    return terms


def classify_evidence(article: dict, record: dict | None = None) -> tuple[str, str]:
    """Pick one evidence label. Unclear items stay Other."""
    kind = article.get("kind") or ""
    if kind == "news":
        return "Other", "kind"
    if kind == "preprint" or is_server_preprint(article):
        return "Preprint (not peer-reviewed)", "kind"
    pub = " ".join(epmc_pub_types(record))
    mesh = [term.lower() for term in epmc_mesh(record)]
    if record and re.search(r"preprint", pub, re.I):
        return "Preprint (not peer-reviewed)", "europepmc-pubtype"
    if record and REVIEW_PUB_RE.search(pub):
        return "Review", "europepmc-pubtype"
    if kind == "trial":
        if re.search(r"natural history|individuals at risk|at-risk|prospective cohort", title_blob(article), re.I):
            return "Human observational study", "kind"
        return "Human trial", "kind"
    if record and TRIAL_PUB_RE.search(pub) and "protocol" not in pub.lower():
        return "Human trial", "europepmc-pubtype"
    if record and OBS_PUB_RE.search(pub):
        return "Human observational study", "europepmc-pubtype"
    mesh_animal = any(term in {"animals", "mice", "rats", "drosophila", "zebrafish"} for term in mesh)
    mesh_human = "humans" in mesh
    if mesh_animal and not mesh_human:
        return "Animal study", "mesh"
    title = article.get("title") or ""
    summary = article.get("summary") or ""
    blob = f"{title}. {summary}"
    if mesh_human and not mesh_animal and TRIAL_WORD_RE.search(blob) and not ANIMAL_RE.search(title):
        return "Human trial", "mesh"
    if mesh_human and not mesh_animal and OBS_RE.search(blob) and not ANIMAL_RE.search(title):
        return "Human observational study", "mesh"
    if ANIMAL_RE.search(title) and HUMAN_RE.search(title):
        return "Other", "unclear"
    if (
        HUMAN_RE.search(title)
        and not ANIMAL_RE.search(title)
        and not CELL_RE.search(title)
        and re.search(r"cohort|pilot study|cross-sectional|case-control|observational|natural history|\bMRI\b|correlat|biomarker", title, re.I)
    ):
        return "Human observational study", "keywords"
    if ANIMAL_RE.search(title) and CELL_RE.search(title):
        return "Other", "unclear"
    reviewish = bool(REVIEW_TITLE_RE.search(title))
    animalish = bool(ANIMAL_RE.search(title) or (ANIMAL_RE.search(summary) and not HUMAN_RE.search(title)))
    cellish = bool(CELL_RE.search(title) or (CELL_RE.search(summary) and not HUMAN_RE.search(title)))
    trialish = bool(TRIAL_WORD_RE.search(title) and HUMAN_RE.search(blob) and not animalish)
    obsish = bool(OBS_RE.search(blob) and HUMAN_RE.search(blob) and not animalish and not trialish)
    signals = [
        name
        for name, flag in (
            ("Review", reviewish),
            ("Human trial", trialish),
            ("Human observational study", obsish),
            ("Animal study", animalish and not cellish),
            ("Cell/lab study", cellish and not animalish),
        )
        if flag
    ]
    if len(signals) == 1:
        return signals[0], "keywords"
    return "Other", "unclear"


def title_blob(article: dict) -> str:
    return f"{article.get('title') or ''} {article.get('summary') or ''}"


def family_line_from_text(text: str) -> str:
    """One cautious sentence taken from an abstract. No abstract means no line."""
    raw = strip_html(text or "")
    raw = re.sub(r"^(abstract|summary)\b[:\s-]*", "", raw, flags=re.I)
    raw = re.sub(
        r"\b(Background|Objective|Objectives|Methods|Results|Conclusions|Purpose)\s*[:\-]\s*",
        "",
        raw,
        flags=re.I,
    )
    raw = re.sub(r"\s+", " ", raw).strip()
    if len(raw) < 80:
        return ""
    sentences: list[str] = []
    for part in re.split(r"(?<=[.!?])\s+", raw):
        sentence = HYPE_RE.sub("", part)
        sentence = re.sub(r"\s+", " ", sentence).strip(" ;,-")
        sentence = re.sub(r"\s+([,.;:])", r"\1", sentence)
        if len(sentence) < 40:
            continue
        if sentence.lower().startswith(("http", "doi", "copyright")):
            continue
        sentences.append(sentence)
    if not sentences:
        return ""

    def rank(sentence: str) -> int:
        score = 0
        if RESULT_RE.search(sentence):
            score += 3
        if METHODS_RE.search(sentence):
            score -= 2
        return score

    best = max(enumerate(sentences), key=lambda pair: (rank(pair[1]), pair[0]))[1]
    if rank(best) < 1 or len(best.split()) < 8:
        return ""
    if len(best) > 220:
        cut = best[:217]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        best = cut.rstrip(",;:") + "…"
    if len(best) < 40:
        return ""
    if best[-1] not in ".!?…":
        best += "."
    return best


def present_evidence(raw: str) -> str:
    if raw in EVIDENCE_LABELS:
        return raw
    return FRIENDLY_EVIDENCE.get(raw, "Research paper")


def apply_reading(article: dict, record: dict | None, *, failed: bool) -> None:
    raw, via = classify_evidence(article, None if failed and not record else record)
    label = present_evidence(raw)
    previous = present_evidence(article.get("evidence") or "")
    if failed and not record and previous in EVIDENCE_LABELS and previous != "Research paper" and via == "unclear":
        label, via = previous, article.get("evidence_via") or "kept"
    if (article.get("kind") or "") == "news":
        label, via = "News", "kind"
    article["evidence"] = label
    article["evidence_via"] = via
    article["family_line"] = ""


def pretty_day(value: str) -> str:
    text = (value or "")[:10]
    try:
        found = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return ""
    return f"{found.day} {found.strftime('%b')} {found.year}"


def checked_label(registry: str, record_date: str) -> str:
    day = pretty_day(record_date)
    if day:
        return f"Checked against {registry}, {day}"
    return f"Checked against {registry}"


REGISTRY_OUT = ROOT / "data" / "registry.json"
CHANGES_OUT = ROOT / "data" / "changes.json"
REGISTRY_NCTS = (
    "NCT05822908",
    "NCT03378414",
    "NCT03701399",
    "NCT05160558",
    "NCT07743866",
    "NCT07752095",
    "NCT07019558",
)


def load_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def ctgov_study_record(nct: str) -> tuple[dict | None, str | None]:
    url = f"https://clinicaltrials.gov/api/v2/studies/{nct}"
    raw, err = fetch_url(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    if err or not raw:
        return None, err or "empty ClinicalTrials.gov study"
    head = raw[:120].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        return None, "ClinicalTrials.gov returned HTML"
    try:
        study = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"parse: {exc}"
    proto = study.get("protocolSection") or {}
    status = proto.get("statusModule") or {}
    overall = str(status.get("overallStatus") or "").strip()
    updated = str((status.get("lastUpdatePostDateStruct") or {}).get("date") or "")
    if not overall:
        return None, "ClinicalTrials.gov study had no overall status"
    return {
        "id": nct,
        "registry": "ClinicalTrials.gov",
        "status": human_status(overall),
        "record_date": updated,
        "label": checked_label("ClinicalTrials.gov", updated),
        "url": f"https://clinicaltrials.gov/study/{nct}",
    }, None


def ctis_study_record() -> tuple[dict | None, str | None]:
    raw, err = fetch_url(
        EU_CT_API,
        headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
    )
    if err or not raw:
        return None, err or "empty EU CTIS response"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"EU CTIS parse: {exc}"
    status = str(payload.get("ctStatus") or "").strip()
    if not status:
        return None, "EU CTIS response had no trial status"
    published = str(payload.get("publishDate") or "")
    end = str(payload.get("endDateEU") or "")
    shown = human_status(status)
    if status.lower() == "ended" and end:
        end_day = pretty_day(end)
        shown = f"Ended, end of trial {end_day}" if end_day else "Ended"
    return {
        "id": "2025-525111-17-00",
        "registry": "EU CTIS",
        "status": shown,
        "record_date": published[:10],
        "label": checked_label("EU CTIS", published),
        "url": EU_CT_URL,
    }, None


def refresh_registry() -> None:
    """Write data/registry.json from ClinicalTrials.gov v2 and the public CTIS record."""
    REGISTRY_OUT.parent.mkdir(parents=True, exist_ok=True)
    previous = load_json_file(REGISTRY_OUT).get("records") or {}
    if not isinstance(previous, dict):
        previous = {}
    records = dict(previous)
    errors: list[str] = []
    for nct in REGISTRY_NCTS:
        record, err = ctgov_study_record(nct)
        if record:
            records[nct] = record
            print(f"  {nct} {record['status']} ({record['label']})")
        else:
            errors.append(f"{nct}: {err}")
            if nct in records:
                print(f"  {nct} failed ({err}). Kept the previous registry record.")
            else:
                print(f"  {nct} failed ({err}).")
        time.sleep(0.15)
    record, err = ctis_study_record()
    if record:
        records[record["id"]] = record
        print(f"  {record['id']} {record['status']} ({record['label']})")
    else:
        errors.append(f"2025-525111-17-00: {err}")
        if "2025-525111-17-00" in records:
            print(f"  CTIS failed ({err}). Kept the previous registry record.")
        else:
            print(f"  CTIS failed ({err}).")
    payload = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "records": records,
        "errors": errors,
    }
    REGISTRY_OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    apply_registry_to_open_trials(records)
    print(f"Wrote {len(records)} registry records → {REGISTRY_OUT}")


def apply_registry_to_open_trials(records: dict) -> None:
    payload = load_json_file(TRIALS_OUT)
    trials = payload.get("trials")
    if not isinstance(trials, list):
        return
    kept: list[dict] = []
    for trial in trials:
        if not isinstance(trial, dict):
            continue
        nct = str(trial.get("nct_id") or "")
        record = records.get(nct) if nct else None
        if not record:
            kept.append(trial)
            continue
        trial["checked_registry"] = record.get("registry") or "ClinicalTrials.gov"
        trial["checked_date"] = record.get("record_date") or ""
        trial["checked_label"] = record.get("label") or checked_label("ClinicalTrials.gov", "")
        status = str(record.get("status") or "")
        lowered = status.lower()
        if lowered == "recruiting":
            trial["status"] = status
            trial["status_key"] = "recruiting"
            kept.append(trial)
        elif "not yet recruiting" in lowered:
            trial["status"] = status
            trial["status_key"] = "not_yet"
            kept.append(trial)
        else:
            print(f"Open trials dropped {nct}: ClinicalTrials.gov status is {status}.")
    payload["trials"] = kept
    payload["trial_count"] = len(kept)
    TRIALS_OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_trials() -> list[dict]:
    trials = load_json_file(TRIALS_OUT).get("trials") or []
    if not isinstance(trials, list):
        return []
    return [item for item in trials if isinstance(item, dict)]


def write_changes(previous_articles: list[dict], articles: list[dict], previous_trials: list[dict]) -> None:
    """What changed this month, diffed against the previous ledger when one exists."""
    month = f"{TODAY.year:04d}-{TODAY.month:02d}"
    month_label = f"{TODAY.strftime('%B')} {TODAY.year}"
    had_previous = bool(previous_articles or previous_trials)
    known: set[str] = set()
    for article in previous_articles:
        known.update(canonical_keys(article))
    papers: list[dict] = []
    for article in articles:
        if str(article.get("date") or "")[:7] != month:
            continue
        keys = set(canonical_keys(article))
        is_new = not (keys & known) if had_previous else False
        evidence = present_evidence(article.get("evidence") or "")
        tags = article.get("tags") or []
        notable = is_new or evidence in {
            "Review of past research",
            "Treatment trial in people",
            "Study in people",
            "Early, not yet peer-reviewed",
        } or any(
            tag in tags for tag in ("Clinical trial", "Therapy/ASO", "Gene therapy", "Review")
        )
        if not notable:
            continue
        papers.append(
            {
                "title": article.get("title") or "",
                "link": article.get("link") or "",
                "date": article.get("date") or "",
                "source": article.get("source") or "",
                "evidence": evidence,
                "new_since_refresh": is_new,
            }
        )
    if not papers:
        for article in articles:
            if str(article.get("date") or "")[:7] != month:
                continue
            papers.append(
                {
                    "title": article.get("title") or "",
                    "link": article.get("link") or "",
                    "date": article.get("date") or "",
                    "source": article.get("source") or "",
                    "evidence": present_evidence(article.get("evidence") or ""),
                    "new_since_refresh": False,
                }
            )
    papers.sort(key=lambda item: item.get("date") or "", reverse=True)
    papers.sort(key=lambda item: not item["new_since_refresh"])
    papers = papers[:6]
    previous_map = {str(trial.get("nct_id") or trial.get("id") or ""): trial for trial in previous_trials}
    current = load_trials()
    current_map = {str(trial.get("nct_id") or trial.get("id") or ""): trial for trial in current}
    new_trials = []
    status_changes = []
    removed_trials = []
    for key, trial in current_map.items():
        if not key:
            continue
        old = previous_map.get(key)
        if old is None:
            if had_previous:
                new_trials.append(
                    {"id": key, "title": trial.get("title") or "", "status": trial.get("status") or ""}
                )
        elif (old.get("status") or "") != (trial.get("status") or ""):
            status_changes.append(
                {
                    "id": key,
                    "title": trial.get("title") or "",
                    "from": old.get("status") or "",
                    "to": trial.get("status") or "",
                }
            )
    for key, trial in previous_map.items():
        if key and key not in current_map:
            removed_trials.append(
                {
                    "id": key,
                    "title": trial.get("title") or "",
                    "from": trial.get("status") or "",
                    "to": "No longer on the open-trial list",
                }
            )
    payload = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "month": month,
        "label": month_label,
        "basis": (
            "Compared with the ledger and open-trial list saved before this refresh. Papers are limited to this month."
            if had_previous
            else "No earlier ledger was available to diff. Papers are items dated this month."
        ),
        "new_trials": new_trials,
        "status_changes": status_changes,
        "removed_trials": removed_trials,
        "papers": papers,
    }
    CHANGES_OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"Changes for {month_label}: {len(new_trials)} new trials, "
        f"{len(status_changes)} status changes, {len(removed_trials)} left the open list, "
        f"{len(papers)} papers."
    )


SUMMARIES_OUT = ROOT / "data" / "summaries.json"
SUMMARY_MODEL = os.environ.get("SCA3_SUMMARY_MODEL", "openai/gpt-4.1-mini")
SUMMARY_URL = os.environ.get("SCA3_SUMMARY_URL", "https://models.github.ai/inference/chat/completions")
SUMMARY_ABOUT = (
    "Plain-language summaries cached by DOI or id. The daily refresh asks GitHub Models "
    "once per uncached paper that has an abstract, and stores only a reply that stays "
    "inside that abstract. A failed call, or a paper with no abstract, leaves no summary."
)
SUMMARY_SYSTEM = (
    "You write one or two plain-language sentences for a parent about one scientific abstract. "
    "Use only facts stated in the abstract. Name what the study did and what it found, including "
    "important numbers the abstract states. Explain jargon in ordinary words. "
    "Do not add context, advice, or caveats the abstract does not state. "
    "Do not use: may, might, could, suggests, preliminary, promising, not a treatment, "
    "not medical advice, does not mean, more research, not a promise, not an approval. "
    "Do not label the kind of study and do not hype. "
    "If the abstract does not state a finding, reply with exactly NONE."
)
SUMMARY_HEDGE_RE = re.compile(
    r"\b(?:may|might|could|suggests?|preliminary|promising)\b|"
    r"not a treatment|not medical|does not mean|more research|not a promise|not an approval|"
    r"other scientists have not|by itself",
    re.I,
)
_summary_token_warned = False


def summary_cache_keys(item: dict) -> list[str]:
    keys: list[str] = []
    ident = str(item.get("id") or "").strip().lower()
    doi = extract_doi(str(item.get("link") or "")) or extract_doi(ident)
    if doi:
        keys.append(doi)
    if ident:
        keys.append(ident)
    link = str(item.get("link") or "").strip().lower()
    if link:
        keys.append(link)
    return keys


def load_summary_items() -> list[dict]:
    payload = load_json_file(SUMMARIES_OUT)
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and str(item.get("summary") or "").strip()]


def accept_model_summary(content: str) -> str | None:
    text = re.sub(r"\s+", " ", content or "").strip().strip('"').strip()
    if not text or text.upper() == "NONE":
        return None
    if SUMMARY_HEDGE_RE.search(text):
        print("  Summary rejected: hedging language.")
        return None
    sentences = [part for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    if len(sentences) < 1 or len(sentences) > 2 or len(text) < 40 or len(text) > 700:
        print("  Summary rejected: expected one or two factual sentences.")
        return None
    return text


def github_model_summary(title: str, abstract: str) -> str | None:
    """One parent-language summary from GitHub Models, or None on any failure."""
    global _summary_token_warned
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if not token:
        if not _summary_token_warned:
            print("  Summaries skipped: no GITHUB_TOKEN.")
            _summary_token_warned = True
        return None
    body = json.dumps(
        {
            "model": SUMMARY_MODEL,
            "temperature": 0,
            "max_tokens": 220,
            "messages": [
                {"role": "system", "content": SUMMARY_SYSTEM},
                {"role": "user", "content": f"Title: {title}\n\nAbstract:\n{abstract[:6000]}"},
            ],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        SUMMARY_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read()
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        print(f"  Summary model HTTP {exc.code}.")
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"  Summary model failed: {exc}.")
        return None
    text = raw.decode("utf-8", "replace").strip()
    if not text or text.upper() == "OK" or (not text.startswith("{") and "json" not in content_type.lower()):
        print("  Summary model returned no completion.")
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        print("  Summary model returned non-JSON.")
        return None
    choices = payload.get("choices") or []
    content = ""
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        content = str(message.get("content") or "")
    return accept_model_summary(content)


def crossref_abstract(doi: str) -> str:
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    raw, err = fetch_url(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    if err or not raw:
        return ""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return ""
    abstract = str((payload.get("message") or {}).get("abstract") or "")
    return re.sub(r"\s+", " ", strip_html(abstract)).strip()


def write_summaries(items: list[dict]) -> None:
    SUMMARIES_OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"about": SUMMARY_ABOUT, "items": items}
    SUMMARIES_OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def fill_summaries(articles: list[dict]) -> None:
    """Cache a model summary once per DOI. Failure leaves the item unsummarized."""
    items = load_summary_items()
    indexed: dict[str, dict] = {}
    for item in items:
        for key in summary_cache_keys(item):
            indexed.setdefault(key, item)
    pending: list[tuple[dict, str]] = []
    for article in articles:
        if not isinstance(article, dict):
            continue
        keys = summary_cache_keys(article)
        if any(key in indexed and str(indexed[key].get("summary") or "").strip() for key in keys):
            continue
        doi = extract_doi(str(article.get("link") or "")) or extract_doi(str(article.get("id") or ""))
        if not doi:
            continue
        pending.append((article, doi))
    if not pending:
        current = load_json_file(SUMMARIES_OUT)
        if current.get("about") != SUMMARY_ABOUT or not isinstance(current.get("items"), list):
            write_summaries(items)
        print(f"Summaries unchanged ({len(items)} cached).")
        return
    print(f"Summaries to fetch: {len(pending)}")
    records, _failed = europepmc_by_doi([doi for _article, doi in pending])
    added = 0
    for article, doi in pending:
        record = records.get(doi) or {}
        abstract = re.sub(r"\s+", " ", strip_html(str(record.get("abstractText") or ""))).strip()
        if len(abstract) < 80:
            abstract = crossref_abstract(doi)
        if len(abstract) < 80:
            continue
        text = github_model_summary(str(article.get("title") or ""), abstract)
        if not text:
            continue
        item = {
            "id": doi,
            "link": article.get("link") or f"https://doi.org/{doi}",
            "summary": text,
        }
        items.append(item)
        for key in summary_cache_keys(item):
            indexed[key] = item
        added += 1
        time.sleep(0.2)
    write_summaries(items)
    print(f"Summaries: {added} new, {len(items)} cached.")


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    previous_articles = load_previous_articles()
    previous_trials = load_trials()
    refresh_open_trials()
    refresh_registry()
    refresh_stocks()
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
            annotate_access(previous)
            kept = json.loads(OUT.read_text(encoding="utf-8"))
            if isinstance(kept, dict):
                kept["articles"] = previous
                kept["access_updated"] = datetime.now(timezone.utc).isoformat()
                write_ledger(kept)
            write_changes(previous_articles, previous, previous_trials)
            fill_summaries(previous)
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
    annotate_access(selected)
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
    write_changes(previous_articles, selected, previous_trials)
    fill_summaries(selected)
    span = f"{min(dated)[:10]} → {max(dated)[:10]}" if dated else "undated"
    print(f"\nWrote {len(selected)} articles ({span}) → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
