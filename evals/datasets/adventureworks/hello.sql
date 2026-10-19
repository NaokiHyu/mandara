-- AdventureWorks の Hello world。
-- 実行: psql -X -d adventureworks -f evals/datasets/adventureworks/hello.sql

\echo '== 1. 全体像：スキーマ（≒エンティティグループ）ごとの表数と行数'
SELECT schemaname            AS schema,
       count(*)              AS tables,
       sum(n_live_tup)       AS rows
FROM pg_stat_user_tables
WHERE schemaname IN ('person', 'humanresources', 'production', 'purchasing', 'sales')
GROUP BY schemaname
ORDER BY rows DESC;

\echo '== 2. 注文 1 件をたどる：注文 → 顧客 → 人 → 配送先住所 → 州 → 国'
SELECT h.salesorderid,
       h.orderdate::date,
       h.status,
       p.firstname || ' ' || p.lastname AS customer,
       a.city,
       sp.name                          AS state,
       cr.name                          AS country,
       h.totaldue
FROM sales.salesorderheader h
JOIN sales.customer        c  ON c.customerid       = h.customerid
JOIN person.person         p  ON p.businessentityid = c.personid
JOIN person.address        a  ON a.addressid        = h.shiptoaddressid
JOIN person.stateprovince  sp ON sp.stateprovinceid = a.stateprovinceid
JOIN person.countryregion  cr ON cr.countryregioncode = sp.countryregioncode
ORDER BY h.salesorderid
LIMIT 5;

\echo '== 3. 国別の売上（part-of 階層での集計の例）'
SELECT cr.name             AS country,
       count(*)            AS orders,
       round(sum(h.subtotal)) AS subtotal
FROM sales.salesorderheader h
JOIN person.address        a  ON a.addressid        = h.shiptoaddressid
JOIN person.stateprovince  sp ON sp.stateprovinceid = a.stateprovinceid
JOIN person.countryregion  cr ON cr.countryregioncode = sp.countryregioncode
GROUP BY cr.name
ORDER BY subtotal DESC;

\echo '== 4. 低カーディナリティ列の例：注文の status（意味は列コメントにしか無い）'
SELECT status, count(*) FROM sales.salesorderheader GROUP BY status ORDER BY status;
SELECT col_description('sales.salesorderheader'::regclass, 6) AS status_comment;
