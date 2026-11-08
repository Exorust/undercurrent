"""Parse arXiv author comments into venue / kind / year / code_url / pages / withdrawn."""
import json, re, sys
from collections import Counter

# canonical name -> regex aliases separated by '|' (spaces mean any whitespace; case-insensitive)
VENUES = {
 "NeurIPS": "NeurIPS|NIPS|Neural Information Processing Systems", "ICML": "ICML|International Conference on Machine Learning",
 "ICLR": "ICLR|International Conference on Learning Representations", "CVPR": "CVPR|Computer Vision and Pattern Recognition",
 "ICCV": "ICCV", "ECCV": "ECCV", "WACV": "WACV|Winter Conference on Applications of Computer Vision",
 "ACL": "ACL", "EMNLP": "EMNLP|Empirical Methods in Natural Language Processing", "NAACL": "NAACL", "EACL": "EACL",
 "COLING": "COLING", "COLM": "COLM", "AAAI": "AAAI", "IJCAI": "IJCAI", "AISTATS": "AISTATS", "UAI": "UAI",
 "COLT": "COLT", "KDD": "KDD|Knowledge Discovery and Data Mining", "WWW": "WWW|The Web Conference|TheWebConf",
 "WSDM": "WSDM", "SIGIR": "SIGIR", "CIKM": "CIKM", "RecSys": "RecSys", "CHI": "CHI", "UIST": "UIST", "CSCW": "CSCW",
 "ICRA": "ICRA", "IROS": "IROS", "CoRL": "CoRL", "RSS": "RSS", "Interspeech": "Interspeech", "ICASSP": "ICASSP",
 "MICCAI": "MICCAI", "SIGGRAPH Asia": "SIGGRAPH Asia", "SIGGRAPH": r"SIGGRAPH(?!\s+Asia)", "ICSE": "ICSE",
 "FSE": "FSE|ESEC/FSE", "ASE": "ASE", "ISSTA": "ISSTA", "PLDI": "PLDI", "POPL": "POPL", "OOPSLA": "OOPSLA",
 "OSDI": "OSDI", "SOSP": "SOSP", "NSDI": "NSDI", "EuroSys": "EuroSys", "ATC": "USENIX ATC|ATC",
 "USENIX Security": "USENIX Security", "CCS": "CCS|ACM CCS", "S&P": r"S&P|IEEE Symposium on Security and Privacy",
 "NDSS": "NDSS", "ISCA": "ISCA", "MICRO": "MICRO", "ASPLOS": "ASPLOS", "HPCA": "HPCA", "MLSys": "MLSys",
 "SC": r"SC(?=\s*['’]?\s*(?:20)?\d\d)|Supercomputing", "SIGMOD": "SIGMOD", "VLDB": "VLDB|PVLDB", "ICDE": "ICDE",
 "STOC": "STOC", "FOCS": "FOCS", "SODA": "SODA", "ICALP": "ICALP", "LICS": "LICS", "CAV": "CAV", "TACL": "TACL",
 "TMLR": "TMLR", "JMLR": "JMLR", "TPAMI": "TPAMI|T-PAMI|Transactions on Pattern Analysis and Machine Intelligence",
 "TIP": "TIP|IEEE Transactions on Image Processing", "IJCV": "IJCV", "TNNLS": "TNNLS|T-NNLS|Transactions on Neural Networks and Learning Systems",
 "TKDE": "TKDE|Transactions on Knowledge and Data Engineering", "TOG": "TOG|ACM Transactions on Graphics",
 "Nature Machine Intelligence": "Nature Machine Intelligence",
 "Nature": r"(?:published in|accepted (?:at|in|by|to|for publication in)|to appear in|appeared in)\s+(?:the\s+)?Nature(?!\s+[A-Za-z])",
 "Science": r"(?:published in|accepted (?:at|in|by|to|for publication in)|to appear in|appeared in)\s+(?:the\s+)?Science(?!\s+[A-Za-z])",
 "ACM Computing Surveys": "ACM Computing Surveys", "ECAI": "ECAI", "IJCNN": "IJCNN", "ICDM": "ICDM", "ICPR": "ICPR",
 "BMVC": "BMVC", "3DV": "3DV", "ACM MM": "ACM MM|ACMMM|ACM Multimedia", "MobiCom": "MobiCom", "INFOCOM": "INFOCOM",
 "SIGCOMM": "SIGCOMM", "ISIT": "ISIT", "CDC": "CDC", "ACC": "ACC", "L4DC": "L4DC",
}
AMBIG = {"SC", "ATC", "ASE", "FSE", "CDC", "ACC", "RSS", "TOG", "TIP", "UAI", "CCS", "MICRO", "WWW", "CHI", "SODA",
         "COLT", "CAV", "ISIT", "S&P", "ACL", "KDD", "ICE", "LICS", "FOCS", "STOC", "NIPS"}

def _compile(name, alts):
    parts = []
    for a in alts.split("|"):
        a = a.replace(" ", r"\s+")
        parts.append(f"(?-i:{a})" if name in AMBIG and a.isupper() or a == "ATC" else a)
    return re.compile(r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z])" % "|".join(parts), re.I)
_PATS = [(n, _compile(n, a)) for n, a in VENUES.items()]

_ACC = re.compile(r"accepted|to appear|published|appeared|camera[- ]ready|\boral\b|spotlight|poster|presented|proceedings|highlight", re.I)
_SUB = re.compile(r"under review|submitted|in submission|submission to|under submission", re.I)
_URL = re.compile(r"https?://[^\s<>\"'{}\\\]]+")
_PROJ = re.compile(r"(?:project\s*page|project|code|website|homepage|github|source)[^h]{0,25}$", re.I)
_PAGES = re.compile(r"(\d+)\s*(?:-\s*)?(?:pages?|pp)\b", re.I)
_WD = re.compile(r"\b(?:has|have|had|is|was|been)\s+(?:been\s+)?withdrawn\b|\bwithdrawn\s+(?:by|due|because|from)\b|"
                 r"decided\s+to\s+withdraw|\b(?:paper|article|manuscript|submission)\s+withdrawn", re.I)
_YR = re.compile(r"[\s'’-]*(20[0-3]\d|(?:1[5-9]|2\d|3[0-5]))(?![\d.]|\s*(?:pages?|pp|figures?|tables?)\b)(?!\w)")
_YR4 = re.compile(r"(?<![\d.:])(20[0-3]\d)(?![\d.]\d)")

def _year(text, m):
    y = _YR.match(text, m.end())
    if y:
        v = y.group(1)
        return int(v) if len(v) == 4 else 2000 + int(v)
    after = _YR4.findall(text[m.end():m.end() + 60])
    if after:
        return int(after[0])
    before = _YR4.findall(text[max(0, m.start() - 70):m.start()])
    if before:
        return int(before[-1])
    return None

def _code(text):
    for m in _URL.finditer(text):
        u = m.group(0).rstrip(".,;:)]}'\"\\")
        host = re.sub(r"^https?://(www\.)?", "", u).split("/")[0].lower()
        if host in ("github.com", "gitlab.com", "huggingface.co") or host.endswith(".github.io"):
            return u
        if not re.search(r"doi\.org|arxiv\.org", host) and _PROJ.search(text[max(0, m.start() - 40):m.start()]):
            return u
    return None

def parse_comment(text):
    out = dict(venue=None, kind=None, year=None, code_url=None, pages=None, withdrawn=False)
    if not text:
        return out
    hits = sorted((m.start(), n, m) for n, p in _PATS for m in [p.search(text)] if m)
    cue = _ACC.search(text)
    if hits:
        pick = next((h for h in hits if cue and h[0] >= cue.start()), hits[0])
        out["venue"], out["year"] = pick[1], _year(text, pick[2])
    a, s = _ACC.search(text), _SUB.search(text)
    if re.search(r"work\s*shop", text, re.I):
        out["kind"] = "workshop"
    elif a and (not s or a.start() < s.start()):
        out["kind"] = "accepted"
    elif s:
        out["kind"] = "submitted"
    elif out["venue"]:
        out["kind"] = "accepted"
    out["code_url"] = _code(text)
    p = _PAGES.search(text)
    out["pages"] = int(p.group(1)) if p else None
    out["withdrawn"] = bool(_WD.search(text))
    return out

CASES = [  # (comment, venue, kind, year); real strings from data/spikes/s1.json plus spec examples
 ("ICML 2025", "ICML", "accepted", 2025),
 ("8 pages, 6 figures, 1 table, submitted to IEEE RO-MAN 2025", None, "submitted", None),
 ("ACL Findings 2024", "ACL", "accepted", 2024),
 ("ECCV 2024; 41 pages, 19 figures", "ECCV", "accepted", 2024),
 ("Accepted by NAACL 2025 Findings", "NAACL", "accepted", 2025),
 ("Accepted to the IEEE/CVF Winter Conference on Applications of Computer Vision (WACV) 2026", "WACV", "accepted", 2026),
 ("Accepted as an Oral paper at ICML 2026. Code available at https://github.com/wbopan/flashtrace", "ICML", "accepted", 2026),
 ("53 Pages, 12 Figures, CAV 2024", "CAV", "accepted", 2024),
 ("This paper is accepted at the CVPR Conference on Computer Vision and Pattern Recognition 2025", "CVPR", "accepted", 2025),
 ("Accepted at NeurIPS 2026 (poster)", "NeurIPS", "accepted", 2026),
 ("Accepted by CVPR2024", "CVPR", "accepted", 2024),
 ("Accepted by IEEE ICASSP 2026", "ICASSP", "accepted", 2026),
 ("Accepted by IJCNN 2025", "IJCNN", "accepted", 2025),
 ("Accepted at the Machine Learning and the Physical Sciences workshop, NeurIPS 2023", "NeurIPS", "workshop", 2023),
 ("Accepted by AAAI 2024", "AAAI", "accepted", 2024),
 ("Accepted at Workshop on Open-World Agents (OWA-NeurIPS 2024) : Synergizing Reasoning", "NeurIPS", "workshop", 2024),
 ("submitted to TIP", "TIP", "submitted", None),
 ("Submitted to IEEE Transactions on Neural Networks and Learning Systems", "TNNLS", "submitted", None),
 ("5 pages, submitted to ICASSP'27", "ICASSP", "submitted", 2027),
 ("4 pages, 1 figure, submitted to WWW'24, short-paper track", "WWW", "submitted", 2024),
 ("This work has been submitted to the Software Engineering in Practice (SEIP) track at ICSE 2027", "ICSE", "submitted", 2027),
 ("Preprint. Under Review", None, "submitted", None),
 ("Accepted into KDD 2024 workshop on Ethical AI", "KDD", "workshop", 2024),
 ("Accepted to the Aligning Reinforcement Learning Experimentalists and Theorists Workshop at NeurIPS 2025", "NeurIPS", "workshop", 2025),
 ("Accepted to the CVPR 2024 Responsible Generative AI (ReGenAI) Workshop", "CVPR", "workshop", 2024),
 ("ICLR 2026 Workshop on Logical Reasoning of Large Language Models", "ICLR", "workshop", 2026),
 ("Outstanding paper in the AI4Law Workshop at ICML 2026", "ICML", "workshop", 2026),
 ("Accepted at the 7th International Workshop on Advances in Mobile App Analysis held in conjunction with ASE 2024", "ASE", "workshop", 2024),
 ("Accepted by ACM SIGCOMM 2025 - 2nd Workshop on Networks for AI Computing (NAIC). 7 pages", "SIGCOMM", "workshop", 2025),
 ("Accepted at EMNLP 2026. 28 pages, 16 figures, 4 tables. Benchmark: https://basiralab.github.io/GNN-CB/", "EMNLP", "accepted", 2026),
 ("To appear in SIGGRAPH Asia 2025", "SIGGRAPH Asia", "accepted", 2025),
 ("EACL 2026", "EACL", "accepted", 2026),
 ("Accepted by BMVC2026", "BMVC", "accepted", 2026),
 ("Accepted to CHI 2026 Proceedings", "CHI", "accepted", 2026),
 ("23 pages, accepted by CVPR 24", "CVPR", "accepted", 2024),
 ("Appeared in ICLR'24", "ICLR", "accepted", 2024),
 ("Accepted by CVPR26 Findings", "CVPR", "accepted", 2026),
 ("ICML 2025 camera-ready version (accepted as an oral presentation). 16 pages", "ICML", "accepted", 2025),
 ("2024 IEEE Conference on Robotics and Automation (ICRA)", "ICRA", "accepted", 2024),
 ("Accepted at Datasets and Benchmarks Track, ACM Knowledge Discovery and Data Mining (KDD) 2026.", "KDD", "accepted", 2026),
 ("ICCV 2025 (Highlight), Project Page: https://zj-dong.github.io/MoGA/", "ICCV", "accepted", 2025),
 ("Accepted at NeurIPS 2025", "NeurIPS", "accepted", 2025),
 ("Accepted at the ICBINB: Where LLMs Need to Improve workshop at ICLR 2026. 12 pages", "ICLR", "workshop", 2026),
 ("Accepted at NeurIPS 2025 Workshop", "NeurIPS", "workshop", 2025),
 ("Accepted at the IEEE Information Theory Workshop 2024", None, "workshop", None),
 ("Accepted to be presented at IROS Workshop Touch2Action", "IROS", "workshop", None),
 ("Accepted at NeurIPS 2025", "NeurIPS", "accepted", 2025),
 ("Under review at ICLR 2026", "ICLR", "submitted", 2026),
 ("Workshop on Foundation Models at ICML 2025", "ICML", "workshop", 2025),
 ("Published in Nature", "Nature", "accepted", None),
 ("Accepted at Nature Machine Intelligence", "Nature Machine Intelligence", "accepted", None),
 ("Natural Science and Nature of things", None, None, None),
 ("IEEE Access", None, None, None),
 ("Published by IEEE Xplore", None, "accepted", None),
 ("Preprint", None, None, None),
]
EXTRA = [  # (comment, field, expected)
 ("12 pages, 4 figures. Code: https://github.com/x/y.", "pages", 12), ("12 pages, 4 figures. Code: https://github.com/x/y.", "code_url", "https://github.com/x/y"),
 ("This paper has been withdrawn", "withdrawn", True), ("withdrawn by the author(s)", "withdrawn", True),
 ("The code is available at \\href{https://github.com/ChangtiWu/SIEVE}{SIEVE}", "code_url", "https://github.com/ChangtiWu/SIEVE"),
 ("Project Page: https://fluxspace.github.io", "code_url", "https://fluxspace.github.io"),
 ("Project page at https://m-niemeyer.github.io/radsplat/ and presented at 3DV (Oral)", "venue", "3DV"),
 ("Link: https://doi.org/10.1029/2025MS005285", "code_url", None), ("Code: https://huggingface.co/a/b)", "code_url", "https://huggingface.co/a/b"),
 ("4-page paper, 3 pp.", "pages", 4), ("5 figures, 8 tables", "pages", None), ("withdrawal of the word withdrawn is fine", "withdrawn", False),
 ("After careful consideration, we have decided to withdraw our submission", "withdrawn", True),
 ("Accepted at SC'24", "venue", "SC"), ("Accepted at Science Advances", "venue", None), ("Accepted to ICML. 12 pages", "year", None),
 ("Accepted to ICML 12 pages", "year", None),
]

def selftest():
    n = 0
    for text, v, k, y in CASES:
        r = parse_comment(text)
        assert (r["venue"], r["kind"], r["year"]) == (v, k, y), (text, r)
        n += 1
    for text, f, e in EXTRA:
        assert parse_comment(text)[f] == e, (text, f, parse_comment(text))
        n += 1
    assert parse_comment(None) == dict(venue=None, kind=None, year=None, code_url=None, pages=None, withdrawn=False)
    assert parse_comment("") ["venue"] is None
    assert len(VENUES) >= 80
    assert n >= 40
    print("ok")

def stats():
    d = json.load(open("data/spikes/s1.json"))["sample_comments"]
    rs = [parse_comment(x["comment"]) for x in d]
    print("n", len(rs), "kinds", dict(Counter(r["kind"] for r in rs)))
    print("top venues", Counter(r["venue"] for r in rs if r["venue"]).most_common(12))
    print("venue found", sum(bool(r["venue"]) for r in rs), "year found", sum(bool(r["year"]) for r in rs),
          "pages", sum(bool(r["pages"]) for r in rs), "withdrawn", sum(r["withdrawn"] for r in rs))
    print("code share", round(sum(bool(r["code_url"]) for r in rs) / len(rs), 3))

if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else stats() if "--stats" in sys.argv else print(__doc__)
