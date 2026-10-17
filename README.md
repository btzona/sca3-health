# Ataxin-3

Ataxin-3 is a public ledger of research and news on spinocerebellar ataxia type 3 (SCA3), also called Machado-Joseph disease. It is for families living with the disease and for people following the science. Cards carry a title, a source, a date, a short excerpt, and a link to the original paper, trial, or story.

**Live site:** [https://btzona.github.io/sca3-health/](https://btzona.github.io/sca3-health/)

Headlines and excerpts stay with their publishers. Each card links out. The site is not medical advice.

Pages beside the ledger:

- [About SCA3](about.html) — a plain-language introduction, checked against GeneReviews and the papers cited on that page.
- [Open trials](trials.html) — interventional SCA3 studies a registry still lists as recruiting or not yet recruiting. `fetch.py` refreshes `data/trials.json` on the daily run and leaves Servier’s S233107 off while the EU registry lists that trial as ended.
- [Pipeline](pipeline.html) — therapy programs by stage, from the 5 October 2026 review.
- [Where things stand](stand.html) — care, the treatment pipeline, reproductive options, and guidance for at-risk minors, reviewed 5 October 2026.
- [For families at risk](families.html) — inheritance, testing, pregnancy, and talking with children.
- [Glossary](glossary.html) — short definitions. The first use of a term on other pages links here.
- [Resources](resources.html) — NAF, CRC-SCA, GeneReviews, NORD, clinics, and registries.

Plain-language “What this means” notes live in `data/summaries.json`. Each note is written from that item’s abstract or registry text. The daily job does not generate them. Add an object to cover another paper.

## Daily refresh

[`.github/workflows/pages.yml`](.github/workflows/pages.yml) refreshes the ledger and deploys the site. It runs:

- every day at 14:23 UTC
- on every push to `main`
- when someone starts it by hand (`workflow_dispatch` on the Actions tab)

The job runs `python3 fetch.py`, copies the HTML pages, `css/`, `js/`, `img/`, and `data/` into a static bundle, then publishes that bundle with the official GitHub Pages actions (`configure-pages`, `upload-pages-artifact`, `deploy-pages`).

A source that fails is logged and skipped. The National Ataxia Foundation site often answers GitHub-hosted runners with a block page; the job then reads archived copies of the foundation’s own RSS feed and still links to `ataxia.org`. PubMed is not used, because it often blocks CI hosts. If a run collects no articles at all, `fetch.py` leaves the existing `data/articles.json` in place and exits successfully.

When a run does collect articles, it merges them into the ledger instead of replacing it. Duplicates collapse on DOI, trial id, or title. The file is capped at 240 articles, and that cap reserves space for items from the first half of 2026 so the shelf still reaches January 2026. The page groups cards by month and offers “Show earlier entries.”

`configure-pages` is called with `enablement: true`. The workflow’s `GITHUB_TOKEN` cannot create a Pages site (that needs a personal access token, or a GitHub App with `administration:write` and `pages:write`). If this repository has never had Pages turned on, open **Settings → Pages → Build and deployment → Source** and choose **GitHub Actions** once. After that, this workflow can deploy on its own. The site is served from the `/sca3-health/` project path; asset and data URLs in the pages are relative (`css/style.css`, `js/app.js`, `data/articles.json`), so they resolve under that path.

## Refresh locally

From the repository root (Python 3, standard library only):

```bash
python3 fetch.py
python3 -m http.server 8765
```

Open [http://127.0.0.1:8765/](http://127.0.0.1:8765/).

The same commands are `npm run fetch` and `npm run serve`.

To check the GitHub Pages subpath on your machine, serve a parent directory:

```bash
mkdir -p /tmp/pages-sim
ln -sfn "$PWD" /tmp/pages-sim/sca3-health
python3 -m http.server 8765 --directory /tmp/pages-sim
```

Then open [http://127.0.0.1:8765/sca3-health/](http://127.0.0.1:8765/sca3-health/).

Set `SCA3_PREPRINT_FROM=2026-01-01` to ask the bioRxiv and medRxiv APIs for the whole year instead of the recent window. Europe PMC already searches preprints back to January 2026 on every run.

## Layout

| Path | Role |
|------|------|
| `index.html` | Ledger, with the home-page note |
| `about.html` | What SCA3 is |
| `trials.html` | Open interventional trials |
| `pipeline.html` | Therapy board, reviewed 5 October 2026 |
| `working.html` | Companies and organizations working on SCA3, with delayed stock closes for the public ones |
| `stand.html` | Where care and trials stand, as of 5 October 2026 |
| `families.html` | For families at risk |
| `glossary.html` | Term definitions |
| `resources.html` | Organizations, clinics, registries |
| `css/style.css` | Layout, including small screens |
| `fonts/` | Self-hosted Manrope and Fraunces (SIL Open Font License) |
| `img/cajal-purkinje.webp` | Small brand mark, Santiago Ramón y Cajal, circa 1900, public domain |
| `js/app.js` | Loads the ledger, filters, month groups, load more, summaries |
| `js/trials.js` | Renders `data/trials.json` |
| `fetch.py` | Source fetch, merge, cap, open-trial refresh, and stock closes. Not deployed. |
| `data/articles.json` | Article ledger, including free-full-text and free-abstract links |
| `data/trials.json` | Open interventional trials |
| `data/stocks.json` | Delayed last closes for the public companies on Who’s working |
| `data/summaries.json` | Hand-written plain-language notes, matched by link |
| `data/glossary.json` | Terms used for the dotted links |

## Sources

Europe PMC and Crossref supply the journal record. bioRxiv and medRxiv are queried directly for recent preprints, and Europe PMC’s preprint index covers the earlier months of 2026. ClinicalTrials.gov API v2 is searched for `SCA3 OR "Machado-Joseph" OR ATXN3`. The National Ataxia Foundation is read from its RSS feed when the host allows it. News comes from a small set of publisher feeds and from a news search limited to named outlets. Items have to mention SCA3, Machado-Joseph disease, ataxin-3, or a named SCA3 program. A paper that only mentions *ATXN3* in an unrelated disease is left out.

## Design

Deep navy, a teal accent, and white cards on a cool paper background. Manrope for text, Fraunces for the large titles. The Cajal drawing is a small round mark beside the name. The footer repeats the credit and the license. Share links are plain intent URLs and inline icons, with Open Graph and Twitter card tags.
