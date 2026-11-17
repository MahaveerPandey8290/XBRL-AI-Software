# XBRL AI Software: AOC-4 XBRL instance generator

A Python application that prepares the XBRL instance document (XML) for the
MCA **AOC-4 XBRL** filing. You enter or import the financial statement data,
upload the supporting documents, the app validates everything against the MCA
business rules, and it writes the XML in the same layout as TaxPrint /
InstantXBRL.

Both MCA taxonomies are supported:

| Taxonomy | Used for | Template it is built from |
|---|---|---|
| **Non-Ind AS** (C&I Taxonomy 2016, `in-gaap` / `in-ca`) | Companies following the Companies (Accounting Standards) Rules | `InstantXBRL_Template_2026.xls` |
| **Ind AS** (Ind AS Taxonomy 2017, `ind-as` / `in-ca`) | Companies following Indian Accounting Standards | `InstantXBRL_INDAS_Template_2026.xls` |

### Cost audit report (Form CRA-4, Costing Taxonomy 2019)

Choose **Costing 2019 - Cost audit report (CRA-4)** on the web page. The 23 parts
of the CRA-4 annexure (`[100100]` to `[100420]`) and MCA's *Business Rules based on
Costing Taxonomy 2019 (V1.1)* are in `xbrl_aoc4/schema/COST.json`, built by
`python -m tools.build_cost_schema <rules.xlsx>`. Upload any of:

- the **CRA-4 input workbook** (download it from the page, empty or filled with
  what was already read): one sheet per part, products and cost auditors
  numbered 1, 2, 3 ...;
- the **cost audit report** (.docx / .pdf): paragraphs go into the cost
  auditor's report text blocks; membership number, FRN, place and date are read;
- an existing CRA-4 XML, and last year's CRA-4 XML for the previous year figures.

Checks in `xbrl_aoc4/cost.py`: product/service sector, at least one product
covered under cost audit, materials/utilities/industry expenses adding up to the
cost statement, quantities sold equal to quantities available, margin totals
equal to profit for audited products, profit before tax equal to profit as per
financial accounts, cost auditors and CRA-2 SRNs, related parties, indirect taxes,
previous year figures, and names consistent across parts.

**Before filing:** the business rules workbook does not contain the taxonomy's
entry point, namespace or data types. Load the official Costing Taxonomy 2019
package once (offline):

    python -m xbrl_aoc4.taxonomy COST "path/to/costing taxonomy.zip"

This writes `xbrl_aoc4/schema/package_COST.json`, which then supplies the real
schemaRef, namespace, data types and period types.

## Quick start (Windows)

1. Install Python 3.10 or newer from python.org (tick "Add Python to PATH").
2. Double-click **`run.bat`**. The first run creates a virtual environment and
   installs the requirements; then the app opens in your browser at
   http://127.0.0.1:5050.

On other systems: `pip install -r requirements.txt` then `python run.py`.

## How to prepare a filing

1. **Create a filing**: choose the taxonomy, enter company name, CIN, financial
   year and standalone/consolidated, and the unit you type amounts in
   (rupees, thousands, lakhs, ...). Amounts are scaled to rupees in the XML.
   Or **import**:
   - a filled InstantXBRL / TaxPrint Excel template (`.xls` / `.xlsx`), or
   - an existing XBRL instance (`.xml`), to correct and regenerate it, or to
     **roll it forward** to the next year (last year's figures become the
     previous-year figures).
2. **Documents**: upload the financial statements workbook, directors' report,
   auditor's report and notes.
   - *Map financial statements*: rows of the balance sheet, profit and loss and
     cash flow sheets are matched to XBRL elements automatically (fuzzy label
     matching plus a list of common Indian captions). You review the
     suggestions, then apply them. The workbook's scale ("₹ in lakhs") is
     detected.
   - Word / PDF / text documents can be put into any text block.
3. **Fill the sections**: every section and table of the template is a screen.
   Fields save automatically. Totals left blank are calculated from their
   components using the template's own formulas. Tables (classes of shares,
   directors, auditors, related parties, fixed assets, ...) get one column per
   member combination.
4. **Validate**, fix what is reported, then **Generate XML**. The file is named
   like TaxPrint's (`FY[2025-2026] <CIN> Standalone_BalanceSheet <date>.xml`)
   and saved under `data/filings/<filing>/output/`.
5. Open the XML in the MCA XBRL validation tool, run validation and
   pre-scrutiny, then attach it to AOC-4 XBRL.

## What is validated

- Filing: CIN format, period dates, period not longer than 18 months,
  standalone/consolidated.
- Data types: amounts, share counts, dates, Yes/No, CIN, PAN, DIN, SRN, ITC codes.
- The MCA business rules shipped in the templates' "Specific Business rules"
  sheet (747 for non-Ind AS, 1,211 for Ind AS): mandatory fields (all years /
  current year / standalone / consolidated), non-negative, percentages,
  dependencies, fields required when a Yes/No answer is Yes, dates not in the
  future, value not more than another element, table-mandatory elements,
  unique PANs, CIN different from the company's own, and more.
- MCA's official business rules for the C&I taxonomy (Non-Ind AS), from
  MCA's own workbook (`reference/mca/`, V1.3): element rules, mandatory line
  items per table, parent/child members and the generic rules (previous year
  figures, 2 decimals, sections that don't apply to the previous year or to
  consolidated documents, and more). Each finding names its rule, e.g.
  "MCA BR 200100". See `reference/mca/README.md` for what is and isn't checked.
- Totals: every total in the template (205 formulas non-Ind AS, 261 Ind AS)
  must equal the sum of its components, and the balance sheet must balance.

### Ind AS: MCA's official business rules

For Ind AS the template's rules are replaced by MCA's own workbook,
"Business Rules for IndAS Taxonomy V1.2" (imported into
`schema/mca_rules_INDAS.json`):

- 812 element rules, of which 796 are checked automatically: mandatory
  fields, fields and tables required when a Yes/No answer is Yes or when a
  balance sheet amount is not zero, non-negative amounts, PAN / CIN / DIN /
  ITC formats, MCA's country and currency lists, dates not in the future,
  unique values, carrying amount = gross less accumulated depreciation,
  totals of "others" tables equal to the face amount, and more. 41 of them
  need MCA's own records (name matches the CIN or DIN, auditor on the ICAI
  list), so they can only be checked on the MCA portal.
- The generic rules: a total needs at least one component (with MCA's
  exemptions), mandatory line items in each table column, numbered members in
  order, no images, previous-year amounts, closing = opening + change,
  at most 2 decimals, sections that don't apply to standalone / consolidated
  reports or have no previous year (left out of the XML), a member on every
  axis, and the opening balance sheet for first-time adopters.
- The notes of an asset (property, plant and equipment, investment property,
  goodwill, intangible and biological assets, borrowings, investments) are
  only required when the balance sheet shows that asset.

When MCA publishes a new version of the workbook, run
`python -m tools.import_business_rules "<workbook>.xlsx" INDAS --report`;
`--report` lists the rules whose wording was not understood.

## Accuracy check

`tools/compare_instances.py` compares two instances fact by fact (concept,
period, dimensions, unit, value). The TaxPrint output sample in
`E:\XBRL SOFTWARE\Output sample` re-imports and regenerates with **all 2,959
facts identical**, and passes validation with no findings. The tests run this
automatically when the sample folder sits next to the code:

```
python -m pytest -q        # or: python tests/test_generator.py
```

## Taxonomy download (recommended)

The app works offline. For exact period types and the taxonomy's calculation
links, open *Taxonomy settings* and click *Download from mca.gov.in* once for
each taxonomy (or run `python -m xbrl_aoc4.taxonomy`). Files are cached in
`%USERPROFILE%\.xbrl_aoc4\taxonomy`.

**Ind AS note**: the Ind AS entry point and namespaces in `xbrl_aoc4/config.py`
come from the MCA Ind AS filing manual and the role URIs inside the Ind AS
template; they have not yet been checked against an Ind AS instance accepted
by MCA. Downloading the taxonomy confirms the namespaces. Override them in a
`settings.json` next to `run.py` if needed:

```json
{"taxonomies": {"INDAS": {"schemaRef": "https://.../in-ci-ent-2017-03-31.xsd",
                          "namespaces": {"ind-as": "http://..."}}},
 "output_dir": "E:/XBRL SOFTWARE/Output"}
```

## Command line

```
python cli.py validate  "Output sample\FY[...].xml"
python cli.py generate  filled_template.xls -o out.xml
```

## Project layout

```
run.py / run.bat          start the web interface
cli.py                    validate / generate without the browser
xbrl_aoc4/
  schema/AS.json          sections, elements, tables, axes, members, rules, totals,
  schema/INDAS.json       drop-down lists (built from the InstantXBRL templates)
  schema/periods_AS.json  period types learned from real instances
  generator.py            XML writer (contexts, dimensions, units, facts)
  schema/mca_rules_INDAS.json  MCA's official Ind AS business rules (V1.2)
  validator.py            MCA business rules, types, totals
  mca_rules.py            checks for MCA's official business rules workbook
  calc.py                 total computation and checks
  periods.py              instant / duration resolution
  taxonomy.py             MCA taxonomy download and cache
  importers/              filled templates, XBRL instances, financial statements, documents
  web/                    Flask app, pages, styles
tools/
  extract_template.py     rebuild schema/*.json from a new InstantXBRL template
  learn_periods.py        learn period types from more instance documents
  compare_instances.py    fact-by-fact comparison of two instances
  import_business_rules.py  MCA business rules workbook -> schema/mca_rules_<KEY>.json
tests/
```

## Updating to a new template year

When a new InstantXBRL template is released, save it also as `.xlsx`
(LibreOffice or Excel) and run:

```
python tools/extract_template.py AS    "<template>.xls" --formulas "<template>.xlsx"
python tools/extract_template.py INDAS "<indas template>.xls" --formulas "<indas template>.xlsx"
```

To improve period-type accuracy, feed more accepted instance documents to
`python tools/learn_periods.py AS file1.xml file2.xml ...`.
