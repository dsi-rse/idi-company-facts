"""Canonical XBRL concept name constants — no string literals outside this module."""

# DEI (Document and Entity Information)
PERIOD_END = "dei:DocumentPeriodEndDate"
REGISTRANT_NAME = "dei:EntityRegistrantName"
PUBLIC_FLOAT = "dei:EntityPublicFloat"
SHARES_OUTSTANDING = "dei:EntityCommonStockSharesOutstanding"
SHELL_COMPANY = "dei:EntityShellCompany"
TRADING_SYMBOL = "dei:TradingSymbol"
NO_TRADING_SYMBOL_FLAG = "dei:NoTradingSymbolFlag"
SECURITY_EXCHANGE_NAME = "dei:SecurityExchangeName"
SECURITY_12B_TITLE = "dei:Security12bTitle"

# Revenue concepts in priority order — first annual match wins.
# Revenues (broadest total) is preferred so that companies reporting both a
# line-item ASC 606 concept AND the total don't get flagged as ambiguous.
# The Including/Excluding assessed-tax pair is placed adjacent so that filers
# tagging only one of them are handled correctly.
REVENUE_CONCEPTS: tuple[str, ...] = (
    "us-gaap:Revenues",  # broad total — preferred
    "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",  # ASC 606 excl. tax
    "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",  # ASC 606 incl. tax
    "us-gaap:SalesRevenueNet",  # deprecated 2018
)
