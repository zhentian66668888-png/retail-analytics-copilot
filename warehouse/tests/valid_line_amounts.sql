select line_id
from {{ ref('stg_order_events') }}
where quantity is null or quantity <= 0
   or unit_price_usd is null or unit_price_usd <= 0
