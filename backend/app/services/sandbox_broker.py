"""Sandbox broker: a local, in-process stand-in for the Trading 212 API.

Point the app at it with ``T212_BASE_ENV=sandbox`` (or ``OPENPF_SANDBOX=1``,
which also seeds sandbox credentials into a fresh database). Every T212 call the
app makes — dashboard reads, Archie's MCP tools, order execution — then hits
``/sandbox-t212/api/v0`` on this same server instead of trading212.com, so the
whole product works end-to-end with no broker account and no real money.

Prices are real (yfinance via ``market_data``, with its own fallbacks); the
portfolio, cash, fills and order history are a deterministic fake seeded on
first use and persisted to a small JSON file so orders you place stick.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SANDBOX_PREFIX = "/sandbox-t212/api/v0"
ACCOUNTS = ("invest", "stocks_isa")

# Credentials the sandbox accepts. Read keys can only read; exec keys can trade —
# mirroring the read/execute key split the app uses against the real broker.
SANDBOX_CREDENTIALS = {
    "invest": {"t212_api_key": "sandbox-invest", "t212_api_secret": "sandbox", "exec_api_key": "sandbox-invest-exec", "exec_api_secret": "sandbox"},
    "stocks_isa": {"t212_api_key": "sandbox-isa", "t212_api_secret": "sandbox", "exec_api_key": "sandbox-isa-exec", "exec_api_secret": "sandbox"},
}
_KEY_TO_ACCOUNT = {
    "sandbox-invest": ("invest", "read"),
    "sandbox-invest-exec": ("invest", "execute"),
    "sandbox-isa": ("stocks_isa", "read"),
    "sandbox-isa-exec": ("stocks_isa", "execute"),
}

# (ticker, name, approx price USD used only if no market data is reachable)
UNIVERSE: list[tuple[str, str, float]] = [
    ("NVDA", "NVIDIA", 180.0), ("MSFT", "Microsoft", 500.0), ("AAPL", "Apple", 240.0),
    ("GOOGL", "Alphabet (Class A)", 240.0), ("AMZN", "Amazon", 225.0), ("META", "Meta Platforms", 720.0),
    ("TSM", "Taiwan Semiconductor (ADR)", 290.0), ("ASML", "ASML Holding (ADR)", 950.0),
    ("AVGO", "Broadcom", 340.0), ("AMD", "Advanced Micro Devices", 210.0), ("MU", "Micron Technology", 190.0),
    ("PLTR", "Palantir Technologies", 180.0), ("TSLA", "Tesla", 430.0), ("NET", "Cloudflare", 220.0),
    ("SHOP", "Shopify", 150.0), ("COIN", "Coinbase Global", 340.0), ("CCJ", "Cameco", 85.0),
    ("OKLO", "Oklo", 120.0), ("SMR", "NuScale Power", 38.0), ("RKLB", "Rocket Lab", 55.0),
    ("ISRG", "Intuitive Surgical", 460.0), ("LLY", "Eli Lilly", 800.0), ("NVO", "Novo Nordisk (ADR)", 55.0),
    ("V", "Visa", 345.0), ("MA", "Mastercard", 570.0), ("JPM", "JPMorgan Chase", 305.0),
    ("BRK.B", "Berkshire Hathaway (Class B)", 495.0), ("COST", "Costco", 930.0), ("KO", "Coca-Cola", 68.0),
    ("XOM", "Exxon Mobil", 115.0), ("NEE", "NextEra Energy", 75.0), ("VST", "Vistra", 200.0),
    ("CEG", "Constellation Energy", 330.0), ("ETN", "Eaton", 370.0), ("ANET", "Arista Networks", 145.0),
    ("CRWD", "CrowdStrike", 480.0), ("SNOW", "Snowflake", 230.0), ("UBER", "Uber", 95.0),
    ("NFLX", "Netflix", 1200.0), ("DIS", "Walt Disney", 112.0), ("IONQ", "IonQ", 65.0),
    ("RGTI", "Rigetti Computing", 30.0), ("ARM", "Arm Holdings (ADR)", 160.0), ("SOFI", "SoFi Technologies", 27.0),
    ("HOOD", "Robinhood Markets", 140.0), ("UUUU", "Energy Fuels", 15.0), ("LEU", "Centrus Energy", 280.0),
    ("SPY", "SPDR S&P 500 ETF", 665.0), ("QQQ", "Invesco QQQ", 600.0),
]

# Wider tradable universe (names only; prices come from market data). Keeps
# agent-drafted baskets resolvable in the sandbox without a real instrument list.
EXTRA_UNIVERSE: list[tuple[str, str]] = [
    ("NXE", "NexGen Energy"), ("UEC", "Uranium Energy"), ("DNN", "Denison Mines"), ("BWXT", "BWX Technologies"),
    ("GEV", "GE Vernova"), ("VRT", "Vertiv Holdings"), ("PWR", "Quanta Services"), ("SMCI", "Super Micro Computer"),
    ("DELL", "Dell Technologies"), ("ORCL", "Oracle"), ("CRM", "Salesforce"), ("NOW", "ServiceNow"),
    ("ADBE", "Adobe"), ("INTC", "Intel"), ("QCOM", "Qualcomm"), ("TXN", "Texas Instruments"),
    ("LRCX", "Lam Research"), ("AMAT", "Applied Materials"), ("KLAC", "KLA"), ("MRVL", "Marvell Technology"),
    ("CRWV", "CoreWeave"), ("IREN", "IREN"), ("NBIS", "Nebius Group"), ("APP", "AppLovin"),
    ("DDOG", "Datadog"), ("ZS", "Zscaler"), ("PANW", "Palo Alto Networks"), ("FTNT", "Fortinet"),
    ("MSTR", "Strategy"), ("PYPL", "PayPal"), ("SQ", "Block"), ("AFRM", "Affirm"),
    ("ABNB", "Airbnb"), ("BKNG", "Booking Holdings"), ("SPOT", "Spotify"), ("RBLX", "Roblox"),
    ("UNH", "UnitedHealth"), ("JNJ", "Johnson & Johnson"), ("PFE", "Pfizer"), ("MRK", "Merck"),
    ("ABBV", "AbbVie"), ("VRTX", "Vertex Pharmaceuticals"), ("REGN", "Regeneron"), ("HIMS", "Hims & Hers Health"),
    ("WMT", "Walmart"), ("PG", "Procter & Gamble"), ("PEP", "PepsiCo"), ("MCD", "McDonald's"),
    ("NKE", "Nike"), ("SBUX", "Starbucks"), ("HD", "Home Depot"), ("LOW", "Lowe's"),
    ("CAT", "Caterpillar"), ("DE", "Deere"), ("GE", "GE Aerospace"), ("RTX", "RTX"),
    ("LMT", "Lockheed Martin"), ("NOC", "Northrop Grumman"), ("BA", "Boeing"), ("ASTS", "AST SpaceMobile"),
    ("LUNR", "Intuitive Machines"), ("JOBY", "Joby Aviation"), ("ACHR", "Archer Aviation"), ("QBTS", "D-Wave Quantum"),
    ("QUBT", "Quantum Computing"), ("FSLR", "First Solar"), ("ENPH", "Enphase Energy"), ("TLN", "Talen Energy"),
    ("NRG", "NRG Energy"), ("FCX", "Freeport-McMoRan"), ("NEM", "Newmont"), ("GOLD", "Barrick Gold"),
    ("MP", "MP Materials"), ("ALB", "Albemarle"), ("CVX", "Chevron"), ("OXY", "Occidental Petroleum"),
    ("SHEL", "Shell (ADR)"), ("BP", "BP (ADR)"), ("AZN", "AstraZeneca (ADR)"), ("GSK", "GSK (ADR)"),
    ("BABA", "Alibaba (ADR)"), ("PDD", "PDD Holdings (ADR)"), ("SE", "Sea Limited (ADR)"), ("MELI", "MercadoLibre"),
    ("NU", "Nu Holdings"), ("SAP", "SAP (ADR)"), ("TM", "Toyota (ADR)"), ("SONY", "Sony (ADR)"),
    ("BAC", "Bank of America"), ("GS", "Goldman Sachs"), ("MS", "Morgan Stanley"), ("SCHW", "Charles Schwab"),
    ("BLK", "BlackRock"), ("AXP", "American Express"), ("T", "AT&T"), ("VZ", "Verizon"),
    ("TMUS", "T-Mobile US"), ("CMCSA", "Comcast"), ("O", "Realty Income"), ("PLD", "Prologis"),
    ("EQIX", "Equinix"), ("DLR", "Digital Realty"), ("AMT", "American Tower"), ("WELL", "Welltower"),
    ("SNDK", "Sandisk"), ("WDC", "Western Digital"), ("STX", "Seagate Technology"), ("RMBS", "Rambus"),
    ("AMKR", "Amkor Technology"), ("ONTO", "Onto Innovation"), ("COHR", "Coherent"), ("TER", "Teradyne"),
]

# London / continental listings, so non-US themes draft and trade end to end.
# (Yahoo symbol, T212 code, shortName, name, currency, type). Names and quote
# currencies were taken from market data; codes follow T212's
# ``<SYMBOL><venue letter>_EQ`` form (l London, d Xetra, p Paris, a Amsterdam,
# m Milan, e Madrid, s Stockholm), pence lines quote in GBX.
INTL_UNIVERSE: list[tuple[str, str, str, str, str, str]] = [
    ('BA.L', 'BAl_EQ', 'BA', 'BAE Systems plc', 'GBX', 'STOCK'),
    ('RR.L', 'RRl_EQ', 'RR', 'Rolls-Royce Holdings plc', 'GBX', 'STOCK'),
    ('QQ.L', 'QQl_EQ', 'QQ', 'QinetiQ Group plc', 'GBX', 'STOCK'),
    ('CHG.L', 'CHGl_EQ', 'CHG', 'Chemring Group PLC', 'GBX', 'STOCK'),
    ('BAB.L', 'BABl_EQ', 'BAB', 'Babcock International Group PLC', 'GBX', 'STOCK'),
    ('SHEL.L', 'SHELl_EQ', 'SHEL', 'Shell plc', 'GBX', 'STOCK'),
    ('BP.L', 'BPl_EQ', 'BP', 'BP p.l.c.', 'GBX', 'STOCK'),
    ('AZN.L', 'AZNl_EQ', 'AZN', 'AstraZeneca PLC', 'GBX', 'STOCK'),
    ('HSBA.L', 'HSBAl_EQ', 'HSBA', 'HSBC Holdings plc', 'GBX', 'STOCK'),
    ('ULVR.L', 'ULVRl_EQ', 'ULVR', 'Unilever PLC', 'GBX', 'STOCK'),
    ('GSK.L', 'GSKl_EQ', 'GSK', 'GSK plc', 'GBX', 'STOCK'),
    ('RIO.L', 'RIOl_EQ', 'RIO', 'Rio Tinto Group', 'GBX', 'STOCK'),
    ('GLEN.L', 'GLENl_EQ', 'GLEN', 'Glencore plc', 'GBX', 'STOCK'),
    ('LSEG.L', 'LSEGl_EQ', 'LSEG', 'London Stock Exchange Group plc', 'GBX', 'STOCK'),
    ('BARC.L', 'BARCl_EQ', 'BARC', 'Barclays PLC', 'GBX', 'STOCK'),
    ('LLOY.L', 'LLOYl_EQ', 'LLOY', 'Lloyds Banking Group plc', 'GBX', 'STOCK'),
    ('NWG.L', 'NWGl_EQ', 'NWG', 'NatWest Group plc', 'GBX', 'STOCK'),
    ('DGE.L', 'DGEl_EQ', 'DGE', 'Diageo plc', 'GBX', 'STOCK'),
    ('REL.L', 'RELl_EQ', 'REL', 'RELX PLC', 'GBX', 'STOCK'),
    ('NG.L', 'NGl_EQ', 'NG', 'National Grid plc', 'GBX', 'STOCK'),
    ('VOD.L', 'VODl_EQ', 'VOD', 'Vodafone Group Public Limited Company', 'GBX', 'STOCK'),
    ('TSCO.L', 'TSCOl_EQ', 'TSCO', 'Tesco PLC', 'GBX', 'STOCK'),
    ('BT-A.L', 'BTAl_EQ', 'BT A', 'BT Group plc', 'GBX', 'STOCK'),
    ('III.L', 'IIIl_EQ', 'III', '3i Group Ord', 'GBX', 'STOCK'),
    ('EXPN.L', 'EXPNl_EQ', 'EXPN', 'Experian plc', 'GBX', 'STOCK'),
    ('BATS.L', 'BATSl_EQ', 'BATS', 'British American Tobacco p.l.c.', 'GBX', 'STOCK'),
    ('AAL.L', 'AALl_EQ', 'AAL', 'Anglo American plc', 'GBX', 'STOCK'),
    ('PRU.L', 'PRUl_EQ', 'PRU', 'Prudential plc', 'GBX', 'STOCK'),
    ('LGEN.L', 'LGENl_EQ', 'LGEN', 'Legal & General Group Plc', 'GBX', 'STOCK'),
    ('SSE.L', 'SSEl_EQ', 'SSE', 'SSE plc', 'GBX', 'STOCK'),
    ('IAG.L', 'IAGl_EQ', 'IAG', 'International Consolidated Airlines Group S.A.', 'GBX', 'STOCK'),
    ('SMSN.L', 'SMSNl_EQ', 'SMSN', 'Samsung Electronics Co., Ltd.', 'USD', 'STOCK'),
    ('VWRL.L', 'VWRLl_EQ', 'VWRL', 'Vanguard FTSE All-World UCITS ETF', 'GBP', 'ETF'),
    ('VUSA.L', 'VUSAl_EQ', 'VUSA', 'Vanguard S&P 500 UCITS ETF', 'GBP', 'ETF'),
    ('ISF.L', 'ISFl_EQ', 'ISF', 'iShares Core FTSE 100 UCITS ETF GBP (Dist)', 'GBX', 'ETF'),
    ('CSPX.L', 'CSPXl_EQ', 'CSPX', 'iShares Core S&P 500 UCITS ETF USD (Acc)', 'USD', 'ETF'),
    ('EQQQ.L', 'EQQQl_EQ', 'EQQQ', 'Invesco EQQQ NASDAQ-100 UCITS ETF', 'GBX', 'ETF'),
    ('ARMR.L', 'ARMRl_EQ', 'ARMR', 'Global X Defence Tech UCITS ETF USD Accumulating', 'USD', 'ETF'),
    ('DFNS.L', 'DFNSl_EQ', 'DFNS', 'VanEck Defense ETF A USD Acc', 'USD', 'ETF'),
    ('WDEF.L', 'WDEFl_EQ', 'WDEF', 'WisdomTree Europe Defence UCITS ETF - EUR Acc', 'EUR', 'ETF'),
    ('SGLN.L', 'SGLNl_EQ', 'SGLN', 'iShares Physical Gold ETC', 'GBX', 'STOCK'),
    ('IGLN.L', 'IGLNl_EQ', 'IGLN', 'iShares Physical Gold ETC', 'USD', 'STOCK'),
    ('INRG.L', 'INRGl_EQ', 'INRG', 'iShares Global Clean Energy Transition UCITS ETF USD (Dist)', 'GBX', 'ETF'),
    ('SEMI.L', 'SEMIl_EQ', 'SEMI', 'iShares MSCI Global Semiconductors UCITS ETF USD Acc', 'GBP', 'ETF'),
    ('RHM.DE', 'RHMd_EQ', 'RHM', 'Rheinmetall AG', 'EUR', 'STOCK'),
    ('SAP.DE', 'SAPd_EQ', 'SAP', 'SAP SE', 'EUR', 'STOCK'),
    ('SIE.DE', 'SIEd_EQ', 'SIE', 'Siemens Aktiengesellschaft', 'EUR', 'STOCK'),
    ('ALV.DE', 'ALVd_EQ', 'ALV', 'Allianz SE', 'EUR', 'STOCK'),
    ('AIR.DE', 'AIRd_EQ', 'AIR', 'Airbus SE', 'EUR', 'STOCK'),
    ('MTX.DE', 'MTXd_EQ', 'MTX', 'MTU Aero Engines AG', 'EUR', 'STOCK'),
    ('HAG.DE', 'HAGd_EQ', 'HAG', 'Hensoldt AG', 'EUR', 'STOCK'),
    ('R3NK.DE', 'R3NKd_EQ', 'R3NK', 'RENK Group AG', 'EUR', 'STOCK'),
    ('IFX.DE', 'IFXd_EQ', 'IFX', 'Infineon Technologies AG', 'EUR', 'STOCK'),
    ('DTE.DE', 'DTEd_EQ', 'DTE', 'Deutsche Telekom AG', 'EUR', 'STOCK'),
    ('BMW.DE', 'BMWd_EQ', 'BMW', 'Bayerische Motoren Werke Aktiengesellschaft', 'EUR', 'STOCK'),
    ('MBG.DE', 'MBGd_EQ', 'MBG', 'Mercedes-Benz Group AG', 'EUR', 'STOCK'),
    ('8RMY.DE', '8RMYd_EQ', '8RMY', 'HANetf ICAV - Future of European Defence Screened UCITS ETF Accumulating', 'EUR', 'ETF'),
    ('HO.PA', 'HOp_EQ', 'HO', 'Thales S.A.', 'EUR', 'STOCK'),
    ('AIR.PA', 'AIRp_EQ', 'AIR', 'Airbus SE', 'EUR', 'STOCK'),
    ('SAF.PA', 'SAFp_EQ', 'SAF', 'Safran SA', 'EUR', 'STOCK'),
    ('MC.PA', 'MCp_EQ', 'MC', 'LVMH Moët Hennessy - Louis Vuitton, Société Européenne', 'EUR', 'STOCK'),
    ('OR.PA', 'ORp_EQ', 'OR', "L'Oréal S.A.", 'EUR', 'STOCK'),
    ('TTE.PA', 'TTEp_EQ', 'TTE', 'TotalEnergies SE', 'EUR', 'STOCK'),
    ('SU.PA', 'SUp_EQ', 'SU', 'Schneider Electric S.E.', 'EUR', 'STOCK'),
    ('AM.PA', 'AMp_EQ', 'AM', 'Dassault Aviation société anonyme', 'EUR', 'STOCK'),
    ('BNP.PA', 'BNPp_EQ', 'BNP', 'BNP Paribas SA', 'EUR', 'STOCK'),
    ('LDO.MI', 'LDOm_EQ', 'LDO', 'Leonardo S.p.a.', 'EUR', 'STOCK'),
    ('FCT.MI', 'FCTm_EQ', 'FCT', 'Fincantieri S.p.A.', 'EUR', 'STOCK'),
    ('ENI.MI', 'ENIm_EQ', 'ENI', 'Eni S.p.A.', 'EUR', 'STOCK'),
    ('ISP.MI', 'ISPm_EQ', 'ISP', 'Intesa Sanpaolo S.p.A.', 'EUR', 'STOCK'),
    ('RACE.MI', 'RACEm_EQ', 'RACE', 'Ferrari N.V.', 'EUR', 'STOCK'),
    ('STMMI.MI', 'STMMIm_EQ', 'STMMI', 'STMicroelectronics N.V.', 'EUR', 'STOCK'),
    ('ASML.AS', 'ASMLa_EQ', 'ASML', 'ASML Holding N.V.', 'EUR', 'STOCK'),
    ('BESI.AS', 'BESIa_EQ', 'BESI', 'BE Semiconductor Industries N.V.', 'EUR', 'STOCK'),
    ('ADYEN.AS', 'ADYENa_EQ', 'ADYEN', 'Adyen N.V.', 'EUR', 'STOCK'),
    ('INGA.AS', 'INGAa_EQ', 'INGA', 'ING Groep N.V.', 'EUR', 'STOCK'),
    ('ASM.AS', 'ASMa_EQ', 'ASM', 'ASM International NV', 'EUR', 'STOCK'),
    ('PRX.AS', 'PRXa_EQ', 'PRX', 'Prosus N.V.', 'EUR', 'STOCK'),
    ('IDR.MC', 'IDRe_EQ', 'IDR', 'Indra Sistemas, S.A.', 'EUR', 'STOCK'),
    ('ITX.MC', 'ITXe_EQ', 'ITX', 'Industria de Diseño Textil, S.A.', 'EUR', 'STOCK'),
    ('SAN.MC', 'SANe_EQ', 'SAN', 'Banco Santander, S.A.', 'EUR', 'STOCK'),
    ('IBE.MC', 'IBEe_EQ', 'IBE', 'Iberdrola, S.A.', 'EUR', 'STOCK'),
    ('SAAB-B.ST', 'SAABBs_EQ', 'SAAB B', 'Saab AB (publ)', 'SEK', 'STOCK'),
    ('ERIC-B.ST', 'ERICBs_EQ', 'ERIC B', 'Telefonaktiebolaget LM Ericsson (publ)', 'SEK', 'STOCK'),
    ('VOLV-B.ST', 'VOLVBs_EQ', 'VOLV B', 'AB Volvo (publ)', 'SEK', 'STOCK'),
    ('ATCO-A.ST', 'ATCOAs_EQ', 'ATCO A', 'Atlas Copco AB (publ)', 'SEK', 'STOCK'),
]

# The seeded book: (account, ticker, quantity, cost basis as a multiple of the price ~1y ago)
SEED_POSITIONS: list[tuple[str, str, float]] = [
    ("stocks_isa", "NVDA", 62.0), ("stocks_isa", "MSFT", 14.0), ("stocks_isa", "GOOGL", 24.0),
    ("stocks_isa", "AMZN", 21.0), ("stocks_isa", "ASML", 4.5), ("stocks_isa", "TSM", 16.0),
    ("stocks_isa", "META", 5.0), ("stocks_isa", "AAPL", 12.0), ("stocks_isa", "LLY", 3.0),
    ("stocks_isa", "V", 8.0), ("stocks_isa", "CCJ", 40.0), ("stocks_isa", "COST", 2.0),
    ("invest", "PLTR", 28.0), ("invest", "TSLA", 6.0), ("invest", "AMD", 12.0),
    ("invest", "MU", 10.0), ("invest", "NET", 9.0), ("invest", "RKLB", 45.0),
    ("invest", "OKLO", 14.0), ("invest", "COIN", 4.0),
]
# Generous fake cash, and it tops itself up (``top_up``): a demo should never
# block on money that doesn't exist.
SEED_CASH_GBP = {"invest": 6000.0, "stocks_isa": 9000.0}
TOP_UP_STEP_GBP = 500.0

_lock = threading.RLock()
_price_cache: dict[str, tuple[float, float]] = {}
_PRICE_TTL = 300.0


def code_for(ticker: str) -> str:
    return f"{ticker.replace('.', '_')}_US_EQ" if "." in ticker else f"{ticker}_US_EQ"


def _ticker_for_code(code: str) -> str:
    row = _INTL.get(code.upper())
    if row:
        return row[0]
    base = code.removesuffix("_US_EQ")
    return base.replace("_", ".") if base.endswith("_B") else base


_NAMES = {code_for(t): n for t, n, _ in UNIVERSE}
_NAMES.update({code_for(t): n for t, n in EXTRA_UNIVERSE})
_FALLBACK = {code_for(t): p for t, _, p in UNIVERSE}
# Keyed by the UPPER code (how the app stores and sends codes); values keep the original case.
_INTL = {code.upper(): row for row in INTL_UNIVERSE for code in [row[1]]}
_NAMES.update({k: row[3] for k, row in _INTL.items()})
_YAHOO_INTL = {row[0] for row in INTL_UNIVERSE}


def currency_of(code: str) -> str:
    row = _INTL.get(code.upper())
    return row[4] if row else "USD"


def display_code(code: str) -> str:
    """The code as T212 spells it (original case: ``BAl_EQ``)."""
    row = _INTL.get(code.upper())
    return row[1] if row else code


def gbp_rate(code: str) -> float:
    """GBP per one unit of the instrument's quote currency."""
    ccy = currency_of(code)
    if ccy == "USD":
        return 1.0 / gbpusd()
    if ccy == "GBX":
        return 0.01
    if ccy == "GBP":
        return 1.0
    hit = _price_cache.get(f"FX:{ccy}")
    if hit and time.time() - hit[1] < _PRICE_TTL:
        return hit[0]
    value = {"EUR": 0.85, "SEK": 0.075, "CHF": 0.93}.get(ccy, 1.0)
    try:
        from app.services.fx import get_fx_rate

        value = float(get_fx_rate(ccy, "GBP")) or value
    except Exception:  # noqa: BLE001
        pass
    _price_cache[f"FX:{ccy}"] = (value, time.time())
    return value


def is_sandbox_enabled_env() -> bool:
    from app.core.sandbox import is_sandbox

    return is_sandbox()


def sandbox_base_url() -> str:
    from app.core.sandbox import sandbox_base_url as _url

    return _url()


def _state_path() -> Path:
    raw = os.environ.get("OPENPF_SANDBOX_STATE", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path(__file__).resolve().parents[2] / "sandbox_state.json"


# ── prices ────────────────────────────────────────────────────────────────────

def _history(ticker: str, days: int):
    from app.services.market_data import fetch_history

    return fetch_history(ticker if ticker in _YAHOO_INTL else ticker.replace(".", "-"), lookback_days=days)


def price(code: str) -> float:
    """Latest price in the instrument's quote currency, cached for a few minutes."""
    now = time.time()
    hit = _price_cache.get(code)
    if hit and now - hit[1] < _PRICE_TTL:
        return hit[0]
    value = _FALLBACK.get(code, 100.0)
    try:
        frame = _history(_ticker_for_code(code), 10)
        latest = float(frame["close"].iloc[-1])
        if math.isfinite(latest) and latest > 0:
            value = latest
        elif hit:
            value = hit[0]
    except Exception:  # noqa: BLE001 — offline / throttled: keep the fallback
        if hit:
            value = hit[0]
    _price_cache[code] = (value, now)
    return value


def _price_on(code: str, on: date) -> float:
    try:
        frame = _history(_ticker_for_code(code), 420)
        dates = frame["date"] if "date" in frame.columns else frame.index
        best = None
        for i, d in enumerate(dates):
            day = d.date() if hasattr(d, "date") else date.fromisoformat(str(d)[:10])
            if day <= on:
                best = i
        if best is not None:
            return float(frame["close"].iloc[best])
    except Exception:  # noqa: BLE001
        pass
    return price(code) * 0.8


def gbpusd() -> float:
    hit = _price_cache.get("GBPUSD")
    if hit and time.time() - hit[1] < _PRICE_TTL:
        return hit[0]
    value = 1.33
    try:
        from app.services.fx import get_fx_rate

        value = float(get_fx_rate("GBP", "USD")) or value
    except Exception:  # noqa: BLE001
        pass
    _price_cache["GBPUSD"] = (value, time.time())
    return value


# ── state ─────────────────────────────────────────────────────────────────────

def _iso(dt: datetime) -> str:
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _rand(seed: str) -> float:
    return int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def _seed_state() -> dict[str, Any]:
    today = date.today()
    fx = gbpusd()
    state: dict[str, Any] = {"seeded_at": _iso(datetime.utcnow()), "accounts": {}}
    for account in ACCOUNTS:
        state["accounts"][account] = {
            "cash": SEED_CASH_GBP[account],
            "positions": {},
            "orders": [],
            "pending": [],
            "dividends": [],
            "transactions": [],
        }
    # The book is built ~13 months ago (first tranche of every name within a
    # few days of a common start), then topped up with second tranches funded
    # by monthly-style deposits. Each account is funded the day before its
    # first buy with exactly what that first wave costs plus the cash left over,
    # so the equity curve starts at the funded value — no long flat stretch of
    # idle cash and no later jump when the other account gets funded — and the
    # contribution-netted return lines up (no phantom gains from unfunded fills).
    start = today - timedelta(days=385)
    for account, ticker, qty in SEED_POSITIONS:
        code = code_for(ticker)
        acct = state["accounts"][account]
        d1 = start + timedelta(days=int(_rand(code + "a") * 6))
        d2 = today - timedelta(days=int(20 + _rand(code + "b") * 200))
        q1 = round(qty * (0.55 + 0.2 * _rand(code + "q")), 4)
        total_cost_usd = 0.0
        for n, (when, q) in enumerate(((d1, q1), (d2, round(qty - q1, 4)))):
            px = _price_on(code, when)
            total_cost_usd += px * q
            filled_at = datetime.combine(when, datetime.min.time()) + timedelta(hours=15, minutes=int(_rand(code + str(when)) * 50))
            rec = _order_record(code, "BUY", q, px, fx, filled_at, "MARKET")
            acct["orders"].append(rec)
            if n == 1:  # top-ups are paid for by a deposit that morning
                acct["transactions"].append({
                    "type": "DEPOSIT", "amount": float(rec["fill"]["walletImpact"]["netValue"]), "currency": "GBP",
                    "dateTime": _iso(filled_at - timedelta(hours=6)), "reference": f"sbx-dep-{account}-{code}",
                })
        acct["positions"][code] = {"quantity": qty, "avg_price": total_cost_usd / qty, "opened_at": _iso(datetime.combine(d1, datetime.min.time()))}
    for account in ACCOUNTS:
        acct = state["accounts"][account]
        acct["orders"].sort(key=lambda o: o["order"]["createdAt"], reverse=True)
        if acct["orders"]:
            earliest: dict[str, dict[str, Any]] = {}
            for o in acct["orders"]:
                cur = earliest.get(o["order"]["ticker"])
                if cur is None or o["order"]["createdAt"] < cur["order"]["createdAt"]:
                    earliest[o["order"]["ticker"]] = o
            first = min(datetime.fromisoformat(o["order"]["createdAt"].rstrip("Z")) for o in acct["orders"])
            spent = sum(float(o["fill"]["walletImpact"]["netValue"]) for o in earliest.values())
            acct["transactions"].append({
                "type": "DEPOSIT", "amount": round(spent + acct["cash"], 2), "currency": "GBP",
                "dateTime": _iso(first - timedelta(days=1)), "reference": f"sbx-dep-{account}",
            })
        acct["transactions"].sort(key=lambda t: t["dateTime"], reverse=True)
    # A couple of dividends so income views have something to show.
    for account, ticker, amount, days_ago in (("stocks_isa", "MSFT", 9.12, 34), ("stocks_isa", "AAPL", 2.31, 58), ("stocks_isa", "V", 3.88, 75), ("stocks_isa", "COST", 2.02, 101), ("stocks_isa", "MSFT", 8.95, 125)):
        paid = datetime.combine(today - timedelta(days=days_ago), datetime.min.time()) + timedelta(hours=12)
        state["accounts"][account]["dividends"].append({
            "ticker": code_for(ticker), "amount": amount, "currency": "GBP", "paidOn": _iso(paid),
            "quantity": next((q for a, t, q in SEED_POSITIONS if a == account and t == ticker), 0.0),
            "reference": f"sbx-div-{ticker}-{days_ago}", "type": "ORDINARY",
        })
    return state


def _order_record(code: str, side: str, qty: float, px: float, fx: float, when: datetime, otype: str) -> dict[str, Any]:
    oid = int(_rand(f"{code}{when.isoformat()}{qty}") * 10**10) or uuid.uuid4().int % 10**10
    rate = gbp_rate(code)
    net_gbp = round(qty * px * rate, 2)
    return {
        "order": {
            "id": oid, "type": otype, "side": side, "status": "FILLED", "ticker": display_code(code),
            "instrument": {"ticker": display_code(code), "name": _NAMES.get(code, code), "currency": currency_of(code), "isin": ""},
            "quantity": qty if side == "BUY" else -qty, "filledQuantity": qty, "filledValue": net_gbp,
            "currency": "GBP", "createdAt": _iso(when), "initiatedFrom": "API",
        },
        "fill": {
            "id": oid + 1, "quantity": qty if side == "BUY" else -qty, "price": round(px, 4), "filledAt": _iso(when), "type": "TRADE",
            "walletImpact": {"currency": "GBP", "netValue": net_gbp, "fxRate": round(rate, 6), "taxes": []},
        },
    }


def _load() -> dict[str, Any]:
    path = _state_path()
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            logger.warning("sandbox state unreadable; reseeding", exc_info=True)
    state = _seed_state()
    _save(state)
    logger.info("Seeded sandbox broker state at %s", path)
    return state


def _save(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(path)


def reset() -> None:
    with _lock:
        _save(_seed_state())


# ── API surface (shapes mirror the real T212 v0 API) ──────────────────────────

class SandboxError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def authenticate(header: str | None) -> tuple[str, str]:
    if not header or not header.startswith("Basic "):
        raise SandboxError(401, "missing credentials")
    try:
        key = base64.b64decode(header.split(" ", 1)[1]).decode().split(":", 1)[0]
    except Exception:  # noqa: BLE001
        raise SandboxError(401, "bad credentials") from None
    hit = _KEY_TO_ACCOUNT.get(key)
    if not hit:
        raise SandboxError(401, "unknown sandbox key")
    return hit


def _positions_payload(acct: dict[str, Any]) -> list[dict[str, Any]]:
    fx = gbpusd()
    out = []
    for code, pos in acct["positions"].items():
        qty = float(pos["quantity"])
        if qty <= 0:
            continue
        px = price(code)
        rate = gbp_rate(code)
        cost_gbp = qty * float(pos["avg_price"]) * rate
        value_gbp = qty * px * rate
        out.append({
            "instrument": {"ticker": display_code(code), "name": _NAMES.get(code, code), "currency": currency_of(code), "isin": ""},
            "createdAt": pos.get("opened_at"),
            "quantity": qty, "quantityAvailableForTrading": qty, "quantityInPies": 0,
            "averagePricePaid": round(float(pos["avg_price"]), 4), "currentPrice": round(px, 4),
            "walletImpact": {
                "currency": "GBP", "totalCost": round(cost_gbp, 2), "currentValue": round(value_gbp, 2),
                "unrealizedProfitLoss": round(value_gbp - cost_gbp, 2), "fxImpact": 0.0,
            },
        })
    return sorted(out, key=lambda p: -p["walletImpact"]["currentValue"])


def account_summary(account: str) -> dict[str, Any]:
    with _lock:
        acct = _load()["accounts"][account]
        positions = _positions_payload(acct)
    invested_value = sum(p["walletImpact"]["currentValue"] for p in positions)
    cost = sum(p["walletImpact"]["totalCost"] for p in positions)
    reserved = sum(float(o.get("value") or 0) for o in acct["pending"] if o.get("side") == "BUY")
    return {
        "id": 10_000_000 + ACCOUNTS.index(account),
        "currency": "GBP",
        "totalValue": round(invested_value + acct["cash"], 2),
        "cash": {"availableToTrade": round(acct["cash"] - reserved, 2), "reservedForOrders": round(reserved, 2), "inPies": 0.0},
        "investments": {"currentValue": round(invested_value, 2), "totalCost": round(cost, 2), "unrealizedProfitLoss": round(invested_value - cost, 2), "realizedProfitLoss": 0.0},
    }


def positions(account: str) -> list[dict[str, Any]]:
    with _lock:
        return _positions_payload(_load()["accounts"][account])


def pending_orders(account: str) -> list[dict[str, Any]]:
    with _lock:
        return list(_load()["accounts"][account]["pending"])


def _page(items: list[dict[str, Any]], params: dict[str, str], path: str) -> dict[str, Any]:
    limit = max(1, min(int(params.get("limit") or 50), 50))
    start = int(params.get("cursor") or 0)
    chunk = items[start:start + limit]
    nxt = start + limit
    extra = "".join(f"&{k}={v}" for k, v in params.items() if k in {"ticker"} and v)
    return {"items": chunk, "nextPagePath": f"{SANDBOX_PREFIX}{path}?limit={limit}&cursor={nxt}{extra}" if nxt < len(items) else None}


def order_history(account: str, params: dict[str, str]) -> dict[str, Any]:
    with _lock:
        items = list(_load()["accounts"][account]["orders"])
    ticker = (params.get("ticker") or "").upper()
    if ticker:
        items = [i for i in items if i["order"]["ticker"].upper() == ticker]
    return _page(items, params, "/equity/history/orders")


def dividends(account: str, params: dict[str, str]) -> dict[str, Any]:
    with _lock:
        items = list(_load()["accounts"][account]["dividends"])
    return _page(items, params, "/equity/history/dividends")


def transactions(account: str, params: dict[str, str]) -> dict[str, Any]:
    with _lock:
        items = list(_load()["accounts"][account]["transactions"])
    return _page(items, params, "/equity/history/transactions")


def instruments() -> list[dict[str, Any]]:
    return [
        {"ticker": code_for(t), "name": n, "shortName": t, "currencyCode": "USD", "type": "ETF" if t in {"SPY", "QQQ"} else "STOCK",
         "isin": "", "workingScheduleId": 1, "minTradeQuantity": 0.01, "maxOpenQuantity": 100000, "addedOn": "2018-01-01T00:00:00.000Z"}
        for t, n in [(t, n) for t, n, _ in UNIVERSE] + EXTRA_UNIVERSE
    ] + [
        {"ticker": code, "name": name, "shortName": short, "currencyCode": ccy, "type": typ, "isin": "",
         "workingScheduleId": 1, "minTradeQuantity": 0.01, "maxOpenQuantity": 100000, "addedOn": "2018-01-01T00:00:00.000Z"}
        for _, code, short, name, ccy, typ in INTL_UNIVERSE
    ]


def exchanges() -> list[dict[str, Any]]:
    return [{"id": 1, "name": "NASDAQ/NYSE (sandbox)", "workingSchedules": [{"id": 1, "timeEvents": []}]}]


def _positive(value: Any) -> bool:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(out) and out > 0


def _top_up_locked(acct: dict[str, Any], account: str, needed_gbp: float) -> float:
    have = float(acct["cash"])
    if needed_gbp <= have + 1e-6:
        return 0.0
    added = math.ceil((needed_gbp - have) / TOP_UP_STEP_GBP) * TOP_UP_STEP_GBP + TOP_UP_STEP_GBP
    acct["cash"] = round(have + added, 2)
    acct["transactions"].insert(0, {
        "type": "DEPOSIT", "amount": added, "currency": "GBP", "dateTime": _iso(datetime.utcnow()),
        "reference": f"sbx-demo-cash-{account}-{uuid.uuid4().hex[:6]}",
    })
    logger.info("sandbox broker: demo cash added £%.0f to %s", added, account)
    return added


def top_up(account: str, needed_gbp: float) -> float:
    """Make sure the fake account's cash covers ``needed_gbp``; returns what was
    added (a DEPOSIT transaction, so contribution-netted returns stay honest)."""
    with _lock:
        state = _load()
        added = _top_up_locked(state["accounts"][account], account, needed_gbp)
        if added:
            _save(state)
        return added


def place_order(account: str, scope: str, kind: str, body: dict[str, Any]) -> dict[str, Any]:
    if scope != "execute":
        raise SandboxError(403, "read key cannot place orders")
    code = str(body.get("ticker") or "").upper()
    if code not in _NAMES:
        raise SandboxError(400, f"unknown instrument {code}")
    try:
        qty = float(body.get("quantity") or 0)
    except (TypeError, ValueError):
        raise SandboxError(400, "quantity must be a number") from None
    if not math.isfinite(qty) or qty == 0:
        raise SandboxError(400, "quantity must be a non-zero finite number")
    if kind in {"limit", "stop_limit"} and not _positive(body.get("limitPrice")):
        raise SandboxError(400, "limitPrice must be a positive number")
    if kind in {"stop", "stop_limit"} and not _positive(body.get("stopPrice")):
        raise SandboxError(400, "stopPrice must be a positive number")
    side = "BUY" if qty > 0 else "SELL"
    now = datetime.utcnow()
    with _lock:
        state = _load()
        acct = state["accounts"][account]
        if kind != "market":
            oid = uuid.uuid4().int % 10**10
            pending = {
                "id": oid, "ticker": code, "type": kind.upper(), "side": side, "quantity": qty, "status": "NEW",
                "limitPrice": body.get("limitPrice"), "stopPrice": body.get("stopPrice"),
                "creationTime": _iso(now), "value": round(abs(qty) * price(code) * gbp_rate(code), 2),
            }
            acct["pending"].append(pending)
            _save(state)
            return pending
        px = price(code)
        fx = gbpusd()
        notional = abs(qty) * px * gbp_rate(code)
        pos = acct["positions"].get(code, {"quantity": 0.0, "avg_price": px, "opened_at": _iso(now)})
        if side == "BUY":
            _top_up_locked(acct, account, notional)  # fake money: never refuse a demo buy for cash
            new_qty = pos["quantity"] + qty
            pos["avg_price"] = (pos["quantity"] * pos["avg_price"] + qty * px) / new_qty
            pos["quantity"] = round(new_qty, 6)
            acct["cash"] = round(acct["cash"] - notional, 2)
        else:
            if abs(qty) > pos["quantity"] + 1e-9:
                raise SandboxError(400, "selling more than held")
            pos["quantity"] = round(pos["quantity"] - abs(qty), 6)
            acct["cash"] = round(acct["cash"] + notional, 2)
        acct["positions"][code] = pos
        if pos["quantity"] <= 0:
            acct["positions"].pop(code, None)
        record = _order_record(code, side, abs(qty), px, fx, now, "MARKET")
        acct["orders"].insert(0, record)
        _save(state)
        order = dict(record["order"])
        order["filledPrice"] = round(px, 4)
        return order


def cancel_order(account: str, scope: str, order_id: str) -> None:
    if scope != "execute":
        raise SandboxError(403, "read key cannot cancel orders")
    with _lock:
        state = _load()
        acct = state["accounts"][account]
        before = len(acct["pending"])
        acct["pending"] = [o for o in acct["pending"] if str(o["id"]) != str(order_id)]
        if len(acct["pending"]) == before:
            raise SandboxError(404, "order not found")
        _save(state)


def seed_config_if_fresh(db) -> bool:
    """With OPENPF_SANDBOX=1, point a *fresh* database at the sandbox broker.

    Never touches a database that already has real credentials configured."""
    from app.services.config_store import ConfigStore

    store = ConfigStore(db)
    # Credentials are locked to the mock broker in sandbox mode (ConfigStore),
    # so "fresh" just means the broker config hasn't been seeded yet.
    if (store.get("broker_config", {}) or {}).get("t212_base_env") == "sandbox":
        return False
    for account in ACCOUNTS:
        store.set_account_credentials(account, {**SANDBOX_CREDENTIALS[account], "enabled": True, "exec_enabled": True})
    store.set_broker({"t212_base_env": "sandbox", "broker_mode": "live"})
    return True
