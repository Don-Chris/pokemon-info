from __future__ import annotations

import json
import logging
import os
import secrets
import string
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from requests import HTTPError

from pokemon_api import PokeAPI


logger = logging.getLogger("pokeapi.soullink")


def _now_ts() -> float:
    return time.time()


def _normalize_location_area_key(value: str) -> str:
    return value.strip().lower().replace(" ", "-")


def _format_location_area(value: str) -> str:
    if not value:
        return ""
    if value.lower() == "starter":
        return "Starter"
    return value.replace("-", " ").title()


def _sanitize_players(value: int) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 2
    return max(2, min(4, count))


def _build_status_options(players: int) -> List[str]:
    entries: List[str] = []
    for idx in range(1, players + 1):
        entries.append(f"S{idx} nicht gefangen")
        entries.append(f"unter S{idx} gestorben")
    entries.extend(["im PC", "aktiv"])
    return entries


def _status_css_class(status: str) -> str:
    normalized = status.strip().lower()
    if normalized == "aktiv":
        return "status-active"
    if normalized == "im pc":
        return "status-pc"
    return "status-muted"


def create_soullink_router(
    *,
    base_api: PokeAPI,
    get_api: Callable[[str, str, str], PokeAPI],
    resolve_identifier: Callable[[str, str, str, Optional[PokeAPI]], str],
    get_version_groups_for_generation: Callable[[str], List[str]],
    supported_languages: List[str],
    templates: Jinja2Templates,
    defaults: Dict[str, str],
    store_path: str,
    code_length: int,
) -> APIRouter:
    router = APIRouter()

    retention_days = 90
    retention_seconds = retention_days * 24 * 60 * 60
    soullink_cache: Optional[Dict[str, Dict[str, Any]]] = None
    soullink_cache_maintenance_done = False
    soullink_lock = threading.RLock()
    location_area_cache: List[Dict[str, str]] = []
    location_area_name_map: Dict[str, str] = {}
    location_area_meta_map: Dict[str, Dict[str, Any]] = {}
    location_area_display_cache: Dict[Tuple[str, str], str] = {}
    location_scope_states: Dict[Tuple[str, str], Dict[str, Any]] = {}

    scope_locations_per_chunk = max(4, int(os.getenv("SOULLINK_SCOPE_LOCATIONS_PER_CHUNK", "12")))
    scope_max_chunks_per_query = max(1, int(os.getenv("SOULLINK_SCOPE_MAX_CHUNKS_PER_QUERY", "6")))

    def _pick_localized_name(names: List[Dict[str, Any]], fallback: str, language: str) -> str:
        for entry in names:
            if entry.get("language", {}).get("name") == language:
                return str(entry.get("name") or fallback)
        return fallback

    def _get_location_area_display(identifier: str, language: str) -> str:
        key = (identifier, language)
        if key in location_area_display_cache:
            return location_area_display_cache[key]

        fallback = _format_location_area(identifier)
        if language == "en":
            location_area_display_cache[key] = fallback
            return fallback

        try:
            area_data = base_api._get(f"location-area/{identifier}")
            raw_name = _pick_localized_name(area_data.get("names", []), fallback, language)
            display = raw_name.strip() or fallback
        except HTTPError:
            display = fallback

        location_area_name_map[_normalize_location_area_key(display)] = identifier
        location_area_display_cache[key] = display
        return display

    def _register_location_area(identifier: str, scope_key: Optional[Tuple[str, str]] = None) -> None:
        if not identifier:
            return
        normalized_identifier = _normalize_location_area_key(identifier)
        meta = location_area_meta_map.get(normalized_identifier)
        if not meta:
            display = _format_location_area(identifier)
            meta = {
                "identifier": normalized_identifier,
                "display": display,
                "scopes": set(),
            }
            location_area_meta_map[normalized_identifier] = meta
            location_area_cache.append({"name": normalized_identifier, "display": display})

        if scope_key:
            meta["scopes"].add(scope_key)

        location_area_name_map[_normalize_location_area_key(meta["display"])] = normalized_identifier
        location_area_name_map[normalized_identifier] = normalized_identifier

    def _get_scope_key(generation: str, version_group: str) -> Tuple[str, str]:
        return (generation.strip().lower(), version_group.strip().lower())

    def _get_or_create_scope_state(scope_key: Tuple[str, str]) -> Dict[str, Any]:
        if scope_key not in location_scope_states:
            location_scope_states[scope_key] = {
                "initialized": False,
                "done": False,
                "locations": [],
                "cursor": 0,
            }
        return location_scope_states[scope_key]

    def _initialize_scope(scope_key: Tuple[str, str]) -> None:
        generation, version_group = scope_key
        state = _get_or_create_scope_state(scope_key)
        if state["initialized"]:
            return

        try:
            version_group_data = base_api._get(f"version-group/{version_group}")
        except HTTPError:
            state["initialized"] = True
            state["done"] = True
            return

        vg_generation = str(version_group_data.get("generation", {}).get("name") or "").lower().strip()
        if generation and vg_generation and vg_generation != generation:
            state["initialized"] = True
            state["done"] = True
            return

        region_names = [str(item.get("name") or "").strip() for item in version_group_data.get("regions", [])]
        region_names = [item for item in region_names if item]

        if not region_names and generation:
            try:
                generation_data = base_api._get(f"generation/{generation}")
                main_region = str(generation_data.get("main_region", {}).get("name") or "").strip()
                if main_region:
                    region_names = [main_region]
            except HTTPError:
                pass

        locations: List[str] = []
        seen_locations: set[str] = set()
        for region_name in region_names:
            try:
                region_data = base_api._get(f"region/{region_name}")
            except HTTPError:
                continue
            for item in region_data.get("locations", []):
                location_name = str(item.get("name") or "").strip()
                if not location_name or location_name in seen_locations:
                    continue
                seen_locations.add(location_name)
                locations.append(location_name)

        state["locations"] = locations
        state["cursor"] = 0
        state["initialized"] = True
        state["done"] = len(locations) == 0

    def _scope_has_more(scope_key: Tuple[str, str]) -> bool:
        state = _get_or_create_scope_state(scope_key)
        if not state["initialized"]:
            return True
        if state["done"]:
            return False
        return int(state["cursor"]) < len(state["locations"])

    def _load_scope_chunk(scope_key: Tuple[str, str], max_locations: int) -> bool:
        _initialize_scope(scope_key)
        state = _get_or_create_scope_state(scope_key)
        if state["done"]:
            return False

        loaded_any = False
        processed = 0
        locations = state["locations"]
        cursor = int(state["cursor"])

        while cursor < len(locations) and processed < max_locations:
            location_name = locations[cursor]
            cursor += 1
            processed += 1

            try:
                location_detail = base_api._get(f"location/{location_name}")
            except HTTPError:
                continue

            for area_ref in location_detail.get("areas", []):
                area_name = str(area_ref.get("name") or "").strip()
                if not area_name:
                    continue
                _register_location_area(area_name, scope_key=scope_key)
                loaded_any = True

        state["cursor"] = cursor
        if cursor >= len(locations):
            state["done"] = True
        return loaded_any

    def _suggest_location_areas(
        query: str,
        language: str,
        generation: Optional[str],
        version_group: Optional[str],
        limit: int = 10,
    ) -> List[str]:
        query_lc = _normalize_location_area_key(query)
        query_text_lc = query.strip().lower()
        if not query_lc:
            return []

        scope_key: Optional[Tuple[str, str]] = None
        if generation and version_group:
            scope_key = _get_scope_key(generation, version_group)
            _initialize_scope(scope_key)

        if not location_area_cache and scope_key:
            _load_scope_chunk(scope_key, max_locations=scope_locations_per_chunk)

        candidate_matches: List[Tuple[int, str]] = []
        seen: set[str] = set()

        def _match_rank(identifier_key: str, display_key: str, display_text: str) -> Optional[int]:
            # 0: starts with query (best)
            if identifier_key.startswith(query_lc) or display_key.startswith(query_lc):
                return 0
            # 1: starts after a separator / word boundary
            if f"-{query_lc}" in identifier_key or f"-{query_lc}" in display_key:
                return 1
            if query_text_lc and f" {query_text_lc}" in display_text:
                return 1
            # 2: generic substring fallback
            if query_lc in identifier_key or query_lc in display_key:
                return 2
            return None

        def collect_matches() -> None:
            for item in location_area_cache:
                identifier = item["name"]
                if identifier in seen:
                    continue
                meta = location_area_meta_map.get(identifier, {})
                if scope_key is not None:
                    scopes = meta.get("scopes") or set()
                    if scope_key not in scopes:
                        continue
                elif generation or version_group:
                    continue
                identifier_key = _normalize_location_area_key(identifier)
                display_key = _normalize_location_area_key(item["display"])
                display_text = str(item["display"] or "").strip().lower()
                rank = _match_rank(identifier_key, display_key, display_text)
                if rank is None:
                    continue
                seen.add(identifier)
                candidate_matches.append((rank, identifier))
                if len(candidate_matches) >= (limit * 4):
                    break

        collect_matches()

        chunks_loaded_this_query = 0
        while (
            len(candidate_matches) < limit
            and scope_key is not None
            and _scope_has_more(scope_key)
            and chunks_loaded_this_query < scope_max_chunks_per_query
        ):
            _load_scope_chunk(scope_key, max_locations=scope_locations_per_chunk)
            chunks_loaded_this_query += 1
            collect_matches()

        candidate_matches.sort(key=lambda pair: (pair[0], pair[1]))
        results: List[str] = []
        for _, identifier in candidate_matches[:limit]:
            display = _get_location_area_display(identifier, language)
            location_area_name_map[_normalize_location_area_key(display)] = identifier
            results.append(display)
        return results

    def _resolve_location_area(value: str, is_starter: bool, language: str) -> Tuple[str, str]:
        if is_starter:
            return "starter", "Starter"
        if not value:
            return "", ""
        key = _normalize_location_area_key(value)
        identifier = location_area_name_map.get(key)
        if not identifier:
            identifier = _normalize_location_area_key(value)
        return identifier, _get_location_area_display(identifier, language)

    def _get_player_sprite(api: PokeAPI, identifier: str) -> str:
        if not identifier:
            return ""
        try:
            return api.get_pokemon_sprite(identifier, key="official_artwork") or api.get_pokemon_sprite(identifier) or ""
        except HTTPError:
            return ""

    def _serialize_link_data_for_board(link_data: Dict[str, Any]) -> Dict[str, Any]:
        players = _sanitize_players(link_data.get("players", 2))
        language = str(link_data.get("language") or defaults["language"]).lower().strip()
        if language not in supported_languages:
            language = defaults["language"]
            link_data["language"] = language

        api = get_api(
            link_data.get("generation", defaults["generation"]),
            link_data.get("version_group", defaults["version_group"]),
            language,
        )

        for row in link_data.get("rows", []):
            row["status_class"] = _status_css_class(str(row.get("status") or ""))
            location = row.get("location") or {}
            if isinstance(location, dict):
                location_identifier = str(location.get("identifier") or "")
                if location_identifier:
                    location["display"] = _get_location_area_display(location_identifier, language)

            players_data = row.get("players") or []
            normalized_players: List[Dict[str, str]] = []
            for idx in range(players):
                player = players_data[idx] if idx < len(players_data) else {}
                if not isinstance(player, dict):
                    player = {"name": str(player), "identifier": ""}
                name_raw = str(player.get("name") or "").strip()
                identifier = str(player.get("identifier") or "").strip()
                if name_raw and not identifier:
                    identifier = resolve_identifier(
                        name_raw,
                        link_data.get("generation", defaults["generation"]),
                        language,
                        api,
                    )
                normalized_players.append(
                    {
                        "name": name_raw,
                        "identifier": identifier,
                        "sprite": _get_player_sprite(api, identifier) if identifier else "",
                    }
                )
            row["players"] = normalized_players

        return link_data

    def _load_soullink_cache() -> Dict[str, Dict[str, Any]]:
        nonlocal soullink_cache, soullink_cache_maintenance_done
        if soullink_cache is not None:
            if not soullink_cache_maintenance_done:
                _maintain_soullink_cache(soullink_cache)
                soullink_cache_maintenance_done = True
            return soullink_cache

        with soullink_lock:
            if soullink_cache is not None:
                return soullink_cache

            if not os.path.exists(store_path):
                soullink_cache = {}
                return soullink_cache

            try:
                with open(store_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, json.JSONDecodeError):
                soullink_cache = {}
                return soullink_cache

            if isinstance(payload, dict):
                soullink_cache = payload
            else:
                soullink_cache = {}
            if not soullink_cache_maintenance_done:
                _maintain_soullink_cache(soullink_cache)
                soullink_cache_maintenance_done = True
            return soullink_cache

    def _entry_last_changed(entry: Dict[str, Any]) -> Optional[float]:
        updated_raw = entry.get("updated_at")
        created_raw = entry.get("created_at")
        for value in (updated_raw, created_raw):
            try:
                if value is not None:
                    return float(value)
            except (TypeError, ValueError):
                continue
        return None

    def _maintain_soullink_cache(payload: Dict[str, Dict[str, Any]]) -> None:
        now = _now_ts()
        changed = False
        remove_codes: List[str] = []

        for code, entry in payload.items():
            if not isinstance(entry, dict):
                remove_codes.append(code)
                changed = True
                continue

            # Backfill metadata for existing sessions.
            if "created_at" not in entry:
                entry["created_at"] = now
                changed = True
            if "updated_at" not in entry:
                entry["updated_at"] = float(entry.get("created_at") or now)
                changed = True

            if not str(entry.get("session_name") or "").strip():
                entry["session_name"] = f"Session {code}"
                changed = True

            last_changed = _entry_last_changed(entry)
            if last_changed is None:
                continue
            if (now - last_changed) > retention_seconds:
                remove_codes.append(code)
                changed = True

        for code in remove_codes:
            payload.pop(code, None)

        if changed:
            _persist_soullink_cache(payload)

    def _persist_soullink_cache(payload: Dict[str, Dict[str, Any]]) -> None:
        cache_dir = os.path.dirname(store_path)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

        with soullink_lock:
            fd, temp_path = tempfile.mkstemp(prefix="soullink-", suffix=".json", dir=cache_dir or None)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                os.replace(temp_path, store_path)
            finally:
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except OSError:
                    pass

    def _generate_soullink_code(existing: Dict[str, Any]) -> str:
        alphabet = string.ascii_lowercase + string.digits
        for _ in range(10):
            code = "".join(secrets.choice(alphabet) for _ in range(code_length))
            if code not in existing:
                return code
        while True:
            code = "".join(secrets.choice(alphabet) for _ in range(code_length + 2))
            if code not in existing:
                return code

    def _get_evolution_chain_id(api: PokeAPI, identifier: str) -> Optional[str]:
        if not identifier:
            return None
        try:
            species = api.get_pokemon_species(identifier)
        except HTTPError:
            return None
        chain_url = species.get("evolution_chain", {}).get("url")
        if not chain_url:
            return None
        return chain_url.rstrip("/").split("/")[-1]

    def _build_default_soullink(
        generation: str,
        version_group: str,
        language: str,
        players: int,
    ) -> Dict[str, Any]:
        player_count = _sanitize_players(players)
        return {
            "session_name": "",
            "generation": generation,
            "version_group": version_group,
            "language": language,
            "players": player_count,
            "player_names": [f"S{idx}" for idx in range(1, player_count + 1)],
            "rows": [
                {
                    "id": secrets.token_hex(6),
                    "location": {"identifier": "starter", "display": "Starter"},
                    "is_starter": True,
                    "status": "aktiv",
                    "players": [{"name": "", "identifier": ""} for _ in range(player_count)],
                    "errors": [],
                }
            ],
            "created_at": _now_ts(),
            "updated_at": _now_ts(),
        }

    @router.post("/soullink/create", response_class=HTMLResponse)
    def soullink_create(
        request: Request,
        generation: Optional[str] = Form(None),
        version_group: Optional[str] = Form(None),
        language: Optional[str] = Form(None),
        players: int = Form(...),
    ) -> Response:
        selected_generation = (generation or defaults["generation"]).strip()
        selected_version_group = (version_group or defaults["version_group"]).strip()
        selected_language = (language or defaults["language"]).lower().strip()
        if selected_language not in supported_languages:
            selected_language = defaults["language"]

        store = _load_soullink_cache()
        code = _generate_soullink_code(store)
        link_data = _build_default_soullink(
            generation=selected_generation,
            version_group=selected_version_group,
            language=selected_language,
            players=_sanitize_players(players),
        )
        link_data["code"] = code
        link_data["session_name"] = f"Session {code}"
        store[code] = link_data
        _persist_soullink_cache(store)
        return RedirectResponse(f"/soullink/{code}", status_code=303)

    @router.get("/soullink/{code}", response_class=HTMLResponse)
    def soullink_board(request: Request, code: str) -> Response:
        store = _load_soullink_cache()
        link_data = store.get(code)
        if not link_data:
            return HTMLResponse("Soullink nicht gefunden.", status_code=404)
        link_data = _serialize_link_data_for_board(link_data)
        players = _sanitize_players(link_data.get("players", 2))
        status_options = _build_status_options(players)

        payload = {
            "code": code,
            "data": link_data,
            "session_name": str(link_data.get("session_name") or f"Session {code}"),
            "data_json": json.dumps(link_data),
            "status_options": status_options,
            "status_options_json": json.dumps(status_options),
            "generations": base_api.list_generations(),
            "version_groups": get_version_groups_for_generation(link_data.get("generation", defaults["generation"])),
            "languages": supported_languages,
        }
        return templates.TemplateResponse(request, "soullink_board.html", {"request": request, **payload})  # type: ignore[call-arg]

    @router.get("/soullink/{code}/state", response_class=JSONResponse)
    def soullink_state(code: str, since: Optional[float] = None) -> JSONResponse:
        store = _load_soullink_cache()
        link_data = store.get(code)
        if not link_data:
            return JSONResponse({"error": "Soullink nicht gefunden."}, status_code=404)

        updated_at = float(link_data.get("updated_at") or 0.0)
        if since is not None and updated_at <= float(since):
            return JSONResponse({"changed": False, "updated_at": updated_at})

        link_data = _serialize_link_data_for_board(link_data)
        return JSONResponse(
            {
                "changed": True,
                "updated_at": updated_at,
                "session_name": str(link_data.get("session_name") or f"Session {code}"),
                "language": link_data.get("language", defaults["language"]),
                "player_names": link_data.get("player_names", []),
                "rows": link_data.get("rows", []),
            }
        )

    @router.get("/soullink/suggest/location", response_class=JSONResponse)
    def soullink_suggest_location(
        query: str = "",
        language: Optional[str] = None,
        generation: Optional[str] = None,
        version_group: Optional[str] = None,
    ) -> JSONResponse:
        if not query:
            return JSONResponse({"results": []})
        selected_language = str(language or defaults["language"]).lower().strip()
        if selected_language not in supported_languages:
            selected_language = defaults["language"]
        return JSONResponse(
            {
                "results": _suggest_location_areas(
                    query,
                    selected_language,
                    generation=str(generation or "").strip() or None,
                    version_group=str(version_group or "").strip() or None,
                )
            }
        )

    @router.post("/soullink/{code}/save", response_class=JSONResponse)
    async def soullink_save(code: str, request: Request) -> JSONResponse:
        store = _load_soullink_cache()
        link_data = store.get(code)
        if not link_data:
            return JSONResponse({"error": "Soullink nicht gefunden."}, status_code=404)

        payload = await request.json()
        session_name_raw = str(payload.get("session_name") or link_data.get("session_name") or f"Session {code}")
        session_name = session_name_raw.strip() or f"Session {code}"
        if len(session_name) > 120:
            session_name = session_name[:120].rstrip() or f"Session {code}"
        selected_language = str(payload.get("language") or link_data.get("language") or defaults["language"]).lower().strip()
        if selected_language not in supported_languages:
            selected_language = str(link_data.get("language") or defaults["language"]).lower().strip()

        players = _sanitize_players(link_data.get("players", 2))
        api = get_api(
            link_data.get("generation", defaults["generation"]),
            link_data.get("version_group", defaults["version_group"]),
            selected_language,
        )

        player_names = payload.get("player_names") or link_data.get("player_names")
        if not isinstance(player_names, list) or len(player_names) != players:
            player_names = [f"S{idx}" for idx in range(1, players + 1)]

        rows_in = payload.get("rows") or []
        rows_out: List[Dict[str, Any]] = []
        errors_by_row: Dict[str, List[str]] = {}
        used_locations: Dict[str, str] = {}
        chain_seen: Dict[str, Tuple[str, str, str]] = {}

        for row in rows_in:
            row_id = str(row.get("id") or secrets.token_hex(6))
            is_starter = bool(row.get("is_starter"))
            location_raw = str(row.get("location") or "").strip()
            location_id, location_display = _resolve_location_area(location_raw, is_starter=is_starter, language=selected_language)

            status = str(row.get("status") or "aktiv")
            player_entries: List[Dict[str, str]] = []
            row_errors: List[str] = []

            if location_id and location_id in used_locations:
                row_errors.append(f"Location Area bereits belegt: {used_locations[location_id]}.")
            if location_id:
                used_locations[location_id] = location_display

            players_in = row.get("players") or []
            for idx in range(players):
                entry = players_in[idx] if idx < len(players_in) else {}
                name_raw = str(entry.get("name") or "").strip()
                identifier = ""
                if name_raw:
                    identifier = resolve_identifier(
                        name_raw,
                        link_data.get("generation", defaults["generation"]),
                        selected_language,
                        api,
                    )
                player_entries.append(
                    {
                        "name": name_raw,
                        "identifier": identifier,
                        "sprite": _get_player_sprite(api, identifier) if identifier else "",
                    }
                )

                if identifier and location_id:
                    chain_id = _get_evolution_chain_id(api, identifier)
                    if chain_id:
                        if chain_id in chain_seen and chain_seen[chain_id][0] != location_id:
                            _, other_location, other_player = chain_seen[chain_id]
                            row_errors.append(
                                f"Pokémon-Linie bereits in {other_location} bei {other_player} gewählt."
                            )
                        else:
                            chain_seen[chain_id] = (location_id, location_display, player_names[idx])

            errors_by_row[row_id] = row_errors
            rows_out.append(
                {
                    "id": row_id,
                    "location": {"identifier": location_id, "display": location_display},
                    "is_starter": is_starter,
                    "status": status,
                    "players": player_entries,
                    "errors": row_errors,
                }
            )

        link_data["session_name"] = session_name
        link_data["language"] = selected_language
        link_data["player_names"] = player_names
        link_data["rows"] = rows_out
        link_data["updated_at"] = _now_ts()
        store[code] = link_data
        _persist_soullink_cache(store)

        return JSONResponse(
            {
                "ok": True,
                "session_name": session_name,
                "language": selected_language,
                "updated_at": link_data["updated_at"],
                "errors": errors_by_row,
                "rows": rows_out,
            }
        )

    return router


__all__ = ["create_soullink_router", "_sanitize_players"]
