import os
from typing import Dict, Any, List, Optional, Tuple
import requests

from ..logging import get_logger


class PaperlessClient:
    """Thin client for Paperless-ngx API with session, timeouts, and logging.

    Only implements the subset we use: documents, tasks, correspondents,
    tags, and document types.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: int = 30,
        verify_tls: bool = True,
    ) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = int(timeout)
        self.verify = bool(verify_tls)
        self.log = get_logger("paperless-client")
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Token {token}",
            "Accept": "application/json",
        })

    # ---------- helpers ----------
    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def _json(self, r: requests.Response) -> Any:
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return None

    # ---------- documents ----------
    def post_document(
        self,
        *,
        file_path: str,
        title: Optional[str] = None,
        created: Optional[str] = None,
        correspondent_id: Optional[int] = None,
        tag_ids: Optional[List[int]] = None,
        document_type_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        url = self._url("/api/documents/post_document/")
        mime = _guess_mime(file_path)
        files = {"document": (os.path.basename(file_path), open(file_path, "rb"), mime)}
        # Use a list of tuples to allow repeated 'tags' fields
        data: List[Tuple[str, str]] = []
        if title:
            data.append(("title", str(title)))
        if created:
            data.append(("created", str(created)))
        if isinstance(correspondent_id, int):
            data.append(("correspondent", str(correspondent_id)))
        if isinstance(document_type_id, int):
            data.append(("document_type", str(document_type_id)))
        if isinstance(tag_ids, list) and tag_ids:
            for tid in tag_ids:
                data.append(("tags", str(int(tid))))

        self.log.info(f"POST document: title={title!r}, created={created}, file={file_path}")
        try:
            with files["document"][1] as fh:
                files = {"document": (files["document"][0], fh, files["document"][2])}
                r = self.s.post(url, files=files, data=data, timeout=self.timeout, verify=self.verify)
            body = self._json(r)
            return {"status_code": r.status_code, "json": body}
        except Exception as e:
            self.log.error(f"POST document failed: {e}")
            raise

    def patch_document(self, doc_id: int, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = self._url(f"/api/documents/{int(doc_id)}/")
        try:
            r = self.s.patch(url, json=payload, timeout=self.timeout, verify=self.verify)
            return self._json(r)
        except Exception as e:
            self.log.error(f"PATCH document {doc_id} failed: {e}")
            try:
                preview = r.text[:500] if 'r' in locals() and hasattr(r, 'text') else ""
                self.log.error(f"PATCH response preview: {preview}")
            except Exception:
                pass
            return None

    def find_document_by_title(self, title: str) -> Optional[int]:
        url = self._url(f"/api/documents/?title__iexact={requests.utils.quote(title)}&ordering=-id&page_size=1")
        try:
            r = self.s.get(url, timeout=self.timeout, verify=self.verify)
            data = self._json(r) or {}
            results = data.get("results") if isinstance(data, dict) else None
            if results and isinstance(results, list) and isinstance(results[0], dict):
                did = results[0].get("id")
                return int(did) if isinstance(did, int) else None
        except Exception as e:
            self.log.warning(f"find_document_by_title failed: {e}")
        return None

    # ---------- tasks ----------
    def get_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        url = self._url(f"/api/tasks/{int(task_id)}/")
        try:
            r = self.s.get(url, timeout=self.timeout, verify=self.verify)
            return self._json(r)
        except Exception as e:
            self.log.warning(f"get_task {task_id} failed: {e}")
            return None

    # ---------- documents (read-only helpers) ----------
    def get_document(self, doc_id: int) -> Optional[Dict[str, Any]]:
        url = self._url(f"/api/documents/{int(doc_id)}/")
        try:
            r = self.s.get(url, timeout=self.timeout, verify=self.verify)
            return self._json(r)
        except Exception as e:
            self.log.warning(f"get_document {doc_id} failed: {e}")
            return None

    # ---------- resources (by name) ----------
    def _get_first_by_name(self, resource: str, name: str) -> Optional[Dict[str, Any]]:
        for param in (f"name__iexact={requests.utils.quote(name)}", f"name__icontains={requests.utils.quote(name)}"):
            url = self._url(f"/api/{resource}/?{param}&page_size=1")
            try:
                r = self.s.get(url, timeout=self.timeout, verify=self.verify)
                data = self._json(r) or {}
                results = data.get("results") if isinstance(data, dict) else None
                if results:
                    return results[0]
            except Exception:
                continue
        return None

    def _create_resource(self, resource: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = self._url(f"/api/{resource}/")
        try:
            r = self.s.post(url, json=payload, timeout=self.timeout, verify=self.verify)
            return self._json(r)
        except Exception as e:
            self.log.error(f"create {resource} failed: {e}")
            return None

    def ensure_correspondent(self, name: str) -> Optional[int]:
        if not name:
            return None
        found = self._get_first_by_name("correspondents", name)
        if found:
            did = found.get("id")
            return int(did) if isinstance(did, int) else None
        created = self._create_resource("correspondents", {"name": name})
        return int(created["id"]) if created and isinstance(created.get("id"), int) else None

    def ensure_document_type(self, name: str) -> Optional[int]:
        if not name:
            return None
        found = self._get_first_by_name("document_types", name)
        if found:
            did = found.get("id")
            return int(did) if isinstance(did, int) else None
        created = self._create_resource("document_types", {"name": name})
        return int(created["id"]) if created and isinstance(created.get("id"), int) else None

    def ensure_tags(self, names: List[str]) -> List[int]:
        ids: List[int] = []
        for name in names:
            if not name:
                continue
            found = self._get_first_by_name("tags", name)
            if found and isinstance(found.get("id"), int):
                ids.append(int(found["id"]))
                continue
            created = self._create_resource("tags", {"name": name})
            if created and isinstance(created.get("id"), int):
                ids.append(int(created["id"]))
        return ids


def _guess_mime(path: str) -> str:
    import mimetypes
    mt, _ = mimetypes.guess_type(path)
    return mt or "application/octet-stream"
