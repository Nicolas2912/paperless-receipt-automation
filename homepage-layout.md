+--------------------------------------------------------------------------------+
| Paperless Receipt Automation                                  [GitHub] [Docs]  |
+--------------------------------------------------------------------------------+
| [Hero]                                                                       ^ |
|                                                                              | |
|  "Turn Scanned Receipts Into Smart, Searchable Data – Automatically."       | |
|   Automate Windows scans → searchable PDFs → Paperless-ngx → product DB.    | |
|                                                                              | |
|  [ Download for Windows 11 ]   [ View GitHub Repo ]   [ Open Product DB UI ] | |
|                                                                              | |
|                           [HeroVisualComposite]                               |
|        (flow diagram: Scan folder -> OCR overlay -> Metadata -> Paperless    |
|         + small inset of analytics dashboard with charts)                    v |
+--------------------------------------------------------------------------------+
| [KeyBenefitsSection]                                                          |
|                                                                              ^|
|  +----------------------+  +-----------------------+  +---------------------+ |
|  | [BenefitCard]        |  | [BenefitCard]         |  | [BenefitCard]       | |
|  | "Hands-off scanning" |  | "Searchable PDFs"     |  | "Deep analytics"    | |
|  | Watches your scans   |  | Invisible text layer  |  | Product DB dashboards|
|  | folder, dedupes via  |  | with PyMuPDF, Paperless| | show spend patterns, |
|  | SHA-256, runs flows. |  | skips heavy OCR.      |  | merchants, products. |
|  +----------------------+  +-----------------------+  +---------------------+ v
+--------------------------------------------------------------------------------+
| [HowItWorksSection]                                                           |
|                                                                              ^|
|  "How it works"                                                               |
|                                                                              |
|  1) [StepCard] Detect                                                         |
|     Watches your scan folder, fixes Windows paths, skips already processed.  |
|                                                                              |
|  2) [StepCard] Transcribe + Overlay                                          |
|     Uses Ollama vision model (qwen2.5vl-receipt) to transcribe and builds    |
|     an invisible-text PDF with PyMuPDF.                                      |
|                                                                              |
|  3) [StepCard] Extract + Upload                                              |
|     Extracts date, merchant, amount; renames to YYYY-MM-DD_<Korresp>_<id>;   |
|     uploads and enforces tags in Paperless-ngx.                              |
|                                                                              |
|  4) [StepCard] Analyze                                                        |
|     Every receipt also feeds into the product database for rich dashboards.  v
+--------------------------------------------------------------------------------+
| [ProductDBAnalyticsTeaser]                                                   ^
|                                                                              |
|  "Your receipts, now a real product database."                               |
|                                                                              |
|  Left: [AnalyticsScreenshotPlaceholder]                                      |
|     - [MonthlySpendLineChart]                                                |
|     - [TopMerchantsBarChart]                                                 |
|     - [PaymentMethodDonut]                                                   |
|                                                                              |
|  Right: bullet list                                                          |
|     - Track spend over time by merchant, city, and payment method.           |
|     - See price history for specific products.                               |
|     - Monitor Pfand deposits vs refunds.                                     |
|     - Export data via API for further analysis.                              v
+--------------------------------------------------------------------------------+
| [UseCasesSection]                                                             |
|                                                                              ^
|  "Built for people who actually automate things."                            |
|                                                                              |
|  +----------------------+  +------------------------+  +--------------------+ |
|  | [PersonaCard]        |  | [PersonaCard]          |  | [PersonaCard]      | |
|  | Home user            |  | Freelancer / Tax user  |  | Small business     | |
|  | Keep all receipts    |  | Exportable, structured |  | Basic expense      | |
|  | searchable at home.  |  | receipt DB for your    |  | tracking from      | |
|  |                      |  | Steuerberater.         |  | ordinary receipts. | |
|  +----------------------+  +------------------------+  +--------------------+ v
+--------------------------------------------------------------------------------+
| [QuickstartSection]                                                           |
|                                                                              ^
|  "Get started in a few commands"                                             |
|                                                                              |
|  1) Install & env setup                                                      |
|     ```                                                                      |
|     conda create -n paperless python=3.11                                    |
|     conda activate paperless                                                 |
|     python -m pip install -r requirements.txt                                |
|     ```                                                                      |
|                                                                              |
|  2) Configure connection + scan folder                                       |
|     - Set PAPERLESS_BASE_URL, PAPERLESS_TOKEN in .env                        |
|     - Edit scan-image-path.txt to point to your scanner output.             |
|                                                                              |
|  3) Start watch mode                                                         |
|     ```                                                                      |
|     $env:PYTHONPATH = "$PWD\src"                                             |
|     python -m paperless_automation watch                                     |
|     ```                                                                      |
|                                                                              |
|  4) Explore the Product DB dashboard                                         |
|     ```                                                                      |
|     python -m paperless_automation productdb serve                           |
|     ```                                                                      |
|                                                                              v
+--------------------------------------------------------------------------------+
| [Footer]                                                                      |
|                                                                              |
|  © 2025 Paperless Receipt Automation  •  MIT License  •  [GitHub] [Docs]     |
|                                                                              |
+--------------------------------------------------------------------------------+
