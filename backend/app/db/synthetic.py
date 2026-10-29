"""Deterministic synthetic SME universe (NO real customers, documents or identifiers).

Every number comes from a seeded RNG so the same scenario is reproducible; amounts are in INR.
Personas are deliberately designed so that downstream models / policies behave in an
explainable way (see PERSONAS below and the README "Demo scenarios" section).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np

from app.ml.kyc import gstin_checksum_char, verhoeff_check_digit

# ----------------------------------------------------------------------------- geography
CITY = {
    # city: (state, gst_code, pin_prefix, areas)
    "Coimbatore": ("Tamil Nadu", "33", "641", ["Peelamedu Industrial Estate", "Ganapathy", "SIDCO Kurichi"]),
    "Chennai": ("Tamil Nadu", "33", "600", ["Ambattur Industrial Estate", "T Nagar", "Guindy"]),
    "Tiruppur": ("Tamil Nadu", "33", "641", ["Avinashi Road", "Kangeyam Road"]),
    "Salem": ("Tamil Nadu", "33", "636", ["Fairlands", "Ammapet"]),
    "Hyderabad": ("Telangana", "36", "500", ["Kukatpally", "Jeedimetla Industrial Area", "Madhapur", "Balanagar"]),
    "Pune": ("Maharashtra", "27", "411", ["Bhosari MIDC", "Hinjewadi Phase 2", "Chakan"]),
    "Nagpur": ("Maharashtra", "27", "440", ["Hingna MIDC", "Dharampeth"]),
    "Mumbai": ("Maharashtra", "27", "400", ["Andheri East", "Bhiwandi Logistics Park"]),
    "Kolhapur": ("Maharashtra", "27", "416", ["Shiroli MIDC", "Gokul Shirgaon"]),
    "Ahmedabad": ("Gujarat", "24", "380", ["Naroda GIDC", "Satellite"]),
    "Surat": ("Gujarat", "24", "395", ["Sachin GIDC", "Adajan"]),
    "Rajkot": ("Gujarat", "24", "360", ["Aji GIDC", "Bhaktinagar"]),
    "Vapi": ("Gujarat", "24", "396", ["Vapi GIDC Phase 1"]),
    "Bengaluru": ("Karnataka", "29", "560", ["Peenya Industrial Area", "Koramangala", "Whitefield"]),
    "Mysuru": ("Karnataka", "29", "570", ["Hootagalli Industrial Area"]),
    "Mangaluru": ("Karnataka", "29", "575", ["Baikampady Industrial Area"]),
    "Kochi": ("Kerala", "32", "682", ["Kakkanad", "Ernakulam North"]),
    "Jaipur": ("Rajasthan", "08", "302", ["Sitapura Industrial Area", "Malviya Nagar"]),
    "Delhi": ("Delhi", "07", "110", ["Nehru Place", "Okhla Phase 2"]),
    "Visakhapatnam": ("Andhra Pradesh", "37", "530", ["Autonagar", "Gajuwaka"]),
    "Rajahmundry": ("Andhra Pradesh", "37", "533", ["Danavaipeta"]),
    "Guntur": ("Andhra Pradesh", "37", "522", ["Mirchi Yard Road", "Brodipet"]),
    "Vijayawada": ("Andhra Pradesh", "37", "520", ["Benz Circle", "Auto Nagar"]),
    "Indore": ("Madhya Pradesh", "23", "452", ["Pithampur Sector 3", "Vijay Nagar"]),
    "Chandigarh": ("Chandigarh", "04", "160", ["Industrial Area Phase 1"]),
}

# industry -> payroll ratio of monthly revenue, export propensity
INDUSTRY = {
    "Manufacturing": dict(payroll=0.14, export=0.10),
    "Retail": dict(payroll=0.08, export=0.0),
    "IT & Professional Services": dict(payroll=0.48, export=0.35),
    "Logistics & Transport": dict(payroll=0.20, export=0.0),
    "Food Processing & Agro": dict(payroll=0.09, export=0.18),
    "Healthcare & Pharma": dict(payroll=0.15, export=0.0),
    "Textiles & Apparel": dict(payroll=0.17, export=0.30),
    "Construction & Infrastructure": dict(payroll=0.18, export=0.0),
    "Hospitality & Travel": dict(payroll=0.26, export=0.0),
    "Trading & Distribution": dict(payroll=0.05, export=0.0),
    "Education & Training": dict(payroll=0.45, export=0.0),
    "Renewable Energy": dict(payroll=0.14, export=0.0),
}

ARCHETYPES: dict[str, dict] = {
    "stable": dict(growth=0.004, vol=0.40, rf=1.0, ef=1.0, delay=(14, 14), emi_delays=[0] * 6, bounce=0, util=(0.35, 0.45), margin=(0.09, 0.14), concentration=1.1),
    "growing": dict(growth=0.025, vol=0.50, rf=1.05, ef=1.0, delay=(18, 15), emi_delays=[0] * 6, bounce=0, util=(0.25, 0.38), margin=(0.11, 0.17), concentration=1.2),
    "seasonal": dict(growth=0.006, vol=0.55, rf=1.0, ef=1.0, delay=(16, 16), emi_delays=[0, 0, 0, 0, 1, 0], bounce=0, util=(0.40, 0.55), margin=(0.08, 0.12), concentration=1.2, seasonal=0.22),
    "volatile": dict(growth=0.0, vol=0.95, rf=0.95, ef=1.0, delay=(20, 22), emi_delays=[0, 2, 0, 3, 0, 1], bounce=1, util=(0.50, 0.65), margin=(0.05, 0.09), concentration=1.3),
    "declining": dict(growth=-0.012, vol=0.55, rf=0.86, ef=1.03, delay=(18, 26), emi_delays=[0, 0, 1, 3, 2, 4], bounce=2, util=(0.50, 0.72), margin=(0.06, 0.09), concentration=1.4),
    "stressed": dict(growth=-0.02, vol=0.65, rf=0.72, ef=1.08, delay=(20, 34), emi_delays=[0, 1, 4, 6, 9, 12], bounce=3, util=(0.62, 0.90), margin=(0.06, 0.09), concentration=1.5),
}

CUSTOMER_POOL = [
    "Apex Industrial Supplies", "Brightway Retail Pvt Ltd", "Cauvery Enterprises", "Deepak & Sons Traders", "Evergreen Exports",
    "Falcon Engineering Works", "Gayatri Distributors", "Horizon Infra Pvt Ltd", "Indus Valley Foods", "Jagruti Textiles",
    "Kiran Medical Stores", "Lotus Retail Chain", "Mahalakshmi Agencies", "Navkar Industries", "Omega Hardware Mart",
    "Pragati Constructions", "Quick Mart Wholesale", "Rathod Brothers", "Shree Ganesh Traders", "Tirumala Enterprises",
    "Uday Auto Parts", "Vishwa Logistics", "Western Plastics Ltd", "Yash Packaging Co", "Zenon Technologies",
    "Annapurna Caterers", "Bhavani Polymers", "Charminar Retail Hub", "Dhanush Electricals", "Eastern Alloys Pvt Ltd",
]
SUPPLIER_POOL = [
    "National Steel Traders", "Reliable Raw Materials", "Metro Chemicals", "Prime Packaging Supplies", "Sri Venkatesh Suppliers",
    "Global Components Pvt Ltd", "Kiran Fuel Services", "Orbit Logistics Partners", "Shakti Power Equipment", "Trinity Wholesalers",
    "United Polymers", "Vijay Paper Mills", "Zeal Auto Ancillaries", "Alpha Cloud Services", "Delta Office Supplies",
]
EXPORT_BUYERS = ["Hamburg Textile Import GmbH (Export)", "Dubai Gulf Traders FZE (Export)", "Singapore Sourcing Pte Ltd (Export)", "Rotterdam Fresh BV (Export)", "Texas Apparel LLC (Export)"]
OTHER_BANKS = ["Meridian Bank", "Unity Finance Bank", "Capital Trust Bank"]

RMS = ["Alex Morgan", "Priya Nair", "Rohan Mehta", "Sana Qureshi"]


# ----------------------------------------------------------------------------- roster
def _r(id_, name, industry, form, city, inc_year, employees, rev, arch, rm=0, segment="client", status="active", persona=None, **ov):
    return dict(id=id_, name=name, industry=industry, form=form, city=city, inc_year=inc_year, employees=employees,
                rev=rev, arch=arch, rm=RMS[rm], segment=segment, status=status, persona=persona, **ov)


L = 100_000.0
CR = 10_000_000.0

ROSTER: list[dict] = [
    # ---- designed personas --------------------------------------------------------------
    _r("SME-1001", "Srinivasa Precision Components Pvt Ltd", "Manufacturing", "Private Limited", "Coimbatore", 2009, 86, 2.4 * CR, "stable", 0,
       persona="Stable manufacturer (Scenario A - healthy)", sanctioned=2.0 * CR, term_out=0.9 * CR, emi=3.4 * L, util_end=0.38, bureau=782,
       products=["Current account", "Cash credit", "Term loan"], balance=1.15 * CR, margin=0.14, delay=(11, 10), no_trueup=True),
    _r("SME-1002", "Nila Fashion Bazaar", "Retail", "Partnership Firm", "Chennai", 2016, 24, 0.55 * CR, "volatile", 1,
       persona="Retail business with volatile cash flows", sanctioned=0.5 * CR, term_out=0.12 * CR, emi=0.9 * L, util_end=0.62, bureau=701,
       products=["Current account", "Overdraft"], balance=0.09 * CR, declared_factor=1.6, retail=True),
    _r("SME-1003", "Ananya Digital Studio LLP", "IT & Professional Services", "LLP", "Hyderabad", 2018, 42, 0.46 * CR, "growing", 0,
       persona="Service business with strong growth potential", sanctioned=0.25 * CR, term_out=0.0, emi=0.0, util_end=0.24, bureau=744,
       products=["Current account"], balance=0.42 * CR, growth=0.04, export_share=0.4, no_trueup=True),
    _r("SME-1004", "Bharat Agro Traders Pvt Ltd", "Trading & Distribution", "Private Limited", "Guntur", 2014, 18, 0.9 * CR, "stable", 2,
       status="onboarding", persona="Incomplete / inconsistent KYC documents (Scenario C)", sanctioned=0.0, term_out=0.0, emi=0.0, util_end=0.0, bureau=688,
       products=["Current account"], balance=0.16 * CR, margin=0.06),
    _r("SME-1005", "Coastal Cold Chain Logistics", "Logistics & Transport", "Private Limited", "Kochi", 2012, 61, 1.1 * CR, "stressed", 0,
       persona="Borrower with rising utilization and delayed repayments", sanctioned=1.2 * CR, term_out=1.35 * CR, emi=7.2 * L, util_end=0.91, util_start=0.58, bureau=648,
       products=["Current account", "Cash credit", "Term loan"], balance=0.06 * CR, recent_rf=0.78, delay=(20, 33)),
    _r("SME-1006", "Deccan Packaging Solutions", "Manufacturing", "Private Limited", "Hyderabad", 2011, 72, 1.5 * CR, "stable", 0,
       persona="Business forecast to experience a short-term liquidity gap (Scenario B)", sanctioned=0.9 * CR, term_out=0.55 * CR, emi=4.6 * L, util_end=0.7, util_start=0.5, bureau=706,
       products=["Current account", "Cash credit", "Term loan"], balance=0.15 * CR, recent_rf=0.6, recent_ef=1.12, delay=(16, 38), margin=0.07, drawings_fixed=True),
    # ---- onboarding pipeline ------------------------------------------------------------
    _r("SME-1007", "Kaveri Agri Machinery", "Manufacturing", "Private Limited", "Salem", 2015, 33, 0.8 * CR, "stable", 1, status="onboarding",
       persona="Clean KYC awaiting analyst confirmation", sanctioned=0.0, term_out=0.0, emi=0.0, util_end=0.0, bureau=731, products=["Current account"], balance=0.22 * CR),
    _r("SME-1008", "Sunrise Diagnostics LLP", "Healthcare & Pharma", "LLP", "Pune", 2019, 27, 0.5 * CR, "growing", 2, status="onboarding",
       persona="KYC in progress - documents outstanding", sanctioned=0.0, term_out=0.0, emi=0.0, util_end=0.0, bureau=719, products=["Current account"], balance=0.12 * CR),
    _r("SME-1009", "Heritage Stays & Travel", "Hospitality & Travel", "Partnership Firm", "Jaipur", 2013, 38, 0.65 * CR, "seasonal", 3, status="onboarding",
       persona="KYC information requested (stale address proof)", sanctioned=0.0, term_out=0.0, emi=0.0, util_end=0.0, bureau=697, products=["Current account"], balance=0.1 * CR),
    _r("SME-1010", "Lakeview Constructions", "Construction & Infrastructure", "Private Limited", "Nagpur", 2010, 55, 1.3 * CR, "volatile", 3, status="onboarding",
       persona="KYC exception: name and signatory mismatches", sanctioned=0.0, term_out=0.0, emi=0.0, util_end=0.0, bureau=676, products=["Current account"], balance=0.2 * CR),
    # ---- active clients (generic archetypes) --------------------------------------------
    _r("SME-1011", "Godavari Rice Mills", "Food Processing & Agro", "Private Limited", "Rajahmundry", 2005, 64, 1.7 * CR, "seasonal", 1, bureau=752, util_end=0.52),
    _r("SME-1012", "Lakshmi Textiles & Exports", "Textiles & Apparel", "Private Limited", "Tiruppur", 2008, 120, 2.2 * CR, "stable", 0, bureau=739, util_end=0.57, export_share=0.45),
    _r("SME-1013", "Pioneer Pharma Distributors", "Healthcare & Pharma", "Private Limited", "Pune", 2012, 29, 1.4 * CR, "growing", 2, bureau=761, util_end=0.33),
    _r("SME-1014", "Vasant Infra Constructions", "Construction & Infrastructure", "Private Limited", "Nagpur", 2007, 140, 3.1 * CR, "declining", 3, bureau=668, util_end=0.74),
    _r("SME-1015", "BlueWave Renewable Systems", "Renewable Energy", "Private Limited", "Ahmedabad", 2017, 47, 1.2 * CR, "growing", 1, bureau=770, util_end=0.29),
    _r("SME-1016", "Hari Om Hospitality LLP", "Hospitality & Travel", "LLP", "Jaipur", 2014, 58, 0.7 * CR, "seasonal", 2, bureau=715, util_end=0.61),
    _r("SME-1017", "Tridhara Auto Components", "Manufacturing", "Private Limited", "Pune", 2010, 96, 2.0 * CR, "stable", 0, bureau=745, util_end=0.46),
    _r("SME-1018", "KnowledgeBridge Learning Pvt Ltd", "Education & Training", "Private Limited", "Bengaluru", 2016, 52, 0.62 * CR, "growing", 1, bureau=735, util_end=0.27),
    _r("SME-1019", "Sahyadri Dairy Products", "Food Processing & Agro", "Partnership Firm", "Kolhapur", 2009, 44, 0.95 * CR, "stable", 2, bureau=724, util_end=0.49),
    _r("SME-1020", "Marina Seafood Exports", "Food Processing & Agro", "Private Limited", "Visakhapatnam", 2011, 78, 2.6 * CR, "volatile", 0, bureau=709, util_end=0.66, export_share=0.7),
    _r("SME-1021", "Metro Medicals & Diagnostics", "Healthcare & Pharma", "Private Limited", "Hyderabad", 2013, 36, 0.85 * CR, "stable", 0, bureau=757, util_end=0.35),
    _r("SME-1022", "Orion Logistics Solutions", "Logistics & Transport", "Private Limited", "Mumbai", 2015, 88, 1.9 * CR, "declining", 3, bureau=683, util_end=0.78),
    _r("SME-1023", "Rajdhani Electricals", "Trading & Distribution", "Partnership Firm", "Delhi", 2004, 22, 1.25 * CR, "stable", 1, bureau=748, util_end=0.41),
    _r("SME-1024", "Zenith Software Labs", "IT & Professional Services", "Private Limited", "Bengaluru", 2019, 65, 0.9 * CR, "growing", 2, bureau=772, util_end=0.22, export_share=0.55),
    _r("SME-1025", "Kaveri Handloom Collective", "Textiles & Apparel", "Partnership Firm", "Salem", 2006, 48, 0.42 * CR, "volatile", 3, bureau=694, util_end=0.63),
    _r("SME-1026", "Surya Steel Fabricators", "Manufacturing", "Private Limited", "Rajkot", 2008, 74, 1.55 * CR, "stressed", 2, bureau=655, util_end=0.86),
    _r("SME-1027", "GreenLeaf Organics", "Food Processing & Agro", "LLP", "Mysuru", 2018, 31, 0.48 * CR, "growing", 1, bureau=728, util_end=0.31),
    _r("SME-1028", "Anand Packaging Industries", "Manufacturing", "Private Limited", "Vapi", 2013, 69, 1.3 * CR, "declining", 1, bureau=672, util_end=0.69),
    # ---- prospects (consented, simulated account-aggregator data) ------------------------
    _r("SME-1029", "Nexgen Cloud Services", "IT & Professional Services", "Private Limited", "Hyderabad", 2020, 38, 0.5 * CR, "growing", 0, segment="prospect", status="prospect", bureau=741, growth=0.035, export_share=0.5, balance=0.31 * CR),
    _r("SME-1030", "Prakash Auto Spares", "Trading & Distribution", "Proprietorship", "Coimbatore", 2011, 14, 0.7 * CR, "stable", 1, segment="prospect", status="prospect", bureau=722, balance=0.11 * CR),
    _r("SME-1031", "Sagar Frozen Foods", "Food Processing & Agro", "Private Limited", "Mangaluru", 2014, 52, 1.2 * CR, "growing", 2, segment="prospect", status="prospect", bureau=736, export_share=0.5, balance=0.2 * CR),
    _r("SME-1032", "Veda Wellness Products", "Healthcare & Pharma", "Private Limited", "Kochi", 2016, 26, 0.58 * CR, "growing", 3, segment="prospect", status="prospect", bureau=749, balance=0.17 * CR),
    _r("SME-1033", "Apex Engineering Works", "Manufacturing", "Private Limited", "Pune", 2009, 83, 1.8 * CR, "stable", 0, segment="prospect", status="prospect", bureau=758, balance=0.28 * CR),
    _r("SME-1034", "Urban Threads Fashion", "Retail", "Private Limited", "Mumbai", 2017, 35, 0.9 * CR, "volatile", 1, segment="prospect", status="prospect", bureau=702, retail=True, balance=0.08 * CR),
    _r("SME-1035", "Shivalik Trading Co", "Trading & Distribution", "Partnership Firm", "Chandigarh", 2006, 17, 1.0 * CR, "seasonal", 2, segment="prospect", status="prospect", bureau=714, balance=0.14 * CR),
    _r("SME-1036", "Omkar Construction Materials", "Construction & Infrastructure", "Private Limited", "Surat", 2012, 45, 1.45 * CR, "growing", 3, segment="prospect", status="prospect", bureau=730, balance=0.19 * CR),
    _r("SME-1037", "Dwaraka Travels & Tours", "Hospitality & Travel", "Proprietorship", "Vijayawada", 2015, 21, 0.38 * CR, "seasonal", 0, segment="prospect", status="prospect", bureau=706, balance=0.07 * CR),
    _r("SME-1038", "PixelCraft Media LLP", "IT & Professional Services", "LLP", "Chennai", 2021, 19, 0.3 * CR, "growing", 1, segment="prospect", status="prospect", bureau=725, growth=0.045, export_share=0.3, balance=0.12 * CR),
    _r("SME-1039", "Greenfield Agri Inputs", "Food Processing & Agro", "Private Limited", "Indore", 2010, 41, 1.05 * CR, "stable", 2, segment="prospect", status="prospect", bureau=740, balance=0.21 * CR),
    _r("SME-1040", "Kalyani Handicrafts", "Retail", "Proprietorship", "Hyderabad", 2022, 6, 0.12 * CR, "stable", 3, segment="prospect", status="prospect", bureau=None, retail=True, thin_file_days=24, persona="Thin-file prospect (insufficient history)", balance=0.03 * CR),
]


# ----------------------------------------------------------------------------- identifiers
def _pan_for(name: str, form: str, rng: np.random.Generator) -> str:
    letters = "ABCDEFGHJKLMNPRSTUVWXY"
    entity = {"Private Limited": "C", "LLP": "F", "Partnership Firm": "F", "Proprietorship": "P"}[form]
    first3 = "".join(rng.choice(list(letters), 3))
    last = "".join(rng.choice(list(letters), 1))
    initial = name.strip()[0].upper() if name.strip()[0].isalpha() else "A"
    digits = "".join(str(int(x)) for x in rng.integers(0, 10, 4))
    return f"{first3}{entity}{initial}{digits}{last}"


def _gstin_for(state_code: str, pan: str, rng: np.random.Generator) -> str:
    body = f"{state_code}{pan}1Z"
    return body + gstin_checksum_char(body)


def _registration(form: str, state_code: str, city: str, inc_year: int, rng: np.random.Generator) -> str:
    st = {"33": "TN", "36": "TG", "27": "MH", "24": "GJ", "29": "KA", "32": "KL", "08": "RJ", "07": "DL", "37": "AP", "23": "MP", "04": "CH"}[state_code]
    n = "".join(str(int(x)) for x in rng.integers(0, 10, 6))
    if form == "Private Limited":
        return f"U{int(rng.integers(10000, 99999))}{st}{inc_year}PTC{n}"
    if form == "LLP":
        return f"AA{'ABCDEFGHJK'[int(rng.integers(0, 10))]}-{int(rng.integers(1000, 9999))}"
    return f"UDYAM-{st}-{int(rng.integers(10, 99))}-{int(rng.integers(1000000, 9999999))}"


def aadhaar_for(rng: np.random.Generator) -> str:
    base = str(int(rng.integers(2, 10))) + "".join(str(int(x)) for x in rng.integers(0, 10, 10))
    return base + verhoeff_check_digit(base)


DIRECTOR_FIRST = ["Arvind", "Meera", "Suresh", "Kavita", "Imran", "Lakshmi", "Rajesh", "Neha", "Venkat", "Farhan", "Anita", "Karthik", "Pooja", "Harish", "Deepa", "Manoj", "Sunitha", "Vikram"]
DIRECTOR_LAST = ["Iyer", "Reddy", "Sharma", "Menon", "Patel", "Naidu", "Khan", "Joshi", "Rao", "Desai", "Pillai", "Gupta", "Kulkarni", "Bhatt", "Nair", "Verma"]


@dataclass
class Identity:
    sme_id: str
    pan: str
    gstin: str
    registration_number: str
    director_name: str
    other_director: str | None
    aadhaar: str
    account_number: str
    ifsc: str
    address: str
    pincode: str
    gst_registration_date: date
    utility_provider: str
    incorporation_date: date


def identity_for(r: dict, seed: int) -> Identity:
    """Deterministic identity per SME (also used to regenerate sample documents)."""
    rng = np.random.default_rng(seed + int(r["id"].split("-")[1]) * 7919)
    state, code, pin_prefix, areas = CITY[r["city"]]
    pan = _pan_for(r["name"], r["form"], rng)
    pin = pin_prefix + f"{int(rng.integers(1, 99)):03d}"
    street = f"{['Plot', 'Unit', 'Shed', 'Door No.'][int(rng.integers(0, 4))]} {int(rng.integers(2, 180))}, {areas[int(rng.integers(0, len(areas)))]}, {r['city']}, {state} {pin}"
    d1 = f"{DIRECTOR_FIRST[int(rng.integers(0, len(DIRECTOR_FIRST)))]} {DIRECTOR_LAST[int(rng.integers(0, len(DIRECTOR_LAST)))]}"
    d2 = f"{DIRECTOR_FIRST[int(rng.integers(0, len(DIRECTOR_FIRST)))]} {DIRECTOR_LAST[int(rng.integers(0, len(DIRECTOR_LAST)))]}" if r["form"] != "Proprietorship" else None
    inc = date(r["inc_year"], int(rng.integers(1, 12)), int(rng.integers(1, 28)))
    return Identity(
        sme_id=r["id"], pan=pan, gstin=_gstin_for(code, pan, rng), registration_number=_registration(r["form"], code, r["city"], r["inc_year"], rng),
        director_name=d1, other_director=d2, aadhaar=aadhaar_for(rng), account_number="".join(str(int(x)) for x in rng.integers(0, 10, 12)),
        ifsc=f"SMEB0{int(rng.integers(100000, 999999))}", address=street, pincode=pin,
        gst_registration_date=inc + timedelta(days=int(rng.integers(20, 120))), utility_provider=f"{state} State Electricity Board", incorporation_date=inc,
    )


# ----------------------------------------------------------------------------- transactions
def _bday(d: date, forward: bool = True) -> date:
    step = 1 if forward else -1
    while d.weekday() >= 5:
        d += timedelta(days=step)
    return d


def _ramp(days_ago: int, window: int = 60) -> float:
    """0 outside the window, grows linearly to 1 'today'."""
    if days_ago >= window:
        return 0.0
    return (window - days_ago) / window


B2B_DOW = np.array([1.15, 1.15, 1.15, 1.15, 1.05, 0.25, 0.10])
RETAIL_DOW = np.array([0.85, 0.85, 0.9, 0.95, 1.2, 1.4, 1.15])


@dataclass
class GenResult:
    txns: list[dict]
    current_balance: float
    utilization_history: list[float]
    params: dict = field(default_factory=dict)


def generate_transactions(r: dict, as_of: date, seed: int) -> GenResult:
    """Simulate ~12 months of account activity for one SME."""
    rng = np.random.default_rng(seed + int(r["id"].split("-")[1]) * 104729)
    arch = dict(ARCHETYPES[r["arch"]])
    arch.update({k: r[k] for k in ("growth", "delay", "margin") if k in r})
    ind = INDUSTRY[r["industry"]]
    rev_now = float(r["rev"])
    retail = bool(r.get("retail"))
    days_total = r.get("thin_file_days", 365)
    start = as_of - timedelta(days=days_total)
    g = float(arch["growth"])
    margin = float(arch["margin"][0] + rng.random() * (arch["margin"][1] - arch["margin"][0])) if isinstance(arch["margin"], tuple) else float(arch["margin"])
    payroll_now = rev_now * ind["payroll"]
    rent_now = rev_now * float(rng.uniform(0.02, 0.035))
    emi = float(r.get("emi", 0.0))
    if emi == 0 and r["segment"] == "prospect" and r["arch"] != "growing" and rng.random() < 0.5:
        emi = rev_now * 0.025  # servicing a loan at another bank (visible via consented data)
    other_bank = r["segment"] == "prospect" or r["status"] == "onboarding"
    gst_ratio = float(rng.uniform(0.03, 0.05))
    util_ratio = float(rng.uniform(0.008, 0.016))
    opex_ratio = float(rng.uniform(0.015, 0.03))
    supplier_ratio = max(0.1, 1.0 - margin - ind["payroll"] - rent_now / rev_now - emi / rev_now - gst_ratio - util_ratio - opex_ratio - margin * 0.85)
    # ^ the last margin*0.85 is distributed to promoters as drawings, leaving a thin retained margin
    drawings_now = rev_now * margin * 0.85
    rf = float(r.get("recent_rf", arch["rf"]))
    ef = float(r.get("recent_ef", arch["ef"]))
    d0, d1 = arch["delay"]
    vol = float(arch["vol"])
    seasonal = float(arch.get("seasonal", 0.0))
    export_share = float(r.get("export_share", ind["export"] if rng.random() < 0.5 else 0.0))
    ncust = int(rng.integers(6, 16)) if not retail else 1
    conc = float(arch["concentration"])
    w = 1.0 / np.arange(1, ncust + 1) ** conc
    w = w / w.sum()
    customers = list(rng.choice(CUSTOMER_POOL, size=ncust, replace=False)) if not retail else ["POS / UPI settlements"]
    suppliers = list(rng.choice(SUPPLIER_POOL, size=int(rng.integers(5, 9)), replace=False))
    avg_inv = rev_now * (0.012 if retail else 0.03)
    avg_sup = rev_now * 0.028
    area = CITY[r["city"]][3][0]

    txns: list[dict] = []

    def add(d: date, direction: str, amt: float, cat: str, cp: str, bounced: bool = False, delay: int = 0):
        if amt <= 0:
            return
        txns.append(dict(txn_date=d, direction=direction, amount=round(float(amt), 2), category=cat, counterparty=cp, is_bounced=bounced, days_delay=int(delay)))

    n_days = (as_of - start).days
    last_emi_idx = 0
    emi_delays = list(r.get("emi_delays", arch["emi_delays"]))
    for i in range(n_days + 1):
        d = start + timedelta(days=i)
        ago = (as_of - d).days
        months_ago = ago / 30.4
        rev_t = rev_now / ((1 + g) ** months_ago) if g > -0.5 else rev_now
        if seasonal:
            rev_t *= 1 + seasonal * math.sin(2 * math.pi * (d.timetuple().tm_yday - 60) / 365)
        shock = 1 + (rf - 1) * _ramp(ago, 55) if rf != 1.0 else 1.0
        eshock = 1 + (ef - 1) * _ramp(ago, 55) if ef != 1.0 else 1.0
        rev_t_eff = rev_t * shock
        dow = d.weekday()
        dow_f = (RETAIL_DOW if retail else B2B_DOW)[dow] / (RETAIL_DOW if retail else B2B_DOW).mean()
        dom_f = 1.0 if retail else (1.22 if d.day >= 25 else (0.9 if d.day <= 7 else 1.0))
        # ---- receipts
        lam = rev_t_eff / 30.4 * dow_f * dom_f / avg_inv
        k = int(math.floor(lam + rng.random())) if not retail else (1 if (dow < 6 or rng.random() < 0.5) else 0)
        for _ in range(k):
            amt = (avg_inv * (1.0 if retail else 1.0)) * float(rng.lognormal(0, vol * (0.6 if retail else 1.0))) / math.exp((vol * (0.6 if retail else 1.0)) ** 2 / 2)
            if retail:
                amt = rev_t_eff / 30.4 * dow_f * float(rng.lognormal(0, 0.35)) / math.exp(0.35 ** 2 / 2)
            cat, cp = "customer_receipt", customers[int(rng.choice(len(customers), p=w))] if not retail else customers[0]
            if export_share and not retail and rng.random() < export_share:
                cat, cp = "export_receipt", str(rng.choice(EXPORT_BUYERS))
            delay_mean = d0 + (d1 - d0) * _ramp(ago, 60)
            add(d, "in", amt, cat, cp, delay=max(0, int(round(rng.normal(delay_mean, 3)))))
        # ---- supplier payments
        sup_cost_t = rev_t * supplier_ratio * eshock  # suppliers don't follow the receipt shock
        if dow < 5:
            lam_s = sup_cost_t / 30.4 * (7 / 5) / avg_sup
            for _ in range(int(math.floor(lam_s + rng.random()))):
                amt = avg_sup * float(rng.lognormal(0, 0.55)) / math.exp(0.55 ** 2 / 2)
                add(d, "out", amt, "supplier_payment", str(rng.choice(suppliers)))
        # ---- fixed / calendar items
        pay_d = _bday(date(d.year, d.month, 28), forward=False)
        if d == pay_d:
            drift = (1 + g / 2) ** (-months_ago)
            add(d, "out", payroll_now * drift * float(rng.uniform(0.985, 1.015)), "payroll", "Payroll - staff salaries & statutory dues")
        if d == _bday(date(d.year, d.month, 5)):
            add(d, "out", rent_now * float(1.0 if d.month < 7 else 1.0), "rent", f"Landlord - {area} Properties")
        if d == _bday(date(d.year, d.month, 12)):
            add(d, "out", rev_now * util_ratio * float(rng.uniform(0.9, 1.15)) * eshock, "utilities", f"{CITY[r['city']][0]} Electricity Board")
        if d == _bday(date(d.year, d.month, 20)):
            add(d, "out", rev_t * gst_ratio * float(rng.uniform(0.92, 1.08)), "tax", "GSTN - GST payment")
        if d.month in (3, 6, 9, 12) and d == _bday(date(d.year, d.month, 15)):
            add(d, "out", max(0.0, rev_t * margin * 12 * 0.25 * 0.25) * float(rng.uniform(0.9, 1.1)), "tax", "Income Tax Dept - advance tax")
        if d == _bday(date(d.year, d.month, 22)):
            # promoter drawings (distribution of retained profit); stays fixed for the stressed persona
            amt = drawings_now * (1.0 if r.get("drawings_fixed") else float(shock)) * float(rng.uniform(0.9, 1.1)) * (1 + g / 2) ** (-months_ago)
            add(d, "out", amt, "owner_drawings", "Promoter drawings")
        if d.day == 15 and rng.random() < 0.6:
            add(d, "out", rev_now * opex_ratio * float(rng.uniform(0.7, 1.3)), "other_opex", str(rng.choice(["Marketing & advertising", "Professional fees", "Travel & conveyance", "Repairs & maintenance"])))
        # EMI (may be late)
        sched = _bday(date(d.year, d.month, 7))
        if emi > 0 and d == sched and ago <= 6 * 30 + 20:
            months_back = (as_of.year - d.year) * 12 + (as_of.month - d.month)
            idx = max(0, min(5, 5 - months_back))
            dl = int(emi_delays[idx]) if idx < len(emi_delays) else 0
            late = _bday(d + timedelta(days=dl))
            if late <= as_of:
                add(late, "out", emi, "loan_repayment", f"{rng.choice(OTHER_BANKS) if other_bank else 'SMEFlow Bank'} - loan EMI", delay=dl)
        elif emi > 0 and d == sched:
            add(d, "out", emi, "loan_repayment", f"{rng.choice(OTHER_BANKS) if other_bank else 'SMEFlow Bank'} - loan EMI", delay=0)

    # one-off capex for realism
    if not retail and rng.random() < 0.5 and days_total > 200:
        cd = as_of - timedelta(days=int(rng.integers(60, 300)))
        add(_bday(cd), "out", rev_now * float(rng.uniform(0.08, 0.25)), "capex", "Equipment purchase")

    # bounced payments (cheque / NACH returns - no cash movement)
    bounce_n = int(r.get("bounce", arch["bounce"]))
    for _ in range(bounce_n):
        span = 80 if r["arch"] in ("stressed", "declining") else 85
        bias = rng.beta(2.2, 1.0) if r["arch"] in ("stressed", "declining") else rng.random()
        bd = _bday(as_of - timedelta(days=int(span * (1 - bias)) + 2))
        add(bd, "out", rev_now * float(rng.uniform(0.01, 0.04)), "supplier_payment", "Cheque return - insufficient funds", bounced=True)

    txns = [t for t in txns if t["txn_date"] <= as_of]
    txns.sort(key=lambda t: (t["txn_date"], 0 if t["direction"] == "in" else 1))

    def _net() -> float:
        return sum((t["amount"] if t["direction"] == "in" else -t["amount"]) for t in txns if not t["is_bounced"])

    target = float(r.get("balance", rev_now * float(rng.uniform(0.2, 0.55))))
    opening_desired = rev_now * float(r.get("opening_factor", rng.uniform(0.3, 0.5)))
    # True-up promoter drawings so the account starts and ends at believable levels (retained profit
    # is distributed to promoters / reinvested). Drawings are floored at a small positive amount.
    drawing_txns = [t for t in txns if t["category"] == "owner_drawings"]
    if drawing_txns and not r.get("no_trueup"):
        adj = (_net() - (target - opening_desired)) / len(drawing_txns)
        for t in drawing_txns:
            t["amount"] = round(max(0.05 * drawings_now, t["amount"] + adj), 2)
    opening = target - _net()
    # guard: ordinary accounts should not sit in unexplained overdraft in the history
    run, lo = opening, opening
    for t in txns:
        if not t["is_bounced"]:
            run += t["amount"] if t["direction"] == "in" else -t["amount"]
        lo = min(lo, run)
    if lo < 0.02 * rev_now:
        opening += 0.03 * rev_now - lo
    bal = opening
    for t in txns:
        if not t["is_bounced"]:
            bal += t["amount"] if t["direction"] == "in" else -t["amount"]
        t["balance_after"] = round(bal, 2)
    # simulated utilisation history (month-end values, SIMULATED from facility data not in the txn feed)
    u_end = float(r.get("util_end", rng.uniform(*arch["util"])))
    u_start = float(r.get("util_start", max(0.05, u_end - (0.25 if r["arch"] in ("stressed",) else (0.12 if r["arch"] == "declining" else rng.uniform(-0.05, 0.05))))))
    uh = [round(float(np.clip(u_start + (u_end - u_start) * i / 5 + rng.normal(0, 0.012), 0.0, 0.99)), 3) for i in range(6)]
    uh[-1] = round(u_end, 3)
    if r["segment"] == "prospect" or r["status"] == "onboarding":
        uh = []
    return GenResult(txns=txns, current_balance=round(bal, 2), utilization_history=uh,
                     params=dict(margin=round(margin, 3), supplier_ratio=round(supplier_ratio, 3), drawings=round(drawings_now), payroll=round(payroll_now), emi=emi))
