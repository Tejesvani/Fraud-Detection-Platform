-- Standard date dimension table.
-- Grain: one calendar date.

with date_spine as (
    {{ generate_date_spine() }}
)

select
    date_day                                                as date_key,
    date_day                                                as full_date,

    extract(year from date_day)::int                        as year,
    extract(month from date_day)::int                       as month,
    extract(day from date_day)::int                         as day_of_month,
    extract(dow from date_day)::int                         as day_of_week,
    extract(doy from date_day)::int                         as day_of_year,
    extract(week from date_day)::int                        as week_of_year,
    extract(quarter from date_day)::int                     as quarter,

    to_char(date_day, 'Day')                                as day_name,
    to_char(date_day, 'Month')                              as month_name,
    to_char(date_day, 'YYYY-MM')                            as year_month,

    case when extract(dow from date_day) in (0, 6)
        then true else false end                            as is_weekend

from date_spine
