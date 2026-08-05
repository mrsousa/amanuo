"""Cliente fino do Microsoft Graph.

Autenticação por *device code flow*: você roda o script, ele mostra um
código e um link; você abre no navegador, faz login e autoriza. O token
fica em cache local (token_cache.bin) pra não precisar logar toda vez.
"""

import json
import os
import sys
import msal
import requests

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.ReadWrite"]
CACHE_FILE = "token_cache.bin"


class GraphClient:
    def __init__(self, config: dict):
        self.config = config
        self._token = None

    # ---------- autenticação ----------
    def _load_cache(self) -> msal.SerializableTokenCache:
        cache = msal.SerializableTokenCache()
        if os.path.exists(CACHE_FILE):
            cache.deserialize(open(CACHE_FILE, "r").read())
        return cache

    def _save_cache(self, cache: msal.SerializableTokenCache):
        if cache.has_state_changed:
            open(CACHE_FILE, "w").write(cache.serialize())

    def _get_token(self) -> str:
        if self._token:
            return self._token

        cache = self._load_cache()
        app = msal.PublicClientApplication(
            self.config["client_id"],
            authority=f"https://login.microsoftonline.com/{self.config['tenant_id']}",
            token_cache=cache,
        )

        result = None
        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(SCOPES, account=accounts[0])

        if not result:
            flow = app.initiate_device_flow(scopes=SCOPES)
            if "user_code" not in flow:
                raise RuntimeError(f"Falha no device flow: {json.dumps(flow)}")
            print("\n" + "=" * 60)
            print(flow["message"])  # instrui a abrir o link e digitar o código
            print("=" * 60 + "\n")
            result = app.acquire_token_by_device_flow(flow)

        self._save_cache(cache)

        if "access_token" not in result:
            raise RuntimeError(
                f"Erro ao obter token: {result.get('error_description', result)}"
            )
        self._token = result["access_token"]
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._get_token()}"}

    # ---------- pastas ----------
    def list_folders(self) -> list[dict]:
        """Retorna todas as pastas de email (id + nome)."""
        folders, url = [], f"{GRAPH}/me/mailFolders?$top=100"
        while url:
            r = requests.get(url, headers=self._headers())
            r.raise_for_status()
            data = r.json()
            folders.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
        return folders

    # ---------- mensagens ----------
    def get_messages(self, folder_id: str, top: int = 25) -> list[dict]:
        """Mensagens de uma pasta (mais recentes primeiro)."""
        url = (
            f"{GRAPH}/me/mailFolders/{folder_id}/messages"
            f"?$top={top}&$select=id,subject,from,receivedDateTime,bodyPreview"
            f"&$orderby=receivedDateTime desc"
        )
        r = requests.get(url, headers=self._headers())
        r.raise_for_status()
        return r.json().get("value", [])

    def get_message_body(self, message_id: str) -> str:
        url = f"{GRAPH}/me/messages/{message_id}?$select=body"
        r = requests.get(url, headers=self._headers())
        r.raise_for_status()
        return r.json().get("body", {}).get("content", "")

    def move_message(self, message_id: str, dest_folder_id: str) -> None:
        url = f"{GRAPH}/me/messages/{message_id}/move"
        r = requests.post(
            url, headers=self._headers(), json={"destinationId": dest_folder_id}
        )
        r.raise_for_status()


def load_config(path: str = "config.json") -> dict:
    if not os.path.exists(path):
        sys.exit("config.json não encontrado. Copie config.example.json e preencha.")
    return json.load(open(path))
