"""Opt-in, read-only credential check. No inference, Actors, uploads or paid jobs.

Run inside the deployed container:
    python tests/integrations_readonly.py --run
Or, from its application working directory:
    docker exec -i <container> python - --run < tests/integrations_readonly.py

Only GET account/model metadata endpoints are permitted. Keys stay in headers and
memory. Neither account responses, keys, URLs, nor exceptions are printed.
Selected-model availability means listed by the API; it does not validate paid
inference, balance, or video capabilities. Null means not verified.

Official references (checked 2026-09-20):
https://platform.claude.com/docs/en/api/models/list
https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key
https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties
https://ai.google.dev/api/models
https://docs.apify.com/api/v2/users-me-get
"""
import concurrent.futures
import json
import os
from pathlib import Path
import sys
from urllib import error, request

ENDPOINTS = {
    "anthropic": "https://api.anthropic.com/v1/models?limit=1000",
    "openrouter_key": "https://openrouter.ai/api/v1/key",
    "openrouter_models": "https://openrouter.ai/api/v1/models",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
    "apify": "https://api.apify.com/v2/users/me",
}
LIMIT = 8 * 1024 * 1024


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get_metadata(endpoint, headers):
    """Endpoint is an internal enum, never a supplied URL. Only HTTPS GET."""
    try:
        req = request.Request(ENDPOINTS[endpoint], method="GET", headers=dict(headers, Accept="application/json"))
        with request.build_opener(NoRedirect()).open(req, timeout=20) as response:
            raw = response.read(LIMIT + 1)
            if len(raw) > LIMIT: return None, dict(reachable=True, request_succeeded=False, authenticated=None)
            data = json.loads(raw)
            if not isinstance(data, dict) or "error" in data:
                return None, dict(reachable=True, request_succeeded=False, authenticated=None)
            return data, dict(reachable=True, request_succeeded=True, authenticated=True)
    except error.HTTPError as exc:
        rejected = exc.code in (401, 403)
        exc.close()
        return None, dict(reachable=True, request_succeeded=False, authenticated=False if rejected else None)
    except Exception:
        return None, dict(reachable=False, request_succeeded=False, authenticated=None)


def verify(provider, key, selected_model):
    result = dict(configured=bool(key), checked=False, reachable=None, request_succeeded=None,
                  authenticated=None, models_checked=False, selected_model_available=None)
    if not key: return result
    result["checked"] = True
    if provider == "anthropic":
        data, state = get_metadata("anthropic", {"x-api-key": key, "anthropic-version": "2023-06-01"})
        result.update(state)
        if data is not None and isinstance(data.get("data"), list):
            ids = {m.get("id") for m in data["data"] if isinstance(m, dict)}
            result["models_checked"] = True
            result["selected_model_available"] = True if selected_model in ids else (None if data.get("has_more") else False)
    elif provider == "gemini":
        data, state = get_metadata("gemini", {"x-goog-api-key": key})
        result.update(state)
        if data is not None and isinstance(data.get("models"), list):
            ids = {str(m.get("name", "")).removeprefix("models/") for m in data["models"] if isinstance(m, dict)}
            result["models_checked"] = True
            result["selected_model_available"] = True if selected_model in ids else (None if data.get("nextPageToken") else False)
    elif provider == "openrouter":
        data, state = get_metadata("openrouter_key", {"Authorization": "Bearer " + key})
        result.update(state)
        if data is not None and isinstance(data.get("data"), dict):
            catalog, _ = get_metadata("openrouter_models", {"Authorization": "Bearer " + key})
            if catalog is not None and isinstance(catalog.get("data"), list):
                ids = {m.get("id") for m in catalog["data"] if isinstance(m, dict)}
                result["models_checked"] = True
                result["selected_model_available"] = "google/" + selected_model in ids
    elif provider == "apify":
        _, state = get_metadata("apify", {"Authorization": "Bearer " + key})
        result.update(state)
    return result


def load_configuration():
    # __file__ is <stdin> under docker exec -i python -, so also support cwd.
    roots = [Path.cwd(), Path.cwd() / "atlas-editor"]
    if globals().get("__file__") and not str(__file__).startswith("<"):
        roots.insert(0, Path(__file__).resolve().parents[1])
    for root in roots:
        if (root / "lib" / "chaves.py").is_file():
            sys.path.insert(0, str(root / "lib"))
            import chaves
            return chaves.ler()
    raise RuntimeError("configuration_unavailable")


def main():
    if "--run" not in sys.argv[1:]:
        print(json.dumps(dict(ok=True, skipped=True, opt_in_required=True)))
        return 0
    config = load_configuration()
    jobs = [(name, str(config.get(name) or ""), str(config.get("modelo" if name == "anthropic" else "modelo_gemini") or ""))
            for name in ("anthropic", "openrouter", "apify", "gemini")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        pending = {name: pool.submit(verify, name, key, model) for name, key, model in jobs}
        result = {name: task.result() for name, task in pending.items()}
    checked = [item for item in result.values() if item["configured"]]
    ok = bool(checked) and all(item["authenticated"] is True and item["selected_model_available"] is not False for item in checked)
    print(json.dumps(dict(ok=ok, read_only=True, inference_performed=False, integrations=result), ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    try: exit_code = main()
    except Exception:
        print(json.dumps(dict(ok=False, read_only=True, inference_performed=False, check_failed=True)))
        exit_code = 1
    sys.exit(exit_code)
