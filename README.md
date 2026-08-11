# Cyber-OSINT-AI

An automated OSINT (Open Source Intelligence) reconnaissance pipeline for
authorized security engagements. Given only a company name, it discovers
domains, infrastructure, employees, emails, public documents, breach
exposure, and dark web mentions - then uses a local LLM to filter the
results down to grounded, non-hallucinated findings and generates a
final Word report.

Every stage runs in Docker for reproducibility, and every tool's API
keys are centralized in a single `.env` file.

---

## Prerequisites

- **Docker** and **Docker Compose**
- **Python 3.10+** with `pip`, for the host-side orchestration scripts
  (`configure_tools.py`, `config.py`, and everything under `stages/`)
- **Linux or macOS** - the install/pipeline scripts are Bash and assume
  a Unix-like environment (WSL2 works fine on Windows)
- **~10GB free disk space** - Docker images for the tool containers,
  plus the local LLM model (~4.9GB for the default `llama3.1:8b`)
- **A GPU is optional but strongly recommended** for the LLM filtering
  stage - this project was developed and tested on a machine with an
  RTX 3050 (8GB VRAM); it will run on CPU-only hardware but noticeably
  slower. See `install_tools.sh` for the Ollama setup this relies on.
- **API keys are optional.** The pipeline is designed to degrade
  gracefully - every tool skips sources it doesn't have credentials
  for rather than failing. See `env.example` for the full list of
  supported services and which ones have usable free tiers.

---

## Installation

### 1. Set up your environment file

```bash
cp env.example .env
```

Open `.env` and fill in whichever API keys you have. You don't need all
of them - every tool in this pipeline degrades gracefully and skips
sources it doesn't have credentials for. `.env` is organized by tool,
with comments noting which service each key belongs to, its free-tier
status where known, and whether it's shared across multiple tools.

### 2. Run the install script

```bash
chmod +x install_tools.sh
./install_tools.sh
```

This will:

- Build every custom Docker image (theHarvester, CertSpotter, maigret,
  Metagoofil, Tor) and pull the official ones (SpiderFoot, Amass,
  Reacher, SearXNG, Valkey)
- Start SpiderFoot once to initialize its database, then generate and
  inject every tool's configuration from `.env` - theHarvester's
  `api-keys.yaml`, SearXNG's `settings.yml`, and SpiderFoot's API keys
  (written directly into its SQLite database, since SpiderFoot doesn't
  read a flat config file)
- Run a smoke test against every tool to confirm it's actually callable
- Install Ollama and pull the local LLM used for the filtering stage

**If you edit `.env` later** (add a key, fix a typo, add a new
service), you don't need to rerun the whole install - just:

```bash
docker compose stop spiderfoot
python3 configure_tools.py
```

(SpiderFoot has to be stopped first since its config lives in a SQLite
database that can't be safely written to while the container holds it
open.)

### 3. (One-time) Configure SpiderFoot's Tor routing

Some dark web modules need SpiderFoot to route through Tor. After the
install script finishes, open `http://localhost:5001` → Settings →
search for "SOCKS", and point it at the `tor` service (host: `tor`,
port: `9050`, type: `TOR`). This only needs to be done once - it's
saved in SpiderFoot's database.

---

## Usage

Run the full pipeline against a target company:

```bash
./run_pipeline.sh "Company Name"
```

Add `--pause` to step through the pipeline one stage at a time,
inspecting each stage's output before continuing - useful when testing
against a new target for the first time:

```bash
./run_pipeline.sh --pause "Company Name"
```

The pipeline runs through 12 stages in order - name-to-domain
resolution, domain discovery, DNS/infrastructure enumeration, employee
discovery, email discovery and validation, document discovery, breach
lookup, dark web discovery, result aggregation, LLM filtering, and
report generation - each stage's output feeding the next. All output
for a given run lands under `output/<company-slug>/`, so multiple
companies can be processed without overwriting each other's results.

The final deliverable is `output/<company-slug>/report_<company-slug>_<date>.docx`.

### Running a single stage

Every stage can also be run independently for testing or re-running
just one step:

```bash
python3 -m stages.domain_discovery --company "Company Name"
```

---

## Project Structure

```
├── configure_tools.py     # generates tool configs from .env (see above)
├── config.py               # reports which API keys are configured/missing
├── install_tools.sh        # one-time setup: build, configure, smoke-test
├── run_pipeline.sh         # runs the full 12-stage pipeline for a company
├── docker-compose.yaml     # all tool containers
├── dockerfiles/            # custom Dockerfiles for tools with no official image
├── lib/                    # shared helpers used across every stage
│   ├── common.py           #   logging, slugify, name/abbreviation helpers
│   ├── config.py           #   .env loading, API status reporting
│   ├── docker_runner.py    #   subprocess wrapper for `docker compose run`
│   ├── network.py          #   domain validation, DNS, company-name matching
│   ├── search.py           #   SearXNG querying, query-variant generation
│   ├── apify_utils.py      #   Apify actor invocation
│   ├── email_normalizer.py #   email validation/normalization
│   └── email_patterns.py   #   email pattern generation for guessing addresses
├── stages/                 # the 12 pipeline stages, run in order by run_pipeline.sh
└── output/<company-slug>/  # per-company results, including the final .docx report
```

---

## Use Cases

This kind of pipeline has real, practical utility in a cybersecurity
context - it's essentially automating the reconnaissance phase that a
human analyst would otherwise do by hand across dozens of disconnected
tools and browser tabs:

- **External attack surface mapping.** Before a penetration test or red
  team engagement, knowing every domain, subdomain, and exposed service
  a company owns is the starting point - this pipeline builds that map
  automatically instead of manually running theHarvester, Amass, and
  certificate transparency lookups one at a time.
- **Employee exposure and social engineering risk assessment.**
  Discovering which employees have public profiles, which emails follow
  predictable patterns, and which credentials have appeared in past
  breaches directly informs phishing-simulation and social-engineering
  risk scoring - a core part of many security assessments.
- **Digital footprint audits.** Companies often don't have a full
  picture of their own public exposure (old subdomains, leaked
  documents, forgotten social accounts). Running this against your own
  organization surfaces exactly that.
- **Breach and dark web monitoring.** Checking whether employee emails
  or company data have surfaced in breach databases or dark web leak
  sites is standard practice for security teams, and this pipeline
  automates that check across an entire employee roster rather than
  one address at a time.
- **Due diligence and third-party risk.** The same recon that maps a
  company's own exposure can inform vendor/supplier security
  assessments before a partnership or acquisition.
- **Repeatable, documented methodology.** Because every stage runs in
  Docker with centralized config, the same assessment can be rerun
  later (to measure change over time) or handed to another analyst with
  the exact same environment - useful for audit trails and engagement
  reports.

---

## AI Integration

AI is used in this project in two distinct ways:

**1. Local LLM filtering (in the pipeline itself).** Every tool in this
pipeline over-collects by design - SpiderFoot alone can emit hundreds of
raw events per domain, most of it noise (ISP metadata, duplicate
records, irrelevant infrastructure). Stage 10 (`llm_filter.py`) runs a
locally-hosted LLM (Llama 3.1 8B via Ollama - chosen specifically to run
fully offline, so no collected data ever leaves the machine) to sort the
aggregated raw output into what's actually relevant versus noise, before
the final report is generated.

This filtering step is built around a few deliberate constraints, since
LLMs are prone to inventing plausible-looking but false details:

- The model only ever _extracts and categorizes_ from data that's
  already been collected - it never generates new findings.
- Every kept item is checked against the actual raw input; nothing is
  presented as a finding unless it's grounded in real collected data.
- If the model fails, times out, or produces something that can't be
  verified against the source data, the original raw data is preserved
  unmodified with a warning - nothing is silently dropped or replaced by
  a hallucinated summary.
- Excluded items aren't deleted either - they're kept in an appendix
  with a reason, so a human reviewer can audit what the LLM chose not to
  surface.

**2. AI-assisted data collection.** Several data-collection stages use
Apify actors that themselves incorporate AI (e.g. AI-driven search result
parsing for employee and document discovery) - these are third-party
tools invoked like any other API in the pipeline, with their output
subject to the same downstream verification (content matching, DNS
validation, email deliverability checks) as any non-AI source.

The result is a pipeline where AI accelerates the _filtering and
triage_ of already-collected, verifiable data - not one that generates
or infers findings that weren't actually discovered.

---

## Data Sensitivity & Handling

This pipeline collects real personal data (names, emails, breach
exposure) and can surface genuinely sensitive material (dark web leak
content). Treat every `output/<company-slug>/` directory accordingly:

- Never commit `output/` or `.env` to a public repository (`.gitignore`
  should already exclude both).
- Restrict access to generated reports to people with a legitimate need
  to see them.
- Purge output data once it's no longer needed for the engagement.
- Review LLM-filtered output before sharing it further - the filtering
  stage is designed to avoid hallucination, but human review remains
  part of the process for anything sensitive enough to act on.

---

## Known Limitations

- A few SpiderFoot event-type names and some third-party API response
  schemas (notably IntelX's) were implemented from documentation or
  best-effort inference rather than fully confirmed against live output
  at the time of writing. Where this applies, it's flagged directly in
  the relevant source file's comments, along with how to verify it.
- CertSpotter's official CLI tool is built for continuous certificate
  monitoring, not one-shot queries - this pipeline uses their public API
  directly instead for DNS/infrastructure discovery.
- Free-tier API limits (Shodan, HaveIBeenPwned, SecurityTrails, and
  others) constrain result depth unless paid tiers are configured - see
  the tier notes in `env.example`.
- The pipeline currently processes one company per run; there's no
  built-in batch/queue mode for multiple targets.

---

## Acknowledgments

This project builds on several excellent open source OSINT tools:

| Tool                                                          | Purpose                                     |
| ------------------------------------------------------------- | ------------------------------------------- |
| [SpiderFoot](https://github.com/smicallef/spiderfoot)         | Multi-source automated OSINT scanning       |
| [theHarvester](https://github.com/laramies/theHarvester)      | Domain, email, and subdomain harvesting     |
| [Amass](https://github.com/owasp-amass/amass)                 | Passive subdomain enumeration               |
| [maigret](https://github.com/soxoj/maigret)                   | Username/profile discovery across platforms |
| [Metagoofil](https://github.com/opsdisk/metagoofil)           | Public document metadata extraction         |
| [Reacher](https://github.com/reacherhq/check-if-email-exists) | Email deliverability validation             |
| [SearXNG](https://github.com/searxng/searxng)                 | Self-hosted metasearch                      |
| [Ollama](https://ollama.com)                                  | Local LLM inference for the filtering stage |

---

## License

[MIT](LICENSE)
