select country_code, country_name
from {{ ref('country_codes') }}
