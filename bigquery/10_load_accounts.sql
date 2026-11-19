-- 10_load_accounts.sql
-- Add a built account universe to the shared abm.accounts table without duplicates.
--   1. bq load --autodetect --source_format=CSV --skip_leading_rows=1 YOUR_PROJECT:abm.accounts_staging out/accounts.csv
--   2. run this MERGE
-- abm.accounts schema: https://github.com/Rahul-peacenik159/abm-tech-footprint/blob/main/bigquery/00_schema.sql

MERGE `YOUR_PROJECT.abm.accounts` AS t
USING (
  SELECT DISTINCT LOWER(domain) AS domain, ANY_VALUE(company) AS company, ANY_VALUE(industry) AS segment
  FROM `YOUR_PROJECT.abm.accounts_staging`
  WHERE domain IS NOT NULL AND domain != '' AND (note IS NULL OR note = '')   -- own domains only
  GROUP BY 1
) AS s
ON t.domain = s.domain
WHEN NOT MATCHED THEN
  INSERT (domain, company, segment) VALUES (s.domain, s.company, s.segment);
