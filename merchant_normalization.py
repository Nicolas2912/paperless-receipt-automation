from typing import Dict, List, Optional, Tuple
import re


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
        "gmbh co kg", "gmbh und co kg", "and co", "co kg",
        "gesellschaft mit beschraenkter haftung", "aktiengesellschaft",
        "kommanditgesellschaft", "offene handelsgesellschaft", "eingetragener kaufmann",
        "gmbh", "ag", "kg", "ug", "se", "ek", "ohg", "spa",
    ]
    out = f" {s} "
    for t in tokens:
        pat = f" {t} "
        if pat in out:
            out = out.replace(pat, " ")
    out = re.sub(r"\s+", " ", out).strip()
    return out


def normalize_korrespondent(name: str) -> str:
    raw = (name or "").strip()
    m = re.match(r'^\s*"?(merchant|korrespondent)"?\s*:\s*"?(.+?)"?\s*,?\s*$', raw, flags=re.IGNORECASE)
    if m:
        raw = m.group(2).strip()
    lowered = raw.lower()
    letters_spaces = _only_letters_and_spaces(lowered)
    cleaned = _remove_legal_tokens(letters_spaces)
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
    """Return the best key contained as substring in norm_text.

    Rules:
    - Prefer the longest matching key.
    - For very short keys (<3), only accept if they match at the start of
      the normalized text (prefix match), to avoid accidental hits.
    - For len>=3, accept anywhere as substring.
    """
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


def choose_tag_for_korrespondent(tag_map: Dict[str, str], extracted_name: str) -> str:
    """Return a tag using exact, substring, then Levenshtein fallback.

    Threshold: max(1, round(0.2 * max(len(key), len(name))))
    Keys shorter than 3 are excluded from fuzzy to avoid noise.
    If nothing matches, return "NO TAG FOUND".
    """
    if not isinstance(tag_map, dict) or not tag_map:
        return "NO TAG FOUND"

    norm_name = normalize_korrespondent(extracted_name)
    norm_index: Dict[str, str] = {}
    for k, v in tag_map.items():
        nk = normalize_korrespondent(str(k))
        if nk:
            norm_index[nk] = str(v)

    # Exact
    if norm_name in norm_index:
        return norm_index[norm_name]

    # Substring (prefer longest; allows prefix for very short keys like 'dm')
    best_key = _best_substring_key(norm_name, list(norm_index.keys()))
    if best_key:
        return norm_index[best_key]

    # Fuzzy
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
            return norm_index[best_key]

    return "NO TAG FOUND"


def resolve_tag_and_key(tag_map: Dict[str, str], extracted_name: str) -> Tuple[str, Optional[str]]:
    """Return (tag_name, matched_key) using the same strategy as above.
    matched_key is the normalized key from tag_map if a match was found,
    else None. Tag is "NO TAG FOUND" if nothing matched.
    """
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
