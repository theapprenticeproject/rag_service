import requests

CAPACITY_MARKERS = (
    "no instances currently available",
    "no available gpu",
    "insufficient capacity",
)


class RunPodError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def is_capacity_error(exc):
    if not isinstance(exc, RunPodError):
        return False
    text = str(exc).lower()
    return any(marker in text for marker in CAPACITY_MARKERS)


class RunPodClient:
    def __init__(self, base_url, api_key, timeout_seconds=20):
        self.base = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        )

    def _safe(self, text):
        text = str(text or "")
        if self.api_key:
            text = text.replace(self.api_key, "***")
        return text[:300]

    def _request(self, method, path, **kwargs):
        try:
            response = self.session.request(method, f"{self.base}{path}", timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise RunPodError(f"request_error {self._safe(exc)}") from exc
        return response

    def _fail(self, response):
        raise RunPodError(f"http_{response.status_code} {self._safe(response.text)}", response.status_code)

    def create_pod(self, payload):
        response = self._request("POST", "/pods", json=payload)
        if response.status_code >= 400:
            self._fail(response)
        return response.json()

    def get_pod(self, pod_id):
        response = self._request("GET", f"/pods/{pod_id}")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            self._fail(response)
        return response.json()

    def find_by_name(self, name):
        response = self._request("GET", "/pods")
        if response.status_code >= 400:
            self._fail(response)
        data = response.json()
        pods = data if isinstance(data, list) else (data or {}).get("pods", [])
        for pod in pods:
            if isinstance(pod, dict) and pod.get("name") == name:
                return pod
        return None

    def delete_pod(self, pod_id):
        response = self._request("DELETE", f"/pods/{pod_id}")
        return response.status_code < 300 or response.status_code == 404

    def stop_pod(self, pod_id):
        response = self._request("POST", f"/pods/{pod_id}/stop")
        return response.status_code < 300

    def ping(self):
        response = self._request("GET", "/pods")
        if response.status_code >= 400:
            raise RunPodError(f"http_{response.status_code}", response.status_code)
        return True


def build_create_payload(settings, run_name, env):
    payload = {
        "name": run_name,
        "imageName": settings.image_name,
        "gpuTypeIds": list(settings.gpu_type_ids or []),
        "gpuCount": settings.gpu_count,
        "containerDiskInGb": settings.container_disk_gb,
        "env": dict(env or {}),
    }
    return payload