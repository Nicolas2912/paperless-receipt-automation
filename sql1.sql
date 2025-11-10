SELECT * from receipt_items WHERE receipt_id = 23
SELECT SUM(line_gross) from receipt_items WHERE receipt_id = 23
SELECT * from correct_data

SELECT SUM(line_gross) from receipt_items GROUP BY receipt_id

-- delete everything
DELETE FROM receipt_items;
DELETE FROM extraction_runs;
DELETE FROM files;
DELETE FROM addresses;
DELETE FROM texts;
DELETE FROM receipts;
DELETE FROM merchants;

WITH comparison AS (
  SELECT
    ri.receipt_id,
    SUM(ri.line_gross) AS receipt_items_total,
    cd.line_gross AS correct_total,
    CASE
      WHEN SUM(ri.line_gross) = cd.line_gross THEN 1
      ELSE 0
    END AS is_correct
  FROM receipt_items ri
  INNER JOIN correct_data cd ON ri.receipt_id = cd.receipt_item
  GROUP BY ri.receipt_id, cd.line_gross
)
SELECT
  receipt_id,
  receipt_items_total,
  correct_total,
  CASE
    WHEN is_correct = 1 THEN 'Correct'
    ELSE 'Incorrect'
  END AS status
FROM comparison
ORDER BY receipt_id;

-- comparison summary
WITH comparison AS (
  SELECT
    ri.receipt_id,
    SUM(ri.line_gross) AS receipt_items_total,
    cd.line_gross AS correct_total,
    CASE
      WHEN SUM(ri.line_gross) = cd.line_gross THEN 1
      ELSE 0
    END AS is_correct
  FROM receipt_items ri
  INNER JOIN correct_data cd ON ri.receipt_id = cd.receipt_item
  GROUP BY ri.receipt_id, cd.line_gross
)
SELECT
  SUM(is_correct) AS correct_count,
  COUNT(*) - SUM(is_correct) AS incorrect_count,
  COUNT(*) AS total_receipts,
  ROUND(100.0 * SUM(is_correct) / COUNT(*), 2) AS accuracy_percentage
FROM comparison;