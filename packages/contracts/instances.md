# Contract: instance-registry en arr-client (Python, binnen core)

Eigenaar: werkstroom B1. Andere modules gebruiken ALLEEN deze interface.

```python
# mediaguard/instances/registry.py
@dataclass(frozen=True)
class Instance:
    id: str                 # 'sonarr-series' | 'sonarr-anime' | 'radarr-films' | 'radarr-animefilms' | eigen
    app: str                # 'sonarr' | 'radarr'
    media_type: str         # 'tv' | 'anime' | 'movie' | 'anime_movie'
    name: str               # weergavenaam NL, bv. 'Sonarr Series'
    url: str                # interne basis-URL, bv. http://mediaguard-sonarr-series:8989
    public_url: str | None  # stabiele URL voor Seerr
    status: str             # 'healthy' | 'degraded' | 'offline' | 'unknown'
    suspended: bool
    shadow_mode: bool
    archived: bool
    hook_fail_policy: str   # 'open' | 'closed'

def list_instances(ctx, include_archived=False) -> list[Instance]
def get_instance(ctx, instance_id: str) -> Instance | None
def arr_client(ctx, instance_id: str) -> ArrClient     # async httpx-client met API-key uit secrets

# mediaguard/arr/client.py  (Sonarr v3-API & Radarr v3-API)
class ArrClient:
    instance_id: str; app: str
    async def get(self, path, **params) -> Any
    async def post(self, path, json) -> Any
    async def put(self, path, json) -> Any
    async def delete(self, path, **params) -> Any
    async def system_status(self) -> dict
    async def command(self, name: str, **body) -> dict   # bv. EpisodeSearch, MoviesSearch, RssSync
```
Tabel `instances` (migratie 020) heeft minimaal de velden hierboven. Alle tabellen met
media-items gebruiken de sleutel `(instance_id, provider_id)` (R2.4).
