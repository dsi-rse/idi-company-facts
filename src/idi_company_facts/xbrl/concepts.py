"""Canonical XBRL concept name constants — no string literals outside this module."""

# DEI (Document and Entity Information)
PERIOD_END = "dei:DocumentPeriodEndDate"
REGISTRANT_NAME = "dei:EntityRegistrantName"
PUBLIC_FLOAT = "dei:EntityPublicFloat"
SHARES_OUTSTANDING = "dei:EntityCommonStockSharesOutstanding"
SHELL_COMPANY = "dei:EntityShellCompany"
TRADING_SYMBOL = "dei:TradingSymbol"
SECURITY_EXCHANGE_NAME = "dei:SecurityExchangeName"
SECURITY_12B_TITLE = "dei:Security12bTitle"

# Revenue concepts in priority order — first annual match wins.
# Most-specific/most-recent standard first; IFRS equivalents follow each US-GAAP concept.
REVENUE_CONCEPTS: tuple[str, ...] = (
    "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",  # ASC 606  without tax (2018+)
    "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",  # ASC 606 with tax
    "ifrs-full:RevenueFromContractsWithCustomers",  # IFRS 15 (2018+)
    "us-gaap:SalesRevenueNet",  # deprecated 2018
    "us-gaap:Revenues",  # broad total
    "ifrs-full:Revenue",  # broad IFRS total
)
