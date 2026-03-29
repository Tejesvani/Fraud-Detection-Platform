{% macro generate_date_spine() %}
    select generate_series(
        (select min(transaction_timestamp)::date from {{ source('raw', 'transactions') }}),
        (select max(transaction_timestamp)::date from {{ source('raw', 'transactions') }}),
        '1 day'::interval
    )::date as date_day
{% endmacro %}
