TL;DR: Focus on time-series spend, merchant comparisons, payment/tax breakdowns, geographic patterns, product price histories, and deposit/refund tracking using line charts, bar charts, stacked bars, heatmaps, and only a few pie/donut charts where proportions really matter.

---

Given your schema, you basically have these “dimensions” to play with:

* Time: `purchase_date_time`
* Merchant: `merchants.name`, `addresses.city`
* Items: `product_name`, `line_type`, `tax_rate`
* Money: `total_*` on `receipts`, `line_*` on `receipt_items`
* Payment: `payment_method`, `currency`

Below are concrete graphics that are actually useful, plus chart types.

---

## 1. Core dashboard: the 5–6 charts almost everyone will care about

1. Total spending over time

* Question: How much do I spend per month/week and is it going up or down?
* Data:

  * X: time (month or week from `purchase_date_time`)
  * Y: sum(`total_gross`) per period
* Chart: Line chart (or area chart).
* Add-ons:

  * Optional split by `currency` or show only EUR for now.
* Why: This is the #1 “finance app” chart; anything sellable needs this.

2. Spending by merchant (top N)

* Question: Who gets most of my money?
* Data:

  * X: merchant name (`merchants.name`)
  * Y: sum(`total_gross`) over a chosen period
* Chart: Horizontal bar chart, sorted descending.
* Variants: Toggle between “this month”, “last 3 months”, “this year”.
* Why: Simple, intuitive, and your schema with `merchants` is perfect for this.

3. Spending by city / location

* Question: Where do I spend (city-level)?
* Data:

  * X: `addresses.city`
  * Y: sum(`total_gross`)
* Chart:

  * For few cities: horizontal bar chart.
  * For many cities: map / choropleth by postal_code or city.
* Why: Uses the address table; adds “wow” factor for a sellable app.

4. Payment method breakdown (cash vs card over time)

* Questions:

  * How much is cash vs card overall?
  * Is my cash usage trending down/up?
* Data:

  * Per month: sum(`total_gross`) grouped by `payment_method`.
* Charts:

  * Overall proportion: Donut/pie chart (CASH vs CARD vs OTHER).
  * Over time: 100% stacked bar per month (x = month, y = % of spend, stacks = payment_method).
* Why: Very intuitive, and your schema already tracks `payment_method`.

5. VAT / tax rate breakdown

* Question: How much spend is at 0/7/19% tax?
* Data: From `receipt_items`

  * Group by `tax_rate`, sum of `line_gross` (or `line_tax`).
* Charts:

  * Donut chart for share of gross spend by tax rate.
  * Alternative: stacked bar over time (x = month, y = sum(line_gross), stacks = tax_rate).
* Why: Very relevant in Germany; good for people who care about food vs non-food splits, business expense analysis, etc.

6. Category-by-proxy: spending by “type” of purchase
   Right now you don’t have categories, just `product_name` and `tax_rate`.

* MVP:

  * Use merchants as proxy categories (Groceries = supermarket merchants, etc.) → bar chart.
  * Or use tax_rate as coarse proxy: 7% vs 19%.
* Later (to really sell the app):

  * Add a `product_categories` table and have the LLM assign each `receipt_items.product_name` to a category.
  * Then do a “spending by category” donut + stacked bar over time.
* Chart: Donut chart (share of total per category) + stacked area over time.
* Why: “Spending by category” is one of the highest-value visuals for consumers; your schema is 90% ready, just missing the category dimension.

---

## 2. Time, habit, and pattern analysis (more advanced, but very attractive)

7. Calendar heatmap: spend per day

* Question: On which days do I spend more?
* Data:

  * Day-level sum of `total_gross`.
* Chart: Calendar heatmap (matrix: days vs weeks, colored by spend).
* Why: Very visually appealing; shows spikes (e.g., salary days, weekends).

8. Day-of-week vs time-of-day heatmap

* Question: When (weekday & hour) do I typically shop?
* Data:

  * Extract weekday and hour from `purchase_date_time`.
  * Aggregate sum(`total_gross`) or count of receipts.
* Chart: Heatmap (x = hour, y = weekday).
* Why: Great “behaviour insight”. Makes your app feel smart.

9. Distribution of receipt totals

* Question: Are most of my receipts small (e.g., snacks) or large (big grocery runs)?
* Data:

  * `total_gross` of each receipt.
* Chart: Histogram or box plot.
* Why: This gives a sense of spending structure, not just totals.

10. Monthly net vs tax vs gross breakdown

* Question: How much of my spend is actually VAT?
* Data:

  * Per month: sum(`total_net`), sum(`total_tax`), sum(`total_gross`).
* Chart: Stacked bar (net + tax = gross), month on x-axis.
* Why: People underestimate how much they pay in VAT. Nice insight when selling.

---

## 3. Product- and merchant-level deep dives

11. Product price history / inflation tracker

* Question: How has the price of a specific product changed over time?
* Data: From `receipt_items`

  * Filter by normalized `product_name` (or a product ID if you add one later).
  * Compute unit price: `line_gross / quantity` (and/or per standardized unit, e.g., per kg if you later parse units).
* Chart: Line chart (x = date, y = unit price).
* Why: Very powerful for “inflation” stories; extremely sellable.

12. Merchant comparison for the same product

* Question: Where is product X cheaper?
* Data:

  * For one product, group by merchant, compute median `unit_price_gross`.
* Chart: Bar chart (merchant vs price).
* Why: This lets your app claim “price comparison from your own historic data”.

13. Basket composition within a single receipt

* Question: What dominated this specific shopping trip?
* Data:

  * For a given receipt: each `receipt_item`’s `line_gross`.
* Chart: Donut or bar chart of top items by share of gross.
* Why: Good for a “receipt details” pane and for demos.

14. Top products by spending / frequency

* Question: Which products do I buy most frequently and spend the most on?
* Data:

  * Group `receipt_items` by `product_name`.
  * Metrics: count(*), sum(`line_gross`).
* Charts:

  * Bar chart (x = product_name, y = sum(line_gross)) for top N.
  * Another bar for top N by purchase count.
* Why: This is a “loyalty to products” view; interesting and easy to compute.

---

## 4. Deposit / Pfand tracking (unique selling point for DE)

Your schema already distinguishes line types and allows negative totals.

15. Net deposit balance over time

* Question: How much Pfand do I pay vs get back?
* Data (from `receipt_items`):

  * Filter where `line_type IN ('DEPOSIT_CHARGE', 'DEPOSIT_REFUND')`.
  * For each month, compute

    * Deposit paid = sum(line_gross) where DEPOSIT_CHARGE
    * Deposit refunded = sum(line_gross) where DEPOSIT_REFUND (likely negative or track absolute values).
* Charts:

  * Two lines: “Pfand paid” vs “Pfand refunded” per month.
  * Or stacked bar with net result.
* Why: This is a niche but very cool feature specific to your data model and Germany.

16. Outstanding deposit estimate

* Question: What is my current net Pfand exposure?
* Data:

  * Cumulative sum(DEPOSIT_CHARGE) + cumulative sum(DEPOSIT_REFUND).
* Chart: Single line showing cumulative outstanding deposit over time.
* Why: Unique insight; almost no consumer apps offer this.

---

## 5. Quality of data / extraction diagnostics (for you and power users)

You have `extraction_runs`, `status`, `notes`.

17. Extraction run status over time

* Question: How reliable is the extraction pipeline?
* Data:

  * For each run: `started_at`, `status`.
* Chart:

  * Stacked bar per month: count of runs by status (‘OK’, ‘WARN’, ‘ERROR’).
* Why: For a sellable product, this is useful for internal monitoring and for “health” indicators.

18. Processing time distribution

* Question: How long does an extraction run take on average and are there outliers?
* Data:

  * Compute duration = finished_at - started_at.
* Chart: Histogram or box plot of durations.
* Why: More for you than end users, but good dev dashboard.

---

## 6. Chart type guidance (so you don’t overuse pies)

Given what you want:

* Use **line charts / area charts** for anything time-based (spending over months, deposit over time, price over time).
* Use **horizontal bar charts** for rankings and comparisons (top merchants, cities, products). They scale much better than pie charts.
* Use **stacked bars / 100% stacked bars** for composition over time (payment methods, tax rates, net vs tax vs gross per month).
* Use **pie/donut charts** very sparingly, only where you show a small number of categories and primarily proportions:

  * Payment method split.
  * Tax-rate split.
  * Category split if you later add categories.
* Use **heatmaps** for patterns along two discrete axes (day vs hour, calendar, etc.).
* Use **histograms / box plots** for distributions (receipt totals, extraction durations, price variability).

---

## 7. If you want an actually sellable MVP

A realistic “V1 analytics” set that’s useful but not overkill:

1. Dashboard

   * Line chart: total monthly spend over last 12–24 months.
   * Horizontal bar: top merchants by spend (period selector).
   * Donut: payment method split (CASH/CARD/OTHER).
   * Donut or stacked bar: tax-rate split (0/7/19).

2. Insights tab

   * Heatmap: daily spend calendar.
   * Histogram: receipt totals.
   * Line chart: outstanding deposit over time (if you reliably classify Pfand lines).

3. Product tab (advanced / upsell)

   * Price history line chart for a selected product.
   * Merchant comparison bar chart for that product.

If you add a `product_category` dimension later, “spending by category over time” should immediately become one of the main charts on the dashboard.

---

TL;DR: Use a left sidebar, a global filter bar, and a 2×2 (or 3×2) chart grid on the main dashboard, with separate pages for Merchants, Products, and Insights; below are concrete console “wireframes” showing chart placement by name.

---

## 1. Core layout decisions

You want this to scale with more features and still be understandable. A reasonable, opinionated layout:

* Left sidebar: navigation + quick stats.
* Top bar: global filters (date range, currency, merchant filter, search).
* Main area:

  * Dashboard: 2×2 or 3×2 grid of high-value charts.
  * Other pages: list/table on the left, detail + charts on the right.
* Detail drawer/modal: open on click for receipts, merchants, products.

This is much easier to grow than a single huge dashboard.

---

## 2. Pages and what goes where

### 2.1 Dashboard (home)

Goal: “At a glance: How much do I spend, where, and how?”

Priority charts:

1. Monthly total spend (line/area)
2. Top merchants (bar)
3. Payment method split (donut)
4. Tax rate split (donut or stacked bar)
5. Calendar heatmap or receipts histogram (secondary)

Layout concept:

* Top-left: Monthly total spend (most important).
* Top-right: Payment methods + tax rate split (two small charts).
* Bottom-left: Top merchants.
* Bottom-right: Calendar heatmap or histogram of receipt totals.

---

## 3. Console mockup: Dashboard

Pretend a standard terminal width; names in brackets are components/charts.

```text
+--------------------------------------------------------------------------------+
| Receipt Intelligence                                          [User] [Settings]|
+--------------------------------------------------------------------------------+
| Time: [ This Month v]   Currency: [ EUR v]   Merchant: [ All v ]  Search: [___]|
+--------------------------------------------------------------------------------+
| NAVIGATION             | DASHBOARD                                             |
|------------------------+-------------------------------------------------------|
| > Dashboard            | +---------------------------------------------------+ |
|   Merchants            | | [MonthlySpendLineChart]                           | |
|   Products             | |   (Sum of total_gross per month)                  | |
|   Insights             | +---------------------------------------------------+ |
|   Receipts             | +------------------------------+  +----------------+ |
|   Data Quality         | | [PaymentMethodDonut]         |  | [TaxRateSplit] | |
|                        | |  CASH / CARD / OTHER         |  | 0% / 7% / 19%  | |
|                        | +------------------------------+  +----------------+ |
|                        | +---------------------------------------------------+ |
|                        | | [TopMerchantsBarChart]                            | |
|                        | |  (Top 10 merchants by spend)                      | |
|                        | +---------------------------------------------------+ |
|                        | +---------------------------------------------------+ |
|                        | | [CalendarDailySpendHeatmap]                       | |
|                        | |  (Intensity = total_gross per day)               | |
|                        | +---------------------------------------------------+ |
+------------------------+-------------------------------------------------------+
| [Status: 128 receipts • Last sync: 2025-11-15 23:41]                           |
+--------------------------------------------------------------------------------+
```

Key points in this layout:

* Global filters immediately under the header affect all charts.
* Navigation is fixed; Dashboard is selected.
* The biggest single chart is the time-series, which matches the primary user question.
* Secondary donut charts are smaller and grouped together.
* Bottom area is for more exploratory/behavioural views.

---

## 4. Merchants page layout

Goal: “Which merchants matter and what is my relationship with each?”

Structure:

* Left side: table of merchants with basic metrics.
* Right side: summary charts for the selected merchant.

```text
+--------------------------------------------------------------------------------+
| Receipt Intelligence                                          [User] [Settings]|
+--------------------------------------------------------------------------------+
| Time: [ This Year v]   Currency: [ EUR v]   Merchant: [ All v ]  Search: [___] |
+--------------------------------------------------------------------------------+
| NAVIGATION             | MERCHANTS                                            |
|------------------------+-------------------------------------------------------|
|   Dashboard            | +----------------------+  +------------------------+  |
| > Merchants            | | [MerchantTable]      |  | [MerchantSpendOverTime]|  |
|   Products             | |  Name | City | ...   |  |  (Line: sum per month) |  |
|   Insights             | |  REWE | Berlin| ...  |  +------------------------+  |
|   Receipts             | |  ALDI | Berlin| ...  |  +------------------------+  |
|   Data Quality         | |  ...                 |  | [MerchantCategorySplit]|  |
|                        | +----------------------+  | (e.g. tax_rate / type) |  |
|                        |                          +------------------------+  |
|                        | +---------------------------------------------------+ |
|                        | | [MerchantReceiptList]                              | |
|                        | |  (Last N receipts for selected merchant)          | |
|                        | +---------------------------------------------------+ |
+------------------------+-------------------------------------------------------+
```

* Selecting a merchant in `[MerchantTable]` updates the right-hand charts and the receipt list.
* This gives a very clean master–detail feel.

---

## 5. Products page layout

Goal: “Which products do I buy, how often, and how do prices move?”

Structure:

* Left: searchable products list with spending/frequency.
* Right: price history & merchant comparison for the selected product.

```text
+--------------------------------------------------------------------------------+
| Receipt Intelligence                                          [User] [Settings]|
+--------------------------------------------------------------------------------+
| Time: [ Last 12 Months v]   Currency: [ EUR v]          Search product: [___]  |
+--------------------------------------------------------------------------------+
| NAVIGATION             | PRODUCTS                                             |
|------------------------+-------------------------------------------------------|
|   Dashboard            | +----------------------+  +------------------------+  |
|   Merchants            | | [ProductTable]       |  | [ProductPriceHistory]  |  |
| > Products             | | Name | Times | Spend |  | (Line: unit price vs t)|  |
|   Insights             | | Milk |  34   | 120€  |  +------------------------+  |
|   Receipts             | | Bread|  20   |  80€  |  +------------------------+  |
|   Data Quality         | | ...                  |  | [ProductMerchantCompare]| |
|                        | +----------------------+  | (Bar: price by merchant)| |
|                        |                          +------------------------+  |
|                        | +---------------------------------------------------+ |
|                        | | [ProductReceiptItemsList]                         | |
|                        | | (All items for selected product)                  | |
|                        | +---------------------------------------------------+ |
+------------------------+-------------------------------------------------------+
```

* `[ProductPriceHistory]` uses `receipt_items` with unit price over time.
* `[ProductMerchantCompare]` uses aggregated prices by merchant for that product.

---

## 6. Insights page layout

Goal: “Deeper behaviour and patterns, more ‘wow’ and analysis.”

Structure:

* No table; full-width charts stacked or grid layout.

```text
+--------------------------------------------------------------------------------+
| Receipt Intelligence                                          [User] [Settings]|
+--------------------------------------------------------------------------------+
| Time: [ Custom Range v]   Currency: [ EUR v]   Search: [___]                   |
+--------------------------------------------------------------------------------+
| NAVIGATION             | INSIGHTS                                             |
|------------------------+-------------------------------------------------------|
|   Dashboard            | +---------------------------------------------------+ |
|   Merchants            | | [DayOfWeekHourHeatmap]                            | |
|   Products             | |  (Spend by weekday x hour)                        | |
| > Insights             | +---------------------------------------------------+ |
|   Receipts             | +------------------------------+  +----------------+ |
|   Data Quality         | | [ReceiptTotalsHistogram]     |  | [PfandBalance] | |
|                        | |  (Distribution of gross     |  |  (Line net Pfand)| |
|                        | |   receipt totals)           |  |  over time)      | |
|                        | +------------------------------+  +----------------+ |
|                        | +---------------------------------------------------+ |
|                        | | [NetVsTaxStackedBar]                              | |
|                        | |  (Monthly net + tax = gross)                      | |
|                        | +---------------------------------------------------+ |
+------------------------+-------------------------------------------------------+
```

This is where you place more niche but impressive things:

* Time-of-day behaviour.
* Pfand / deposits tracking.
* Net vs tax decomposition.

---

## 7. Receipts page layout (drill-down)

Goal: “See individual receipts, and then visual breakdown for a single one.”

Structure:

* Left: table of receipts.
* Right: selected receipt details + item breakdown chart.

```text
+--------------------------------------------------------------------------------+
| Receipt Intelligence                                          [User] [Settings]|
+--------------------------------------------------------------------------------+
| Time: [ Custom Range v]   Currency: [ EUR v]   Search receipt/merchant: [___]  |
+--------------------------------------------------------------------------------+
| NAVIGATION             | RECEIPTS                                             |
|------------------------+-------------------------------------------------------|
|   Dashboard            | +------------------------------+  +----------------+  |
|   Merchants            | | [ReceiptTable]               |  | [ReceiptSummary] | |
|   Products             | | Date | Merchant | Gross | ...|  |  (Header info)   | |
|   Insights             | | ...                          |  +----------------+  |
| > Receipts             | +------------------------------+  +----------------+  |
|   Data Quality         |                                  | [ReceiptItemsChart]| |
|                        |                                  | (Donut or bars by  | |
|                        |                                  |  line_gross share) | |
|                        |                                  +----------------+  |
|                        |                                  +----------------+  |
|                        |                                  | [RawReceiptPreview]||
|                        |                                  | (image/text)       | |
|                        |                                  +----------------+  |
+------------------------+-------------------------------------------------------+
```

Clicking a row in `[ReceiptTable]` updates the right-hand charts and details.

---

## 8. Data Quality page (optional but useful for you)

Use your `extraction_runs` table to monitor the pipeline.

```text
+--------------------------------------------------------------------------------+
| Receipt Intelligence                                          [User] [Settings]|
+--------------------------------------------------------------------------------+
| Time: [ Last 90 Days v]                                                   [___]|
+--------------------------------------------------------------------------------+
| NAVIGATION             | DATA QUALITY                                        |
|------------------------+-------------------------------------------------------|
|   Dashboard            | +------------------------------+  +----------------+  |
|   Merchants            | | [ExtractionRunsOverTime]     |  | [RunDurationBox]| |
|   Products             | | (Stacked status counts)      |  | (Duration dist.)| |
|   Insights             | +------------------------------+  +----------------+  |
|   Receipts             | +---------------------------------------------------+ |
| > Data Quality         | | [ExtractionRunTable]                             | |
|                        | |  run_id | receipt | status | duration | notes ... | |
|                        | +---------------------------------------------------+ |
+------------------------+-------------------------------------------------------+
```

---

## 9. Why this design is reasonably “intelligent”

* Clear separation of concerns: Dashboard (overview), Merchants, Products, Insights, Receipts, Data Quality.
* Global filters at the top ensure charts stay in sync, instead of each chart having its own date range confusion.
* Master–detail patterns on Merchants / Products / Receipts maximize reuse of your data model and feel natural.
* Most-important charts are visually largest and placed higher/left; secondary ones are smaller or lower.

If you later want to change direction (e.g., single-page “stories” instead of multiple tabs), the chart components and their relationships still make sense; only routing and layout containers change.

---

TL;DR: Offer a small set of sane presets (Last 7/30 days, This month, Last month, Last 3/6/12 months, Year to date, All time, Custom range) and let the app auto-adjust the time granularity.

Here’s a concrete, opinionated set I’d put in the `Time:` dropdown:

```text
Time: [ Last 30 days v ]

Dropdown options:

- Today
- Last 7 days
- Last 30 days
- This month
- Last month
- Last 3 months
- Last 6 months
- Last 12 months
- Year to date
- All time
- Custom range...
```

Explanation and reasoning (so you don’t over/under-do it):

1. Short-term behavior

   * `Today`
   * `Last 7 days`
   * `Last 30 days`
     Why: Good for recent habits and debugging the pipeline (“did yesterday’s receipts parse correctly?”).
     If data is sparse, you can show an info note like “Only 2 receipts in this range.”

2. Monthly logic users already think in

   * `This month`
   * `Last month`
     Why: Most people mentally budget in calendar months; also perfect for “How did I do this month vs last?”

3. Medium horizons for trend spotting

   * `Last 3 months`
   * `Last 6 months`
   * `Last 12 months`
     Why: This is where the “trend” charts actually become meaningful without being too noisy.

4. Annual framing and long-term

   * `Year to date`
   * `All time`
     Why: `YTD` matches how people think about yearly budgets and taxes, `All time` is good for first-time “wow” moments and sanity checks.

5. Custom

   * `Custom range...` → opens a date-range picker.
     Why: Needed for all non-standard questions (“since I moved”, “between two specific dates”).

If you want to be smart about it (and you should), couple each option with a default time bucketing rule:

```text
If range <= 31 days      -> group by day
If range <= 6 months     -> group by week or month (configurable)
If range <= 2 years      -> group by month
If range >  2 years      -> group by quarter or year
```

You can keep the UI simple: only show the single `Time:` dropdown, and let the backend decide the aggregation granularity based on the selection.
