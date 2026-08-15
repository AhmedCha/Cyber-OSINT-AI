# OSINT Shared Database Schema (osint_shared.db)

> **⚠️ EXTREME DANGER WARNING: UNDERSTANDING DATA TIERS**
>
> The `raw_*` tables contain **unvalidated, unreviewed candidate data**, including unverified email guesses, raw scan noise, and potential false positives. **You must NEVER use `raw_*` tables as input to any external action taken against real people or infrastructure.**
>
> Any integration, reporting, or secondary system mapping this data MUST strictly read from the `reviewed_*` tables. The `reviewed_*` tables represent the human-reviewable, LLM-filtered findings generated at the very end of the pipeline.

## Core Schema Design

The database utilizes a universal standard schema to seamlessly handle highly varied JSON document structures across 12 different tools. All tables enforce uniqueness via a composite primary key on `(company_slug, record_key)`.

### Table Layout

| Table Name                  | Description / Tier                              | Primary Key `record_key` examples |
| :-------------------------- | :---------------------------------------------- | :-------------------------------- |
| **raw_domains**             | **[DO NOT ACTION]** Output from Stage 1/2       | `example.com`                     |
| **raw_emails**              | **[DO NOT ACTION]** Output from email discovery | `user@example.com`                |
| **raw_employees**           | **[DO NOT ACTION]** Discovered OSINT personas   | `john-doe`                        |
| **raw_dns_infra**           | **[DO NOT ACTION]** Raw ammas/certspotter dumps | `example.com`                     |
| **raw_documents**           | **[DO NOT ACTION]** Extracted metadata          | `filename` or `url`               |
| **raw_breaches**            | **[DO NOT ACTION]** HIBP / credential dumps     | `email`                           |
| **raw_darkweb**             | **[DO NOT ACTION]** Raw paste/onion scrapes     | `target`                          |
| **reviewed_domains**        | **[SAFE]** Cleaned, in-scope domains            | `example.com`                     |
| **reviewed_emails**         | **[SAFE]** Verified employee emails             | `user@example.com`                |
| **reviewed_employees**      | **[SAFE]** Filtered target personas             | `john-doe`                        |
| **reviewed_infrastructure** | **[SAFE]** Validated IP/ASN/host footprints     | `target_domain`                   |
| **reviewed_documents**      | **[SAFE]** Relevant document leaks              | `filename` or `url`               |
| **reviewed_breaches**       | **[SAFE]** Attributed credential leaks          | `email`                           |
| **reviewed_darkweb**        | **[SAFE]** High-confidence darkweb findings     | `target`                          |

### Column Definitions (Applies to ALL tables)

- `company_slug` **(TEXT)**: Normalized company identifier (e.g., `example-corp`).
- `record_key` **(TEXT)**: The unique identifying field for the record (e.g., the actual domain string, email address, or hash).
- `data` **(JSON)**: The entire JSON object representing the item, matching the exact shape produced by the relevant pipeline stage or final LLM template.
- `updated_at` **(TIMESTAMP)**: Defaults to `CURRENT_TIMESTAMP`. Automatically updated on `INSERT OR REPLACE` when a stage is re-run.

