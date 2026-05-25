from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from requests import HTTPError

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pokemon_api import PokeAPI
from urllib.parse import quote

APP_GENERATION = os.getenv("POKEAPI_GENERATION", "generation-i")
APP_VERSION_GROUP = os.getenv("POKEAPI_VERSION_GROUP", "red-blue")
APP_LANGUAGE = os.getenv("POKEAPI_LANGUAGE", "de")
APP_LOG_LEVEL = os.getenv("APP_LOG_LEVEL", "INFO").upper()
APP_ENABLE_GLOBAL_NAME_FALLBACK = os.getenv("POKEAPI_ENABLE_GLOBAL_NAME_FALLBACK", "false").lower() == "true"

logging.basicConfig(
    level=getattr(logging, APP_LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("pokeapi.web")

app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
templates.env.cache = None

_api_cache: Dict[Tuple[str, str, str], PokeAPI] = {}
_name_cache_by_generation: Dict[str, List[str]] = {}
_name_cache_lc_by_generation: Dict[str, List[str]] = {}
_name_cache_de_by_generation: Dict[str, List[str]] = {}
_name_cache_de_lc_by_generation: Dict[str, List[str]] = {}
_name_to_identifier_by_generation: Dict[str, Dict[str, str]] = {}
_localized_name_cache_ready_by_generation: Dict[str, bool] = {}
_name_to_identifier_global: Optional[Dict[str, str]] = None
_species_identifiers_by_generation: Dict[str, List[str]] = {}
_all_species_index_cache: Optional[List[Tuple[int, str]]] = None

_GENERATION_NATDEX_CUTOFF: Dict[str, int] = {
    "generation-i": 151,
    "generation-ii": 251,
    "generation-iii": 386,
    "generation-iv": 493,
    "generation-v": 649,
    "generation-vi": 721,
    "generation-vii": 809,
    "generation-viii": 905,
    "generation-ix": 1025,
}

base_api = PokeAPI(generation=APP_GENERATION, version_group=APP_VERSION_GROUP, language=APP_LANGUAGE)
logger.info(
    "App gestartet mit generation=%s, version_group=%s, language=%s",
    APP_GENERATION,
    APP_VERSION_GROUP,
    APP_LANGUAGE,
)


def _parse_level(level_raw: Optional[str]) -> Optional[int]:
    if not level_raw:
        return None
    try:
        value = int(level_raw)
    except ValueError:
        return None
    return value if value > 0 else None


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    name: Optional[str] = Query(default=None),
    level: Optional[str] = Query(default=None),
    generation: Optional[str] = Query(default=None),
    version_group: Optional[str] = Query(default=None),
) -> HTMLResponse:
    selected_generation = generation or APP_GENERATION
    selected_version_group = version_group or APP_VERSION_GROUP

    logger.debug(
        "GET / mit name=%s level=%s generation=%s version_group=%s",
        name,
        level,
        selected_generation,
        selected_version_group,
    )
    api = _get_api(selected_generation, selected_version_group, APP_LANGUAGE)
    generations = base_api.list_generations()
    version_groups = _get_version_groups_for_generation(selected_generation)
    data: Dict[str, Any] = {
        "name": name or "",
        "level": level or "",
        "generation": selected_generation,
        "version_group": selected_version_group,
        "generations": generations,
        "version_groups": version_groups,
        "error": None,
        "pokemon": None,
        "moves": [],
        "effective_types": [],
        "dangerous_types": [],
        "evolution_chain": [],
    }

    if not name:
        return _render_template(request, data)

    level_value = _parse_level(level)

    try:
        resolved_name = _resolve_identifier(name, selected_generation, api=api)
        pokemon_name = _get_display_name(api, resolved_name)
        capture_rate = _get_capture_rate(api, resolved_name)
        own_types = _get_pokemon_type_entries(api, resolved_name)
        sprite = api.get_pokemon_sprite(resolved_name, key="official_artwork") or api.get_pokemon_sprite(resolved_name)
        pokedex_entry = _get_pokedex_entry(api, resolved_name, selected_version_group)
        english_name = _get_english_pokemon_name(api, resolved_name)

        # Fetch all moves (no level cap) and mark the last 4 moves the Pokémon can learn up to the given level
        all_moves = api.get_pokemon_moves(resolved_name, level=None, version_group=selected_version_group, limit=1000)
        effective = api.list_attacking_type_multipliers(resolved_name, up_to_generation=selected_generation)

        # Determine the subset of moves to use for 'Besonders gefaehrdet' and highlighting
        if level_value is not None:
            # moves with a defined level <= level_value
            eligible = [m for m in all_moves if isinstance(m.get("level"), int) and m.get("level") <= level_value]
            # sort by level desc, then name
            eligible_sorted = sorted(eligible, key=lambda m: (int(m.get("level", 0)), m.get("identifier", "")), reverse=True)
            selected_dangerous_moves = eligible_sorted[:4]
        else:
            selected_dangerous_moves = all_moves

        dangerous = api.list_dangerous_types_from_moves(selected_dangerous_moves, up_to_generation=selected_generation)

        # Highlight keys: (level, identifier) for selected moves
        highlighted_keys: set[Tuple[int, str]] = set()
        if level_value is not None:
            for move in selected_dangerous_moves:
                try:
                    move_level = int(move.get("level", 0))
                except (TypeError, ValueError):
                    move_level = 0
                move_identifier = str(move.get("identifier") or "")
                highlighted_keys.add((move_level, move_identifier))

        moves = _attach_move_type_sprites(api, all_moves)
        for move in moves:
            try:
                move_level = int(move.get("level", 0))
            except (TypeError, ValueError):
                move_level = 0
            move_identifier = str(move.get("identifier") or "")
            move["highlight"] = (move_level, move_identifier) in highlighted_keys
            # attach pokewiki url for move using english name if available
            english = move.get("english_name") or move.get("name")
            move["pokewiki_url"] = _build_pokewiki_move_url(english)

        effective_types = _attach_type_sprites(api, effective)
        for item in effective_types:
            item["strength_color"] = _multiplier_color(item.get("multiplier", 1.0))
        dangerous_types = _attach_type_sprites(api, [item for item in dangerous if item["count"] > 0])
        evolution_chain = _get_evolution_chain(api, resolved_name)

        data.update(
            {
                "pokemon": {
                    "display_name": pokemon_name,
                    "capture_rate": capture_rate,
                    "capture_rate_color": _capture_rate_color(capture_rate),
                    "sprite": sprite,
                    "types": own_types,
                    "pokedex_entry": pokedex_entry,
                    "english_name": english_name,
                    "pokewiki_url": _build_pokewiki_url(api, resolved_name),
                },
                "moves": moves,
                "effective_types": effective_types,
                "dangerous_types": dangerous_types,
                "evolution_chain": evolution_chain,
            }
        )
    except HTTPError as exc:
        logger.warning(
            "HTTPError bei Suche nach '%s' (generation=%s, version_group=%s): %s",
            name,
            selected_generation,
            selected_version_group,
            exc,
        )
        data["error"] = f"API error: {exc.response.status_code}"
    except Exception as exc:
        logger.exception(
            "Unerwarteter Fehler bei Suche nach '%s' (generation=%s, version_group=%s)",
            name,
            selected_generation,
            selected_version_group,
        )
        data["error"] = f"Error: {exc}"

    return _render_template(request, data)


@app.get("/version-groups", response_class=JSONResponse)
def version_groups(generation: str) -> JSONResponse:
    groups = _get_version_groups_for_generation(generation)
    return JSONResponse({"results": groups})


@app.get("/suggest", response_class=JSONResponse)
def suggest(query: str = "", generation: Optional[str] = None) -> JSONResponse:
    generation_value = generation or APP_GENERATION
    if not query:
        return JSONResponse({"results": []})
    _ensure_name_cache(generation_value)
    query_lc = query.strip().lower()
    results: List[str] = []
    names = _name_cache_de_by_generation.get(generation_value, [])
    names_lc = _name_cache_de_lc_by_generation.get(generation_value, [])
    for name, name_lc in zip(names, names_lc):
        if name_lc.startswith(query_lc):
            results.append(name)
        if len(results) >= 10:
            break
    return JSONResponse({"results": results})


def _attach_type_sprites(api: PokeAPI, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for item in items:
        type_id = item.get("type_identifier") or ""
        sprite = api.get_type_sprite(type_id) if type_id else None
        enriched.append({**item, "sprite": sprite})
    return enriched


def _get_pokemon_type_entries(api: PokeAPI, identifier: str) -> List[Dict[str, Any]]:
    pokemon = api.get_pokemon(identifier)
    entries: List[Dict[str, Any]] = []
    for item in pokemon.get("types", []):
        type_id = item.get("type", {}).get("name")
        if not type_id:
            continue
        entries.append({
            "type": api.get_type(type_id).get("names", []) and api._pick_name(api.get_type(type_id).get("names", []), type_id) or type_id,
            "type_identifier": type_id,
        })
    return _attach_type_sprites(api, entries)


def _attach_move_type_sprites(api: PokeAPI, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for item in items:
        type_id = item.get("type_identifier") or ""
        sprite = api.get_type_sprite(type_id) if type_id else None
        enriched.append({**item, "type_sprite": sprite})
    return enriched


def _render_template(request: Request, data: Dict[str, Any]) -> HTMLResponse:
    context = {"request": request, **data}
    try:
        return templates.TemplateResponse(request, "index.html", context)
    except TypeError:
        return templates.TemplateResponse("index.html", context)


def _get_api(generation: str, version_group: str, language: str) -> PokeAPI:
    key = (generation, version_group, language)
    if key not in _api_cache:
        _api_cache[key] = PokeAPI(generation=generation, version_group=version_group, language=language)
    return _api_cache[key]


def _ensure_name_cache(generation: str) -> None:
    if (
        generation in _name_cache_by_generation
        and generation in _name_cache_lc_by_generation
        and generation in _name_cache_de_by_generation
        and generation in _name_cache_de_lc_by_generation
        and generation in _name_to_identifier_by_generation
    ):
        return

    names = _get_species_identifiers_up_to_generation(generation)
    if not names:
        try:
            data = base_api._get(f"generation/{generation}")
            species = data.get("pokemon_species", [])
            names = [item["name"] for item in species]
        except HTTPError:
            data = base_api._get("pokemon-species?limit=20000")
            names = [item["name"] for item in data.get("results", [])]

    # Wichtig für Performance: KEINE N+1-Calls mehr auf pokemon-species/{name}.
    # Für Suggestions nutzen wir den Identifier direkt als Anzeige-Name.
    # Das sind genau 1 API-Call pro Generation (bzw. Fallback-Liste).
    display_names = [name.replace("-", " ").title() for name in names]
    name_map: Dict[str, str] = {}
    for name, display_name in zip(names, display_names):
        name_map[_normalize_name_key(display_name)] = name
        name_map[_normalize_name_key(name)] = name

    _name_cache_by_generation[generation] = names
    _name_cache_lc_by_generation[generation] = [name.lower() for name in names]
    _name_cache_de_by_generation[generation] = display_names
    _name_cache_de_lc_by_generation[generation] = [_normalize_name_key(name) for name in display_names]
    _name_to_identifier_by_generation[generation] = name_map


def _extract_resource_id(url: str) -> Optional[int]:
    if not url:
        return None
    stripped = url.rstrip("/")
    tail = stripped.split("/")[-1]
    try:
        return int(tail)
    except ValueError:
        return None


def _load_all_species_index() -> List[Tuple[int, str]]:
    global _all_species_index_cache
    if _all_species_index_cache is not None:
        return _all_species_index_cache

    data = base_api._get("pokemon-species?limit=20000")
    rows: List[Tuple[int, str]] = []
    for item in data.get("results", []):
        name = item.get("name")
        species_id = _extract_resource_id(item.get("url", ""))
        if not name or species_id is None:
            continue
        rows.append((species_id, name))

    rows.sort(key=lambda pair: pair[0])
    _all_species_index_cache = rows
    return rows


def _get_species_identifiers_up_to_generation(generation: str) -> List[str]:
    generation_key = generation.lower().strip()
    if generation_key in _species_identifiers_by_generation:
        return _species_identifiers_by_generation[generation_key]

    all_rows = _load_all_species_index()
    cutoff = _GENERATION_NATDEX_CUTOFF.get(generation_key)
    if cutoff is None:
        names = [name for _, name in all_rows]
    else:
        names = [name for species_id, name in all_rows if species_id <= cutoff]

    _species_identifiers_by_generation[generation_key] = names
    return names


def _ensure_localized_name_cache(generation: str) -> None:
    if _localized_name_cache_ready_by_generation.get(generation):
        return

    _ensure_name_cache(generation)
    names = _name_cache_by_generation.get(generation, [])
    if not names:
        _localized_name_cache_ready_by_generation[generation] = True
        return

    display_names = list(_name_cache_de_by_generation.get(generation, []))
    if len(display_names) != len(names):
        display_names = [name.replace("-", " ").title() for name in names]

    name_map = dict(_name_to_identifier_by_generation.get(generation, {}))

    logger.info("Baue lokalisierte Namenszuordnung für %s (%d Spezies)", generation, len(names))
    for idx, identifier in enumerate(names):
        try:
            species_data = base_api._get(f"pokemon-species/{identifier}")
            localized_name = _pick_localized_name(species_data.get("names", []), identifier)
        except HTTPError:
            localized_name = identifier

        display_names[idx] = localized_name
        name_map[_normalize_name_key(localized_name)] = identifier
        name_map[_normalize_name_key(identifier)] = identifier

    _name_cache_de_by_generation[generation] = display_names
    _name_cache_de_lc_by_generation[generation] = [_normalize_name_key(item) for item in display_names]
    _name_to_identifier_by_generation[generation] = name_map
    _localized_name_cache_ready_by_generation[generation] = True
    logger.info("Lokalisierte Namenszuordnung für %s aufgebaut", generation)


def _resolve_identifier(name: str, generation: str, api: Optional[PokeAPI] = None) -> str:
    _ensure_name_cache(generation)
    mapping = _name_to_identifier_by_generation.get(generation, {})
    normalized = _normalize_name_key(name)
    if normalized in mapping:
        return mapping[normalized]

    direct_identifier = _normalize_identifier(name)

    # Schneller Check: wenn das bereits ein valider Identifier ist, direkt nutzen.
    if direct_identifier:
        probe_api = api or _get_api(generation, APP_VERSION_GROUP, APP_LANGUAGE)
        try:
            probe_api.get_pokemon(direct_identifier)
            return direct_identifier
        except HTTPError:
            pass

    # Langsamer Fallback nur bei Bedarf: pro Generation einmal deutsche Namen laden.
    _ensure_localized_name_cache(generation)
    mapping = _name_to_identifier_by_generation.get(generation, {})
    if normalized in mapping:
        return mapping[normalized]

    # Optionaler Fallback (deaktiviert per Default), falls man bewusst globale
    # Namensauflösung für exotische Eingaben aktivieren möchte.
    if APP_ENABLE_GLOBAL_NAME_FALLBACK:
        _ensure_global_name_cache()
        if _name_to_identifier_global and normalized in _name_to_identifier_global:
            return _name_to_identifier_global[normalized]

    return direct_identifier


def _pick_localized_name(names: List[Dict[str, Any]], fallback: str) -> str:
    for entry in names:
        if entry.get("language", {}).get("name") == APP_LANGUAGE:
            return entry.get("name", fallback)
    return fallback


def _ensure_global_name_cache() -> None:
    global _name_to_identifier_global
    if _name_to_identifier_global is not None:
        return
    try:
        data = base_api._get("pokemon-species?limit=20000")
    except HTTPError:
        _name_to_identifier_global = {}
        return
    mapping: Dict[str, str] = {}
    for item in data.get("results", []):
        identifier = item.get("name")
        if not identifier:
            continue
        display_name = identifier.replace("-", " ").title()
        mapping[_normalize_name_key(display_name)] = identifier
        mapping[_normalize_name_key(identifier)] = identifier
    _name_to_identifier_global = mapping


def _get_display_name(api: PokeAPI, identifier: str) -> str:
    try:
        return api.get_pokemon_name(identifier)
    except HTTPError:
        try:
            data = api.get_pokemon(identifier)
            raw = data.get("name", identifier)
            return raw.replace("-", " ").title()
        except HTTPError:
            return identifier


def _get_english_pokemon_name(api: PokeAPI, identifier: str) -> str:
    try:
        data = api.get_pokemon(identifier)
        raw = str(data.get("name", identifier))
    except HTTPError:
        raw = identifier
    return raw.strip().lower()


def _select_recent_moves(moves: List[Dict[str, Any]], count: int = 4) -> List[Dict[str, Any]]:
    ranked = sorted(
        moves,
        key=lambda item: (int(item.get("level", 0) or 0), str(item.get("identifier", ""))),
        reverse=True,
    )
    return ranked[: max(0, count)]


def _build_pokewiki_url(api: PokeAPI, identifier: str) -> Optional[str]:
    try:
        species = api.get_pokemon_species(identifier)
    except HTTPError:
        return None
    # prefer English localized name
    names = species.get("names", [])
    en_name = None
    for entry in names:
        if entry.get("language", {}).get("name") == "en":
            en_name = entry.get("name")
            break
    if not en_name:
        en_name = species.get("name")
    if not en_name:
        return None
    # pokeWiki expects specific encoding
    return f"https://www.pokewiki.de/{quote(en_name)}"


def _build_pokewiki_move_url(move_english_name: str) -> Optional[str]:
    if not move_english_name:
        return None
    return f"https://www.pokewiki.de/{quote(move_english_name)}"


def _normalize_name_key(name: str) -> str:
    value = name.strip().lower()
    value = value.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    value = value.replace(" ", "-")
    return value


def _normalize_identifier(name: str) -> str:
    return name.strip().lower().replace(" ", "-")


def _get_capture_rate(api: PokeAPI, identifier: str) -> Optional[int]:
    try:
        return api.get_capture_rate(identifier)
    except HTTPError:
        return None


def _get_version_groups_for_generation(generation: str) -> List[str]:
    try:
        data = base_api._get(f"generation/{generation}")
    except HTTPError:
        return base_api.list_version_groups()
    groups = data.get("version_groups", [])
    return [item["name"] for item in groups]


def _capture_rate_color(rate: Optional[int]) -> str:
    if rate is None:
        return "#94a3b8"
    ratio = max(0, min(rate, 255)) / 255.0
    red = int(255 * (1 - ratio))
    green = int(255 * ratio)
    return f"rgb({red}, {green}, 80)"


def _multiplier_color(multiplier: float) -> str:
    # Gewünschte Darstellung:
    # x1.0 -> grau/weiß, x0.5 -> orange, x0 -> rot, >1 -> grün
    try:
        value = float(multiplier)
    except (TypeError, ValueError):
        value = 1.0

    if value <= 0.0:
        return "rgb(220, 38, 38)"  # rot
    if abs(value - 0.5) < 1e-9:
        return "rgb(249, 115, 22)"  # orange
    if abs(value - 1.0) < 1e-9:
        return "rgb(203, 213, 225)"  # grau/weiß

    if value > 1.0:
        # von hellgrün (x2) zu kräftigerem grün (x4)
        clamped = min(4.0, value)
        ratio = (clamped - 1.0) / 3.0
        red = int(134 + (34 - 134) * ratio)
        green = int(239 + (197 - 239) * ratio)
        blue = int(172 + (94 - 172) * ratio)
        return f"rgb({red}, {green}, {blue})"

    # z. B. 0.25
    return "rgb(251, 146, 60)"  # dunkleres orange


def _get_pokedex_entry(api: PokeAPI, identifier: str, version_group: str) -> Optional[str]:
    try:
        species = api.get_pokemon_species(identifier)
    except HTTPError:
        return None

    entries = species.get("flavor_text_entries", [])
    if not isinstance(entries, list):
        return None

    version_candidates = set(part for part in version_group.lower().split("-") if part)
    fallback_text: Optional[str] = None

    for entry in entries:
        lang = entry.get("language", {}).get("name")
        if lang != APP_LANGUAGE:
            continue

        text_raw = entry.get("flavor_text") or ""
        text = text_raw.replace("\n", " ").replace("\f", " ").strip()
        if not text:
            continue

        version_name = (entry.get("version", {}).get("name") or "").lower()
        if version_name in version_candidates:
            return text
        if fallback_text is None:
            fallback_text = text

    return fallback_text


def _get_evolution_chain(api: PokeAPI, identifier: str) -> List[Dict[str, Any]]:
    species = api.get_pokemon_species(identifier)
    chain_url = species.get("evolution_chain", {}).get("url")
    if not chain_url:
        return []
    chain_id = chain_url.rstrip("/").split("/")[-1]
    data = api._get(f"evolution-chain/{chain_id}")
    chain = data.get("chain")
    if not chain:
        return []
    if not chain.get("evolves_to"):
        return []
    entries = _flatten_chain_entries(chain, is_base=True, level=None)
    if len(entries) <= 1:
        return []
    result: List[Dict[str, Any]] = []
    for entry in entries:
        name = entry["name"]
        level = entry.get("level")
        try:
            display_name = _get_display_name(api, name)
            sprite = api.get_pokemon_sprite(name, key="official_artwork") or api.get_pokemon_sprite(name)
            result.append(
                {
                    "identifier": name,
                    "name": display_name,
                    "query_name": display_name,
                    "sprite": sprite,
                    "level": level,
                    "is_base": entry.get("is_base", False),
                }
            )
        except HTTPError:
            continue
    return result


def _flatten_chain_entries(node: Dict[str, Any], is_base: bool, level: Optional[int]) -> List[Dict[str, Any]]:
    entries = [
        {
            "name": node.get("species", {}).get("name", ""),
            "level": level,
            "is_base": is_base,
        }
    ]
    for child in node.get("evolves_to", []):
        details = child.get("evolution_details", [])
        next_level = details[0].get("min_level") if details else None
        entries.extend(_flatten_chain_entries(child, is_base=False, level=next_level))
    return [entry for entry in entries if entry.get("name")]
