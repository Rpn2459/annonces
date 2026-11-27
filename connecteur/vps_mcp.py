"""Connecteur MCP « VPS Guy Hoquet » (version Docker) : quelques actions précises, aucune commande libre.

Tourne dans un conteneur qui a accès au socket Docker de l'hôte. Actions exposées à Claude :
  - statut_application        : état de la page du générateur dans le conteneur du site (lecture seule)
  - mettre_a_jour_application : télécharge la dernière version depuis GitHub, vérifie, sauvegarde, remplace
  - redemarrer_service        : redémarre UN conteneur de la liste autorisée
  - lire_journaux             : dernières lignes de log d'UN conteneur de la liste autorisée

Le jeton secret est généré au premier démarrage dans /data/token (jamais affiché dans les logs).
"""
import hmac
import os
import secrets
from pathlib import Path

import docker
import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

TARGET = os.environ.get("TARGET_CONTAINER", "generateur-annonce-web-1")
WEB_DIR = os.environ.get("WEB_DIR", "/usr/share/nginx/html")
SOURCE_URL = os.environ.get("SOURCE_URL", "")
SERVICES = [s.strip() for s in os.environ.get("ALLOWED_SERVICES", TARGET).split(",") if s.strip()]
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8765"))
TOKEN_FILE = Path(os.environ.get("TOKEN_FILE", "/data/token"))


def load_token():
    t = os.environ.get("MCP_TOKEN", "").strip()
    if t:
        return t
    if not TOKEN_FILE.exists():
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(secrets.token_urlsafe(40))
        os.chmod(TOKEN_FILE, 0o600)
    return TOKEN_FILE.read_text().strip()


TOKEN = load_token()
mcp = FastMCP("vps-guy-hoquet", host=HOST, port=PORT)


def _client():
    return docker.from_env()


def _check_service(name):
    if name not in SERVICES:
        raise ValueError(f"Service non autorisé : {name!r}. Autorisés : {SERVICES}")


def _exec(script, env=None):
    """Exécute un script FIXE (défini dans ce fichier) dans le conteneur du site."""
    c = _client().containers.get(TARGET)
    code, out = c.exec_run(["sh", "-c", script], environment=env or {}, workdir=WEB_DIR)
    return code, out.decode(errors="replace").strip()[-3000:]


@mcp.tool()
def statut_application() -> str:
    """Montre l'état de la page du générateur sur le VPS (fichiers, taille, présence de la correction)."""
    code, out = _exec(
        'ls -la index.html index.html.bak 2>&1; '
        'echo "Somme de contrôle : $(md5sum index.html | cut -d" " -f1)"; '
        'echo "Correction première image présente : $(grep -c \'Cover frame\' index.html) occurrence(s)"'
    )
    return f"Conteneur {TARGET} (code {code}) :\n{out}"


@mcp.tool()
def mettre_a_jour_application() -> str:
    """Met à jour la page du générateur depuis GitHub. Vérifie le fichier, sauvegarde l'ancienne version (index.html.bak) puis remplace."""
    if not SOURCE_URL:
        return "SOURCE_URL non configurée."
    script = (
        'set -e; '
        'curl -fsSL -o /tmp/new.html "$SOURCE_URL"; '
        '[ "$(wc -c < /tmp/new.html)" -gt 100000 ] || { echo "Fichier trop petit, rien modifié"; exit 1; }; '
        'grep -q "video-btn" /tmp/new.html || { echo "Fichier inattendu, rien modifié"; exit 1; }; '
        'cp index.html index.html.bak; cp /tmp/new.html index.html; chmod 644 index.html; rm /tmp/new.html; '
        'echo "Mise à jour effectuée. Ancienne version : index.html.bak"; ls -la index.html'
    )
    code, out = _exec(script, {"SOURCE_URL": SOURCE_URL})
    return f"(code {code})\n{out}"


@mcp.tool()
def redemarrer_service(nom: str) -> str:
    """Redémarre un conteneur de la liste autorisée."""
    _check_service(nom)
    _client().containers.get(nom).restart(timeout=20)
    return f"{nom} redémarré."


@mcp.tool()
def lire_journaux(nom: str, lignes: int = 100) -> str:
    """Lit les dernières lignes de journal d'un conteneur autorisé (maximum 300 lignes)."""
    _check_service(nom)
    n = max(1, min(int(lignes), 300))
    out = _client().containers.get(nom).logs(tail=n).decode(errors="replace")
    return out[-6000:] or "(aucune sortie)"


class TokenAuth:
    """Refuse toute requête sans « Authorization: Bearer <jeton> »."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            ok = False
            # Chemins d'identification OAuth : répondre 404 (et non 401) pour que le client
            # comprenne que ce serveur n'utilise pas OAuth et se rabatte sur l'adresse secrète.
            p0 = scope.get("path", "")
            if "/.well-known" in p0 or p0.rsplit("/", 1)[-1] in ("register", "authorize", "token"):
                await JSONResponse({"erreur": "introuvable"}, status_code=404)(scope, receive, send)
                return
            h = dict(scope["headers"]).get(b"authorization", b"").decode()
            if hmac.compare_digest(h, f"Bearer {TOKEN}"):
                ok = True
            else:
                # Variante « adresse secrète » : https://hôte/<jeton>/mcp (pour les clients sans en-tête personnalisé)
                p = scope.get("path", "")
                seg, _, rest = p[1:].partition("/")
                if seg and hmac.compare_digest(seg, TOKEN):
                    ok = True
                    newp = "/" + rest
                    scope = dict(scope, path=newp, raw_path=newp.encode())
            if not ok:
                await JSONResponse({"erreur": "non autorisé"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


if __name__ == "__main__":
    # access_log désactivé : l'adresse secrète ne doit jamais apparaître dans les journaux
    uvicorn.run(TokenAuth(mcp.streamable_http_app()), host=HOST, port=PORT, access_log=False)
