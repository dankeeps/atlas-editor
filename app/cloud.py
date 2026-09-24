"""Autenticação e upload para a instalação web; somente biblioteca padrão."""
import base64
import errno
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import threading
import time
from http.cookies import SimpleCookie, CookieError
from urllib.parse import urlsplit

COOKIE = "atlas_session"
TTL = 12 * 60 * 60
MAX_UPLOAD = int(os.environ.get("ESTUDIO_MAX_UPLOAD_BYTES", str(20 * 1024**3)))
HASH_PATTERN = re.compile(r"scrypt\$32768\$8\$1\$([a-f0-9]{32})\$([a-f0-9]{64})")
_attempts = {}
_lock = threading.Lock()
_scrypt_slots = threading.BoundedSemaphore(2)


def users():
    result = []
    path = os.environ.get("ESTUDIO_USERS_FILE")
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                result = json.load(f)
        except (OSError, ValueError):
            result = []
    if not isinstance(result, list):
        result = []
    migrated_emails = {str(u.get("email", "")).lower() for u in result if isinstance(u, dict)}
    result = [u for u in result if isinstance(u, dict) and u.get("active", True) and u.get("email")
              and HASH_PATTERN.fullmatch(str(u.get("password_hash", "")))]
    email = os.environ.get("EDITOR_IA_ADMIN_EMAIL", "").strip().lower()
    password_hash = os.environ.get("EDITOR_IA_ADMIN_PASSWORD_HASH", "")
    # A conta migrada é a fonte de verdade caso a senha tenha sido alterada.
    if email and HASH_PATTERN.fullmatch(password_hash) and email not in migrated_emails:
        result.append(dict(id="admin", email=email, name="Administrador", role="admin", password_hash=password_hash))
    return result


def enabled():
    return bool(os.environ.get("EDITOR_IA_DEPLOYMENT") == "vps" or os.environ.get("EDITOR_IA_ADMIN_EMAIL")
                or os.environ.get("ESTUDIO_USERS_FILE"))


def configuration(host):
    if not enabled():
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise RuntimeError("Configure o login antes de expor o Atlas Editor à rede.")
        return
    origin = os.environ.get("EDITOR_IA_PUBLIC_URL", "").rstrip("/")
    parsed = urlsplit(origin)
    if (parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.path
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or (parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))):
        raise RuntimeError("EDITOR_IA_PUBLIC_URL deve ser a origem HTTPS pública.")
    if len(os.environ.get("EDITOR_IA_SESSION_SECRET", "").encode()) < 32 or not users():
        raise RuntimeError("Configure o segredo de sessão e pelo menos um usuário do Atlas Editor.")
    if not hasattr(hashlib, "scrypt"):
        raise RuntimeError("Use Python com suporte OpenSSL/scrypt para o login.")


def safe_origin(handler, port):
    host = handler.headers.get("Host", "").lower()
    origin = handler.headers.get("Origin", "")
    configured = os.environ.get("EDITOR_IA_PUBLIC_URL", "").rstrip("/")
    local = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    allowed_hosts = local | ({urlsplit(configured).netloc.lower()} if configured else set())
    if host not in allowed_hosts or handler.headers.get("Sec-Fetch-Site") == "cross-site":
        return False
    if enabled():
        if urlsplit(configured).hostname in ("localhost", "127.0.0.1", "::1"):
            return origin in {"http://" + h for h in local}
        return bool(origin) and origin == configured
    return not origin or origin in {"http://" + h for h in local}


def verify_password(password, stored):
    match = HASH_PATTERN.fullmatch(stored or "")
    if not isinstance(password, str) or len(password.encode()) > 1024:
        return False
    salt, expected = match.groups() if match else ("0" * 32, "0" * 64)
    with _scrypt_slots:
        actual = hashlib.scrypt(password.encode(), salt=salt.encode(), n=32768, r=8, p=1,
                                dklen=32, maxmem=128 * 1024 * 1024)
    return bool(match) and hmac.compare_digest(actual.hex(), expected)


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _signature(value):
    return _b64(hmac.new(os.environ["EDITOR_IA_SESSION_SECRET"].encode(), value.encode(), hashlib.sha256).digest())


def token(user):
    payload = dict(email=user["email"].lower(), exp=int(time.time()) + TTL, nonce=secrets.token_hex(16),
                   credential=hashlib.sha256(user["password_hash"].encode()).hexdigest())
    encoded = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return encoded + "." + _signature(encoded)


def session(handler):
    if not enabled():
        return dict(email="local", name="Local", role="admin")
    try:
        cookie = SimpleCookie(handler.headers.get("Cookie", ""))
        raw = cookie[COOKIE].value
        if len(raw) > 2048:
            return None
        encoded, signature = raw.split(".")
        if not hmac.compare_digest(_signature(encoded), signature):
            return None
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if not time.time() < payload["exp"] <= time.time() + TTL + 60:
            return None
        return next((u for u in users() if u["email"].lower() == payload["email"]
                     and hmac.compare_digest(hashlib.sha256(u["password_hash"].encode()).hexdigest(), payload["credential"])), None)
    except (ValueError, KeyError, TypeError, AttributeError, CookieError):
        return None


def login(email, password, address):
    now = time.time()
    with _lock:
        recent = [t for t in _attempts.get(address, []) if t > now - 60]
        if len(recent) >= 15:
            raise ValueError("Muitas tentativas. Aguarde um minuto e tente novamente.")
        _attempts[address] = recent + [now]
        for key in list(_attempts):
            if not _attempts[key] or _attempts[key][-1] < now - 60:
                del _attempts[key]
    email = str(email or "").strip().lower()
    user = next((u for u in users() if u["email"].lower() == email), None)
    if not verify_password(password, user["password_hash"] if user else ""):
        return None
    with _lock:
        _attempts.pop(address, None)
    return user


def cookie(value="", age=TTL):
    secure = os.environ.get("EDITOR_IA_PUBLIC_URL", "").startswith("https://")
    return f"{COOKIE}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={age}" + ("; Secure" if secure else "")


class UploadError(ValueError):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


def body_size(handler, limit=None):
    if limit is None: limit = MAX_UPLOAD
    if handler.headers.get("Transfer-Encoding"):
        raise ValueError("Envie o arquivo com Content-Length.")
    raw = handler.headers.get("Content-Length", "0")
    if not re.fullmatch(r"\d{1,12}", raw):
        raise ValueError("Tamanho do envio inválido.")
    size = int(raw)
    if size > limit:
        unit, divisor = ("GiB", 1024**3) if limit >= 1024**3 else ("MiB", 1024**2)
        raise UploadError(f"O arquivo excede o limite de {limit / divisor:g} {unit} por envio.", 413)
    return size


def receive(handler, path):
    """Stream de 1 MiB e troca atômica: nunca carrega o vídeo inteiro em memória."""
    remaining = body_size(handler)
    if not remaining:
        raise ValueError("O arquivo está vazio.")
    temporary = path + "." + secrets.token_hex(8) + ".part"
    try:
        # Folga mínima para metadados; outros processos podem consumir espaço
        # depois desta consulta, por isso ENOSPC também é tratado durante a escrita.
        if shutil.disk_usage(os.path.dirname(os.path.abspath(path))).free < remaining + 64 * 1024**2:
            raise UploadError("Não há espaço suficiente na VPS para receber este arquivo.", 507)
        with open(temporary, "xb") as output:
            while remaining:
                block = handler.rfile.read(min(1024 * 1024, remaining))
                if not block:
                    raise ValueError("O envio foi interrompido. Tente novamente.")
                output.write(block)
                remaining -= len(block)
        os.replace(temporary, path)
    except OSError as error:
        if error.errno in (errno.ENOSPC, errno.EDQUOT):
            raise UploadError("O espaço da VPS acabou durante o envio. O arquivo incompleto foi descartado.", 507) from None
        raise
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


LOGIN_HTML = '''<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Entrar · Atlas Editor</title><style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#101013;color:#eee;font:15px system-ui}main{width:min(420px,calc(100% - 40px));padding:38px;background:#19191e;border:1px solid #303038;border-radius:18px}h1{font-size:27px;margin:0 0 10px}p{color:#a8a8b2;line-height:1.5}label{display:block;margin:20px 0 8px}input,button{width:100%;padding:13px;border-radius:9px;font:inherit}input{background:#101013;border:1px solid #42424c;color:white}button{background:#a5f078;color:#12240c;border:0;font-weight:650;cursor:pointer;margin-top:25px}#erro{color:#ffafa8;min-height:22px;font-size:13px}button:disabled{opacity:.5}</style><main><h1>Atlas Editor</h1><p>Entre para acessar seus projetos e arquivos.</p><form id="login"><label for="email">E-mail</label><input id="email" type="email" autocomplete="username" required autofocus><label for="senha">Senha</label><input id="senha" type="password" autocomplete="current-password" required><button>Entrar</button><p id="erro" role="alert"></p></form></main><script>
document.querySelector('form').onsubmit=async e=>{e.preventDefault();const b=document.querySelector('button'),err=document.querySelector('#erro');b.disabled=true;err.textContent='';try{const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:document.querySelector('#email').value,password:document.querySelector('#senha').value})});const d=await r.json();if(!r.ok)throw Error(d.erro||'Não foi possível entrar.');location.replace('/');}catch(e){err.textContent=e.message;}finally{b.disabled=false;}};
</script></html>'''
