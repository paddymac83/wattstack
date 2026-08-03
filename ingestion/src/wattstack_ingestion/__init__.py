"""wattstack-ingestion: real GB market data clients and exploratory
plotting. Deliberately separate from wattstack-core -- this package
is for YOU, to build intuition about real Elexon/NESO data and decide
what the optimizer and UI should support next. It is not a dependency
of core or web, and core's PriceProvider protocol is the intended
seam if/when real data eventually feeds the optimizer itself.
"""
