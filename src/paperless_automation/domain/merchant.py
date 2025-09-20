from typing import Dict, List, Optional, Tuple
import re
import unicodedata

from ..logging import get_logger

LOG = get_logger("merchant")


def _only_letters_and_spaces(s: str) -> str:
    kept: List[str] = []
    for ch in (s or ""):
        if ch.isalpha() or ch.isspace():
            kept.append(ch)
    out = "".join(kept)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _remove_legal_tokens(s: str) -> str:
    if not s:
        return s
    tokens = [
        "gmbh co kg",
        "gmbh und co kg",
        "and co",
        "co kg",
        "gesellschaft mit beschraenkter haftung",
        "gesellschaft mit beschränkter haftung",
        "aktiengesellschaft",
        "kommanditgesellschaft",
        "offene handelsgesellschaft",
        "eingetragener kaufmann",
        "gmbh",
        "ag",
        "kg",
        "ug",
        "se",
        "ek",
        "ohg",
        "spa",
    ]
    out = f" {s} "
    for t in tokens:
        pat = f" {t} "
        if pat in out:
            out = out.replace(pat, " ")
    out = re.sub(r"\s+", " ", out).strip()
    return out


def normalize_korrespondent(name: str) -> str:
    raw_input = name or ""
    raw = raw_input.strip()
    m = re.match(r'^\s*"?(merchant|korrespondent)"?\s*:\s*"?(.+?)"?\s*,?\s*$', raw, flags=re.IGNORECASE)
    if m:
        raw = m.group(2).strip()

    raw = raw.replace("\u00A0", " ")
    nfc = unicodedata.normalize("NFC", raw)
    lowered = nfc.lower()
    letters_spaces = _only_letters_and_spaces(lowered)
    cleaned = _remove_legal_tokens(letters_spaces)

    try:
        LOG.info(
            "raw=\"{}\" | nfc=\"{}\" | lowered=\"{}\" | letters_only=\"{}\" | cleaned=\"{}\"".format(
                raw_input, nfc, lowered, letters_spaces, cleaned
            )
        )
    except Exception:
        pass

    return cleaned


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    la, lb = len(a), len(b)
    if la > lb:
        a, b = b, a
        la, lb = lb, la
    prev = list(range(la + 1))
    for j in range(1, lb + 1):
        cur = [j] + [0] * la
        bj = b[j - 1]
        for i in range(1, la + 1):
            cost = 0 if a[i - 1] == bj else 1
            cur[i] = min(cur[i - 1] + 1, prev[i] + 1, prev[i - 1] + cost)
        prev = cur
    return prev[la]


def _best_substring_key(norm_text: str, keys: List[str]) -> Optional[str]:
    if not norm_text or not keys:
        return None
    best: Tuple[int, Optional[str]] = (0, None)
    for k in keys:
        if not k:
            continue
        if len(k) < 3:
            ok = norm_text.startswith(k)
        else:
            ok = (k in norm_text)
        if ok:
            ln = len(k)
            if ln > best[0]:
                best = (ln, k)
    return best[1]



def resolve_tag_and_key(tag_map: Dict[str, str], extracted_name: str) -> Tuple[str, Optional[str]]:
    if not isinstance(tag_map, dict) or not tag_map:
        return ("NO TAG FOUND", None)
    norm_name = normalize_korrespondent(extracted_name)
    norm_index: Dict[str, str] = {}
    for k, v in tag_map.items():
        nk = normalize_korrespondent(str(k))
        if nk:
            norm_index[nk] = str(v)
    if norm_name in norm_index:
        return (norm_index[norm_name], norm_name)
    best_key = _best_substring_key(norm_name, list(norm_index.keys()))
    if best_key:
        return (norm_index[best_key], best_key)
    best_key = None
    best_dist = 10**9
    name_len = len(norm_name)
    for k in norm_index.keys():
        if len(k) < 3:
            continue
        d = _levenshtein(norm_name, k)
        if d < best_dist:
            best_dist = d
            best_key = k
    if best_key is not None:
        thr = max(1, round(0.2 * max(len(best_key), name_len)))
        if best_dist <= thr:
            return (norm_index[best_key], best_key)
    return ("NO TAG FOUND", None)
