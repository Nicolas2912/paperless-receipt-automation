SELECT * from receipt_items WHERE receipt_id = 9
SELECT SUM(line_gross) from receipt_items WHERE receipt_id = 1
SELECT * from correct_data

SELECT SUM(line_gross) from receipt_items GROUP BY receipt_id

SELECT * from receipt

SELECT
  merchant_id,
  name,
  length(name)               AS len_chars,
  hex(name)                  AS hex_bytes
FROM merchants
WHERE merchant_id IN (1, 9);

WITH RECURSIVE
  limits AS (
    SELECT max(length(name)) AS maxlen
    FROM merchants
    WHERE merchant_id IN (1, 9)
  ),
  idx(i) AS (
    SELECT 1
    FROM limits
    UNION ALL
    SELECT i + 1
    FROM idx, limits
    WHERE i < limits.maxlen
  )
SELECT
  substr(m1.name, i, 1)                  AS c1,
  hex(substr(m1.name, i, 1))             AS c1_hex,
  substr(m9.name, i, 1)                  AS c9,
  hex(substr(m9.name, i, 1))             AS c9_hex,
  i
FROM idx
JOIN merchants m1 ON m1.merchant_id = 1
JOIN merchants m9 ON m9.merchant_id = 9
WHERE c1 != c9 OR c1_hex != c9_hex;


DELETE from correct_data
-- create correct data TABLE
CREATE TABLE correct_data (
  receipt_item INT,
  line_gross INT
);

INSERT INTO correct_data (receipt_item, line_gross) VALUES
(1, 5901),
(2, 1330),
(3, 6484),
(4, 1470),
(5, 1372),
(6, 805),
(7, 770),
(8, 2750),
(9, 3282),
(10, 1733),
(11, 5101),
(12, 725),
(13, 4129),
(14, 12375),
(15, 921),
(16, 1247);

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