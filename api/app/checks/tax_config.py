"""UK PAYE income tax and employee National Insurance thresholds for the 2026/27 tax year.

!!! MUST BE CHECKED AGAINST GOV.UK BEFORE LAUNCH !!!
These figures are what the payslip_realistic check is judged against. Confirm every value at:
  - https://www.gov.uk/guidance/rates-and-thresholds-for-employers-2026-to-2027
  - https://www.gov.uk/income-tax-rates
They assume England, Wales and Northern Ireland rates (not Scotland), a standard tax code and
monthly pay, and category A National Insurance.
"""

from datetime import date
from decimal import Decimal

TAX_YEAR_START = date(2026, 4, 6)
TAX_YEAR_END = date(2027, 4, 5)

# Standard tax codes we can check. Emergency / non-cumulative suffixes are stripped first.
STANDARD_TAX_CODES = {"1257L"}

# Income tax (annual figures; the check divides by 12 for monthly pay)
PERSONAL_ALLOWANCE = Decimal("12570")
BASIC_RATE_BAND = Decimal("37700")         # taxable income above the allowance taxed at basic rate
ADDITIONAL_RATE_THRESHOLD = Decimal("125140")
BASIC_RATE = Decimal("0.20")
HIGHER_RATE = Decimal("0.40")
ADDITIONAL_RATE = Decimal("0.45")

# Employee Class 1 National Insurance, category A (monthly figures)
NI_PRIMARY_THRESHOLD_MONTHLY = Decimal("1048")
NI_UPPER_EARNINGS_LIMIT_MONTHLY = Decimal("4189")
NI_MAIN_RATE = Decimal("0.08")
NI_UPPER_RATE = Decimal("0.02")

# How far a payslip may be from our calculation before it is flagged
TOLERANCE_PCT = Decimal("5")
TOLERANCE_FLOOR = Decimal("2.00")  # pounds; stops tiny amounts (e.g. expected £0) flagging on pennies
